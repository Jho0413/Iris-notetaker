---
name: Iris
description: Notes that see what was shown. A quiet local tool whose stage belongs to the notes.
colors:
  # Light theme (default, :root in src/index.css)
  bg: "#f6f6f9"
  surface: "#ffffff"
  surface-2: "#eeeef3"
  surface-3: "#e4e4ec"
  border: "#dedee6"
  border-strong: "#c9c9d4"
  field-border: "#8a8a99"
  preview: "#000000"
  text: "#17171c"
  text-2: "#5b5b68"
  accent: "#4b44d6"
  accent-hover: "#3d36c2"
  accent-text: "#ffffff"
  accent-soft: "#ecebff"
  accent-line: "#cfccfa"
  recording: "#c92a2a"
  on-recording: "#ffffff"
  recording-soft: "#fdecec"
  recording-line: "#f3c4c4"
  success: "#1d7a3e"
  success-soft: "#e9f6ee"
  success-line: "#bfe3cc"
  warning: "#8f5a00"
  warning-soft: "#fdf3e1"
  warning-line: "#efd6a6"
  info: "#2457c5"
  info-soft: "#eaf0fd"
  info-line: "#c4d4f6"
  plate: "#ffffff"
  # The notes palette's own hues; its other six names reuse grey (text-2), indigo (accent),
  # blue (info), green (success), amber (warning) and red (recording)
  teal: "#0f6b64"
  teal-soft: "#e5f5f2"
  teal-line: "#b5e0d9"
  orange: "#b23c0a"
  orange-soft: "#fff0e6"
  orange-line: "#f8cfb3"
  pink: "#b0175a"
  pink-soft: "#fdebf3"
  pink-line: "#f4c2d7"
  violet: "#6d28d9"
  violet-soft: "#f3edff"
  violet-line: "#ddd0fb"
  scrim: "rgb(14 15 19 / 0.45)"
  # Dark theme (:root[data-theme='dark']); plate stays white
  bg-dark: "#0e0f13"
  surface-dark: "#16171d"
  surface-2-dark: "#1c1d24"
  surface-3-dark: "#262732"
  border-dark: "#2b2c36"
  border-strong-dark: "#3a3b48"
  field-border-dark: "#666778"
  text-dark: "#ececf2"
  text-2-dark: "#a2a2b0"
  accent-dark: "#8f8cff"
  accent-hover-dark: "#a7a5ff"
  accent-text-dark: "#0e0f13"
  accent-soft-dark: "#25244a"
  accent-line-dark: "#3d3b78"
  recording-dark: "#ff6b6b"
  on-recording-dark: "#1a0b0c"
  recording-soft-dark: "#3a1a1d"
  recording-line-dark: "#6a2a2f"
  success-dark: "#5fd38a"
  success-soft-dark: "#142b1e"
  success-line-dark: "#24523a"
  warning-dark: "#f0b44c"
  warning-soft-dark: "#2e2412"
  warning-line-dark: "#5a4419"
  info-dark: "#7fa6ff"
  info-soft-dark: "#172240"
  info-line-dark: "#2c3f70"
  teal-dark: "#5fd4c4"
  teal-soft-dark: "#112e2b"
  teal-line-dark: "#1f5650"
  orange-dark: "#ffa36b"
  orange-soft-dark: "#36210f"
  orange-line-dark: "#6a3c1d"
  pink-dark: "#ff8cc0"
  pink-soft-dark: "#3a1a2b"
  pink-line-dark: "#6a2c4b"
  violet-dark: "#c2a8ff"
  violet-soft-dark: "#2a2142"
  violet-line-dark: "#4b3b7a"
  scrim-dark: "rgb(0 0 0 / 0.6)"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif"
    fontSize: "3.5rem"
    fontWeight: 300
    lineHeight: 1
    letterSpacing: "-0.02em"
    fontFeature: "'tnum'"
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  title-sm:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  body-notes:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 400
    lineHeight: 1.7
  body-lg:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.6
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 500
    lineHeight: 1.5
  caption:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.4
  mono:
    fontFamily: "ui-monospace, 'SF Mono', Menlo, Consolas, monospace"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  xs: "4px"
  sm: "6px"
  plate: "8px"
  md: "10px"
  pill: "999px"
spacing:
  "1": "4px"
  "1.5": "6px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "5": "20px"
  "6": "24px"
  "8": "32px"
  "12": "48px"
  "16": "64px"
