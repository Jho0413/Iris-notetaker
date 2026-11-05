// Saving the notes as a PDF (Export PDF in the desktop app). The page prints itself with its
// print stylesheet (frontend/src/index.css, @media print); main.cjs asks where to save it.

/** A file name for the notes of a session called `title`: path separators and characters macOS
 * or Windows reject become "_", control characters go, and an empty name becomes "Iris notes". */
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

/** printToPDF options: A4 with the page's own margins (@page), backgrounds kept, so boxes keep
 * their colours. */
const PDF_OPTIONS = { pageSize: 'A4', printBackground: true, preferCSSPageSize: true }

module.exports = { pdfFileName, PDF_OPTIONS }
