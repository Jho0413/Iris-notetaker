import { useState } from 'react'
import { irisApp } from '../api/appBridge'
import { Banner } from '../components/Banner'
import { ADD_KEY, KEY_ACTION_FAILED, noKeyWarning } from './settings'
import { useKeyChange } from './useKeyChange'

export function NoKeyBanner(props: { withAction?: boolean; onChanged?: () => void }) {
  const app = irisApp()
  const warning = noKeyWarning(Boolean(app))
  const [failed, setFailed] = useState(false)
  const key = useKeyChange((result) => (result.ok ? props.onChanged?.() : setFailed(true)))

  return (
    <Banner
      tone="warning"
      className="banner-inline"
      end={
        app &&
        props.withAction !== false && (
          <button type="button" className="btn btn-primary btn-sm" onClick={() => { setFailed(false); void key.change('saved') }} disabled={key.busy}>
            {ADD_KEY}
          </button>
        )
      }
    >
      <strong>{warning.title}</strong> {failed ? KEY_ACTION_FAILED : warning.detail}
    </Banner>
  )
}
