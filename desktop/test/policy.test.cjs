const assert = require('node:assert/strict')
const path = require('node:path')
const { pathToFileURL } = require('node:url')
const { test } = require('node:test')
const policy = require('../src/policy.cjs')

test('isIrisUrl matches app://iris only', () => {
  for (const u of ['app://iris/', 'app://iris/sessions/x', 'app://iris']) assert.ok(policy.isIrisUrl(u), u)
  for (const u of ['app://iris.evil/', 'app://other/', 'http://iris/', 'https://localhost:5173/', 'file:///x', 'app://a:b@iris/', '', null, undefined]) {
    assert.ok(!policy.isIrisUrl(u), String(u))
  }
})

test('isDesktopPage: html files inside the pages folder', () => {
  const pages = path.resolve('/Applications/Iris.app/Contents/Resources/app/pages')
  assert.ok(policy.isDesktopPage(pathToFileURL(path.join(pages, 'key.html')).href + '?mode=first', pages))
  assert.ok(!policy.isDesktopPage(pathToFileURL(path.join(pages, '..', 'main.cjs')).href, pages))
  assert.ok(!policy.isDesktopPage(pathToFileURL('/etc/key.html').href, pages))
  assert.ok(!policy.isDesktopPage('app://iris/key.html', pages))
  assert.ok(!policy.isDesktopPage('file:///Applications/Iris.app/Contents/Resources/app/pages/../../x.html', pages))
})

test('externalUrl lets http(s) out, nothing else', () => {
  assert.equal(policy.externalUrl('https://platform.openai.com/api-keys'), 'https://platform.openai.com/api-keys')
  assert.equal(policy.externalUrl('http://example.com'), 'http://example.com/')
  for (const u of ['file:///etc/passwd', 'javascript:alert(1)', 'app://iris/', 'smb://host/share', 'x']) {
    assert.equal(policy.externalUrl(u), null, u)
  }
})

test('permissions: microphone and screen capture, for the Iris UI only', () => {
  assert.ok(policy.permissionAllowed('media', 'app://iris/live', { mediaTypes: ['audio'] }))
  assert.ok(policy.permissionAllowed('media', 'app://iris/live', { mediaTypes: ['audio', 'video'] }))
  assert.ok(policy.permissionAllowed('display-capture', 'app://iris/live'))
  assert.ok(!policy.permissionAllowed('media', 'https://evil.example/', { mediaTypes: ['audio'] }))
  assert.ok(!policy.permissionAllowed('media', 'file:///x/key.html', { mediaTypes: ['audio'] }))
  assert.ok(!policy.permissionAllowed('display-capture', 'app://other/'))
  for (const p of ['notifications', 'geolocation', 'clipboard-read', 'openExternal', 'hid', 'fullscreen']) {
    assert.ok(!policy.permissionAllowed(p, 'app://iris/'), p)
  }
  assert.ok(!policy.permissionAllowed('media', 'app://iris/', { mediaTypes: ['audio', 'something-else'] }))
})

test('permission checks follow the same rule', () => {
  assert.ok(policy.permissionCheckAllowed('media', 'app://iris'))
  assert.ok(!policy.permissionCheckAllowed('media', 'https://evil.example'))
  assert.ok(!policy.permissionCheckAllowed('notifications', 'app://iris'))
})

test('figures open in an Iris window; other app:// URLs do not', () => {
  assert.ok(policy.isSessionFileUrl('app://iris/api/sessions/ab12cd/files/visuals/v1.jpg'))
  assert.ok(policy.isSessionFileUrl('app://iris/api/sessions/ab12cd/files/frames/000000001.jpg'))
  for (const u of [
    'app://iris/',
    'app://iris/sessions/ab12cd',
    'app://iris/api/sessions/ab12cd/export',
    'app://iris/api/sessions/ab12cd/files/visuals/../../x',
    'app://iris/api/sessions/ab12cd/files/visuals/.hidden',
    'app://iris/api/sessions/ab12cd/files/visuals/v1.jpg?x=1',
    'app://other/api/sessions/ab12cd/files/visuals/v1.jpg',
    'https://evil.example/api/sessions/ab12cd/files/visuals/v1.jpg',
  ]) {
    assert.ok(!policy.isSessionFileUrl(u), u)
  }
  // And such a URL is never handed to the system browser.
  assert.equal(policy.externalUrl('app://iris/api/sessions/ab12cd/files/visuals/v1.jpg'), null)
})

test('fromIrisWindow: only the Iris page’s top frame in that window', () => {
  const contents = {}
  const win = { webContents: contents, isDestroyed: () => false }
  const event = (frame, sender = contents) => ({ sender, senderFrame: frame })
  const top = (url) => ({ url, parent: null })
  assert.ok(policy.fromIrisWindow(event(top('app://iris/settings')), win))
  assert.ok(policy.fromIrisWindow(event(top('app://iris/')), win))
  // Another window's contents, an iframe, another origin, the app's own pages, no frame, no window.
  assert.ok(!policy.fromIrisWindow(event(top('app://iris/settings'), {}), win))
  assert.ok(!policy.fromIrisWindow(event({ url: 'app://iris/settings', parent: top('app://iris/') }), win))
  for (const url of ['https://evil.example/', 'file:///Applications/Iris.app/pages/starting.html', 'app://iris.evil/', '']) {
    assert.ok(!policy.fromIrisWindow(event(top(url)), win), url)
  }
  assert.ok(!policy.fromIrisWindow({ sender: contents, senderFrame: null }, win))
  assert.ok(!policy.fromIrisWindow(event(top('app://iris/')), null))
  assert.ok(!policy.fromIrisWindow(event(top('app://iris/')), { webContents: contents, isDestroyed: () => true }))
})
