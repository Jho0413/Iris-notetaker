const ACTIVATE_OWNER_SCRIPT = `
ObjC.import('AppKit')
ObjC.import('CoreGraphics')
function run(argv) {
  const number = Number(argv[0])
  const windows = ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionAll, 0)))
  const win = windows.find((w) => w.kCGWindowNumber === number)
  if (!win) return 'no window'
  const owner = $.NSRunningApplication.runningApplicationWithProcessIdentifier(win.kCGWindowOwnerPID)
  if (owner.isNil()) return 'no app'
  return owner.activateWithOptions($.NSApplicationActivateAllWindows) ? 'activated' : 'refused'
}
`

const ACTIVATE_TIMEOUT_MS = 5000

function windowNumber(sourceId) {
  const match = /^window:(\d+):\d+$/.exec(String(sourceId))
  return match ? match[1] : null
}

function bringSharedAppForward(sourceId, { execFile, log }) {
  const number = windowNumber(sourceId)
  if (!number) return Promise.resolve()
  return new Promise((resolve) => {
    execFile('/usr/bin/osascript', ['-l', 'JavaScript', '-e', ACTIVATE_OWNER_SCRIPT, number], { timeout: ACTIVATE_TIMEOUT_MS }, (err, stdout) => {
      const outcome = String(stdout ?? '').trim()
      if (err) log(`[capture] couldn't bring the shared window's app forward: ${err.message}`)
      else if (outcome !== 'activated') log(`[capture] didn't bring the shared window's app forward: ${outcome}`)
      resolve()
    })
  })
}

module.exports = { windowNumber, bringSharedAppForward }
