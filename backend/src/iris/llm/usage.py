import io
import logging
import math
import os
import re
import threading
import time
import wave
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from iris.config import PRICES_CHECKED, ModelPrice
from iris.models import Session, SessionUsage, Stage, UsageRecord, UsageTotals
from iris.store.layout import write_model

if TYPE_CHECKING:
    from iris.pipeline.context import StageContext

log = logging.getLogger(__name__)

STAGE_PURPOSES: dict[Stage, str] = {
    Stage.TRANSCRIBE: "transcription",
    Stage.SELECT: "screen_reading",
    Stage.WRITE: "notes",
}

TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "input_image_tokens",
    "input_audio_tokens",
    "output_tokens",
    "reasoning_tokens",
    "output_image_tokens",
    "images",
)
MAX_ERROR_CHARS = 500


@dataclass
class _Scope:
    log: Path
    summary: Path
    stage: Stage | None
    prices: Mapping[str, ModelPrice]
    recorded: int = 0


_scope: ContextVar[_Scope | None] = ContextVar("iris_usage_scope", default=None)
_purpose: ContextVar[str | None] = ContextVar("iris_usage_purpose", default=None)
_lock = threading.Lock()


@contextmanager
def recording(ctx: "StageContext", stage: Stage | None) -> Iterator[None]:
    scope = _Scope(ctx.paths.usage_log, ctx.paths.usage, stage, ctx.settings.model_prices)
    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)
        if scope.recorded:
            try:
                write_summary(scope.log, scope.summary)
            except Exception:
                log.warning("could not write %s", scope.summary, exc_info=True)


@contextmanager
def purpose(name: str) -> Iterator[None]:
    token = _purpose.set(name)
    try:
        yield
    finally:
        _purpose.reset(token)


def _attr_or_key(obj: Any, *path: str) -> Any:
    for name in path:
        if obj is None:
            return None
        obj = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    return obj


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(int(value), 0)


def _wav_seconds(request: Mapping[str, Any]) -> float | None:
    file = request.get("file")
    data = file[1] if isinstance(file, tuple) and len(file) > 1 else file
    if not isinstance(data, bytes | bytearray):
        return None
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / w.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError):
        return None


def billed_seconds(seconds: float) -> float:
    return float(math.ceil(round(seconds, 3)))


def _duration_billed_seconds(usage: Any, request: Mapping[str, Any], response: Any) -> float | None:
    seconds = _attr_or_key(usage, "seconds")
    if not isinstance(seconds, int | float):
        seconds = _attr_or_key(response, "duration")
    if not isinstance(seconds, int | float):
        seconds = _wav_seconds(request)
    return seconds


def measure(operation: str, request: Mapping[str, Any], response: Any) -> dict[str, float]:
    usage = _attr_or_key(response, "usage")
    m: dict[str, float] = {}
    if operation.startswith("chat."):
        m["input_tokens"] = _int(_attr_or_key(usage, "prompt_tokens"))
        m["cached_input_tokens"] = _int(
            _attr_or_key(usage, "prompt_tokens_details", "cached_tokens")
        )
        m["input_image_tokens"] = _int(_attr_or_key(usage, "prompt_tokens_details", "image_tokens"))
        m["input_audio_tokens"] = _int(_attr_or_key(usage, "prompt_tokens_details", "audio_tokens"))
        m["output_tokens"] = _int(_attr_or_key(usage, "completion_tokens"))
        m["reasoning_tokens"] = _int(
            _attr_or_key(usage, "completion_tokens_details", "reasoning_tokens")
        )
    elif operation.startswith("responses."):
        m["input_tokens"] = _int(_attr_or_key(usage, "input_tokens"))
        m["cached_input_tokens"] = _int(
            _attr_or_key(usage, "input_tokens_details", "cached_tokens")
        )
        m["output_tokens"] = _int(_attr_or_key(usage, "output_tokens"))
        m["reasoning_tokens"] = _int(
            _attr_or_key(usage, "output_tokens_details", "reasoning_tokens")
        )
    elif operation.startswith("images."):
        m["input_tokens"] = _int(_attr_or_key(usage, "input_tokens"))
        m["input_image_tokens"] = _int(_attr_or_key(usage, "input_tokens_details", "image_tokens"))
        m["output_tokens"] = _int(_attr_or_key(usage, "output_tokens"))
        output_breakdown = _attr_or_key(usage, "output_tokens_details")
        m["output_image_tokens"] = (
            _int(_attr_or_key(output_breakdown, "image_tokens"))
            if output_breakdown is not None
            else m["output_tokens"]
        )
        m["images"] = len(_attr_or_key(response, "data") or [])
    elif operation.startswith("audio."):
        if (
            _attr_or_key(usage, "type") == "tokens"
            or _attr_or_key(usage, "input_tokens") is not None
        ):
            m["input_tokens"] = _int(_attr_or_key(usage, "input_tokens"))
            m["input_audio_tokens"] = _int(
                _attr_or_key(usage, "input_token_details", "audio_tokens")
            )
            m["output_tokens"] = _int(_attr_or_key(usage, "output_tokens"))
        else:
            seconds = _duration_billed_seconds(usage, request, response)
            if seconds is not None and seconds > 0:
                m["audio_seconds"] = billed_seconds(float(seconds))
    return m


