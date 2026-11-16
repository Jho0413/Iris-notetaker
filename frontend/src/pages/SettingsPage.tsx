import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useBlocker } from 'react-router-dom'
import { api, type AppSettings } from '../api/client'
import { act } from '../api/request'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Icon } from '../components/Icon'
import { NoKeyBanner } from '../settings/NoKeyBanner'
import { UNREACHABLE } from '../lib/messages'
import { useTitle } from '../hooks/useTitle'
import { irisApp } from '../api/appBridge'
import {
  asksBeforeLeaving,
  BROWSER_KEY_NOTE,
  KEY_ACTION_FAILED,
  keyChanged,
  languageName,
  languageOptions,
  pendingLanguage,
  saveFailure,
  savedLanguage,
} from '../settings/settings'

/** A re-read that fails while settings are shown is tried again this often, this many times:
 * the engine takes a few seconds to come back after a key change. */
const REREAD_DELAY_MS = 2000
const REREAD_TRIES = 5

type Load = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; settings: AppSettings }

async function fetchSettings(): Promise<Load> {
  const { data } = await act(api.GET('/api/settings'))
  return data ? { kind: 'ready', settings: data } : { kind: 'error' }
}

/** Grouped rows of settings. Changes collect in a save bar and apply once saved. */
export function SettingsPage() {
  useTitle('Settings')
  const [load, setLoad] = useState<Load>({ kind: 'loading' })
  const [tick, setTick] = useState(0)
  // The language chosen but not saved yet; null when nothing has changed.
  const [draft, setDraft] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  // After the app changed the key: what to confirm, or that it couldn't.
  const [keyResult, setKeyResult] = useState<{ ok: boolean; text: string } | null>(null)

  const retries = useRef(0)
  const shown = useRef(load)
  useEffect(() => {
    shown.current = load
  }, [load])
  useEffect(() => {
    let live = true
    let timer: number | undefined
    void fetchSettings().then((next) => {
      if (!live) return
      // A failed re-read (e.g. while the engine restarts after a key change) keeps what is shown,
      // and any unsaved change, and tries again shortly.
      if (next.kind === 'error' && shown.current.kind === 'ready') {
        if (retries.current < REREAD_TRIES) {
          retries.current += 1
          timer = window.setTimeout(() => setTick((n) => n + 1), REREAD_DELAY_MS)
        }
        return
      }
      if (next.kind === 'ready') retries.current = 0
      setLoad(next)
    })
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [tick])
  // Read again when the window regains focus: the key may have changed from the app's menu.
  useEffect(() => {
    const again = () => setTick((n) => n + 1)
    window.addEventListener('focus', again)
    return () => window.removeEventListener('focus', again)
  }, [])

  const settings = load.kind === 'ready' ? load.settings : null
  const language = settings?.transcribe_language
  const pending = pendingLanguage(draft, language?.value)
  const dirty = pending !== null

  // Leaving with an unsaved change asks first, inside Iris and when closing or reloading the tab.
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    asksBeforeLeaving(pending, currentLocation.pathname, nextLocation.pathname),
  )
  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const choose = useCallback((value: string) => {
    setDraft(value)
    setSaved(false)
    setSaveError(null)
  }, [])

  function discard() {
    setDraft(null)
    setSaveError(null)
  }

  async function save() {
    if (pending === null) return
    setSaving(true)
    setSaveError(null)
    const { data, status } = await act(api.PUT('/api/settings', { body: { transcribe_language: pending } }))
    if (data) {
      setLoad({ kind: 'ready', settings: data })
      setDraft(null)
      setSaved(true)
    }
    setSaving(false)
    if (status !== 200) setSaveError(saveFailure(status))
  }

  return (
    <div className="page-settings">
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p className="lede">Saved on this computer. Changes apply to sessions you start from now on.</p>
        </div>
      </div>

      {load.kind === 'loading' && <SettingsSkeleton />}

      {load.kind === 'error' && (
        <div className="error-panel" role="alert">
          <h2>{UNREACHABLE}</h2>
          <p>Your settings are safe; they'll show here once Iris answers.</p>
          <button type="button" className="btn" onClick={() => setTick((n) => n + 1)}>
            <Icon name="retry" /> Try again
          </button>
        </div>
      )}

      {settings && !settings.api_key_set && <NoKeyBanner withAction={false} />}

      {settings && language && (
        <>
          <section className="settings-group" aria-labelledby="settings-transcription">
            <h2 id="settings-transcription">Transcription</h2>
            <div className="settings-rows">
              <LanguageRow settings={settings} value={draft ?? language.value} onChange={choose} changed={dirty} />
            </div>
          </section>

          <section className="settings-group" aria-labelledby="settings-openai">
            <h2 id="settings-openai">OpenAI</h2>
            <div className="settings-rows">
              <ApiKeyRow
                keySet={settings.api_key_set}
                onResult={(result) => {
                  setKeyResult(result)
                  if (result.ok) setTick((n) => n + 1) // the engine restarted: read the settings again
                }}
              />
            </div>
            <p className={keyResult && !keyResult.ok ? 'error-line' : 'saved-line'} role="status">
              {keyResult && (
                <>
                  <Icon name={keyResult.ok ? 'check' : 'alert'} /> {keyResult.text}
                </>
              )}
            </p>
          </section>

          <section className="settings-group" aria-labelledby="settings-about">
            <h2 id="settings-about">About</h2>
            <div className="settings-rows">
              <div className="settings-row settings-row-inline">
                <div className="settings-row-text">
                  <span className="settings-row-title">Version</span>
                </div>
                <div className="settings-row-control num">{settings.version}</div>
              </div>
              <div className="settings-row">
                <div className="settings-row-text">
                  <span className="settings-row-title">Data folder</span>
                  <p className="settings-row-desc">Your sessions, notes and figures.</p>
                </div>
                <div className="settings-row-control">
                  <code className="settings-path" title={settings.data_dir}>
                    {settings.data_dir}
                  </code>
                </div>
              </div>
            </div>
          </section>

          {saveError && (
            <div className="banner banner-error settings-banner" role="alert">
              <Icon name="alert" />
              <div className="banner-body">
                <p>{saveError}</p>
              </div>
            </div>
          )}

          {pending !== null && (
            <div className="save-bar" role="region" aria-label="Unsaved changes">
              <p>
                Spoken language changed to <strong>{languageName(pending)}</strong>.
              </p>
              <button type="button" className="btn" onClick={discard} disabled={saving}>
                Discard
              </button>
              <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={saving}>
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
          )}

          <p className="saved-line" role="status">
            {saved && !dirty && (
              <>
                <Icon name="check" /> {savedLanguage(language.value)}
              </>
            )}
          </p>
        </>
      )}

      <ConfirmDialog
        open={blocker.state === 'blocked'}
        title="Leave without saving?"
        body="Your change to the spoken language hasn't been saved."
        confirm="Leave"
        cancel="Keep editing"
        onConfirm={() => blocker.proceed?.()}
        onCancel={() => blocker.reset?.()}
      />
    </div>
  )
}

