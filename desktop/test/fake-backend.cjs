// A stand-in for `python -m iris serve --secrets-from-stdin` in backend.test.cjs. Like the real
// backend, it reads the secrets line ({"openai_api_key", "api_token"}) from stdin before serving,
// and refuses requests without that token. Behaviour from argv[2]:
//   ok      serve /api/health (with the token) until SIGTERM
//   crash   exit 3 right away, without reading stdin
//   slow    never answer health
//   stubborn  ignore SIGTERM
// EXIT_AFTER_MS in the environment: exit on its own after that long.
const http = require('node:http')
const mode = process.argv[2]
const port = Number(process.argv[3])
if (mode === 'crash') process.exit(3)
// Bounded lifetime for a process the test can't signal (fake-wrapper.cjs's child).
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
    res.writeHead(404)
    res.end()
  })
  server.listen(port, '127.0.0.1')
  if (mode !== 'stubborn') process.on('SIGTERM', () => server.close(() => process.exit(0)))
}
