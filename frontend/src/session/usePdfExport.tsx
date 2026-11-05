import { useCallback, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import type { Session } from '../api/client'
import { PrintSheet, type Printable } from '../components/PrintSheet'
import { irisApp } from '../settings/appBridge'
import { fetchNotes, fetchTranscript } from './load'

/** The sheet laid out, and its figures and fonts loaded: a PDF made earlier would leave them out.
 *  The sheet is invisible but laid out, so the fonts it uses (KaTeX's) start loading here. */
async function loaded(sheet: HTMLElement): Promise<void> {
  sheet.getBoundingClientRect()
  const images = [...sheet.querySelectorAll('img')]
  await Promise.all(images.map((img) => (img.complete ? undefined : img.decode().catch(() => undefined))))
  await document.fonts.ready
}

/**
 * Export PDF, the same from the library and the session page: the session's notes are drawn in a
 * print-only sheet (PrintSheet) and printed from there, so nothing on screen changes, wherever
 * the user is. The desktop app saves the file where the user chooses; a browser opens its print
 * dialog, where "Save as PDF" is one choice. One export at a time. Render `sheet` in the page.
 */
export function usePdfExport() {
  const [printable, setPrintable] = useState<Printable | null>(null)
  const [exporting, setExporting] = useState<string | null>(null)
  const running = useRef(false)

  /** Resolves false when the PDF couldn't be made (saved or cancelled is true). */
  const exportPdf = useCallback(async (session: Session): Promise<boolean> => {
    if (running.current) return true
    running.current = true
    setExporting(session.id)
    try {
      const [notes, transcript] = await Promise.all([fetchNotes(session.id), fetchTranscript(session.id)])
      if (!notes) throw new Error(`the notes of session ${session.id} couldn't be loaded`)
      flushSync(() => setPrintable({ session, notes, transcript: transcript ?? null }))
      const sheet = document.querySelector<HTMLElement>('.print-sheet')
      if (sheet) await loaded(sheet)
      const app = irisApp()
      if (app) await app.savePdf(session.title)
      else window.print()
      return true
    } catch (err) {
      console.error('Export PDF failed:', err)
      return false
    } finally {
      setPrintable(null)
      setExporting(null)
      running.current = false
    }
  }, [])

  const sheet = printable && <PrintSheet printable={printable} />
  return { exportPdf, exporting, sheet }
}
