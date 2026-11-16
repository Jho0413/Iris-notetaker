import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { irisApp, type Phase } from '../api/appBridge.ts'
import type { CaptureMode } from './liveCapture.ts'

export interface LiveLocationState {
  startRequest?: { id: number; mode: CaptureMode }
}

export function useRecordFromMenuBar() {
  const navigate = useNavigate()
  useEffect(
    () =>
      irisApp()?.onRecordCommand((command) => {
        if (command !== 'start-audio' && command !== 'start-screen') return
        const mode = command === 'start-audio' ? 'audio' : 'screen'
        const state: LiveLocationState = { startRequest: { id: Date.now(), mode } }
        navigate('/live', { state })
      }),
    [navigate],
  )
}

export function menuBarPhase(pagePhase: Phase, leftUploads: number): Phase {
  return pagePhase === 'idle' && leftUploads > 0 ? 'stopping' : pagePhase
}

export function menuBarReporter(
  send: (phase: Phase, startedAt: number, macSoundRefused: boolean, screen: boolean) => void,
) {
  const idle = { phase: 'idle' as Phase, startedAt: 0, macSoundRefused: false, screen: false }
  let page = idle
  let leftUploads = 0
  const tell = () => send(menuBarPhase(page.phase, leftUploads), page.startedAt, page.macSoundRefused, page.screen)
  return {
    phase(phase: Phase, startedAt: number, macSoundRefused = false, screen = false) {
      page = { phase, startedAt, macSoundRefused, screen }
      tell()
    },
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

export const menuBar = menuBarReporter((phase, startedAt, macSoundRefused, screen) =>
  irisApp()?.recordingChanged(phase, startedAt, macSoundRefused, screen),
)
