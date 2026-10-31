import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { LiveCapture } from '../capture/liveCapture'

type Phase = 'idle' | 'recording' | 'stopping'

export function LivePage() {
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const capture = useRef<LiveCapture | null>(null)
  const preview = useRef<HTMLVideoElement>(null)

  useEffect(() => () => void capture.current?.stop(), [])

  async function start() {
    setError(null)
    const { data, error } = await api.POST('/api/live', {
      body: { title: title || 'Live session' },
    })
    if (error || !data) return setError('Could not start a session')
    try {
      capture.current = new LiveCapture(data.id)
      const display = await capture.current.start()
      if (preview.current) preview.current.srcObject = display
      setSessionId(data.id)
      setPhase('recording')
    } catch (err) {
      setError(`Capture failed: ${err}`)
    }
  }

  async function stop() {
    if (!capture.current || !sessionId) return
    setPhase('stopping')
    await capture.current.stop()
    capture.current = null
    navigate(`/sessions/${sessionId}`)
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
        <button onClick={stop} disabled={phase === 'stopping'}>
          {phase === 'stopping' ? 'Finishing…' : 'Stop and make notes'}
        </button>
      )}
      <video ref={preview} className="preview" autoPlay muted hidden={phase === 'idle'} />
      {/* TODO: show the running transcript and draft notes while recording */}
      {error && <p className="error">{error}</p>}
    </div>
  )
}
