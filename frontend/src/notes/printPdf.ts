export interface ExportSteps<T> {
  load(): Promise<T | undefined>
  show(data: T): HTMLElement | null
  ready(sheet: HTMLElement): Promise<void>
  print(): Promise<unknown>
  hide(): void
}

export async function runExport<T>(steps: ExportSteps<T>): Promise<boolean> {
  try {
    const data = await steps.load()
    if (data === undefined) throw new Error("the notes couldn't be loaded")
    const sheet = steps.show(data)
    if (!sheet) throw new Error("the print sheet wasn't drawn (the page closed meanwhile)")
    await steps.ready(sheet)
    if (!sheet.isConnected) throw new Error('the print sheet was removed (the page closed meanwhile)')
    await steps.print()
    return true
  } catch (err) {
    console.error('Export PDF failed:', err)
    return false
  } finally {
    steps.hide()
  }
}

export function browserPrint(win: Pick<Window, 'print' | 'addEventListener' | 'removeEventListener'>,
  fallbackMs = 60_000): Promise<void> {
  return new Promise((resolve) => {
    const done = () => {
      win.removeEventListener('afterprint', done)
      clearTimeout(timer)
      resolve()
    }
    const timer = setTimeout(done, fallbackMs)
    win.addEventListener('afterprint', done)
    win.print()
  })
}
