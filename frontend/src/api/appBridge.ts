// The desktop app's bridge (desktop/pages/preload-main.cjs). Present only inside Iris.app: in a
// browser there is none, and the engine reads the key from its own settings instead.

export interface IrisApp {
  /** Opens the app's key window; resolves once the engine runs with the new key, or cancelled. */
  changeKey(): Promise<{ done: boolean }>
  /** Asks, then removes the stored key; resolves once the engine restarted, or cancelled. */
  removeKey(): Promise<{ done: boolean }>
  /** Saves the page, as printed, as a PDF named after `title`, where the user chooses; resolves
   * once saved, or cancelled. */
  savePdf(title: string): Promise<{ done: boolean }>
  /** Tells the app the Live page's phase, when recording started (ms since the epoch), whether
   * macOS refused the Mac's sound to the recording (the menu bar icon then shows the window,
   * whose page says how to allow it) and whether the screen is shared now. */
  recordingChanged(phase: Phase, startedAt: number, macSoundRefused: boolean, screen: boolean): void
  /** Calls `listener` with what is chosen in the menu bar: a recording to start, Share Screen…
   * or Stop Sharing, Stop, or, once stopped, what to do with the recording; returns the
   * unsubscribe. */
  onRecordCommand(listener: (command: RecordCommand) => void): () => void
}

/**
 * `stopped`: capture is paused (Stop): make notes, continue, start over or discard; `continuing`:
 * asking for the sources again (Continue), still stopped until they are granted.
 * `stopping`: finishing the upload, to make notes or, leaving the page, to keep the recording.
 * `unfinished`: capture has ended, but making notes (or keeping it) failed; `finishing`: retrying.
 */
export type Phase =
  | 'idle'
  | 'requesting'
  | 'recording'
  | 'stopped'
  | 'continuing'
  | 'stopping'
  | 'unfinished'
  | 'finishing'

/** The menu bar's commands (desktop/src/tray.cjs). The Live page handles all but the starts. */
export type RecordCommand =
  | 'start-audio'
  | 'start-screen'
  | 'share-screen'
  | 'stop-sharing'
  | 'stop'
  | 'continue'
  | 'make-notes'
  | 'start-over'
  | 'discard'

declare global {
  interface Window {
    irisApp?: IrisApp
  }
}

/** The bridge, or undefined outside the desktop app. */
export function irisApp(): IrisApp | undefined {
  return typeof window === 'undefined' ? undefined : window.irisApp
}
