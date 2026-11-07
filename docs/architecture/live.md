# Live sessions

Recording in the browser, and processing the session while it records. The stages themselves are in
[`pipeline.md`](pipeline.md); how live lanes handle failures is in [`errors.md`](errors.md).

## Capture in the browser

The Live page starts one of two recordings, each with one click:

- **Record audio:** the microphone and, in the desktop app, the Mac's sound (the other side of a
  call), mixed into one track. No screen is shared. In a browser it is the microphone alone, and
  the page says so (a browser can share a tab's sound only while that tab is shared).
- **Record audio + screen:** Iris asks for a screen, window or tab to share, then for the
  microphone.

Audio is uploaded in 5-second chunks and the screen at about one frame per second, on the same
clock. The audio sources are always mixed into the one track that is recorded, even when the
microphone is the only one, so the recording stays one stream however many sources it has.
Failed uploads are retried, and making notes waits until everything has arrived before the
session is finished.

**Stop** stops capturing at once, and the page asks what to do with the recording:

- **Make notes:** upload the rest and finish the session.
- **Continue recording:** capture again in the same recording. While stopped, the microphone, the
  Mac's sound and any shared screen are released (macOS's indicators go off), so continuing asks
  for them again: with a screen, the picker opens again (cancelling it leaves it stopped). The
  recorder is paused, not ended, so the audio stays one WebM stream, and its timestamps leave the
  stopped time out (measured in Electron 45); frames are timed the same way. The session's clock
  is the recorded time, so the engine needs nothing for it.
- **Start over:** delete this recording and start a new one with the same title and mode.
- **Discard:** delete the recording (`DELETE /sessions/{id}`, which stops its live processing);
  nothing more is uploaded, and the page goes back to its start.

Start over and Discard ask first, since they delete what was recorded. While stopped the session
stays `recording`, so leaving the page or closing the tab ends it as it would while recording. The captured chunks stay in the session folder (`live/`), so a failed run can be retried
with `uv run iris run <session_id>`.

