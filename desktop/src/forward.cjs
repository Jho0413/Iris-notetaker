// Forwards one app://iris/api request to the backend over node:http, streaming both ways.
//
// node:http rather than Electron's net.fetch: piping honours backpressure, so a 500 MB upload
// costs the main process about 90 MB of memory instead of about 1.6 GB (net.fetch reads the
// whole request body ahead of the socket), measured with a real upload
// (docs/architecture/desktop.md, "The app:// handler"). The target is always 127.0.0.1, so
// Chromium's network stack adds nothing here.

const http = require('node:http')
const { Readable } = require('node:stream')
const routes = require('./routes.cjs')

// Sent with every API response. fetch, XHR and EventSource ignore it; it only applies when a
// response is shown as a document, such as a figure opened full size in its own window: no
// script, and an opaque origin (sandbox), so even a hostile SVG couldn't call the API.
const API_CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"

// Responses that carry no body; a Response with one would throw.
const NO_BODY = new Set([101, 204, 205, 304])

/**
 * @param {Request} request  the page's request, as protocol.handle gives it
 * @param {string} apiPath   path and query, from routes.route()
 * @param {{port: number, token: string}} target
 * @returns {Promise<Response>}
 */
function forward(request, apiPath, target) {
  const url = routes.forwardUrl(target.port, apiPath)
  const headers = {}
  for (const [name, value] of routes.forwardHeaders(request.headers, target.token)) headers[name] = value
  const hasBody = request.body && request.method !== 'GET' && request.method !== 'HEAD'
  if (hasBody && !('content-length' in headers)) headers['transfer-encoding'] = 'chunked'

  return new Promise((resolve, reject) => {
    const req = http.request(url, { method: request.method, headers, signal: request.signal ?? undefined })
    req.on('error', reject)
    req.on('response', (res) => {
      const out = new Headers()
      for (const [name, value] of Object.entries(res.headers)) {
        for (const v of [].concat(value ?? [])) out.append(name, v)
      }
      out.set('content-security-policy', API_CSP)
      const location = out.get('location')
      if (location) {
        const rewritten = routes.rewriteLocation(location, target.port)
        if (rewritten) out.set('location', rewritten)
        else out.delete('location')
      }
      const code = res.statusCode ?? 0
      const status = code >= 200 && code <= 599 ? code : 502
      const empty = NO_BODY.has(status) || request.method === 'HEAD'
      if (empty) res.resume()
      // Cancelling the page's stream (an EventSource closed, a page unloaded) destroys `res`,
      // which closes the socket, so the backend sees the client go away.
      resolve(new Response(empty ? null : Readable.toWeb(res), { status, statusText: res.statusMessage, headers: out }))
    })
    if (hasBody) {
      const body = Readable.fromWeb(request.body)
      body.on('error', (err) => req.destroy(err))
      body.pipe(req)
    } else {
      req.end()
    }
  })
}

module.exports = { forward, API_CSP }
