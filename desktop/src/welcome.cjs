// The first-run welcome is shown once per install, never again (tested in test/welcome.test.cjs).
// `welcomeShown` in app-state.json records it. main.cjs sets it on the first launch whatever
// happens (a key saved, "Set up later", the window closed), and on any launch that already has a
// key, so an existing install that later removes its key isn't welcomed.

const WELCOME_SHOWN = 'welcomeShown'

/** Show the welcome only on a first launch with no key. */
function shouldWelcome({ hasKey, seen }) {
  return !hasKey && !seen
}

module.exports = { WELCOME_SHOWN, shouldWelcome }
