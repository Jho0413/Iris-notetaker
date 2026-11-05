import asyncio
import contextlib
import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from iris import __version__
from iris.api import api_router
from iris.api.token import TokenMiddleware
from iris.config import Settings, get_settings
from iris.events import EventBus
from iris.jobs.runner import JobRunner
from iris.parent import watch_parent
from iris.store.db import SessionStore
from iris.user_settings import apply_user_settings, load_user_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.sessions_dir.mkdir(parents=True, exist_ok=True)
        store = SessionStore(settings.db_path)
        bus = EventBus()
        # What the Settings page saved applies on top, except where a value is pinned by the
        # environment or .env; the loaded settings are kept to tell the two apart.
        user = load_user_settings(settings.user_settings_path)
        current = apply_user_settings(settings, user)
        app.state.loaded_settings = settings
        app.state.user_settings = user
        app.state.settings_lock = asyncio.Lock()
        app.state.settings = current
        app.state.store = store
        app.state.bus = bus
        app.state.runner = JobRunner(store, bus, current)
        watcher = None
        if settings.parent_pid is not None:
            watcher = asyncio.create_task(
                watch_parent(settings.parent_pid, settings.parent_poll_s), name="parent-watch"
            )
        yield
        if watcher is not None:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        await app.state.runner.shutdown()
        store.close()

    app = FastAPI(title="Iris", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if settings.api_token is not None:  # added last, so it runs first
        app.add_middleware(TokenMiddleware, token=settings.api_token.get_secret_value())
    app.include_router(api_router)
    app.add_exception_handler(RequestValidationError, _validation_error)
    return app


def _escape_unencodable(value: object) -> object:
    """``value`` with text that can't be written as UTF-8 (half of an emoji, a lone surrogate)
    escaped, recursively."""
    if isinstance(value, str):
        return value.encode("utf-8", "backslashreplace").decode("utf-8")
    if isinstance(value, list | tuple):
        return [_escape_unencodable(v) for v in value]
    if isinstance(value, dict):
        return {k: _escape_unencodable(v) for k, v in value.items()}
    return value


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI's usual 422, except that the input it echoes back is escaped where it can't be
    encoded: JSON can carry half of an emoji, and echoing it as is turned the 422 into a 500."""
    assert isinstance(exc, RequestValidationError)
    errors = _escape_unencodable(jsonable_encoder(exc.errors()))
    return JSONResponse(status_code=422, content={"detail": errors})


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
