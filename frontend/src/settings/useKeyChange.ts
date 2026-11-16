import { useState } from 'react'
import { irisApp } from '../api/appBridge'
import { KEY_ACTION_FAILED, keyChanged } from './settings'

export function useKeyChange(onResult: (result: { ok: boolean; text: string }) => void) {
  const [busy, setBusy] = useState(false)
  async function change(action: 'saved' | 'removed') {
    const app = irisApp()
    if (!app) return
    setBusy(true)
    try {
      const { done } = action === 'saved' ? await app.changeKey() : await app.removeKey()
      if (done) onResult({ ok: true, text: keyChanged(action) })
    } catch {
      onResult({ ok: false, text: KEY_ACTION_FAILED })
    } finally {
      setBusy(false)
    }
  }
  return { busy, change }
}
