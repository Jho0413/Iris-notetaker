import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from iris import __version__
from iris.api import api_router
from iris.config import Settings, get_settings
from iris.events import EventBus
from iris.jobs.runner import JobRunner
from iris.store.db import SessionStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.sessions_dir.mkdir(parents=True, exist_ok=True)
        store = SessionStore(settings.db_path)
        bus = EventBus()
        app.state.settings = settings
        app.state.store = store
        app.state.bus = bus
        app.state.runner = JobRunner(store, bus, settings)
        yield
        await app.state.runner.shutdown()
        store.close()

    app = FastAPI(title="Iris", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
