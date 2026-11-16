import { useEffect } from 'react'

export function useTitle(title: string | undefined) {
  useEffect(() => {
    document.title = title ? `${title} · Iris` : 'Iris'
  }, [title])
}
