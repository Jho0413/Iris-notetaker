"""Model-call accounting: every OpenAI call made in a stage is recorded for its session.

How the pieces fit:

- ``recording(ctx, stage)`` names the session and stage that calls made inside it belong to. The
  job runner enters it around every stage. It is held in a ``ContextVar``, so stages running
  concurrently (and their concurrent calls) each keep their own.
- ``purpose(name)`` labels calls where one stage makes several kinds (render: SVG, image, figure
  check). Other stages get a default purpose from ``STAGE_PURPOSES``.
- ``iris.llm.client.get_openai`` returns a client whose model-call methods report each call to
  ``record_call``: usage read from the response, latency, and cost from ``Settings.model_prices``.
- Recording never fails a call or a stage: any error while recording is logged and dropped.

Cached work makes no call, so a re-run that hits its caches adds no records and no cost.

Files (``store/layout.py``):

- ``usage_calls.jsonl``: one ``UsageRecord`` per line, appended as each call finishes. This is the
  source of truth: appending is constant-time however long the session, and a crash loses at most
  the line being written (unreadable lines are skipped with a warning when read).
- ``usage.json``: ``SessionUsage`` (records plus totals per stage, model and purpose), rewritten
  from the log when a stage that made calls ends. The API and ``iris cost`` read the log, so they
  are current while a stage is still running.

Appends are serialized by a lock, so concurrent stages in this process never interleave lines.
"""

import io
import logging
import math
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
from iris.models import SessionUsage, Stage, UsageRecord, UsageTotals
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
    """Record model calls made inside this block for the session; refresh ``usage.json`` after."""
    scope = _Scope(ctx.paths.usage_log, ctx.paths.usage, stage, ctx.settings.model_prices)
    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)
        if scope.recorded:  # a stage that only hit its caches leaves the files untouched
            try:
                write_summary(scope.log, scope.summary)
            except Exception:
                log.warning("could not write %s", scope.summary, exc_info=True)


@contextmanager
def purpose(name: str) -> Iterator[None]:
    """Label model calls made inside this block (e.g. ``"figure_check"``)."""
    token = _purpose.set(name)
    try:
        yield
    finally:
        _purpose.reset(token)


# --- Reading usage from responses -----------------------------------------------------------------


def _get(obj: Any, *path: str) -> Any:
    """Attribute or key lookup along ``path``; SDK objects, dicts and fakes all work."""
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
    """Duration-billed audio is charged per second, rounded up."""
    return float(math.ceil(round(seconds, 3)))


def measure(operation: str, request: Mapping[str, Any], response: Any) -> dict[str, float]:
    """Usage reported by one response, as ``UsageRecord`` fields."""
    usage = _get(response, "usage")
    m: dict[str, float] = {}
    if operation.startswith("chat."):
        m["input_tokens"] = _int(_get(usage, "prompt_tokens"))
        m["cached_input_tokens"] = _int(_get(usage, "prompt_tokens_details", "cached_tokens"))
        m["input_image_tokens"] = _int(_get(usage, "prompt_tokens_details", "image_tokens"))
        m["input_audio_tokens"] = _int(_get(usage, "prompt_tokens_details", "audio_tokens"))
        m["output_tokens"] = _int(_get(usage, "completion_tokens"))
        m["reasoning_tokens"] = _int(_get(usage, "completion_tokens_details", "reasoning_tokens"))
    elif operation.startswith("responses."):
        m["input_tokens"] = _int(_get(usage, "input_tokens"))
        m["cached_input_tokens"] = _int(_get(usage, "input_tokens_details", "cached_tokens"))
        m["output_tokens"] = _int(_get(usage, "output_tokens"))
        m["reasoning_tokens"] = _int(_get(usage, "output_tokens_details", "reasoning_tokens"))
    elif operation.startswith("images."):
        m["input_tokens"] = _int(_get(usage, "input_tokens"))
        m["input_image_tokens"] = _int(_get(usage, "input_tokens_details", "image_tokens"))
        m["output_tokens"] = _int(_get(usage, "output_tokens"))
        details = _get(usage, "output_tokens_details")
        # Without a breakdown, an image model's output is all image tokens.
        m["output_image_tokens"] = (
            _int(_get(details, "image_tokens")) if details is not None else m["output_tokens"]
        )
        m["images"] = len(_get(response, "data") or [])
    elif operation.startswith("audio."):
        if _get(usage, "type") == "tokens" or _get(usage, "input_tokens") is not None:
            m["input_tokens"] = _int(_get(usage, "input_tokens"))
            m["input_audio_tokens"] = _int(_get(usage, "input_token_details", "audio_tokens"))
            m["output_tokens"] = _int(_get(usage, "output_tokens"))
        else:
            # Duration-billed (whisper-1): the reported seconds, else the response's duration,
            # else the length of the WAV that was sent.
            seconds = _get(usage, "seconds")
            if not isinstance(seconds, int | float):
                seconds = _get(response, "duration")
            if not isinstance(seconds, int | float):
                seconds = _wav_seconds(request)
            if seconds is not None and seconds > 0:
                m["audio_seconds"] = billed_seconds(float(seconds))
    return m


