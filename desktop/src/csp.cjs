// The Content-Security-Policy sent with index.html. Scripts load from the app only; the inline
// theme script in index.html (it runs before first paint) is allowed by its hash, so editing it
// keeps working without touching this file. Styles allow inline: React and KaTeX set style
// attributes.

const crypto = require('node:crypto')

/** sha256 sources for each inline <script> (one without a src) in an HTML document. */
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
