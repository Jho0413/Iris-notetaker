from fastapi import APIRouter

from iris.api.routes import events, health, live, sessions, settings, usage

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(sessions.router)
api_router.include_router(events.router)
api_router.include_router(live.router)
api_router.include_router(usage.router)
api_router.include_router(settings.router)

__all__ = ["api_router"]
