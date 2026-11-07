// Recording from the menu bar icon in the desktop app (desktop/src/tray.cjs). The app relays its
// commands over the bridge; the Live page records exactly as from its own buttons.
import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { irisApp } from '../settings/appBridge.ts'
import type { CaptureMode } from './liveCapture.ts'

/** Location state asking the Live page to start recording `mode`. `id` is fresh per request, so
 * asking again while the page is already open starts too. */
export interface LiveLocationState {
  startRequest?: { id: number; mode: CaptureMode }
}

/** For the layout: "Record Audio" or "Record Screen…" opens the Live page, which then starts. */
export function useRecordFromMenuBar() {
  const navigate = useNavigate()
  useEffect(
    () =>
      irisApp()?.onRecordCommand((command) => {
        if (command !== 'start-audio' && command !== 'start-screen') return // the Live page's own
        const mode = command === 'start-audio' ? 'audio' : 'screen'
        const state: LiveLocationState = { startRequest: { id: Date.now(), mode } }
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
export function menuBarReporter(send: (phase: string, startedAt: number, macSoundRefused: boolean) => void) {
  const idle = { phase: 'idle', startedAt: 0, macSoundRefused: false }
  let page = idle
  let leftUploads = 0
  const tell = () => send(menuBarPhase(page.phase, leftUploads), page.startedAt, page.macSoundRefused)
  return {
    /** The Live page's phase now; `macSoundRefused`: macOS refused the Mac's sound to its audio
     * recording. */
    phase(phase: string, startedAt: number, macSoundRefused = false) {
      page = { phase, startedAt, macSoundRefused }
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
      page = idle
      tell()
    },
  }
}

export const menuBar = menuBarReporter((phase, startedAt, macSoundRefused) =>
  irisApp()?.recordingChanged(phase, startedAt, macSoundRefused),
)
