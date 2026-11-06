// Recording from the menu bar icon in the desktop app (desktop/src/tray.cjs). The app relays its
// commands over the bridge; the Live page records exactly as from its own buttons.
import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { irisApp } from '../settings/appBridge'

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
