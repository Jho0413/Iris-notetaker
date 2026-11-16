import { useState } from 'react'
import { irisApp } from '../api/appBridge'
import { KEY_ACTION_FAILED, noKeyWarning } from './settings'
import { Icon } from '../components/Icon'

/**
 * The warning that Iris has no OpenAI API key, so it can't make notes. Everything else still
 * works. In the desktop app it offers Add key… (the app's key window) unless `withAction` is off,
 * as on Settings, whose key row offers it right below.
 */
export function NoKeyBanner(props: { withAction?: boolean; onChanged?: () => void }) {
  const app = irisApp()
  const warning = noKeyWarning(Boolean(app))
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)

  async function addKey() {
    if (!app) return
    setBusy(true)
    setFailed(false)
    try {
      const { done } = await app.changeKey()
      if (done) props.onChanged?.()
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="banner banner-warning banner-inline" role="status">
      <Icon name="alert" />
      <div className="banner-body">
        <p>
          <strong>{warning.title}</strong> {failed ? KEY_ACTION_FAILED : warning.detail}
        </p>
      </div>
      {app && props.withAction !== false && (
        <button type="button" className="btn btn-primary btn-sm" onClick={() => void addKey()} disabled={busy}>
          Add key…
        </button>
      )}
    </div>
  )
}
