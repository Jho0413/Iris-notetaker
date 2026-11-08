// Sharing an app's window brings that app to the front, so the user goes straight to what they
// are recording instead of staying on Iris. Chromium's own focus-on-share
// (CaptureController.setFocusBehavior) does nothing for a source chosen by
// setDisplayMediaRequestHandler (measured, Electron 45, macOS 26), so the main process does it.
//
// Electron has no API from a window to its app. macOS does: a window source's id
// (`window:<number>:0`) carries the window's CGWindowID, and the window list gives its owner's
// pid with no permission (only window titles need Screen Recording). osascript runs it, through
// the Objective-C bridge (no Apple Events, so no Automation prompt). It activates the app, which
// brings its windows forward; raising one window of several would need Accessibility access.

/** Finds the window's owner and activates it. The window number arrives as an argument, never
 * spliced into the script. Prints "no window" or "no app" when there is nothing to activate. */
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

/** The CGWindowID in a desktopCapturer window source id (`window:<number>:<n>`); null for a
 * screen or anything else. */
function windowNumber(sourceId) {
  const match = /^window:(\d+):\d+$/.exec(String(sourceId))
  return match ? match[1] : null
}

/**
 * Bring the app that owns the shared window to the front; a screen changes nothing. Never fails
 * the share: the recording runs either way, so a failure is only logged.
 *
 * @param {string} sourceId the chosen desktopCapturer source id
 * @param {{execFile: Function, log: (line: string) => void}} deps node:child_process execFile, the app's log
 * @returns {Promise<void>} once done (or failed)
 */
function bringSharedAppForward(sourceId, { execFile, log }) {
  const number = windowNumber(sourceId)
  if (!number) return Promise.resolve()
  return new Promise((resolve) => {
    execFile('/usr/bin/osascript', ['-l', 'JavaScript', '-e', ACTIVATE_OWNER_SCRIPT, number], { timeout: 5000 }, (err, stdout) => {
      const outcome = String(stdout ?? '').trim()
      if (err) log(`[capture] couldn't bring the shared window's app forward: ${err.message}`)
      else if (outcome !== 'activated') log(`[capture] didn't bring the shared window's app forward: ${outcome}`)
      resolve()
    })
  })
}

module.exports = { windowNumber, bringSharedAppForward }
