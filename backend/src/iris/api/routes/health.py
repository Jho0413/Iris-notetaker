from fastapi import APIRouter
from pydantic import BaseModel

from iris import __version__

router = APIRouter(tags=["health"])


class Health(BaseModel):
    """The backend is up. Setup problems are not reported here: a run that hits one fails with the
    generic message, and the log says why. The one exception is a missing API key, which
    ``/api/settings`` reports as yes or no so the UI can warn before anything is run."""

    ok: bool
    version: str


@router.get("/health", response_model=Health)
def health() -> Health:
    return Health(ok=True, version=__version__)
