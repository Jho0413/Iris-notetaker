// What Iris's own picker (pages/picker.html) offers to record, as pure functions (tested in
// test/picker.test.cjs). main.cjs fetches the sources with desktopCapturer when the picker opens
// and gives the picker this list, a snapshot: it isn't refreshed while the picker is open.
//
// The entire screen comes first and is selected by default: it keeps recording whichever app
// the user switches to, where a window stops being captured once it is hidden or closed.

/** System Settings at Privacy & Security › Screen & System Audio Recording. */
const SCREEN_SETTINGS_URL = 'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture'

/**
 * The picker's list: screens first (the main display first), then app windows.
 *
 * @param {{id: string, name: string, displayId: string, hasThumbnail: boolean}[]} sources
 *   from desktopCapturer.getSources, in its order (windows front to back)
 * @param {{ownWindowIds: string[], displays: {id: number, label?: string}[], primaryDisplayId: number}} context
 *   ownWindowIds: the media source ids of Iris's own windows (the picker too), never offered:
 *   recording Iris itself is never what the user wants, and a hidden window stops being captured
 * @returns {{screens: {id: string, label: string, detail: string, recommended: boolean}[],
 *   windows: {id: string, title: string}[]}}
 */
function listSources(sources, { ownWindowIds, displays, primaryDisplayId }) {
  const screenSources = sources.filter((s) => s.id.startsWith('screen:'))
  const primary = String(primaryDisplayId)
  const ordered = [
    ...screenSources.filter((s) => s.displayId === primary),
    ...screenSources.filter((s) => s.displayId !== primary),
  ]
  const several = ordered.length > 1
  const screens = ordered.map((s, i) => ({
    id: s.id,
    // One display: "Entire screen". Several: numbered, with the display's name to tell them apart.
    label: several ? `Screen ${i + 1}` : 'Entire screen',
    detail: several ? (displays.find((d) => String(d.id) === s.displayId)?.label ?? '') : '',
    recommended: i === 0,
  }))

  const own = new Set(ownWindowIds)
  const windows = sources
    .filter((s) => s.id.startsWith('window:') && !own.has(s.id))
    // Untitled or empty windows are helper panels, overlays and the like, not something to record.
    .filter((s) => s.name.trim() !== '' && s.hasThumbnail)
    .map((s) => ({ id: s.id, title: s.name.trim() }))

  return { screens, windows }
}

/** What is selected when the picker opens: the first screen, else the first window, else nothing. */
function defaultSelection({ screens, windows }) {
  return screens[0]?.id ?? windows[0]?.id ?? null
}

/** The id the picker page sent back, if it is one it was offered; otherwise null. */
function chosenId(list, id) {
  if (typeof id !== 'string') return null
  return [...list.screens, ...list.windows].some((item) => item.id === id) ? id : null
}

/**
 * A display-media request for the Mac's sound alone (the Live page's "Record audio"): answered
 * with `{audio: 'loopback'}` and no picker. Nothing is chosen, and with no screen source macOS
 * asks for no Screen Recording permission (measured, Electron 45, macOS 26). A request with a
 * picture must get one: answering it with sound only is refused.
 */
function soundOnly(request) {
  return !request.videoRequested && Boolean(request.audioRequested)
}

module.exports = { SCREEN_SETTINGS_URL, listSources, defaultSelection, chosenId, soundOnly }