_DATED_SNAPSHOT_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def find_price(model: str, prices: Mapping[str, ModelPrice]) -> ModelPrice | None:
    return prices.get(model) or prices.get(_DATED_SNAPSHOT_SUFFIX.sub("", model))


def price_call(
    model: str, m: Mapping[str, float], prices: Mapping[str, ModelPrice]
) -> tuple[float | None, str | None]:
    price = find_price(model, prices)
    if price is None:
        return None, f"no price for model {model!r}"
    input_tokens = m.get("input_tokens", 0)
    if price.long_context_above is not None and input_tokens > price.long_context_above:
        if price.long_context is None:
            return None, f"{model}: no long-context price"
        price = price.long_context
    image_in = m.get("input_image_tokens", 0) if price.image_input is not None else 0
    audio_in = m.get("input_audio_tokens", 0) if price.audio_input is not None else 0
    text_in = max(input_tokens - image_in - audio_in, 0)
    cached = min(m.get("cached_input_tokens", 0), text_in)
    image_out = m.get("output_image_tokens", 0) if price.image_output is not None else 0
    text_out = max(m.get("output_tokens", 0) - image_out, 0)
    per_million = [
        ("input", text_in - cached, price.input),
        ("cached input", cached, price.cached_input),
        ("image input", image_in, price.image_input),
        ("audio input", audio_in, price.audio_input),
        ("output", text_out, price.output),
        ("image output", image_out, price.image_output),
    ]
    cost, missing = 0.0, []
    for name, quantity, rate in per_million:
        if quantity:
            if rate is None:
                missing.append(name)
            else:
                cost += quantity * rate / 1_000_000
    if seconds := m.get("audio_seconds", 0):
        if price.audio_per_second is None:
            missing.append("per-second audio")
        else:
            cost += seconds * price.audio_per_second
    if missing:
        return None, f"{model}: no {', '.join(missing)} price"
    return round(cost, 8), None


def record_call(
    operation: str,
    request: Mapping[str, Any],
    response: Any,
    latency_s: float,
    error: BaseException | None = None,
) -> None:
    try:
        scope = _scope.get()
        if scope is None:
            log.debug("model call outside a usage scope not recorded: %s", operation)
            return
        model = str(request.get("model") or _attr_or_key(response, "model") or "unknown")
        m = {} if error is not None else measure(operation, request, response)
        if request.get("stream"):
            cost, unpriced = None, "streamed responses are not measured"
        else:
            cost, unpriced = price_call(model, m, scope.prices)
        record = UsageRecord(
            at=datetime.now(UTC),
            stage=scope.stage,
            purpose=_purpose.get() or STAGE_PURPOSES.get(scope.stage, operation),
            operation=operation,
            model=model,
            latency_s=round(latency_s, 3),
            cost_usd=cost,
            unpriced=unpriced,
            error=None if error is None else f"{type(error).__name__}: {error}"[:MAX_ERROR_CHARS],
            **m,
        )
        append_record(scope.log, record)
        scope.recorded += 1
    except Exception:
        log.warning("could not record usage of %s", operation, exc_info=True)


