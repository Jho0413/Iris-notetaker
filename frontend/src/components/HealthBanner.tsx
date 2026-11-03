import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { Icon } from './Icon'

/** Warns when the backend has no OpenAI key: processing would fail, existing notes still open. */
export function HealthBanner() {
  const [missingKey, setMissingKey] = useState(false)

  useEffect(() => {
    let live = true
    api.GET('/api/health').then(({ data }) => live && setMissingKey(data?.openai_key === false))
    return () => {
      live = false
    }
  }, [])

  if (!missingKey) return null
  return (
    <div className="banner banner-warning" role="status">
      <Icon name="alert" />
      <div className="banner-body">
        <p>
          <strong>Processing needs an OpenAI API key.</strong> Add <code>OPENAI_API_KEY</code> to{' '}
          <code>backend/.env</code> and restart the backend. Finished notes still open.
        </p>
      </div>
    </div>
  )
}
