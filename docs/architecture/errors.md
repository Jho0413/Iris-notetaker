# Errors and retries

How failures are classified, which are retried, and what the user sees. To investigate a failed run,
see [`../development.md`](../development.md), "Troubleshooting".

## Kinds

Every failure is sorted into a kind (`iris/llm/errors.py`, `classify`), from the OpenAI SDK's
exception classes and status codes, and each kind is handled the same way everywhere:

| Kind | What it is | What happens |
|---|---|---|
| `TEMPORARY` | Connection errors, timeouts, 408, 409, 429 rate limits, 5xx | The unit is retried |
| `BLOCKING` | 401, 403, 404 (a missing model), a 429 with code `insufficient_quota`, no API key | The run fails at once; work in progress is cancelled |
| `REJECTED` | 400, 422: this request only | The unit fails (figures: the source crop) |
| `BAD_OUTPUT` | Our own validation: refusals, unparseable or invalid output | The unit fails (figures: quality retries, as before) |
| `INTERNAL` | Anything else | The unit fails (figures: the source crop) |

## The retry ladder

The SDK retries connection errors, timeouts, 408, 409, 429 and 5xx twice itself, over about ten
seconds. On top of that, `retry_temporary` retries one *unit* of work after a temporary error,
waiting `IRIS_API_RETRY_DELAYS_S` before each further attempt (default 15, 60 and 120 s, so a unit
gives up after about 3.5 minutes of outage). No other kind is retried. The units:

| Unit | Gives up | Then |
|---|---|---|
| Transcription chunk | after the retries | The stage fails once the other chunks finish (finished chunks are cached) |
| Selection batch | after the retries | The stage fails (finished batches are cached) |
| Notes section | after the retries | The stage fails (finished sections are cached) |
| Synthesis call | after the retries | Never fatal: the assembled notes are the result (`synthesis.json` records why) |
| Figure | at once, no retries | The source crop is shown, marked unverified (below); the run carries on |
| Live audio window | after the retries | The audio lane fails; stop falls back (below) |
| Live select batch | after the retries | The select lane fails; stop falls back |
| Live notes section | after the retries, then the lane's own cap (two tries per payload, a minute apart) | The section is left to the write stage at stop |

A blocking error is never retried: nothing else can succeed until someone fixes the setup. Units
that run together (transcription chunks, figures, and stages in one group such as write ∥ render)
are cancelled at once (`gather_units` and `asyncio.TaskGroup`), so a rejected key doesn't wait for
the rest of the chunks. Any other failure of a stage also cancels the stage running beside it,
since the run has failed; finished units are cached, so **Try again** resumes from the failed stage
and pays only for what wasn't done.

## Figures fall back

An API error while redrawing a figure (any kind but blocking) gives its source crop at once: no Iris
retries, marked `verified: false`, with the reason in the log and in `problems` in `renders.json`
(for debugging; never served). The fallback is not cached, so a later run tries the redraw again.
Bad output keeps the quality ladder (`IRIS_RENDER_ATTEMPTS` per renderer, then the other renderer,
then the source crop, cached). The live select lane's background renders follow the same rule. In
the UI such a figure is labelled "Original image", with no reason.

## Live lanes

Live lanes ([`live.md`](live.md)) classify and retry the same way, except that no retry is made
once an upstream lane has failed (nothing can be promoted), and the draft lane makes none after
stop either: such a retry's wait ends and no new attempt starts (`retries_until`, set by the
processor); a call already running finishes. The audio and select lanes keep their retries at
stop, since their flush (the last windows and moments) is what stop waits for, bounded by
`IRIS_LIVE_FINISH_TIMEOUT_S`. A lane that fails still falls back silently at stop: the session is
processed like an upload (or from detect), which meets a blocking error again and fails fast.
Nothing user-visible changes while recording.

## What the user sees

Only messages we wrote, from a fixed list (`FailureMessage` in `models.py`; the UI keeps the same
list and shows the generic message for anything else). A known input problem raises
`UserFacingError` with its own message (`failure_kind: input`): an unreadable or undecodable
upload, an upload with no audio track, a live recording with no audio or an unreadable one, or a
recording with no speech or screen content to write from. Everything else, including a missing or
rejected key, exhausted credit or a missing model, gets "Iris couldn't finish making the notes.
Try again; finished steps are kept." (`failure_kind: general`).

The session stores `failure_message`, `failure_kind` and the failed stage (`current_stage`) for the
API, and the raw error with its time (`error_detail`, `failed_at`) for developers only: it is never
part of the API's `Session`, the `error` event carries only the message, kind and stage,
`visual_rendered` events carry no `problems`, and `GET /usage` flags failed calls without their
error text. Developers read the raw error in the backend log and with `iris show <session_id>`.
Sessions stored by older versions, whose `error` held the raw text, are migrated when the store
opens: the text becomes `error_detail` and the user sees the generic message.
