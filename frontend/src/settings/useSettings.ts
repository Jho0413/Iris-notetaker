import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { act } from '../api/request'
import { afterSettingsRead, SETTINGS_REREAD_MS, type SettingsLoad } from './settings'

export function useSettings(retryFirstRead: boolean) {
  const [load, setLoad] = useState<SettingsLoad>({ kind: 'loading' })
  const [tick, setTick] = useState(0)
  const reread = useCallback(() => setTick((n) => n + 1), [])
  const retries = useRef(0)
  const shown = useRef(load)
  useEffect(() => {
    shown.current = load
  }, [load])
  useEffect(() => {
    window.addEventListener('focus', reread)
    return () => window.removeEventListener('focus', reread)
  }, [reread])
  useEffect(() => {
    let live = true
    let timer: number | undefined
    void act(api.GET('/api/settings')).then(({ data }) => {
      if (!live) return
      const after = afterSettingsRead(data, shown.current, retryFirstRead, retries.current)
      retries.current = after.retries
      if (after.retry) timer = window.setTimeout(reread, SETTINGS_REREAD_MS)
      if (after.next) setLoad(after.next)
    })
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [tick, retryFirstRead, reread])
  return { load, setLoad, reread }
}
