from fastapi import APIRouter
from pydantic import BaseModel

from iris import __version__

router = APIRouter(tags=["health"])


class Health(BaseModel):
    """The backend is up. Setup problems (such as a missing API key) are not reported to the UI:
    a run that hits one fails with the generic message, and the log says why."""

    ok: bool
    version: str


@router.get("/health", response_model=Health)
def health() -> Health:
    return Health(ok=True, version=__version__)
