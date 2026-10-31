from fastapi import APIRouter
from pydantic import BaseModel

from iris import __version__
from iris.api.deps import SettingsDep

router = APIRouter(tags=["health"])


class Health(BaseModel):
    ok: bool
    version: str
    openai_key: bool


@router.get("/health", response_model=Health)
def health(settings: SettingsDep) -> Health:
    return Health(
        ok=True,
        version=__version__,
        openai_key=bool(settings.openai_api_key),
    )
