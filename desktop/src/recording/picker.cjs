const SCREEN_SETTINGS_URL = 'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture'

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
    label: several ? `Screen ${i + 1}` : 'Entire screen',
    detail: several ? (displays.find((d) => String(d.id) === s.displayId)?.label ?? '') : '',
    recommended: i === 0,
  }))

  const own = new Set(ownWindowIds)
  const windows = sources
    .filter((s) => s.id.startsWith('window:') && !own.has(s.id))
    .filter((s) => s.name.trim() !== '' && s.hasThumbnail)
    .map((s) => ({ id: s.id, title: s.name.trim() }))

  return { screens, windows }
}

function defaultSelection({ screens, windows }) {
  return screens[0]?.id ?? windows[0]?.id ?? null
}

function chosenId(list, id) {
  if (typeof id !== 'string') return null
  return [...list.screens, ...list.windows].some((item) => item.id === id) ? id : null
}

function soundOnly(request) {
  return !request.videoRequested && Boolean(request.audioRequested)
}

module.exports = { SCREEN_SETTINGS_URL, listSources, defaultSelection, chosenId, soundOnly }
