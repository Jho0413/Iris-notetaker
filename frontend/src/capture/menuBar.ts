// Recording from the menu bar icon in the desktop app (desktop/src/tray.cjs). The app relays its
// commands over the bridge; the Live page records exactly as from its own buttons.
import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { irisApp } from '../settings/appBridge.ts'

/** Location state asking the Live page to start. A fresh value per request, so asking again
 * while the page is already open starts too. */
export interface LiveLocationState {
  startRequest?: number
}

/** For the layout: "Record Screen…" opens the Live page, which then starts. */
export function useRecordFromMenuBar() {
  const navigate = useNavigate()
  useEffect(
    () =>
      irisApp()?.onRecordCommand((command) => {
        if (command !== 'start') return // Stop is the Live page's own
        const state: LiveLocationState = { startRequest: Date.now() }
        navigate('/live', { state })
      }),
    [navigate],
  )
}

/**
 * The phase the app is told: the Live page's own, except "stopping" while the page is idle but
 * a Live page left earlier is still uploading (`leftUploads`). Chunks not yet uploaded are only in
 * this window's memory, so quitting must still ask.
 */
export function menuBarPhase(pagePhase: string, leftUploads: number): string {
  return pagePhase === 'idle' && leftUploads > 0 ? 'stopping' : pagePhase
}

/**
 * What the app is told about recording, from every Live page this window shows, in one place:
 * a page that was left reports late (when its upload settles), and must not overwrite what a newer
 * Live page reported.
 */
export function menuBarReporter(send: (phase: string, startedAt: number) => void) {
  let page = { phase: 'idle', startedAt: 0 }
  let leftUploads = 0
  const tell = () => send(menuBarPhase(page.phase, leftUploads), page.startedAt)
  return {
    /** The Live page's phase now. */
    phase(phase: string, startedAt: number) {
      page = { phase, startedAt }
      tell()
    },
    /** The Live page went away; `stillStopping`: its stop, still uploading what it captured. */
    left(stillStopping?: Promise<unknown>) {
      if (stillStopping) {
        leftUploads++
        void stillStopping
          .catch(() => {})
          .finally(() => {
            leftUploads--
            tell()
          })
      }
      page = { phase: 'idle', startedAt: 0 }
      tell()
    },
  }
}

export const menuBar = menuBarReporter((phase, startedAt) => irisApp()?.recordingChanged(phase, startedAt))
