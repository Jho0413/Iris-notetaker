import { useEffect } from 'react'

/** Set the document title for the current page ("Algebra lecture · Iris"). */
export function useTitle(title: string | undefined) {
  useEffect(() => {
    document.title = title ? `${title} · Iris` : 'Iris'
  }, [title])
}
