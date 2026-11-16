const WELCOME_SHOWN = 'welcomeShown'

function shouldWelcome({ hasKey, seen }) {
  return !hasKey && !seen
}

module.exports = { WELCOME_SHOWN, shouldWelcome }
