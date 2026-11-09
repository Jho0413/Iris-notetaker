"""What went wrong, and what to do about it.

``classify`` sorts any exception into an ``ErrorKind``, from the OpenAI SDK's exception classes and
status codes. Every unit of work (a transcription chunk, a selection batch, a notes section, a
figure, a live window) acts on the kind:

- ``TEMPORARY`` errors are retried by ``retry_temporary``, after the SDK's own short retries;
- ``BLOCKING`` errors (a missing or rejected key, exhausted credit, a missing model) fail the run
  at once and cancel the work in progress (``gather_units``), since nothing else can succeed;
- ``REJECTED`` (this input only), ``BAD_OUTPUT`` and ``INTERNAL`` errors fail the unit. Each
  stage decides what a failed unit means: render shows the source crop, the others fail the run
  once their other units have finished (and been cached).

What the user is told is separate (``UserFacingError``, ``FailureMessage`` in ``models.py``): only
messages we wrote, never the exception text, which goes to the log and the session record
(``iris show``).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum

import openai

from iris.llm.client import MissingAPIKey
from iris.models import FailureMessage, Stage

log = logging.getLogger(__name__)


class ErrorKind(StrEnum):
    TEMPORARY = "temporary"  # try the same call again later: connection, timeout, 408/409/429/5xx
    BLOCKING = "blocking"  # nothing will work until someone fixes the setup: 401/403/404, quota
    REJECTED = "rejected"  # this request only (400/422): other requests may still succeed
    BAD_OUTPUT = "bad_output"  # the model answered, but unusably: refusal, unparseable, invalid
    INTERNAL = "internal"  # anything else, including bugs


TEMPORARY_STATUSES = frozenset({408, 409, 429})
BLOCKING_STATUSES = frozenset({401, 403, 404})
REJECTED_STATUSES = frozenset({400, 422})
QUOTA_CODE = "insufficient_quota"  # a 429 that waiting won't fix: the account is out of credit


class UserFacingError(Exception):
    """A known problem with the user's input, with a message we wrote for them.

    Raised only where the input is at fault (an unreadable upload, a recording with no audio);
    ``detail`` is for developers (log and session record), ``message`` is shown in the UI.
    """

    def __init__(self, message: FailureMessage, detail: str | None = None):
        super().__init__(detail or message)
        self.message: FailureMessage = message


class StageError(Exception):
    """A stage failed; ``cause`` is what it failed on (classified in its place)."""

    def __init__(self, stage: Stage, cause: BaseException):
        super().__init__(f"{stage} failed: {type(cause).__name__}: {cause}")
        self.stage = stage
        self.cause = cause


def classify(exc: BaseException) -> ErrorKind:
    if isinstance(exc, StageError):
        return classify(exc.cause)
    if isinstance(exc, MissingAPIKey):
        return ErrorKind.BLOCKING
    if isinstance(exc, openai.APIConnectionError):  # includes APITimeoutError
        return ErrorKind.TEMPORARY
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        if status == 429 and exc.code == QUOTA_CODE:
            return ErrorKind.BLOCKING
        if status in TEMPORARY_STATUSES or status >= 500:
            return ErrorKind.TEMPORARY
        if status in BLOCKING_STATUSES:
            return ErrorKind.BLOCKING
        if status in REJECTED_STATUSES:
            return ErrorKind.REJECTED
        return ErrorKind.INTERNAL
    if isinstance(exc, openai.LengthFinishReasonError | openai.ContentFilterFinishReasonError):
        return ErrorKind.BAD_OUTPUT  # the response was cut off or filtered
    if isinstance(exc, UserFacingError):
        return ErrorKind.INTERNAL  # not a model error; the runner reports its own message
    if isinstance(exc, ValueError):  # our validation of model output (incl. pydantic's)
        return ErrorKind.BAD_OUTPUT
    return ErrorKind.INTERNAL


def describe(exc: BaseException) -> str:
    """One line for logs and records: the kind, type and message (developers only)."""
    return f"{classify(exc)}: {type(exc).__name__}: {exc}"


async def _sleep(delay: float) -> bool:
    await asyncio.sleep(delay)
    return True


@dataclass(frozen=True)
class RetryScope:
    """When ``retry_temporary`` stops retrying: ``stopped()`` says no further attempt should
    start; ``pause(delay)`` waits up to ``delay`` seconds and returns False if stopped meanwhile."""

    stopped: Callable[[], bool]
    pause: Callable[[float], Awaitable[bool]]


NO_STOP = RetryScope(stopped=lambda: False, pause=_sleep)  # outside ``retries_until``
_SCOPE: ContextVar[RetryScope | None] = ContextVar("retry_scope", default=None)


@contextmanager
def retries_until(
    stopped: Callable[[], bool], pause: Callable[[float], Awaitable[bool]]
) -> Iterator[None]:
    """Within this block (and tasks started in it), ``retry_temporary`` makes no further attempt
    once ``stopped()``. The live lanes use it so that a retry never holds up stop: a call already
    running finishes, but no new one starts (``live/processor.py``)."""
    token = _SCOPE.set(RetryScope(stopped, pause))
    try:
        yield
    finally:
        _SCOPE.reset(token)


async def retry_temporary[T](
    call: Callable[[], Awaitable[T]], delays: Sequence[float], *, what: str
) -> T:
    """``call()``, tried again after each of ``delays`` (seconds) while it fails with a
    ``TEMPORARY`` error. Any other error, or the last temporary one, is raised as is, as is a
    temporary one once the ``retries_until`` scope has stopped.

    One unit of work at a time: the SDK has already retried the request a couple of times over
    about ten seconds, so these delays are longer (``Settings.api_retry_delays_s``).
    """
    scope = _SCOPE.get() or NO_STOP
    for delay in delays:
        try:
            return await call()
        except Exception as exc:
            if classify(exc) is not ErrorKind.TEMPORARY or scope.stopped():
                raise
            log.warning("%s: temporary API error (%s); retrying in %gs", what, describe(exc), delay)
            if not await scope.pause(delay):
                raise
    return await call()


async def gather_units[T](units: Iterable[Awaitable[T]]) -> list[T | Exception]:
    """Run units of work concurrently and return each one's result or exception, in order.

    Units that fail don't stop the others: their finished work is kept (and cached), and the
    caller decides what the failures mean. A ``BLOCKING`` error is different: nothing else can
    succeed, so the units still running are cancelled and the error is raised at once.
    """
    units = list(units)
    results: list[T | Exception] = [None] * len(units)  # type: ignore[list-item]

    async def guarded(i: int, unit: Awaitable[T]) -> None:
        try:
            results[i] = await unit
        except Exception as exc:
            if classify(exc) is ErrorKind.BLOCKING:
                raise
            results[i] = exc

    try:
        async with asyncio.TaskGroup() as group:
            for i, unit in enumerate(units):
                group.create_task(guarded(i, unit))
    except BaseExceptionGroup as group_error:
        raise first_error(group_error) from None
    return results


def first_error(error: BaseException) -> BaseException:
    """The first leaf exception of a (possibly nested) exception group."""
    while isinstance(error, BaseExceptionGroup):
        error = error.exceptions[0]
    return error