components:
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: "0 0.75rem"
    height: "2rem"
    typography: "{typography.body}"
  button-secondary-hover:
    backgroundColor: "{colors.surface-2}"
  button-secondary-active:
    backgroundColor: "{colors.surface-3}"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-text}"
    rounded: "{rounded.sm}"
    padding: "0 0.75rem"
    height: "2rem"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text-2}"
    rounded: "{rounded.sm}"
    padding: "0 0.75rem"
    height: "2rem"
  button-ghost-hover:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text}"
  button-stop:
    backgroundColor: "{colors.recording}"
    rounded: "{rounded.sm}"
    padding: "0 0.75rem"
    height: "2rem"
  button-lg:
    padding: "0 1.125rem"
    height: "2.5rem"
    typography: "{typography.body-lg}"
  button-sm:
    padding: "0 0.5rem"
    height: "1.75rem"
    typography: "{typography.label}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: "0 0.625rem"
    height: "2.25rem"
  chip:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text-2}"
    rounded: "{rounded.pill}"
    padding: "0 0.5625rem"
    height: "1.5rem"
    typography: "{typography.label}"
  chip-processing:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
  chip-finished:
    backgroundColor: "{colors.success-soft}"
    textColor: "{colors.success}"
  chip-recording:
    backgroundColor: "{colors.recording-soft}"
    textColor: "{colors.recording}"
  badge-verified:
    backgroundColor: "{colors.success-soft}"
    textColor: "{colors.success}"
    rounded: "{rounded.pill}"
    padding: "1px 7px"
    typography: "{typography.caption}"
  badge-unverified:
    backgroundColor: "{colors.warning-soft}"
    textColor: "{colors.warning}"
    rounded: "{rounded.pill}"
    padding: "1px 7px"
  badge-screenshot:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text-2}"
    rounded: "{rounded.pill}"
    padding: "1px 7px"
  topbar:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text}"
    height: "3.5rem"
    padding: "0 1.25rem"
  navlink:
    textColor: "{colors.text-2}"
    rounded: "{rounded.sm}"
    padding: "0 0.625rem"
    height: "2rem"
  tab:
    textColor: "{colors.text-2}"
    height: "2.5rem"
    typography: "{typography.body}"
  tab-selected:
    textColor: "{colors.text}"
  rail:
    backgroundColor: "{colors.surface-2}"
    rounded: "{rounded.md}"
    padding: "16px"
    width: "18.75rem"
  library-list:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    width: "60rem"
  library-row-hover:
    backgroundColor: "{colors.surface-2}"
  banner-warning:
    backgroundColor: "{colors.warning-soft}"
    rounded: "{rounded.md}"
    padding: "12px 14px"
  banner-error:
    backgroundColor: "{colors.recording-soft}"
    rounded: "{rounded.md}"
    padding: "12px 14px"
  banner-info:
    backgroundColor: "{colors.surface-2}"
    rounded: "{rounded.md}"
    padding: "12px 14px"
  recording-bar:
    backgroundColor: "{colors.recording-soft}"
    textColor: "{colors.recording}"
    height: "3.5rem"
    padding: "0 1.25rem"
  select:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: "0 2rem 0 0.625rem"
    height: "2rem"
  settings-rows:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.md}"
    width: "47.5rem"
  settings-row:
    padding: "12px 16px"
    height: "3.5rem"
  save-bar:
    backgroundColor: "{colors.accent-soft}"
    rounded: "{rounded.md}"
    padding: "10px 12px 10px 16px"
  lock-chip:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.text-2}"
    rounded: "{rounded.pill}"
    padding: "0 0.4375rem"
    height: "1.25rem"
    typography: "{typography.caption}"
  dialog:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "20px"
    width: "26.25rem"
  notes-column:
    textColor: "{colors.text}"
    typography: "{typography.body-notes}"
    width: "68ch"
  notes-box:
    backgroundColor: "the box colour's wash (--ks)"
    rounded: "{rounded.md}"
    padding: "14px 18px"
  notes-quote:
    backgroundColor: "{colors.surface-2}"
    rounded: "{rounded.md}"
    padding: "12px 16px"
  figure-plate:
    backgroundColor: "{colors.plate}"
    rounded: "{rounded.plate}"
    padding: "12px"
  ribbon-tip:
    backgroundColor: "{colors.text}"
    textColor: "{colors.bg}"
    rounded: "{rounded.sm}"
    padding: "4px 8px"
    typography: "{typography.caption}"
