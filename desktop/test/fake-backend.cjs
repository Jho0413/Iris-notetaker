const http = require('node:http')
const mode = process.argv[2]
const port = Number(process.argv[3])
if (mode === 'crash') process.exit(3)
if (process.env.EXIT_AFTER_MS) setTimeout(() => process.exit(0), Number(process.env.EXIT_AFTER_MS))

let input = ''
process.stdin.setEncoding('utf8')
process.stdin.on('data', (chunk) => (input += chunk))
process.stdin.on('end', () => serve(JSON.parse(input)))

function serve(secrets) {
  const token = secrets.api_token
  const env = ['OPENAI_API_KEY', 'IRIS_API_TOKEN'].filter((k) => k in process.env)
  console.log(
    `fake backend ${mode} stdin=${JSON.stringify(Object.keys(secrets))} key=${secrets.openai_api_key ? 'set' : 'unset'} ` +
      `secrets-in-env=${env.length ? env.join(',') : 'none'} parent=${process.env.IRIS_PARENT_PID}`,
  )
  if (mode === 'stubborn') process.on('SIGTERM', () => console.log('ignoring SIGTERM'))
  const server = http.createServer((req, res) => {
    if (mode === 'slow') return
    if (req.headers['x-iris-token'] !== token) {
      res.writeHead(401)
      return res.end()
    }
    if (req.url === '/api/health') {
      res.writeHead(200, { 'content-type': 'application/json' })
      return res.end('{"ok":true}')
    }
    if (req.url === '/api/sessions') {
      res.writeHead(200, { 'content-type': 'application/json' })
      return res.end('[{"id":"a","status":"processing"}]')
    }
    if (req.url === '/api/not-json') {
      res.writeHead(200, { 'content-type': 'text/plain' })
      return res.end('not json')
    }
    const stop = req.method === 'POST' && req.url.match(/^\/api\/live\/([^/]+)\/stop$/)
    if (stop) {
      console.log(`stopped ${stop[1]}`)
      res.writeHead(stop[1] === 'a' ? 409 : 200, { 'content-type': 'application/json' })
      return res.end('{}')
    }
    res.writeHead(404)
    res.end()
  })
  server.listen(port, '127.0.0.1')
  if (mode !== 'stubborn') process.on('SIGTERM', () => server.close(() => process.exit(0)))
}
