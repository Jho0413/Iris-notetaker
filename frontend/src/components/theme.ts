// Light, dark or system theme (frontend/DESIGN.md). The choice is stored in localStorage; the
// resolved theme is written to <html data-theme>. index.html runs the same resolution inline
// before first paint, so dark mode never flashes white. The theme is the screen's only: printing
// (Export PDF, the browser's Print) always uses the light colours (index.css), whatever it is.
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

export function useTheme(): [ThemeChoice, (choice: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(stored)

  useEffect(() => {
    apply(choice)
    const media = query()
    const onChange = () => apply('system')
    if (choice === 'system') media.addEventListener('change', onChange)
    return () => {
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
