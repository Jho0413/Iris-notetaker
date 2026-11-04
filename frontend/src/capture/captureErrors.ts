// User-facing messages for capture failures. getDisplayMedia and getUserMedia reject with
// DOMExceptions whose `name` says what went wrong; the default messages are not actionable.

export type CaptureDevice = 'screen' | 'microphone'

/** A capture failure with a message that can be shown to the user as is. */
export class CaptureError extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options)
    this.name = 'CaptureError'
  }
}

function errorName(err: unknown): string {
  return err instanceof Error || err instanceof DOMException ? err.name : ''
}

/** Running in the macOS desktop app, which serves the UI from app://iris. */
export function inDesktopApp(): boolean {
  return typeof location !== 'undefined' && location.protocol === 'app:'
}

export function describeCaptureError(
  err: unknown,
  device: CaptureDevice,
  desktopApp: boolean = inDesktopApp(),
): string {
  switch (errorName(err)) {
    case 'NotAllowedError':
    case 'PermissionDeniedError':
      if (desktopApp) {
        return device === 'screen'
          ? 'Screen recording was cancelled or blocked. Choose a screen or window to record. If ' +
              'macOS blocked it, allow Iris in System Settings → Privacy & Security → Screen & ' +
              'System Audio Recording, then quit and reopen Iris.'
          : 'Microphone access was blocked. Allow Iris in System Settings → Privacy & Security → ' +
              'Microphone, then try again.'
      }
      return device === 'screen'
        ? 'Screen sharing was cancelled or blocked. Choose a screen, window or tab to share. ' +
            'On macOS, also allow screen recording for your browser in System Settings.'
        : 'Microphone access was blocked. Allow the microphone for this site in the browser ' +
            'and try again.'
    case 'NotFoundError':
    case 'DevicesNotFoundError':
      return device === 'screen'
        ? 'There is no screen available to share.'
        : 'No microphone was found. Connect one and try again.'
    case 'NotReadableError':
    case 'TrackStartError':
      return `The ${device} could not be started. Another app may be using it.`
    case 'AbortError':
      return `Starting the ${device} was interrupted. Try again.`
    case 'CaptureError':
      return (err as Error).message
    default: // the browser's own message isn't written for users: never shown
      return `The ${device} couldn't be started. Try again.`
  }
}

/** The first audio format MediaRecorder supports that the backend can finalize (WebM). */
export function pickAudioMimeType(isTypeSupported: (type: string) => boolean): string | null {
  for (const type of ['audio/webm;codecs=opus', 'audio/webm']) {
    if (isTypeSupported(type)) return type
  }
  return null
}
