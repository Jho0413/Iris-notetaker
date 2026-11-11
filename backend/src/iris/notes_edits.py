"""The user's edits to the notes: which version a reader gets, and whether it is out of date.

The generated notes (notes.json, written by assemble) are never changed by an edit. The latest
save is kept beside them in notes_edits.json (``NotesEdits``), written by the API only, so a
re-run never touches it. Readers get the edited version while one exists; ``based_on`` records
which generated notes it started from, so after a re-run made new notes the edits are reported
``stale`` and the user chooses between them.

The app reads the notes through ``read_notes``: out-of-date edits are shown, flagged, for the user
to choose. An AI assistant can't ask the user, so it reads ``read_newest_notes``: always the
newest version.
"""

import hashlib
import logging

from iris.models import Notes, NotesEdited, NotesEdits
from iris.notes_format import upgraded
from iris.store.layout import SessionPaths, read_model

log = logging.getLogger(__name__)

# Longest edited notes accepted, in characters; generated notes are a small fraction of this.
MAX_EDITED_CHARS = 1_000_000


def generated_hash(generated: Notes) -> str:
    """Names one version of the generated notes, as stored."""
    return hashlib.sha256(generated.markdown.encode()).hexdigest()


def as_read(generated: Notes, edits: NotesEdits | None) -> Notes:
    """What a reader gets: the edited notes when there are edits, else the generated ones, in
    the current format either way."""
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


def _read_edits(paths: SessionPaths) -> NotesEdits | None:
    """The user's latest saved edits, or None if there are none or they are damaged. A file that
    exists but can't be opened raises ``OSError``: its edits may be intact, and passing it over
    would let the next save replace them."""
    try:
        return read_model(paths.notes_edits, NotesEdits)
    except FileNotFoundError:
        return None
    except ValueError:
        # Written atomically, so this is damage from outside Iris. The session stays readable
        # with its generated notes; the next save replaces the file.
        log.error("ignoring unreadable %s", paths.notes_edits, exc_info=True)
        return None


def newest(generated: Notes, edits: NotesEdits | None) -> Notes:
    """The newest version of the notes: the user's edits, unless Iris made the notes again after
    them (the edits are stale); then the new notes. The edits stay on disk either way."""
    if edits is not None and edits.based_on != generated_hash(generated):
        edits = None
    return as_read(generated, edits)


def read_newest_notes(paths: SessionPaths) -> Notes:
    """``newest`` from the session folder. Raises as ``read_notes`` does."""
    return newest(read_model(paths.notes, Notes), _read_edits(paths))


def read_notes(paths: SessionPaths) -> Notes:
    """The notes as a reader gets them (``as_read``). Raises ``FileNotFoundError`` while there
    are no notes yet, and ``ValueError`` if notes.json can't be read."""
    return as_read(read_model(paths.notes, Notes), _read_edits(paths))