/** The OpenAI key: managed by the desktop app (through its bridge), or read by the engine at start-up in
 * the browser. The page only learns whether a key is set, never the key. */
function ApiKeyRow(props: { keySet: boolean; onResult: (result: { ok: boolean; text: string }) => void }) {
  const app = irisApp()
  const [busy, setBusy] = useState(false)

  async function run(action: 'saved' | 'removed') {
    if (!app) return
    setBusy(true)
    try {
      const { done } = action === 'saved' ? await app.changeKey() : await app.removeKey()
      if (done) props.onResult({ ok: true, text: keyChanged(action) })
    } catch {
      props.onResult({ ok: false, text: KEY_ACTION_FAILED })
    } finally {
      setBusy(false)
    }
  }

  let control
  let description
  if (!app) {
    control = props.keySet ? (
      <span className="lock-chip">
        <Icon name="lock" /> Set outside Iris
      </span>
    ) : (
      <span className="chip chip-warning">
        <Icon name="alert" /> Not set
      </span>
    )
    description = BROWSER_KEY_NOTE
  } else if (props.keySet) {
    control = (
      <>
        <span className="chip chip-finished">
          <Icon name="check" /> Key set
        </span>
        <button type="button" className="btn btn-sm" onClick={() => void run('saved')} disabled={busy}>
          Replace…
        </button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => void run('removed')} disabled={busy}>
          Remove
        </button>
      </>
    )
    description = (
      <>
        Encrypted with your Keychain; this page never sees it. A new key is checked with OpenAI before it's saved.
      </>
    )
  } else {
    control = (
      <>
        <span className="chip chip-warning">
          <Icon name="alert" /> No key
        </span>
        <button type="button" className="btn btn-sm btn-primary" onClick={() => void run('saved')} disabled={busy}>
          Add key…
        </button>
      </>
    )
    description = (
      <>
        Iris sends the audio and what was on screen to OpenAI to write the notes, billed to your OpenAI account.
        Create a key at platform.openai.com/api-keys.
      </>
    )
  }

  return (
    <div className="settings-row">
      <div className="settings-row-text">
        <span className="settings-row-title">API key</span>
        <p className="settings-row-desc">{description}</p>
      </div>
      <div className="settings-row-control">{control}</div>
    </div>
  )
}

function LanguageRow(props: {
  settings: AppSettings
  value: string
  changed: boolean
  onChange: (value: string) => void
}) {
  const { transcribe_language: language, languages } = props.settings
  const options = useMemo(() => languageOptions(languages, language.default), [languages, language.default])
  const pinned = language.source === 'pinned'
  return (
    <div className="settings-row">
      <div className="settings-row-text">
        <span className="settings-row-title">
          <label htmlFor="settings-language">Spoken language</label>
          {pinned && (
            <span className="lock-chip">
              <Icon name="lock" /> Set outside Iris
            </span>
          )}
        </span>
        <p className="settings-row-desc" id="settings-language-desc">
          {pinned ? (
            <>
              <code>{language.env_var}</code> in the environment or <code>.env</code> wins over this page. Remove
              it there to choose here.
            </>
          ) : (
            <>
              Iris tells the transcription model which language is spoken, so it doesn't mishear it. Choose{' '}
              <strong>Detect automatically</strong> if your sessions mix languages.
            </>
          )}
        </p>
      </div>
      <div className="settings-row-control">
        <span className="select-wrap">
          <select
            id="settings-language"
            className={`select${props.changed ? ' changed' : ''}`}
            value={props.value}
            disabled={pinned}
            aria-describedby="settings-language-desc"
            onChange={(e) => props.onChange(e.target.value)}
          >
            {options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </span>
      </div>
    </div>
  )
}

function SettingsSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading settings">
      {[0, 1].map((group) => (
        <div className="settings-group" key={group}>
          <div className="skeleton sk-line" style={{ width: 96 }} />
          <div className="settings-rows">
            {[0, 1].map((row) => (
              <div className="settings-row" key={row}>
                <div className="settings-row-text">
                  <div className="skeleton sk-line" style={{ width: '30%' }} />
                  <div className="skeleton sk-line" style={{ width: '70%' }} />
                </div>
                <div className="settings-row-control">
                  <div className="skeleton" style={{ width: 200, height: 32 }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
