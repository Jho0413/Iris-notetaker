"""Model usage and cost per session (recorded by ``iris.llm.usage``)."""

from fastapi import APIRouter

from iris.api.deps import PathsDep
from iris.llm.usage import read_usage
from iris.models import SessionUsageView

router = APIRouter(prefix="/sessions", tags=["usage"])


@router.get("/{session_id}/usage", response_model=SessionUsageView)
def get_usage(paths: PathsDep) -> SessionUsageView:
    """Every model call made for the session, with totals per stage, model and purpose.

    A session with no model calls yet (or only cache hits) has empty records and zero totals.
    Calls whose cost is unknown are counted in ``unpriced_calls`` and excluded from ``cost_usd``.
    Read from the call log, so it is current while stages are still running. Failed calls are
    flagged (``failed``); what they failed on stays in the log (``iris cost``, ``iris show``).
    """
    return read_usage(paths.usage_log).view()
