// What Iris windows may load, open and use, as pure functions (tested in test/policy.test.cjs).
// main.cjs applies them to every web contents.

const path = require('node:path')
const { fileURLToPath } = require('node:url')
const routes = require('./routes.cjs')

/** The Iris UI: app://iris/... Compared by scheme and host (URL.origin is "null" for app:). */
function isIrisUrl(url) {
  try {
    const u = new URL(url)
    return u.protocol === `${routes.SCHEME}:` && u.host === routes.HOST && !u.username && !u.password
  } catch {
    return false
  }
}

/** One of the app's own pages (key screen, starting screen) inside `pagesDir`. */
function isDesktopPage(url, pagesDir) {
  try {
    const u = new URL(url)
    if (u.protocol !== 'file:') return false
    const file = fileURLToPath(u)
    return routes.isInside(path.resolve(pagesDir), path.resolve(file)) && file.endsWith('.html')
  } catch {
    return false
  }
}

/** A link the user clicked that should open in their browser: http(s) only, else null. */
function externalUrl(url) {
  try {
    const u = new URL(url)
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.href : null
  } catch {
    return null
  }
}

/**
 * A session file the UI opens in a new window ("Open full size" on a figure):
 * app://iris/api/sessions/<id>/files/<dir>/<name>, nothing else.
 */
function isSessionFileUrl(url) {
  if (!isIrisUrl(url)) return false
  const { pathname, search, hash } = new URL(url)
  return !search && !hash && /^\/api\/sessions\/[A-Za-z0-9_-]+\/files\/[A-Za-z0-9_-]+\/[A-Za-z0-9_.-]+$/.test(pathname) && !pathname.includes('/.')
}

// Microphone and screen capture, for the live recorder. Nothing else (notifications,
// geolocation, clipboard reads, ...) is ever granted.
const GRANTED = new Set(['media', 'display-capture'])

/**
 * Whether to grant a permission request.
 * @param {string} permission
 * @param {string} requestingUrl
 * @param {{mediaTypes?: string[]}} [details]
 */
function permissionAllowed(permission, requestingUrl, details = {}) {
  if (!GRANTED.has(permission) || !isIrisUrl(requestingUrl)) return false
  if (permission === 'media') {
    const types = details.mediaTypes ?? []
    return types.every((t) => t === 'audio' || t === 'video')
  }
  return true
}

/** Permission checks (navigator.permissions, device labels): the same rule, by origin. */
function permissionCheckAllowed(permission, requestingOrigin) {
  return GRANTED.has(permission) && isIrisUrl(requestingOrigin)
}

/**
 * IPC from the Iris UI itself: the top frame of `win`'s page, showing app://iris. Not another
 * window, not an iframe inside the page, and not one of the app's own pages (e.g. the starting
 * screen) loaded in the same window. Used for the main window's bridge (pages/preload-main.cjs).
 */
function fromIrisWindow(event, win) {
  const frame = event?.senderFrame
  return Boolean(
    win && !win.isDestroyed?.() && event.sender === win.webContents && frame && frame.parent === null &&
      isIrisUrl(frame.url),
  )
}

module.exports = {
  isIrisUrl,
  isDesktopPage,
  isSessionFileUrl,
  externalUrl,
  permissionAllowed,
  permissionCheckAllowed,
  fromIrisWindow,
}
