"""The user's edits to the notes: which version a reader gets, and whether it is out of date.

The generated notes (notes.json, written by assemble) are never changed by an edit. The latest
save is kept beside them in notes_edits.json (``NotesEdits``), written by the API only, so a
re-run never touches it. Readers get the edited version while one exists; ``based_on`` records
which generated notes it started from, so after a re-run made new notes the edits are reported
``stale`` and the user chooses between them.
"""

import hashlib

from iris.models import Notes, NotesEdited, NotesEdits
from iris.notes_format import upgraded

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
