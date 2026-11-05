// Light, dark or system theme (frontend/DESIGN.md). The choice is stored in localStorage; the
// resolved theme is written to <html data-theme>. index.html runs the same resolution inline
// before first paint, so dark mode never flashes white.
import { useEffect, useState } from 'react'

export type ThemeChoice = 'system' | 'light' | 'dark'
export const THEME_KEY = 'iris-theme'

const query = () => window.matchMedia('(prefers-color-scheme: dark)')

function stored(): ThemeChoice {
  try {
    const value = localStorage.getItem(THEME_KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

function apply(choice: ThemeChoice) {
  const dark = choice === 'dark' || (choice === 'system' && query().matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

/** Run `print` with the light theme: printed notes are on white paper, and the notes palette's
 *  light colours are the ones made for it. The theme comes back afterwards. */
export async function inLight<T>(print: () => Promise<T>): Promise<T> {
  const root = document.documentElement
  const theme = root.dataset.theme
  root.dataset.theme = 'light'
  try {
    return await print()
  } finally {
    if (theme) root.dataset.theme = theme
  }
}

export function useTheme(): [ThemeChoice, (choice: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(stored)

  useEffect(() => {
    apply(choice)
    // The browser's own Print (and Export → PDF in a browser) prints in light too.
    const light = () => (document.documentElement.dataset.theme = 'light')
    const back = () => apply(choice)
    window.addEventListener('beforeprint', light)
    window.addEventListener('afterprint', back)
    const media = query()
    const onChange = () => apply('system')
    if (choice === 'system') media.addEventListener('change', onChange)
    return () => {
      window.removeEventListener('beforeprint', light)
      window.removeEventListener('afterprint', back)
      media.removeEventListener('change', onChange)
    }
  }, [choice])

  function update(next: ThemeChoice) {
    try {
      if (next === 'system') localStorage.removeItem(THEME_KEY)
      else localStorage.setItem(THEME_KEY, next)
    } catch {
      // Storage can be unavailable (private mode); the choice still applies for this visit.
    }
    setChoice(next)
  }

  return [choice, update]
}