---

# Design System: Iris

This file records the design system as built in `src/index.css` and `src/components`.

## Overview

**Creative North Star: "The stage belongs to the notes"**

Iris is a quiet working tool around a reading column. The shell (top bar, library, rail, recorder)
is an Operate surface: dense, neutral, set in a 14px system sans, with hairlines doing the work
cards and shadows would do elsewhere. The notes are a Read surface: 17px text at 1.7 line height in
a 68-character column, shown only once the notes are finished. Until then progress is told in
plain sentences in the reading column's progress view, then in a 300px side rail, never as metrics
or stage names stacked above the notes.

Colour is cool, lightly indigo-tinted neutral in two layers: a white (or near-black) content
surface on a slightly darker ground, and a second neutral layer (`surface-2`) for the top bar,
rail, table headers and code. One indigo accent carries action, links, focus and the current step.
Red, green, amber and blue each mean exactly one kind of state. Light and dark themes differ only
in token values; figures stay on white plates in both, because redraws and screenshots are drawn on
white.

The one decorative element is the session ribbon: the session's length as a ticked line, with each
figure pinned where it was shown.

**Key Characteristics:**
- System UI sans throughout; no web fonts. KaTeX keeps its own math fonts.
- Two type sizes do most of the work: 14px for the app, 17px for the notes.
- Flat, hairline-bordered surfaces; one shadow, reserved for the dialog and the recording bar.
- One indigo accent; state colours used only for their state, always with words.
- Light, dark and system themes from one token set, switched by `data-theme` on `<html>`.
- Calm motion: 160–200ms state transitions, no page-load choreography, all off under reduced motion.

## Colors

Cool, accent-tinted neutrals with one indigo voice and four single-meaning state colours, each in a
strong / soft / line triplet.

### Primary
- **Iris Indigo** (`accent`; dark `accent-dark`): primary buttons, links, the focus ring, the
  selected-tab underline, the running step's spinner, ribbon pins and the wordmark. `accent-hover` darkens it in light and lightens it in dark. Text on it is
  `accent-text` (white in light, near-black in dark).
- **Indigo Wash** (`accent-soft`) with **Indigo Hairline** (`accent-line`): the processing chip, hovered drop zone and choice tiles, text selection, and the step path.

### Secondary (state colours)
- **Recording Red** (`recording`, `-soft`, `-line`): the recording dot, recording bar and stop
  button; failed chips and error banners; destructive actions; invalid LaTeX shown as source.
- **Finished Green** (`success`, `-soft`, `-line`): the Finished chip, finished steps and the
  Verified redraw badge.
- **Warning Amber** (`warning`, `-soft`, `-line`): warning banners, unverified original crops (badge and ribbon pin), capture warnings in the recording bar.
- **Info Blue** (`info`, `-soft`, `-line`): no app state of its own; the notes palette's blue.

### The notes palette
Colours inside the notes belong to the writer, not to the app: each box and step names one of ten
colours (`{color=…}` in the notes format, `src/notes/format.ts`), and a missing or unknown name is
grey. Each name is a text / wash / line triplet in both themes, set on the block as `--k`, `--ks`
and `--kl` by its `.c-<name>` class: **grey** (`text-2`, `surface-2`, `border`), **indigo**
(`accent`), **blue** (`info`), **teal** (`teal`), **green** (`success`), **amber** (`warning`),
**orange** (`orange`), **red** (`recording`), **pink** (`pink`) and **violet** (`violet`). Every
label colour is at least 4.6:1 on its own wash in both themes. Iris's writer uses indigo for a
problem and its steps, green for an answer or decision, blue for a definition or key idea, amber
for watch out and open questions and grey for the rest; the user can change any of them.

### Neutral
- **Cool Ground** (`bg`): the page behind everything.
- **Paper** (`surface`): the reading column, library list, inputs, buttons, dialog, banners.
- **Desk** (`surface-2`): top bar, progress rail, library header and row hover, table headers,
  code blocks, neutral chips, figure placeholders, skeletons.
- **Desk Pressed** (`surface-3`): pressed buttons, the selected theme-switch segment, tab counts,
  progress tracks, inline code in banners.
- **Hairline** (`border`) and **Control Edge** (`border-strong`): dividers and panel borders;
  button outlines, the dashed drop zone, ribbon ticks. **Field Edge** (`field-border`): text
  input outlines, at least 3:1 against `surface` in both themes.
