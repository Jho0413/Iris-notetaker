export interface IrisApp {
  changeKey(): Promise<{ done: boolean }>
  removeKey(): Promise<{ done: boolean }>
  savePdf(title: string): Promise<{ done: boolean }>
  recordingChanged(phase: Phase, startedAt: number, macSoundRefused: boolean, screen: boolean): void
  onRecordCommand(listener: (command: RecordCommand) => void): () => void
}

export type Phase =
  | 'idle'
  | 'requesting'
  | 'recording'
  | 'stopped'
  | 'continuing'
  | 'stopping'
  | 'unfinished'
  | 'finishing'

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

export function irisApp(): IrisApp | undefined {
  return typeof window === 'undefined' ? undefined : window.irisApp
}
