import { useCallback, useMemo, useState } from 'react'
import { api, type AppSettings } from '../api/client'
import { act } from '../api/request'
import { Banner } from '../components/Banner'
import { LeaveWithoutSavingDialog } from '../components/ConfirmDialog'
import { ErrorPanel } from '../components/ErrorPanel'
import { Icon } from '../components/Icon'
import { NoKeyBanner } from '../settings/NoKeyBanner'
import { useAskBeforeLeaving } from '../hooks/useAskBeforeLeaving'
import { useTitle } from '../hooks/useTitle'
import { irisApp } from '../api/appBridge'
import {
  BROWSER_KEY_NOTE,
  ADD_KEY,
  languageName,
  languageOptions,
  pendingLanguage,
  saveFailure,
  savedLanguage,
} from '../settings/settings'
import { useKeyChange } from '../settings/useKeyChange'
import { useSettings } from '../settings/useSettings'

export function SettingsPage() {
  useTitle('Settings')
  const { load, setLoad, reread } = useSettings(false)
  const [draft, setDraft] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [keyResult, setKeyResult] = useState<{ ok: boolean; text: string } | null>(null)

  const settings = load.kind === 'ready' ? load.settings : null
  const language = settings?.transcribe_language
  const pending = pendingLanguage(draft, language?.value)
  const dirty = pending !== null

  const blocker = useAskBeforeLeaving(dirty)

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
        <ErrorPanel detail="Your settings are safe; they'll show here once Iris answers." onRetry={reread} />
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
                  if (result.ok) reread()
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

          {saveError && <Banner tone="error" className="settings-banner">{saveError}</Banner>}

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

      <LeaveWithoutSavingDialog blocker={blocker} body="Your change to the spoken language hasn't been saved." />
    </div>
  )
}

function ApiKeyRow(props: { keySet: boolean; onResult: (result: { ok: boolean; text: string }) => void }) {
  const app = irisApp()
  const { busy, change: run } = useKeyChange(props.onResult)

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
          {ADD_KEY}
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
          <div className="skeleton sk-line" style={{ width: '6rem' }} />
          <div className="settings-rows">
            {[0, 1].map((row) => (
              <div className="settings-row" key={row}>
                <div className="settings-row-text">
                  <div className="skeleton sk-line" style={{ width: '30%' }} />
                  <div className="skeleton sk-line" style={{ width: '70%' }} />
                </div>
                <div className="settings-row-control">
                  <div className="skeleton" style={{ width: '12.5rem', height: '2rem' }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