- **Ink** (`text`) and **Muted Ink** (`text-2`): body and headings; meta, captions, labels,
  inactive tabs and nav links (at least 5.7:1 on `surface-2` in both themes).
- **Plate White** (`plate`): behind every figure, in both themes.
- **Scrim** (`scrim`): behind the open dialog.

### Named Rules
**The One Indigo Rule.** Indigo means "you can act here" or "this is where you are": action, link,
focus, the current step, the selected tab. It is never used as decoration or as a background fill
for whole regions.

**The One Meaning Rule.** In the app (chips, banners, buttons, badges, the ribbon) red is
recording, failure and deletion; green is finished; amber is warning and unverified. No state
colour borrows another's job, and none ever appears without a word that says the same thing.
Inside the notes, colour is the writer's (the notes palette) and carries no app meaning.

**The Token-Only Rule.** Themes differ only in custom-property values. Components reference tokens,
never literal colours, so a new component is dark-mode ready by construction.

**The White Plate Rule.** Figures sit on `plate` white in both themes. Never invert, tint or dim a
figure to suit dark mode; it would misrepresent what was shown.

## Typography

**Display Font:** System UI (`-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif`)
**Body Font:** the same stack
**Label/Mono Font:** `ui-monospace, 'SF Mono', Menlo, Consolas, monospace` for code and raw
event detail

**Character:** One native family at every level, so the app feels like part of the laptop and the
notes read like a well-set document. Hierarchy comes from size and weight, not from typeface.

### Hierarchy
- **Display** (300, 3.5rem, 1, −0.02em, tabular): the recorder's elapsed clock only.
- **Headline** (600, 1.5rem, 1.2, −0.01em): page titles (Library, session title) and the notes' own
  title.
- **Title** (600, 1.375rem, 1.2): notes section headings, with 48px above and 12px below.
- **Title small** (600, 1.125rem): notes sub-headings (32px above, 8px below), empty-state and error
  headings. The dialog heading sits just under it at 1.0625rem.
- **Body notes** (400, 1.0625rem, 1.7): the reading column, capped at 68ch. Tables in the notes
  drop to 0.9375rem at 1.5; captions to 0.875rem.
- **Body large** (400, 0.9375rem, 1.6): page ledes, transcript lines (72ch), large buttons, choice
  and drop-zone titles.
- **Body** (400, 0.875rem, 1.5): the app's base size. Buttons, tabs and nav links set it at 500.
- **Label** (500, 0.8125rem): chips, table headers, hints, file and row meta, breadcrumbs, rail
  headings (600), step details, transcript timestamps.
- **Caption** (400–500, 0.75rem): ribbon scale and tooltip, figure badges, tab counts.

### Named Rules
**The Two Sizes Rule.** The app is set at 14px; the notes at 17px on 1.7. New UI uses the app
scale; anything that is the user's content uses the notes scale.

**The Tabular Numbers Rule.** Every timer, timestamp, duration, count and percentage uses tabular
numerals (`.num`), so live values never jitter.

**The Sentence Case Rule.** Labels, headings and buttons are sentence case. Weights are 400, 500
and 600 (300 only for the recorder clock).

## Layout

**The Relative Size Rule.** The window is resized often, so nothing is sized in fixed pixels that
could make it overlap: controls (buttons, fields, chips, tabs, menus) are sized in rem, columns
are sized to their content or as shares of the row (`fr`, `max-content`), page widths are in rem
and breakpoints in em. Only hairlines, radii, shadows and the notes' own drawing stay in px. Sizes
below are given in px at the default text size (1rem = 16px).

- **Shell:** a sticky 56px top bar; main content padded 32px 24px 64px (24px 16px 48px under 560px).
- **Widths:** library and most pages cap at 960px; settings at 760px; upload and live setup at
  600px; the session page at 1200px, centred.
- **Session page:** header (breadcrumb, title and meta left; status chip and actions right), the
  ribbon 20px below, then a two-column grid: reading column `minmax(0, 1fr)` plus a 300px sticky
  rail, 32px apart. The notes column is capped at 68ch inside its track. Until the notes are
  finished the reading column holds the progress view instead, and there is no ribbon.
- **Library:** one bordered list; every row shares its columns (subgrid):
  `title minmax(0, 1fr) · status minmax(max-content, 12rem) · created · cost (right-aligned) ·
  actions`, the last three as wide as what they hold, so no cell runs into the next at any width;
  the title takes what is left and shortens with an ellipsis. Rows are at least 56px tall, each
  whole row a link.
