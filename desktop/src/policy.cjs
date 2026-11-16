const { fileURLToPath } = require('node:url')
const routes = require('./routes.cjs')

function isIrisUrl(url) {
  try {
    const u = new URL(url)
    return u.protocol === `${routes.SCHEME}:` && u.host === routes.HOST && !u.username && !u.password
  } catch {
    return false
  }
}

function externalUrl(url) {
  try {
    const u = new URL(url)
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.href : null
  } catch {
    return null
  }
}

function isSessionFileUrl(url) {
  if (!isIrisUrl(url)) return false
  const { pathname, search, hash } = new URL(url)
  return !search && !hash && /^\/api\/sessions\/[A-Za-z0-9_-]+\/files\/[A-Za-z0-9_-]+\/[A-Za-z0-9_.-]+$/.test(pathname) && !pathname.includes('/.')
}

const GRANTED = new Set(['media', 'display-capture'])

function permissionAllowed(permission, requestingUrl, details = {}) {
  if (!GRANTED.has(permission) || !isIrisUrl(requestingUrl)) return false
  if (permission === 'media') {
    const types = details.mediaTypes ?? []
    return types.every((t) => t === 'audio' || t === 'video')
  }
  return true
}

function permissionCheckAllowed(permission, requestingOrigin) {
  return GRANTED.has(permission) && isIrisUrl(requestingOrigin)
}

function fromIrisWindow(event, win) {
  return isIrisUrl(senderTopFrame(event, win)?.url)
}

function fromDesktopPage(event, win, pageFile) {
  try {
    return fileURLToPath(senderTopFrame(event, win)?.url) === pageFile
  } catch {
    return false
  }
}

function senderTopFrame(event, win) {
  const frame = event.senderFrame
  return win && !win.isDestroyed() && event.sender === win.webContents && frame?.parent === null ? frame : null
}

module.exports = {
  isIrisUrl,
  isSessionFileUrl,
  externalUrl,
  permissionAllowed,
  permissionCheckAllowed,
  fromIrisWindow,
  fromDesktopPage,
}
