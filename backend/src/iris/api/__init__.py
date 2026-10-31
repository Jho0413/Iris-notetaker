from fastapi import APIRouter

from iris.api.routes import events, health, live, sessions

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(sessions.router)
api_router.include_router(events.router)
api_router.include_router(live.router)

__all__ = ["api_router"]
