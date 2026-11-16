import { useCallback, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import type { Session } from '../api/client'
import { PrintSheet, type Printable } from './PrintSheet'
import { irisApp } from '../api/appBridge'
import { shownTitle } from '../lib/format'
import { fetchNotes, fetchTranscript } from './load'
import { browserPrint, runExport } from './printPdf'

async function loaded(sheet: HTMLElement): Promise<void> {
  sheet.getBoundingClientRect()
  const images = [...sheet.querySelectorAll('img')]
  await Promise.all(images.map((img) => (img.complete ? undefined : img.decode().catch(() => undefined))))
  await document.fonts.ready
}

export function usePdfExport() {
  const [printable, setPrintable] = useState<Printable | null>(null)
  const [exporting, setExporting] = useState<string | null>(null)
  const running = useRef(false)

  const exportPdf = useCallback(async (session: Session): Promise<boolean> => {
    if (running.current) return true
    running.current = true
    setExporting(session.id)
    const ok = await runExport<Printable>({
      load: async () => {
        const [notes, transcript] = await Promise.all([fetchNotes(session.id), fetchTranscript(session.id)])
        return notes && { session, notes, transcript: transcript ?? null }
      },
      show: (data) => {
        flushSync(() => setPrintable(data))
        return document.querySelector<HTMLElement>('.print-sheet')
      },
      ready: loaded,
      print: () => {
        const app = irisApp()
        return app ? app.savePdf(shownTitle(session.title)) : browserPrint(window)
      },
      hide: () => setPrintable(null),
    })
    setExporting(null)
    running.current = false
    return ok
  }, [])

  const sheet = printable && <PrintSheet printable={printable} />
  return { exportPdf, exporting, sheet }
}
