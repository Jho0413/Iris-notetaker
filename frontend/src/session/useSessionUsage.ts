import { useEffect, useState } from 'react'
import { api, type SessionUsage } from '../api/client'
import { act } from '../api/request'

/**
 * The session's model usage (`GET /usage`), fetched again whenever `refreshKey` changes (a step
 * or the session finished: see `usageRefreshKey`). A failed fetch keeps the last value, so the
 * cost never blanks out while the backend is briefly unreachable.
 */
export function useSessionUsage(sessionId: string, refreshKey: string): SessionUsage | null {
  const [usage, setUsage] = useState<SessionUsage | null>(null)
  useEffect(() => {
    let live = true
    void act(api.GET('/api/sessions/{session_id}/usage', { params: { path: { session_id: sessionId } } })).then(({ data }) => {
      if (live && data) setUsage(data)
    })
    return () => {
      live = false
    }
  }, [sessionId, refreshKey])
  return usage
}
