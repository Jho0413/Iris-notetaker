// The desktop app's bridge (desktop/pages/preload-main.cjs). Present only inside Iris.app: in a
// browser there is none, and the engine reads the key from its own settings instead.

export interface IrisApp {
  /** Opens the app's key window; resolves once the engine runs with the new key, or cancelled. */
  changeKey(): Promise<{ done: boolean }>
  /** Asks, then removes the stored key; resolves once the engine restarted, or cancelled. */
  removeKey(): Promise<{ done: boolean }>
}

declare global {
  interface Window {
    irisApp?: IrisApp
  }
}

/** The bridge, or undefined outside the desktop app. */
export function irisApp(): IrisApp | undefined {
  return typeof window === 'undefined' ? undefined : window.irisApp
}
