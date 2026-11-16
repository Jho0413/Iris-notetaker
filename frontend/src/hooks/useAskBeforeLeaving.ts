import { useEffect } from 'react'
import { useBlocker } from 'react-router-dom'

export function asksBeforeLeaving(unsaved: boolean, fromPath: string, toPath: string): boolean {
  return unsaved && fromPath !== toPath
}

export function useAskBeforeLeaving(unsaved: boolean) {
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    asksBeforeLeaving(unsaved, currentLocation.pathname, nextLocation.pathname),
  )
  useEffect(() => {
    if (!unsaved) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [unsaved])
  return blocker
}