@contextmanager
def timed(operation: str, request: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    out: dict[str, Any] = {}
    start = time.perf_counter()
    try:
        yield out
    except Exception as exc:
        record_call(operation, request, None, time.perf_counter() - start, exc)
        raise
    record_call(operation, request, out.get("response"), time.perf_counter() - start)


def _last_line_cut_off(f: io.BufferedRandom) -> bool:
    size = f.seek(0, io.SEEK_END)
    if not size:
        return False
    f.seek(size - 1)
    return f.read(1) != b"\n"


def append_record(log_path: Path, record: UsageRecord) -> None:
    line = (record.model_dump_json() + "\n").encode()
    with _lock, log_path.open("a+b") as f:
        if _last_line_cut_off(f):
            line = b"\n" + line
        f.write(line)


def _split_at_newlines_only(text: str) -> list[str]:
    return text.split("\n")


def read_records(log_path: Path) -> list[UsageRecord]:
    if not log_path.exists():
        return []
    records = []
    with _lock:
        lines = _split_at_newlines_only(log_path.read_text(encoding="utf-8", errors="replace"))
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            records.append(UsageRecord.model_validate_json(line))
        except ValueError:
            log.warning("skipping unreadable line %d of %s", n, log_path)
    return records


def write_summary(log_path: Path, summary_path: Path) -> None:
    write_model(summary_path, read_usage(log_path))


def summarize(records: list[UsageRecord]) -> SessionUsage:
    total = UsageTotals()
    by_stage: dict[str, UsageTotals] = {}
    by_model: dict[str, UsageTotals] = {}
    by_purpose: dict[str, UsageTotals] = {}
    for r in records:
        stage = r.stage.value if r.stage else "none"
        for totals in (
            total,
            by_stage.setdefault(stage, UsageTotals()),
            by_model.setdefault(r.model, UsageTotals()),
            by_purpose.setdefault(r.purpose, UsageTotals()),
        ):
            _add(totals, r)
    order = {s.value: i for i, s in enumerate(Stage)}
    return SessionUsage(
        prices_checked=PRICES_CHECKED,
        records=records,
        total=total,
        by_stage=dict(sorted(by_stage.items(), key=lambda kv: order.get(kv[0], len(order)))),
        by_model=dict(sorted(by_model.items())),
        by_purpose=dict(sorted(by_purpose.items())),
    )


def _add(totals: UsageTotals, r: UsageRecord) -> None:
    totals.calls += 1
    totals.failed_calls += r.error is not None
    if r.cost_usd is None:
        totals.unpriced_calls += 1
    else:
        totals.cost_usd = round(totals.cost_usd + r.cost_usd, 8)
    totals.latency_s = round(totals.latency_s + r.latency_s, 3)
    totals.audio_seconds += r.audio_seconds
    for name in TOKEN_FIELDS:
        setattr(totals, name, getattr(totals, name) + getattr(r, name))


def read_usage(log_path: Path) -> SessionUsage:
    return summarize(read_records(log_path))


@dataclass
class _Tally:
    device_and_inode: tuple[int, int]
    counted_bytes: int
    totals: UsageTotals


_tallies: dict[Path, _Tally] = {}
_tally_lock = threading.Lock()


def running_total(log_path: Path) -> UsageTotals:
    with _tally_lock:
        try:
            with log_path.open("rb") as f:
                return _advance(log_path, f)
        except FileNotFoundError:
            _tallies.pop(log_path, None)
            return UsageTotals()
        except OSError:
            log.warning("could not read %s", log_path, exc_info=True)
            _tallies.pop(log_path, None)
            return UsageTotals()


def _advance(log_path: Path, f: io.BufferedReader) -> UsageTotals:
    st = os.fstat(f.fileno())
    device_and_inode = (st.st_dev, st.st_ino)
    tally = _tallies.get(log_path)
    if (
        tally is None
        or tally.device_and_inode != device_and_inode
        or st.st_size < tally.counted_bytes
    ):
        tally = _tallies[log_path] = _Tally(device_and_inode, 0, UsageTotals())
    if st.st_size > tally.counted_bytes:
        f.seek(tally.counted_bytes)
        data = f.read(st.st_size - tally.counted_bytes)
        complete_lines_end = data.rfind(b"\n") + 1
        for line in data[:complete_lines_end].split(b"\n"):
            if not line.strip():
                continue
            try:
                _add(tally.totals, UsageRecord.model_validate_json(line))
            except ValueError:
                log.warning("skipping unreadable line in %s", log_path)
        tally.counted_bytes += complete_lines_end
    return tally.totals.model_copy()


def with_cost(session: Session, log_path: Path) -> Session:
    totals = running_total(log_path)
    if not totals.calls:
        return session
    return session.model_copy(
        update={"cost_usd": totals.cost_usd, "cost_partial": totals.unpriced_calls > 0}
    )


def forget_total(log_path: Path) -> None:
    with _tally_lock:
        _tallies.pop(log_path, None)
