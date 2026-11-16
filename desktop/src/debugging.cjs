const REMOTE_DEBUGGING_SWITCHES = ['remote-debugging-port', 'remote-debugging-pipe']

function refusedDebugSwitch({ packaged, hasSwitch }) {
  if (!packaged) return null
  return REMOTE_DEBUGGING_SWITCHES.find((name) => hasSwitch(name)) ?? null
}

module.exports = { refusedDebugSwitch }
