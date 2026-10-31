import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { uploadRecording } from '../api/client'

export function UploadPage() {
  const navigate = useNavigate()
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const session = await uploadRecording(file, title || undefined)
      navigate(`/sessions/${session.id}`)
    } catch (err) {
      setError(String(err))
      setBusy(false)
    }
  }

  return (
    <form className="card" onSubmit={onSubmit}>
      <h2>Upload a recording</h2>
      <input
        type="file"
        accept="video/*,audio/*"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
      />
      <input placeholder="Title (optional)" value={title} onChange={(e) => setTitle(e.target.value)} />
      <button disabled={!file || busy}>{busy ? 'Uploading…' : 'Make notes'}</button>
      {error && <p className="error">{error}</p>}
    </form>
  )
}
