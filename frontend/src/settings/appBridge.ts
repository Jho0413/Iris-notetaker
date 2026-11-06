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
  /** Tells the app the Live page's phase, and when recording started (ms since the epoch), for
   * the menu bar icon. */
  recordingChanged(phase: string, startedAt: number): void
  /** Calls `listener` when "Record Screen…" or Stop is chosen in the menu bar; returns the
   * unsubscribe. */
  onRecordCommand(listener: (command: RecordCommand) => void): () => void
}

export type RecordCommand = 'start' | 'stop'

declare global {
  interface Window {
    irisApp?: IrisApp
  }
}

/** The bridge, or undefined outside the desktop app. */
export function irisApp(): IrisApp | undefined {
  return typeof window === 'undefined' ? undefined : window.irisApp
}
