function pdfFileName(title) {
  const stem = String(title ?? '')
    .replace(/[\u0000-\u001f\u007f]/g, '')
    .replace(/[/\\:*?"<>|]/g, '_')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^\.+/, '')
    .slice(0, 120)
    .trim()
  return `${stem || 'Iris notes'}.pdf`
}

const PDF_OPTIONS = { pageSize: 'A4', printBackground: true, preferCSSPageSize: true }

module.exports = { pdfFileName, PDF_OPTIONS }
