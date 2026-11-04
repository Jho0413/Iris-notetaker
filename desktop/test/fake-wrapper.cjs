// A stand-in for development's `uv run python …` in backend.test.cjs: a wrapper that ignores
// SIGTERM and runs the fake backend as its own child, which inherits the wrapper's stdout and
// stderr. Killing the wrapper leaves the grandchild holding the pipes until it exits on its own
// (EXIT_AFTER_MS, read by fake-backend.cjs).
const { spawn } = require('node:child_process')
const path = require('node:path')
process.on('SIGTERM', () => {})
spawn(process.execPath, [path.join(__dirname, 'fake-backend.cjs'), 'ok', process.argv[2]], { stdio: 'inherit' })
setInterval(() => {}, 1000) // the wrapper itself stays until killed
