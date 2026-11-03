import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { LiveCapture } from '../capture/liveCapture'

type Phase = 'idle' | 'recording' | 'stopping'

export function LivePage() {
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState<string | null>(null)
  const capture = useRef<LiveCapture | null>(null)
  const preview = useRef<HTMLVideoElement>(null)

  useEffect(() => () => void capture.current?.stop().catch(() => {}), [])

  async function start() {
    setError(null)
    const { data, error } = await api.POST('/api/live', {
      body: { title: title || 'Live session' },
    })
    if (error || !data) return setError('Could not start a session')
    try {
      // Sharing stopped from the browser's own controls (or the mic went away): finish.
      capture.current = new LiveCapture(data.id, { onEnded: () => void stop() })
      const display = await capture.current.start()
      if (preview.current) preview.current.srcObject = display
      setPhase('recording')
    } catch (err) {
      capture.current = null
      setError(err instanceof Error ? err.message : `Capture failed: ${err}`)
    }
  }

  async function stop() {
    const current = capture.current
    if (!current) return
    setError(null)
    setPhase('stopping')
    try {
      await current.stop()
    } catch (err) {
      // Nothing captured is lost; stopping again retries the uploads and the end request.
      setError(err instanceof Error ? err.message : String(err))
      setPhase('recording')
      return
    }
    capture.current = null
    navigate(`/sessions/${current.sessionId}`)
  }

  return (
    <div className="card">
      <h2>Live session</h2>
      {phase === 'idle' && (
        <>
          <input placeholder="Title (optional)" value={title} onChange={(e) => setTitle(e.target.value)} />
          <button onClick={start}>Share screen and start</button>
        </>
      )}
      {phase !== 'idle' && (
        <button onClick={() => void stop()} disabled={phase === 'stopping'}>
          {phase === 'stopping' ? 'Finishing…' : 'Stop and make notes'}
        </button>
      )}
      <video ref={preview} className="preview" autoPlay muted hidden={phase === 'idle'} />
      {/* TODO: show the running transcript and draft notes while recording */}
      {error && <p className="error">{error}</p>}
    </div>
  )
}
