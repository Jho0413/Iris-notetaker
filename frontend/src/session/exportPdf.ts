import { inLight } from '../components/theme'
import { irisApp } from '../settings/appBridge'

/** Export → PDF: the notes as printed (index.css, @media print). The desktop app saves the file
 *  where the user chooses; a browser opens its print dialog, where "Save as PDF" is one choice.
 *  Resolves false when the user cancelled the save. */
export async function exportPdf(title: string): Promise<boolean> {
  const app = irisApp()
  if (!app) {
    window.print()
    return true
  }
  return (await inLight(() => app.savePdf(title))).done
}
