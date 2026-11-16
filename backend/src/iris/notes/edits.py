import hashlib

from iris.models import Notes, NotesEdited, NotesEdits
from iris.notes.format import upgraded
from iris.store.layout import SessionPaths, read_model, read_optional

MAX_EDITED_CHARS = 1_000_000


def generated_hash(generated: Notes) -> str:
    return hashlib.sha256(generated.markdown.encode()).hexdigest()


def as_read(generated: Notes, edits: NotesEdits | None) -> Notes:
    current = upgraded(generated)
    stamp = generated_hash(generated)
    if edits is None:
        return current.model_copy(update={"generated_hash": stamp})
    return current.model_copy(
        update={
            "markdown": edits.markdown,
            "generated_hash": stamp,
            "edited": NotesEdited(saved_at=edits.saved_at, stale=edits.based_on != stamp),
        }
    )


def newest(generated: Notes, edits: NotesEdits | None) -> Notes:
    if edits is not None and edits.based_on != generated_hash(generated):
        edits = None
    return as_read(generated, edits)


def read_newest_notes(paths: SessionPaths) -> Notes:
    return newest(read_model(paths.notes, Notes), read_optional(paths.notes_edits, NotesEdits))


def read_notes(paths: SessionPaths) -> Notes:
    return as_read(read_model(paths.notes, Notes), read_optional(paths.notes_edits, NotesEdits))
