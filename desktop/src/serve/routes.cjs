const path = require('node:path')

const SCHEME = 'app'
const HOST = 'iris'
const ORIGIN = `${SCHEME}://${HOST}`
const TOKEN_HEADER = 'x-iris-token'

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
    return { kind: 'deny' }
  }
  if (pathname.includes('\0')) return { kind: 'deny' }
  return { kind: 'static', pathname }
}

function forwardUrl(port, apiPath) {
  if (!Number.isInteger(port) || port <= 0 || port > 65535) throw new Error(`bad port ${port}`)
  if (!apiPath.startsWith('/api')) throw new Error(`not an API path: ${apiPath}`)
  const target = new URL(`http://127.0.0.1:${port}${apiPath}`)
  if (target.host !== `127.0.0.1:${port}`) throw new Error(`forward escaped the backend: ${apiPath}`)
  return target.href
}

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

function forwardHeaders(headers, token) {
  const out = new Headers()
  for (const [name, value] of headers) {
    if (!DROPPED_REQUEST_HEADERS.has(name.toLowerCase())) out.append(name, value)
  }
  if (token) out.set(TOKEN_HEADER, token)
  return out
}

function resolveInDist(distDir, pathname) {
  const root = path.resolve(distDir)
  const relative = pathname.replace(/^\/+/, '')
  const file = path.resolve(root, relative)
  return isInside(root, file) ? file : null
}

function isInside(root, file) {
  const rel = path.relative(root, file)
  if (rel === '') return true
  return rel !== '..' && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel)
}

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