- **Spacing rhythm:** a 4px base. Common steps are 4, 6, 8, 12, 16, 20, 24, 32, 48 and 64. 6px
  separates a label from its control and icon from text; 8px separates sibling buttons; 16–20px
  separates blocks; 48px opens a notes section.
- **Breakpoints:**
  - ≤1120px (70em): the session becomes one column and the rail collapses into a summary bar above the
    tabs, expandable in place.
  - ≤860px (53.75em): the library drops its header, date and cost columns, status moves under the title; the
    recording bar hides capture status; the session header stacks.
  - ≤560px (35em): tighter top bar and page padding; top-bar button labels hide to icons.
  - ≤400px (25em): the top bar's items close up (2px apart) so it fits a 360px phone.

## Elevation & Depth

Iris is flat. Depth comes from tonal layering (`bg` → `surface` → `surface-2` → `surface-3`) and
1px hairlines, not from shadows. Exactly two elements float in reading and recording: the
confirmation dialog and the fixed recording bar, which share one soft two-layer shadow (darker and
wider in dark mode). Editing the notes adds the controls that hover over the notes while you edit
them: the block toolbar, its menus, the formatting bubble, the formula editor and the drag label.
They take the same shadow and exist only in edit mode. Hover lifts are tonal (a background step),
except the ribbon pin, which rises 2px.

### Shadow Vocabulary
- **Float** (`box-shadow: 0 8px 24px -8px rgb(23 23 40 / 0.18), 0 2px 6px -2px rgb(23 23 40 / 0.08)`;
  dark `0 12px 32px -8px rgb(0 0 0 / 0.6), 0 2px 8px -2px rgb(0 0 0 / 0.4)`): the dialog, the
  recording bar, and the editing controls over the notes (see Editing the notes).

### Named Rules
**The Hairline Rule.** Separate with a 1px `border` line or a tonal step. A new panel, card or
row gets a hairline, never a shadow.

## Shapes

Gently rounded, never soft. Controls (buttons, inputs, nav links, tooltips) use 6px; panels
(rail, library list, banners, dialog, notes boxes and quotes, code blocks, drop zone) use 10px;
figure plates use 8px; inline code and the focus ring use 4px. Status chips, figure badges and tab
counts are full pills, which sets state apart from controls. The only non-rectangular forms are the
recording dot, the step-path rings and the ribbon pins (a 10×18 tab, rounded on top).

**The Two Radii Rule.** 6px if you can press it, 10px if it holds things. Pills are reserved for
status.

## Components

### Buttons
Compact, quiet, and obvious about the one thing to do.
- **Shape:** gently rounded (6px), 32px tall, 12px side padding, 500 weight, 6px icon gap, 16px icons.
- **Primary:** indigo fill with `accent-text`. At most one per region; it is the screen's main action
  (Export, Try again, Resume, Make notes, Start recording).
- **Secondary (default):** `surface` fill with a `border-strong` outline and `text` label.
- **Ghost:** no fill or border, `text-2` label; used for row actions and rail toggles.
- **Stop / destructive:** solid recording red with `on-recording` text; the recording bar's Stop and the confirm button of
  a dialog that removes something (deleting a session; discarding, restoring over or replacing
  edited notes). Nowhere else.
- **Sizes:** large 40px (18px padding, 15px text) for the start-recording action; small 28px (8px
  padding, 13px text).
- **Hover / Focus:** a 160ms tonal step (`surface-2`, or `accent-hover`); pressed steps to
  `surface-3`; disabled at 50% opacity. Focus is the global 2px indigo ring with a 2px offset.

