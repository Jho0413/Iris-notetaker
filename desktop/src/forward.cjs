const http = require('node:http')
const { Readable } = require('node:stream')
const routes = require('./routes.cjs')

const API_CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"

const NO_BODY = new Set([101, 204, 205, 304])

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

module.exports = { forward }
