// A stand-in for `python -m iris serve` in backend.test.cjs. Behaviour from argv[2]:
//   ok      serve /api/health (with the token) until SIGTERM
//   crash   exit 3 right away
//   slow    never answer health
//   stubborn  ignore SIGTERM
// EXIT_AFTER_MS in the environment: exit on its own after that long.
const http = require('node:http')
const mode = process.argv[2]
const port = Number(process.argv[3])
if (mode === 'crash') process.exit(3)
// Bounded lifetime for a process the test can't signal (fake-wrapper.cjs's child).
if (process.env.EXIT_AFTER_MS) setTimeout(() => process.exit(0), Number(process.env.EXIT_AFTER_MS))
console.log(`fake backend ${mode} env-token=${process.env.IRIS_API_TOKEN ? 'set' : 'unset'} parent=${process.env.IRIS_PARENT_PID}`)
if (mode === 'stubborn') process.on('SIGTERM', () => console.log('ignoring SIGTERM'))
const server = http.createServer((req, res) => {
  if (mode === 'slow') return
  if (req.headers['x-iris-token'] !== process.env.IRIS_API_TOKEN) {
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