### Chips and Badges
- **Status chip:** 24px pill, 13px/500, hairline border, always a word ("Finished", "Failed ·
  Transcribing") plus an icon or dot. Processing, polishing and starting use indigo with a spinning
  icon; finished green with a check; failed red with an alert icon; recording red with a
  pulsing dot; stalled and orphaned states stay neutral, and so does stopped (a recording ended
  without notes), with a dash icon. The amber `chip-warning` marks a missing
  setting (no API key).
- **Figure badge:** smaller pill (12px/500) under a figure: Verified redraw (green), Screenshot
  (neutral), Original crop · not verified (amber).

### Cards / Containers
- **Corner Style:** 10px.
- **Background:** `surface` for content containers, `surface-2` for the rail and info banners, a
  state `-soft` fill with its `-line` border for warning and error banners.
- **Shadow Strategy:** none (see Elevation & Depth).
- **Border:** 1px `border`.
- **Internal Padding:** 12–16px for banners and rail, 20px for choice tiles and the dialog, 32px for
  error panels.

### Inputs / Fields
- **Style:** 36px, `surface` fill, 1px `field-border`, 6px radius, 10px padding; label above at
  500 with a 13px `text-2` hint, 6px apart.
- **Focus:** border turns indigo and the 2px ring sits flush (0 offset).
- **Drop zone:** a dashed `border-strong` panel (10px radius, 40px padding) that turns indigo-washed
  on hover or drag-over; the whole zone is a labelled file input and takes the focus ring.
- **Select:** the native `<select>`, 32px, styled as a field (`field-border`, 6px radius), with a
  16px chevron drawn as a mask in `text-2` so it follows the theme. A changed, unsaved value keeps
  the indigo focus edge (1px inset ring) until it is saved or discarded.
- **Disabled:** 60% opacity.

### Settings
Grouped rows, like the library list: each group is a 13px `text-2` heading at 600 over one
bordered `surface` panel (10px radius) of rows at least 56px tall, split by hairlines. A row is
the setting's name (500) and a 13px `text-2` description at most 60ch on the left, its control on
the right (a 240px select, a value, or a mono path). Under 560px a row stacks, control below,
except one-value rows (Version) that stay on one line.
- **Save bar:** changes collect instead of saving at once. While anything is unsaved, a sticky bar
  sits 16px above the window's bottom edge: `accent-soft` with an `accent-line` hairline, the change
  in words ("Spoken language changed to **French**"), then Discard and the primary Save. No shadow.
  Leaving the page with an unsaved change asks first: the confirm dialog inside Iris; closing,
  reloading or quitting asks too (the browser's prompt, or the desktop app's own dialog).
- **Saved:** a 13px `success` line with a check, saying what now applies.
- **API key row** (OpenAI group): a status chip (green "Key set", amber "No key") then the
  actions: Replace… and a ghost Remove, or the primary Add key… when there is none. In the browser
  it says where the engine reads the key, with no actions. A key change is confirmed on the status
  line under the group, which takes no space while empty.
- **No-key warning:** the warning banner at the top of Library and Settings, its one action
  (Add key…, small primary) on the same line as the text (`banner-inline`). Settings leaves the
  action to the key row right below it.
- **Pinned by `.env`:** the control is disabled, a neutral "Set outside Iris" lock chip (20px pill)
  follows the name, and the description names the variable and how to free it.

### Export
The app exports PDF only: "Export PDF" (primary once the session is finished, download icon) on
the session page, and a small "Export PDF" on each finished library row. Both do the same thing
and change nothing on screen: no navigation, no tab switch, no theme change. The notes are drawn
in an invisible print sheet and printed from there. The engine's Markdown export stays an API
route (`GET /export`) for other apps; the UI doesn't offer it. Before printing, the notes' figures
and fonts finish loading. Printing (Export PDF, or the browser's Print) shows only the session's
title, its meta line and the notes, in the light colours on white whatever the app's theme (dark
applies to the screen only), A4 with 16mm by 15mm margins, 11pt text at 1.55, boxes keeping their colours. Steps, boxes and quotes split
wherever the page ends, so nothing tall leaves a big gap: a split box is drawn as two matching
boxes, and a title always stays with what follows it. Figures,
tables, code blocks and display formulas never split. The desktop app saves the
PDF where the user chooses (named after the session); a browser opens its print dialog.

### Editing the notes
Edit in place: the notes keep their reading look (same column, boxes, steps and figures), and no
Markdown is ever shown. "Edit notes" (secondary, pencil) sits beside Export once the session is
finished.
- **Editing strip:** sticky under the top bar, in the notes column: `accent-soft` with an
  `accent-line` hairline, "Editing notes" (600) and a 13px `text-2` hint, the status ("No changes
  yet", "Unsaved changes", "Saving…"), then Close (Discard once changed) and the primary Save,
  both small. Under 560px the hint goes and the status and buttons take their own line. Leaving
  with unsaved changes asks, as in Settings; Discard asks too (red confirm).
- **Selected block:** clicking any part of a block (text, title, border, padding, figure) selects
  the innermost block: a 1px `accent-line` outline 4px out (2px on boxes, following their radius).
- **Block toolbar:** floats above the selected block's top-right corner (Float, `surface`, 8px
  radius, 3px padding, 28px buttons of 13px 500 text, hairline separators): for a block inside a
  box or step first "Inside step ↑" / "Inside box ↑" in `text-2` (selects the block around it), then
  the drag handle (`text-2`, grab cursor), the colour (its dot and name; the dot alone under 560px)
  for boxes and steps, the type ("Turn into"), move up, move down, add below and delete.
- **Menus:** under their button (Float, 10px radius, 6px padding): a 12px `text-2` 600 heading,
  32px rows with a `text-2` icon, a hairline between groups. Add below lists text, heading, box,
  step, maths, then "Figures from this session" (one already placed is disabled, "Already in the
  notes"). Up and Down move, Escape closes and returns focus.
- **Colour swatches:** five to a row, 28px circles of the colour's wash and line with a 14px dot
  of its label colour; the current one is ringed in its colour.
- **Formatting bubble:** over selected text: bold, italic, strikethrough and "make maths" (Σ).
- **Formula editor:** clicking a formula (or Enter on it) opens LaTeX in a mono textarea with a live
  KaTeX preview on `surface-2`, then Cancel and Done. A new or empty formula reads "Empty formula"
  in `text-2` and is left out when saved.
- **Dragging:** by the handle, with the pointer (mouse or touch): after 5px a label ("Box · Key
  idea", `text` on `bg`, 6px radius) follows the pointer, the dragged block dims to 35%, and one
  3px indigo drop line with a ring at its start shows where it lands: full width between top-level
  blocks, indented to the text inside a box or step (move the pointer left of that text to leave
  it). Near the window's edges the page scrolls; Escape cancels; the moved block flashes once.
  Every drag has a toolbar equivalent (move up and down, "Inside … ↑").
- **Edited notes:** the meta line ends "Edited by you · Restore generated notes" (a link-style
  button; red confirm). After a re-run with edits, an amber banner says so with "Use the new
  notes" (red confirm) and the primary "Keep my edits".

### Renaming
A session's name is edited in place, on the session page (click the title, or the pencil that
shows on hover and to the keyboard) and in a library row (the pencil in its actions). The title
stays text in its own type and place, marked only by a 2px indigo underline, the one place Iris
uses an underlined field instead of a boxed one (a title isn't a form field). The line under it
says "Enter to save · Esc to cancel" in `text-2`, or why a save failed in `recording` red. Enter or
leaving the field saves, Esc cancels, and an empty or unchanged name keeps the old one. No Save or
Cancel buttons.

### Navigation
- **Top bar:** `surface-2` with a bottom hairline; wordmark (indigo iris mark plus "Iris"), nav
  links, then Upload, Record live, a 32px ghost gear button for Settings (`surface-3` while on the
  page) and the light / dark / system switch on the right.
- **Nav links:** 32px, `text-2` at 500, hover to `text` on `surface-3`.
- **Theme switch:** a 3-segment control on `surface`; the pressed segment is `surface-3`.
- **Tabs:** 40px text tabs, 20px apart, over a hairline; selected tab in `text` with a 2px indigo
  underline; counts in small neutral pills. ARIA tabs pattern.
- **Breadcrumb:** 13px `text-2` with a chevron separator.

### Progress Rail
A 300px sticky panel on `surface-2`: a "Progress" step list (finished steps green check, running
step indigo spinner and `text` at 500, failed step red), the "On this page" outline,
a "Cost" line (13px `text-2` label at 600, the value right-aligned in `text` at 500 with tabular
numerals; laid out even while empty), and a collapsed Details disclosure holding each step's plain
label with its cost and timing, and raw errors in mono. Under 1120px it
becomes a one-line summary bar that expands in place.

### Progress View
Shown in the reading column until the notes are finished (no notes before then): an 18px/600
heading for the state ("Making your notes"), the current step with its count in `text` at 500,
"Running for m:ss" in 13px `text-2` with tabular numerals, the full step list on `surface` in a
hairline box (the rail's step rows), and one `text-2` line saying the notes appear there and the
page can be left. Each line keeps its height while empty, so nothing moves as steps advance.

### Notes Column
- **Boxes** (`:::box[Title]{color=…}`): the colour's wash with its 1px line, 10px radius, 14px 18px
  padding; the title (the writer's own words, never a matched label) in 600 weight in the label
  colour. No title, no heading line. A box inside a step or box sits 12px below what precedes it;
  blocks go one level deep.
- **Steps** (`:::step[Title]{color=…}`): unboxed. Steps in a row form one numbered path: a 23px
  ring in the step colour with the number inside (tabular, 12px 600; never the word "Step"), a 1px
  path in the colour's line token down to the next ring, and the title in `text`. Anything between
  two steps ends the path; the next starts at 1. A box or figure inside a step stays on its path.
  The path is an `ol.step-run` (avoid `.step`: the progress list owns it).
- **Quotes** (`>`): a neutral box, `surface-2` with a hairline.
- **Problems** the notes can't be drawn as written (no end, unknown colour or kind of block, a
  third level, an unknown figure): one 14px line in `recording` red, in plain words, inside or in
  place of the block; a box with no end or of an unknown kind has a dashed edge. An image from
  outside the session is never loaded: a `text-2` line says so.
- **Math:** inline on the baseline; display math 12px above and below and scrolls sideways; invalid
  LaTeX shows as red mono source.
- **Tables and code:** hairline rows with a `surface-2` header, sideways scroll; code blocks on
  `surface-2` in mono at 14px, 10px radius.
- **Figures** (`::figure{#id}`, resolved from the session's visuals): centred on a white plate (8px radius, hairline, 12px padding, max height 520px);
  caption below at 14px `text-2` (max 56ch), then "Shown at m:ss" and a provenance badge. A
  figure the ribbon jumps to flashes a 2px indigo outline.

### Session Ribbon (signature)
A 22px track under the session header: a baseline with a tick every tenth of the session, start and
end times at each end and a centred label ("3 figures, placed where they were shown") in 12px
`text-2`. Each figure is a 10×18 pin at its source time: hollow indigo while being redrawn, filled
indigo when done, filled amber for an unverified crop. Hover or focus raises the pin 2px and shows
an inverted tooltip (`text` on `bg`) with time and caption; activating it scrolls to the figure.
Pins are buttons in a labelled group.

### Recording Bar and Recorder
A fixed 56px bar over the top bar while recording: `recording-soft` with a red hairline and the
Float shadow, a pulsing red dot and "Recording" with the tabular clock, capture status, and the Stop
button on the right. Below it the recorder centres the 56px light clock and a 16:10 preview, and
under the preview one 14px `text-2` progress line in tabular numerals ("Transcribed to 4:30 · 3
screens · 2 figures read"), not announced.

### Feedback States
Empty states are centred text (18px heading, `text-2` body at 52ch) with optional choice tiles.
Loading uses `surface-2` skeleton bars with a slow shimmer. Figure placeholders are `surface-2`
boxes with an indigo spinner. Status changes are announced politely; errors use alert banners with
a working retry.

### Motion
State changes only: 160ms (`cubic-bezier(0.16, 1, 0.3, 1)`) for hover, tab, chip and disclosure
changes; 200ms fades for new transcript lines and figures replacing placeholders. The recording dot
pulses (1.6s), spinners turn (1.4s). No entrance choreography. Under `prefers-reduced-motion` all
animation and transition is off and the dot is solid.

## Do's and Don'ts

### Do:
- **Do** keep the notes column fixed at 68ch and 17px/1.7; the notes appear whole once finished,
  and progress lines keep their height so nothing moves as steps advance.
- **Do** say state in words first: every chip, badge and step carries a label, with colour and icon
  as secondary signals.
- **Do** use the global focus ring (2px `accent`, 2px offset) on every interactive element,
  including whole-row links and the drop zone.
- **Do** use `.num` tabular numerals for every clock, timestamp, count and percentage.
- **Do** put figures on the white plate with their caption, source time and provenance badge.
- **Do** add new colours as a light and dark token pair, checked to WCAG AA in both themes.

### Don't:
- **Don't** add shadows to cards, panels or rows; the Float shadow belongs to the dialog and the
  recording bar only.
- **Don't** put metric tiles, cost cards or pipeline stage names above the notes; progress lives in
  the rail as plain sentences, with per-step costs and times behind Details.
- **Don't** use indigo or a state colour as decoration, or a state colour for a different state.
- **Don't** add web fonts, display faces, ALL-CAPS labels or eyebrow text above headings.
- **Don't** hard-code a colour in a component, or invert or tint figures in dark mode.
- **Don't** add a second decorative element; the session ribbon is the only one.
