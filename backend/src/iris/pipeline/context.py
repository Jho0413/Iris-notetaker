from collections.abc import Callable
from dataclasses import dataclass, field

from iris.config import Settings
from iris.models import EventType, SourceKind
from iris.store.layout import SessionPaths

Emit = Callable[[EventType, dict], None]


def _no_emit(_type: EventType, _data: dict) -> None:
    pass


@dataclass
class StageContext:
    """Everything a stage needs. Stages read and write files under ``paths`` and nothing else."""

    session_id: str
    source: SourceKind
    paths: SessionPaths
    settings: Settings
    emit: Emit = field(default=_no_emit)
