const crypto = require('node:crypto')

function inlineScriptHashes(html) {
  const hashes = []
  for (const match of html.matchAll(/<script(\s[^>]*)?>([\s\S]*?)<\/script>/gi)) {
    const attrs = match[1] ?? ''
    if (/\ssrc\s*=/i.test(attrs)) continue
    const digest = crypto.createHash('sha256').update(match[2], 'utf8').digest('base64')
    hashes.push(`'sha256-${digest}'`)
  }
  return hashes
}

function contentSecurityPolicy(indexHtml) {
  return [
    "default-src 'self'",
    `script-src 'self' ${inlineScriptHashes(indexHtml).join(' ')}`.trim(),
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "media-src 'self' blob:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join('; ')
}

module.exports = { contentSecurityPolicy, inlineScriptHashes }
