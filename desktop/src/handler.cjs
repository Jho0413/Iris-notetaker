// The app:// protocol: serves the built UI and forwards /api to the backend with the launch
// token. The page talks only to app://iris, so it never
// sees the backend's port or the token, and the UI still loads (and can say so) if the backend
// is down. Routing decisions are pure functions in routes.cjs.

const fs = require('node:fs')
const path = require('node:path')
const { contentSecurityPolicy } = require('./csp.cjs')
const { forward: forwardHttp } = require('./forward.cjs')
const routes = require('./routes.cjs')

/** Call before app.ready. No corsEnabled: other origins must not read app:// responses. */
function registerScheme(protocol) {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: routes.SCHEME,
      privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true, codeCache: true },
    },
  ])
}

/**
 * @param {object} opts
 * @param {Electron.Protocol} opts.protocol  the session's protocol object
 * @param {typeof forwardHttp} [opts.forward]  how API requests reach the backend (tests swap it)
 * @param {string} opts.distDir  the built UI (frontend/dist)
 * @param {() => {port: number, token: string} | null} opts.backend  null while it isn't running
 * @param {(line: string) => void} opts.log
 */
function handleAppProtocol({ protocol, distDir, backend, log, forward = forwardHttp }) {
  const realDist = fs.realpathSync(distDir)
  const indexFile = path.join(realDist, 'index.html')
  const indexHtml = fs.readFileSync(indexFile, 'utf8')
  const csp = contentSecurityPolicy(indexHtml)

  protocol.handle(routes.SCHEME, async (request) => {
    const r = routes.route(request.url)
    try {
      if (r.kind === 'api') return await forwardApi(request, r.path)
      if (r.kind === 'static') return await serveStatic(r.pathname)
    } catch (err) {
      log(`[app] ${request.method} ${request.url} failed: ${err?.stack ?? err}`)
      return text(500, 'Internal error')
    }
    return text(404, 'Not found')
  })

  async function forwardApi(request, apiPath) {
    const target = backend()
    if (!target) return json(503, { detail: 'The Iris engine is not running.' })
    try {
      return await forward(request, apiPath, target)
    } catch (err) {
      if (request.signal?.aborted) return text(499, 'Cancelled')
      log(`[app] forward ${request.method} ${apiPath} failed: ${err}`)
      return json(502, { detail: 'The Iris engine is not responding.' })
    }
  }

  async function serveStatic(pathname) {
    const file = routes.resolveInDist(realDist, pathname)
    if (file) {
      let real
      try {
        real = await fs.promises.realpath(file)
      } catch {
        real = null // missing
      }
      if (real && routes.isInside(realDist, real)) {
        const stat = await fs.promises.stat(real)
        if (stat.isFile() && real !== indexFile) {
          return new Response(await fs.promises.readFile(real), {
            headers: { 'content-type': routes.contentType(real), 'cache-control': 'no-cache' },
          })
        }
        if (stat.isFile() || stat.isDirectory()) return serveIndex()
      }
    }
    return file && routes.isClientRoute(pathname) ? serveIndex() : text(404, 'Not found')
  }

  async function serveIndex() {
    return new Response(await fs.promises.readFile(indexFile), {
      headers: {
        'content-type': 'text/html; charset=utf-8',
        'content-security-policy': csp,
        'cache-control': 'no-cache',
      },
    })
  }
}

function text(status, body) {
  return new Response(body, { status, headers: { 'content-type': 'text/plain; charset=utf-8' } })
}

function json(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

module.exports = { registerScheme, handleAppProtocol }
