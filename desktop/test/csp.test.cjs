const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { test } = require('node:test')
const { contentSecurityPolicy, inlineScriptHashes } = require('../src/csp.cjs')

const sha = (s) => `'sha256-${crypto.createHash('sha256').update(s).digest('base64')}'`

test('inline scripts are hashed, external ones skipped', () => {
  const html = '<script>a()</script><script type="module" src="/x.js"></script><SCRIPT nonce="n">\nb()\n</SCRIPT>'
  assert.deepEqual(inlineScriptHashes(html), [sha('a()'), sha('\nb()\n')])
})

test('the policy allows only the app and the hashed inline scripts', () => {
  const csp = contentSecurityPolicy('<script>theme()</script>')
  assert.match(csp, /default-src 'self'/)
  assert.match(csp, new RegExp(`script-src 'self' ${sha('theme()').replace(/[+/]/g, '\\$&')}(;|$)`))
  assert.doesNotMatch(csp, /unsafe-eval/)
  assert.doesNotMatch(csp.split(';').find((d) => d.includes('script-src')), /unsafe-inline/)
  assert.match(csp, /object-src 'none'/)
  assert.match(csp, /frame-ancestors 'none'/)
})

test("workers load only from the app's own files, as the live frame timer does (#39)", () => {
  const directives = contentSecurityPolicy('<script>theme()</script>').split('; ')
  const workers = ['worker-src', 'child-src', 'script-src']
    .map((name) => directives.find((d) => d.startsWith(`${name} `)))
    .find(Boolean)
  assert.match(workers, /'self'/)
  assert.doesNotMatch(workers, /blob:|data:/)
})

test("the frontend's index.html theme script is covered", () => {
  const index = path.join(__dirname, '..', '..', 'frontend', 'index.html')
  const html = fs.readFileSync(index, 'utf8')
  const hashes = inlineScriptHashes(html)
  assert.equal(hashes.length, 1, 'index.html has one inline script (the theme)')
})