| Browser (desktop) | Microphone | Shared audio | Status |
|---|---|---|---|
| Chrome, Edge | Yes | Tab audio when sharing a tab (keep "Also share tab audio" on). System audio when sharing the entire screen on Windows and ChromeOS. On macOS, tab audio only | Supported. A live session with a real mic and screen share works in Chrome |
| Firefox | Yes | None (Firefox can't share audio) | Expected to work; not checked yet |
| Safari | Yes | None | Untested; Iris says so if the browser can't record WebM audio |
| Iris for macOS ([`desktop.md`](desktop.md)) | Yes | System audio with any screen or window, from Iris's picker; with Record audio, the Mac's sound alone, with no picker and no Screen Recording permission | Supported. Live recordings of a window with sound, and of audio alone, work end to end |

Shared-audio modes:

- **Mic only:** Record audio in a browser, or sharing a window or screen without audio. Only the
  microphone is recorded.
- **Mic plus the Mac's sound:** Record audio in the desktop app. If macOS refuses the Mac's sound
  (System Audio Recording), the recording runs with the microphone, and the page says how to allow
  it ([`desktop.md`](desktop.md), "Capture").
- **Mic plus shared audio:** sharing a tab (or a screen, where supported) with audio. Both are mixed
  into one track. Use headphones, or the mic also picks up the shared audio from your speakers.
  Shared audio is recorded without echo cancellation, noise suppression or gain control (it is
  what plays, not a voice in a room); the microphone keeps them.

**The session is made only once what is recorded has been granted** (`POST /live` comes after
the screen, the mic and the Mac's sound are answered), so a permission prompt never leaves a
session behind, even if Iris is quit or crashes while it is open, and nothing counts as recording
until then. A start cancelled while `POST /live` is in flight deletes the session once it is made.

What happens when capture is interrupted:

- **Permission denied or picker cancelled:** Iris shows what to allow; no session was made.
  Anything already granted (such as the screen share when the mic is then denied) is stopped. The
  Mac's sound is the exception: refused, the audio recording starts with the microphone alone.
- **Sharing stopped, or you leave the Live page or close the tab, before recording starts** (for
  example while the mic prompt is still open): the start fails and everything granted is stopped.
  A session made in the moment before recording began is deleted.
- **"Stop sharing" in the browser's own bar, or the mic is disconnected:** the recording finishes and
  is processed.
- **Backend unreachable:** uploads are retried. Stop waits up to 30 seconds, then shows an error.
  The page then shows **Recording stopped**: **Try again** resends what is still queued, then ends
  the session. Audio is never discarded while the tab stays open. Screen frames are queued up to
  120 (about 2 minutes); during a longer outage the oldest queued frames are dropped to bound
  memory, so the notes may miss what was on screen then.
- **Closing the tab while recording:** the browser asks for confirmation. If you leave, the
  session is processed with what arrived: the page ends it with a beacon, or a keepalive request
  where beacons aren't allowed (the app's `app://` pages). If the browser crashes instead, the
  session stays `recording`; finish it with
  `curl -X POST http://127.0.0.1:8000/api/live/<session_id>/end`. The desktop app hides its window
  instead of closing it while recording, and its menu bar icon stops the recording
  ([`desktop.md`](desktop.md)); reloading the page asks "Stop recording?".

## Processing while recording

The same work as an upload, done while the session is happening. What is done live *is* the final
work: at stop, only what was still open is new (the last transcription window, the screen shown at
the end and the notes sections not yet final), so the time from stop to finished notes depends on
that, not on how long the session was. A screen that stays open the whole session (one long worked
example or sketch) is only read at stop, with its figures rendered then, so such a session finishes
about as fast as an upload of it would (measured in [`../roadmap.md`](../roadmap.md)). An
audio-only recording's sections are written as the speech is transcribed: a 5-minute meeting
replayed as Record audio with `IRIS_NOTES_CONTEXT_CHARS=2000` had 5 sections written before stop,
the first 98 s in, and one left to write at stop (18 s from stop to done). Live
processing is best effort: the captured chunks stay the recording, and if anything live fails the
session is processed like an upload.

No draft is shown: the Live page shows one progress line ("Transcribed to 4:30 · 3 screens ·
2 figures read · 1 section written") and the Session page shows progress until the finished notes
are ready. Window lengths, hold-back and concurrency are `IRIS_LIVE_*` settings
(`backend/.env.example`). `IRIS_LIVE_PROCESSING=false` turns live processing off: the session is
then processed after stop, like an upload.

```
Browser                    API routes                 LiveProcessor (one per recording, asyncio tasks)
MediaRecorder 5 s chunks ─▶ POST /live/{id}/audio ─wake─▶ audio lane:  reorder ▸ decode ▸ cut at pauses ▸ transcribe ─┐
Frame ticker 1 fps ───────▶ POST /live/{id}/frame ─wake─▶ screen lane: hold back ▸ normalise ▸ MomentDetector ─────────┤
                                                                                                                       ▼
                                                          live timeline: transcript, moments, selection, watermarks
                                                                                                                       │
                                                          select lane: gate ▸ align ▸ select ▸ render (background) ◀──┤
                                                          draft lane:  horizon ▸ frozen sections ▸ notes_sections/ ◀──┘
Stop ─────────────────────▶ POST /live/{id}/end ───────▶ job: finish lanes ▸ ingest ▸ promote live outputs ▸ rest of PLAN
Live + Session pages ◀──── SSE events (progress only; the notes once done: GET /sessions/{id}/notes)
```

### Capture and store

`POST /live` says whether the recording shares a screen (`screen`, false for Record audio); it is
stored as `live/capture.json` before the lanes start, so a lane restarted later reads it too. The
browser posts 5 s WebM chunks (`seq` 0, 1, 2, ...) and, with a screen, ~1 fps JPEG frames (`t` =
seconds since start, the audio's clock; an audio-only recording refuses frames with 409). Each is
stored atomically under `live/` and never modified; the route then wakes the lane that consumes it
and returns without waiting for any processing.

### Lanes

`iris/live/` holds one processor per recording session (`LiveRegistry`, owned by the job runner,
started at `POST /live`, or on the first chunk after a backend restart). It runs four lanes as
asyncio tasks. Lanes communicate only through the **live timeline** (`iris/live/timeline.py`),
which holds what each lane has finished and up to when:

| Watermark | Meaning | Advanced by |
|---|---|---|
| `transcribed_to` (T_a) | the transcript is complete up to here | audio lane |
| `closed_to` (T_m) | every moment that ends by here is closed | screen lane; with no screen shared no moment can open, so it follows T_a |
| `selected_to` (T_s) | every closed moment that ends by here has been read | select lane; once every closed moment is read, it follows T_m |
| `horizon` | `min(T_a, T_s)`: evidence before it is final (select publishes in time order: nothing it reads later starts before T_s). With no screen it is T_a: sections are written from the speech as it is transcribed | (derived) |

Lanes wait on watermarks (an `asyncio.Condition`), never poll. A lane that has finished (after
stop) or failed counts as "through" any time, so nothing downstream waits forever.

| Lane | Input | Does | Publishes |
|---|---|---|---|
| **Audio** | `live/audio/*.webm` | Decodes the contiguous run of chunks into one PCM stream (`live/audio.pcm`, placed by timestamp exactly as ingest does). Cuts windows in the decoded audio (so never at a chunk edge) at the quietest point 5–15 s after the last cut, deciding each cut only once that whole range plus a second is decoded, so cuts depend on the audio alone and a restarted lane reuses its cached windows (`live/transcript_windows/`). Transcribes at most `IRIS_LIVE_TRANSCRIBE_CONCURRENCY` windows at once, each prompted (whisper-1) with the tail of the text committed when it is sent: normally the previous window. Every window is sent the spoken language (the Settings page or `IRIS_TRANSCRIBE_LANGUAGE`, default `en`, fixed when the session starts), so a misdetected first window can't pass its language on through the prompt; a cached window is reused only if it was sent the same language. Commits windows in order; a window still failing after its temporary-error retries (or on any other error) fails the lane | transcript segments, T_a, `TRANSCRIPT_CHUNK` |
| **Screen** | `live/frames/*.jpg` | Holds frames back `IRIS_LIVE_FRAME_HOLDBACK_S` (session time) to order them, samples them onto the `frame_fps` grid and normalises them with ingest's own helpers (size fixed by the first readable frame), writing `frames/` with ingest's names and bytes, and feeds `MomentDetector` the frames read back from `frames/`, as detect reads them. An audio-only recording (`live/capture.json`) has no frames: T_m follows T_a | closed moments, T_m, `MOMENT_FOUND` |
| **Select** | closed moments + transcript | Gates each moment until T_a ≥ its end + `IRIS_LIVE_SPEECH_PAD_S`, aligns it, and reads it with the select stage's batch step and one call in flight (moments that pass the gate meanwhile go together in the next batch, up to `selection_batch_size`; visual ids continue across batches). Renders each figure in the background (`IRIS_LIVE_RENDER_CONCURRENCY`) by the render stage's rules: an API error gives the source crop, uncached, so the render stage tries it again at stop | contents, visuals, T_s, `VISUAL_SELECTED`, `VISUAL_RENDERED` |
| **Draft** | timeline | Writes notes sections ahead of stop; no draft is made or shown. Packs the evidence before the horizon into sections like write does. A section that closes before the horizon is *frozen*: written once, with the payload and cache key write computes for it at stop (`notes_sections/`). Nothing past the horizon is written live: the sections still open are write's at stop. A payload whose call fails (after its temporary-error retries) is retried after a minute; one that fails twice, or once with a blocking error, is given up until it changes, and the write stage retries it at stop | `notes_sections/`, `NOTES_UPDATED` (`completed_parts`: sections written, for the Live page's progress line) |

### Joining what was split

Inputs arrive in pieces, so each lane has a rule for putting them back together:

- *Words split across chunks:* raw chunks are never transcribed (only chunk 0 has the WebM header,
  and edges fall mid-word). Windows are cut at pauses in the decoded stream, and the previous
  window's text is passed as the prompt.
- *Late, repeated or lost chunks:* the decoder only consumes the contiguous run of `seq`. A missing
  chunk is waited for `IRIS_LIVE_AUDIO_GAP_WAIT_S` (longer than the browser's upload timeout plus a
  retry), then decoding resumes at the next WebM cluster and the gap is silence, as in ingest.
  Frames are ordered by holding them back briefly; a frame later than that is skipped live, and
  recorded as skipped input only if ingest would have sampled it (its grid cell has no earlier
  frame).
- *Content built up over time:* `MomentDetector` keeps a slide build or a worked example as one
  moment with ordered builds, and closes it only when the next screen has settled and lasted
  `min_moment_s`, so a brief switch to another window never splits it. Open moments are not read.
- *Speech that outlasts the screen:* the select gate waits for the transcript past the moment's end.
- *Topics spanning sections:* sections freeze only behind the horizon and at moment boundaries;
  select is given earlier moments' content. Whole-session consolidation is the synthesis pass.

Late data is never lost: everything captured stays on disk and the final run uses all of it.

### Stop

`POST /live/{id}/end` (after the browser has flushed its uploads) starts the job:

1. **Finish the lanes.** Each lane flushes: the last audio window is cut wherever the audio ends,
   the detector closes the last moment (`finish`), the last moments are read and figures still
   rendering complete (figures not yet started are left to the render stage, which renders every
   figure at stop; once the live selection is promoted, the live renders are its cache hits). The
   draft lane finishes a section call in flight and stops; the write stage writes the sections
   still open. A lane still flushing after `IRIS_LIVE_FINISH_TIMEOUT_S` is cancelled and counts as
   failed.
2. **Ingest** as for an upload: `audio.wav` and `frames.json` from the captured chunks.
3. **Promote** (`iris/live/promote.py`). What the live lanes finished replaces the stages that
   would redo it; what is written is exactly what the timeline holds (segments in its order, its
   `language`, the first window with speech's; moments in closing order; contents and visuals in
   reading order), since the frozen sections' cache keys depend on it:

   | Lanes at stop | Written from the timeline | The run continues with |
   |---|---|---|
   | `timeline.complete`: audio, screen and select finished, none failed, none skipped captured input | `transcript.json`, `moments.json`, `alignment.json` (align over those two), `selection.json` | write ∥ render, assemble, synthesize. Frozen sections and live renders hit their caches: only the sections still open at stop and figures not rendered live are new work |
   | Audio finished cleanly; screen or select failed or skipped input | `transcript.json` | detect, align, select, … (transcription isn't paid twice) |
   | Audio failed, skipped input or didn't run | nothing | the full plan, as for an upload (model caches still reused where inputs match) |

   Promotion also falls back to the transcript only if the live screen work doesn't hold together
   on disk after ingest (duplicate moment ids, a selection naming an unknown moment, a frame or
   source crop missing): the stages it would skip check those. The draft lane's failure doesn't
   block promotion: its sections are only a cache. The live lanes write no `notes_draft.json`;
   a run that selects again deletes one left by an earlier run ([`pipeline.md`](pipeline.md),
   "Stage order").
   Promoted stages make no model calls. Each is reported as `stage_started`/`stage_finished` with
   `promoted: true`, and the events the stage itself would emit are replayed (`moment_found` per
   moment, `visual_selected` per figure), so the UI's progress and counts look as for a run; the
   transcript is fetched whole once transcribe finishes, and transcribe's `stage_finished` carries
   its size (`segments`) because the live windows' `transcript_chunk` events came before the run
   started. A re-run (`POST /sessions/{id}/run`) has no processor and runs its plan as before.
4. **Synthesis**, as for uploads ([`pipeline.md`](pipeline.md), "Synthesize").

A lane whose upstream lane failed (`upstream_failed`) stops making model calls for the rest of the
recording: nothing it makes can be promoted.

## Replaying a recording

`uv run iris replay-live <file>... [--url URL] [--speed X]` (`iris/live/replay.py`) plays a
recorded file into a running backend's live API as the browser would: audio re-encoded with PyAV
to one WebM/Opus stream posted as ~5 s byte ranges with `seq`, screen frames as JPEG once a second
with `t`, paced in real time (or X times faster), then `POST /end`. A file with no video is
replayed as Record audio (`screen: false`). It reports when the first
transcript, moment, figure and notes section events arrived, the number of notes sections written
before stop, what was promoted, the time from stop to `done` and the cost (total and after stop)
from `/usage`. It makes paid calls through the backend, so point the backend at a scratch data
folder:

```sh
cd backend
IRIS_DATA_DIR=/tmp/iris-scratch uv run iris serve --port 8001   # in one terminal
uv run iris replay-live recording.mov --url http://127.0.0.1:8001 [--speed 2]
```
