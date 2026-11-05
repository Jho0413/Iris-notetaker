import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'

/** A failed read is tried again this often, this many times: the engine takes a few seconds to
 * come back after a key change. */
const RETRY_MS = 2000
const RETRIES = 5

/** Whether the engine has an OpenAI API key: true, false, or null while unknown (still loading,
 * or Iris never answered; then no warning is shown). `refresh` asks again, e.g. after a key change.
 * It also asks again when the window regains focus: a key changed from the app's menu closes the
 * key window, which hands focus back here. A failed read keeps the last answer and retries. */
export function useApiKeySet(): { set: boolean | null; refresh: () => void } {
  const [set, setSet] = useState<boolean | null>(null)
  const [tick, setTick] = useState(0)
  const retries = useRef(0)
  useEffect(() => {
    const again = () => setTick((n) => n + 1)
    window.addEventListener('focus', again)
    return () => window.removeEventListener('focus', again)
  }, [])
  useEffect(() => {
    let live = true
    let timer: number | undefined
    const failed = () => {
      if (!live || retries.current >= RETRIES) return
      retries.current += 1
      timer = window.setTimeout(() => setTick((n) => n + 1), RETRY_MS)
    }
    api
      .GET('/api/settings')
      .then(({ data }) => {
        if (!data) return failed()
        retries.current = 0
        if (live) setSet(data.api_key_set)
      })
      .catch(failed)
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [tick])
  return { set, refresh: useCallback(() => setTick((n) => n + 1), []) }
}
