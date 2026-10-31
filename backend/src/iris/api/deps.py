from typing import Annotated

from fastapi import Depends, HTTPException, Request

from iris.config import Settings
from iris.events import EventBus
from iris.jobs.runner import JobRunner
from iris.models import Session
from iris.store.db import SessionStore
from iris.store.layout import SessionPaths, session_paths


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> SessionStore:
    return request.app.state.store


def get_bus(request: Request) -> EventBus:
    return request.app.state.bus


def get_runner(request: Request) -> JobRunner:
    return request.app.state.runner


SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[SessionStore, Depends(get_store)]
BusDep = Annotated[EventBus, Depends(get_bus)]
RunnerDep = Annotated[JobRunner, Depends(get_runner)]


def get_session(session_id: str, store: StoreDep) -> Session:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    return session


SessionDep = Annotated[Session, Depends(get_session)]


def get_paths(session: SessionDep, settings: SettingsDep) -> SessionPaths:
    return session_paths(settings.sessions_dir, session.id)


PathsDep = Annotated[SessionPaths, Depends(get_paths)]
