// The packaged app refuses to start with a Chromium remote-debugging switch. A debugger attached
// to Iris runs code as Iris, which the Keychain trusts with the OpenAI key without asking. The
// Node switches (--inspect, NODE_OPTIONS, ELECTRON_RUN_AS_NODE) are turned off by Electron fuses
// (electron-builder.yml), but no fuse covers these. Development keeps every debugging switch.

const REMOTE_DEBUGGING_SWITCHES = ['remote-debugging-port', 'remote-debugging-pipe']

/** The remote-debugging switch the app was started with, if it must refuse to run; else null.
 * `hasSwitch` is Electron's `app.commandLine.hasSwitch`, which parses switches as Chromium does. */
function refusedDebugSwitch({ packaged, hasSwitch }) {
  if (!packaged) return null
  return REMOTE_DEBUGGING_SWITCHES.find((name) => hasSwitch(name)) ?? null
}

module.exports = { refusedDebugSwitch }
