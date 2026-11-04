// What the app:// handler does with a request, as pure functions (tested in test/routes.test.cjs).
// The Electron glue is in handler.cjs.
//
//   app://iris/api/...   → forwarded to the backend at http://127.0.0.1:<port>/api/...
//   app://iris/<file>    → a file inside the built UI (frontend/dist)
//   anything else        → index.html, so React Router can show deep routes after a reload

const path = require('node:path')

const SCHEME = 'app'
const HOST = 'iris'
const ORIGIN = `${SCHEME}://${HOST}`
const TOKEN_HEADER = 'x-iris-token'

// Hop-by-hop headers (RFC 9110 §7.6.1) are not forwarded, and nor is a token the page made up.
const DROPPED_REQUEST_HEADERS = new Set([
  'connection',
  'keep-alive',
  'proxy-authorization',
  'proxy-connection',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'host',
  TOKEN_HEADER,
])

/**
 * Decide what a request URL is for.
 * @returns {{kind: 'api', path: string} | {kind: 'static', pathname: string} | {kind: 'deny'}}
 *   `path` is the path and query to forward; `pathname` is the decoded file path to look up.
 */
function route(requestUrl) {
  let url
  try {
    url = new URL(requestUrl)
  } catch {
    return { kind: 'deny' }
  }
  if (url.protocol !== `${SCHEME}:` || url.host !== HOST || url.username || url.password) {
    return { kind: 'deny' }
  }
  if (url.pathname === '/api' || url.pathname.startsWith('/api/')) {
    return { kind: 'api', path: url.pathname + url.search }
  }
  let pathname
  try {
    pathname = decodeURIComponent(url.pathname)
  } catch {
    return { kind: 'deny' } // malformed escapes, e.g. "%E0%A4%A"
  }
  if (pathname.includes('\0')) return { kind: 'deny' }
  return { kind: 'static', pathname }
}

/**
 * The backend URL for an `api` route. Only the path and query come from the request: the result
 * is always on 127.0.0.1:<port>, whatever the path holds ("//evil.example", "@evil", ...).
 */
function forwardUrl(port, apiPath) {
  if (!Number.isInteger(port) || port <= 0 || port > 65535) throw new Error(`bad port ${port}`)
  if (!apiPath.startsWith('/api')) throw new Error(`not an API path: ${apiPath}`)
  const target = new URL(`http://127.0.0.1:${port}${apiPath}`)
  if (target.host !== `127.0.0.1:${port}`) throw new Error(`forward escaped the backend: ${apiPath}`)
  return target.href
}

/**
 * A backend redirect's Location as seen by the page: a backend URL (or a relative one) becomes
 * the same path on app://iris; anything else is dropped (null), so a redirect can never send the
 * page, or the token, elsewhere.
 */
function rewriteLocation(location, port) {
  const backend = `http://127.0.0.1:${port}`
  let url
  try {
    url = new URL(location, `${backend}/`)
  } catch {
    return null
  }
  if (url.origin !== backend) return null
  return `${ORIGIN}${url.pathname}${url.search}${url.hash}`
}

/**
 * Headers to send to the backend: the page's own, minus hop-by-hop ones, plus the launch token.
 * The token always overwrites anything the page sent under that name.
 * @param {Iterable<[string, string]>} headers
 */
function forwardHeaders(headers, token) {
  const out = new Headers()
  for (const [name, value] of headers) {
    if (!DROPPED_REQUEST_HEADERS.has(name.toLowerCase())) out.append(name, value)
  }
  if (token) out.set(TOKEN_HEADER, token)
  return out
}

/**
 * The file in `distDir` for a decoded pathname, or null if it would fall outside it (`..`,
 * absolute paths). Symlinks are checked by the caller against the real path.
 */
function resolveInDist(distDir, pathname) {
  const root = path.resolve(distDir)
  const relative = pathname.replace(/^\/+/, '')
  const file = path.resolve(root, relative)
  return isInside(root, file) ? file : null
}

/** Whether `file` is `root` or below it. Both must be absolute and normalized. */
function isInside(root, file) {
  const rel = path.relative(root, file)
  if (rel === '') return true
  return rel !== '..' && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel)
}

/**
 * A missing path is a client-side route (index.html) unless it names a file, e.g. a missing
 * "/assets/x.js" is a 404 rather than HTML served as JavaScript.
 */
function isClientRoute(pathname) {
  const last = pathname.split('/').pop() ?? ''
  return !last.includes('.')
}

const CONTENT_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.txt': 'text/plain; charset=utf-8',
  '.map': 'application/json',
}

function contentType(file) {
  return CONTENT_TYPES[path.extname(file).toLowerCase()] ?? 'application/octet-stream'
}

module.exports = {
  ORIGIN,
  SCHEME,
  HOST,
  TOKEN_HEADER,
  route,
  forwardUrl,
  forwardHeaders,
  rewriteLocation,
  resolveInDist,
  isInside,
  isClientRoute,
  contentType,
}
