// The steps of Export PDF, in order, with what each one needs passed in, so the order and every
// failure path are testable under plain Node (tests/printPdf.test.ts). usePdfExport.tsx wires
// them to the engine, React and the desktop app.

export interface ExportSteps<T> {
  /** What the sheet shows; undefined when it can't be loaded. */
  load(): Promise<T | undefined>
  /** Draws the print sheet and returns it, or null when it couldn't be drawn (the page is gone). */
  show(data: T): HTMLElement | null
  /** Resolves once the sheet's figures and fonts are loaded. */
  ready(sheet: HTMLElement): Promise<void>
  /** Prints the page, which then shows the sheet alone; resolves once the page has been printed. */
  print(): Promise<unknown>
  /** Removes the sheet. */
  hide(): void
}

/**
 * Loads, draws, prints, then removes the sheet, whatever happened. Resolves false when the PDF
 * couldn't be made; saved or cancelled by the user is true. Nothing is printed without the sheet:
 * the page underneath would be saved under the session's name.
 */
export async function runExport<T>(steps: ExportSteps<T>): Promise<boolean> {
  try {
    const data = await steps.load()
    if (data === undefined) throw new Error("the notes couldn't be loaded")
    const sheet = steps.show(data)
    if (!sheet) throw new Error("the print sheet wasn't drawn (the page closed meanwhile)")
    await steps.ready(sheet)
    await steps.print()
    return true
  } catch (err) {
    console.error('Export PDF failed:', err)
    return false
  } finally {
    steps.hide()
  }
}

/** The browser's Print, resolved once it is done with the page: `afterprint`, or a minute at most
 *  where it never comes. Chrome's print() blocks until then; some browsers return at once and
 *  capture the page later, so the sheet must stay until afterprint. */
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