# --- Pricing --------------------------------------------------------------------------------------

_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def find_price(model: str, prices: Mapping[str, ModelPrice]) -> ModelPrice | None:
    """Exact model id, else the id without a dated snapshot suffix. Nothing else is guessed."""
    return prices.get(model) or prices.get(_SNAPSHOT.sub("", model))


def price_call(
    model: str, m: Mapping[str, float], prices: Mapping[str, ModelPrice]
) -> tuple[float | None, str | None]:
    """(cost in USD, None), or (None, why it is unpriced)."""
    price = find_price(model, prices)
    if price is None:
        return None, f"no price for model {model!r}"
    input_tokens = m.get("input_tokens", 0)
    if price.long_context_above is not None and input_tokens > price.long_context_above:
        if price.long_context is None:
            return None, f"{model}: no long-context price"
        price = price.long_context
    # Breakdowns are billed separately only where the model has a separate price for them;
    # otherwise (chat models reading images) they are billed as ordinary input/output tokens.
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


# --- Recording ------------------------------------------------------------------------------------


def record_call(
    operation: str,
    request: Mapping[str, Any],
    response: Any,
    latency_s: float,
    error: BaseException | None = None,
) -> None:
    """Record one finished (or failed) call in the current scope. Never raises."""
    try:
        scope = _scope.get()
        if scope is None:
            log.debug("model call outside a usage scope not recorded: %s", operation)
            return
        model = str(request.get("model") or _get(response, "model") or "unknown")
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
    """Time a call made inside the block and record it; put the response in ``out["response"]``."""
    out: dict[str, Any] = {}
    start = time.perf_counter()
    try:
        yield out
    except Exception as exc:
        record_call(operation, request, None, time.perf_counter() - start, exc)
        raise
    record_call(operation, request, out.get("response"), time.perf_counter() - start)


def append_record(log_path: Path, record: UsageRecord) -> None:
    """Append one record to the call log. Constant-time: the log is never re-read here."""
    line = (record.model_dump_json() + "\n").encode()
    with _lock, log_path.open("a+b") as f:
        # After a crash cut off the last line, start a new one rather than extend the damage.
        # Writes in append mode always go to the end, wherever the read position is.
        size = f.seek(0, io.SEEK_END)
        if size:
            f.seek(size - 1)
            if f.read(1) != b"\n":
                line = b"\n" + line
        f.write(line)


def read_records(log_path: Path) -> list[UsageRecord]:
    """Records in the call log; unreadable lines (e.g. cut off by a crash) are skipped."""
    if not log_path.exists():
        return []
    records = []
    with _lock:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
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
    """A session's usage so far, from its call log."""
    return summarize(read_records(log_path))
