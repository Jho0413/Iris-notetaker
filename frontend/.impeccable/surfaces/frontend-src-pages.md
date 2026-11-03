---
version: 1
slug: "frontend-src-pages"
primary_target: "frontend/src/pages"
related_targets: ["frontend/src/components","frontend/src/index.css"]
---

Scope: the Iris web app (library, upload, live, session/notes). Mode: Operate for the shell and screens, Read for the notes column.
Audience and job: one person on a laptop, students and professionals equally; upload or record a session, leave, come back to finished notes, export.
Constraints: existing API and SSE events only; system fonts; light, dark and system themes; WCAG AA in both.

## Direction contract

THESIS: A quiet working tool that hands the stage to the notes. It refuses the dashboard default of cards, metrics and pipeline jargon on top of the notes; progress is plain sentences in a side rail and the notes column never moves.
OWN-WORLD: cool, accent-tinted neutrals with a second neutral layer for the rail and top bar; one indigo accent for action, focus and the current step; red only for recording and failure, green for finished, amber for draft and unverified. System sans throughout, a 17px reading column at 68ch, hairlines instead of shadows, figures on white plates in both themes.
STORY: the user sees at once which sessions are finished, working or need attention, opens one, watches results arrive without anything jumping, then reads and exports.
FIRST VIEWPORT: session page: breadcrumb, title and meta on the left, status chip and actions on the right; the session ribbon under it; tabs and the reading column on the left; the 300px progress rail on the right. The primary action (Export, Try again or Resume) is in the header's top right.
FORM: user-pinned (clean neutral, reading column plus rail, dense library list, focused recorder), signature move: the session ribbon, figures marked at their source time. seed: none (unavailable)
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
