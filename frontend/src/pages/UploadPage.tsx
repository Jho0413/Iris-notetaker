import { useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { HealthBanner } from '../components/HealthBanner'
import { Icon } from '../components/Icon'
import { formatBytes } from '../session/format'
import { UploadError, uploadWithProgress } from '../session/upload'
import { useTitle } from '../session/useTitle'

const ACCEPT = 'video/*,audio/*'

function stem(name: string): string {
  return name.replace(/\.[^.]+$/, '')
}

function isMedia(file: File): boolean {
  return file.type.startsWith('video/') || file.type.startsWith('audio/') || file.type === ''
}

export function UploadPage() {
  useTitle('Upload')
  const navigate = useNavigate()
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [titleEdited, setTitleEdited] = useState(false)
  const [progress, setProgress] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [over, setOver] = useState(false)
  const abort = useRef<AbortController | null>(null)

  useEffect(() => () => abort.current?.abort(), [])

  function choose(next: File | null) {
    setError(null)
    if (next && !isMedia(next)) {
      setError(`"${next.name}" isn't a video or audio file.`)
      return
    }
    setFile(next)
    if (next && !titleEdited) setTitle(stem(next.name))
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    setOver(false)
    if (progress === null) choose(e.dataTransfer.files[0] ?? null)
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    setError(null)
    setProgress(0)
    abort.current = new AbortController()
    try {
      const session = await uploadWithProgress(file, title.trim() || undefined, setProgress, abort.current.signal)
      navigate(`/sessions/${session.id}`)
    } catch (err) {
      setProgress(null)
      if (err instanceof UploadError && err.kind === 'aborted') return
      if (err instanceof UploadError && err.kind === 'network') {
        setError("The upload was interrupted. Check that the backend is running (make dev), then try again.")
      } else {
        setError(`The backend didn't accept the recording: ${err instanceof Error ? err.message : String(err)}`)
      }
    }
  }

  const uploading = progress !== null

  return (
    <div className="page-narrow">
      <div className="page-head">
        <div>
          <h1>Upload a recording</h1>
          <p className="lede">
            A lecture, meeting or demo where something was shown. Iris transcribes it, finds what was
            on screen and writes the notes.
          </p>
        </div>
      </div>
      <HealthBanner />
      <form className="form" onSubmit={onSubmit}>
        {file ? (
          <div className="file-card">
            <Icon name="file" />
            <div>
              <span className="file-name">{file.name}</span>
              <span className="file-meta num">
                {formatBytes(file.size)}
                {file.type ? ` · ${file.type}` : ''}
              </span>
            </div>
            {!uploading && (
              <button type="button" className="btn btn-sm" onClick={() => choose(null)}>
                Change
              </button>
            )}
          </div>
        ) : (
          <label
            className={`dropzone${over ? ' over' : ''}`}
            onDragOver={(e) => {
              e.preventDefault()
              setOver(true)
            }}
            onDragLeave={() => setOver(false)}
            onDrop={onDrop}
          >
            <Icon name="upload" />
            <strong>Choose a recording or drop it here</strong>
            <span>Video or audio: MP4, MOV, WebM, M4A, MP3, WAV…</span>
            <input type="file" accept={ACCEPT} onChange={(e) => choose(e.target.files?.[0] ?? null)} />
          </label>
        )}

        <div className="field">
          <label htmlFor="upload-title">Title</label>
          <input
            id="upload-title"
            className="input"
            value={title}
            placeholder="Taken from the file name"
            disabled={uploading}
            onChange={(e) => {
              setTitle(e.target.value)
              setTitleEdited(true)
            }}
          />
        </div>

        {error && (
          <div className="banner banner-error" role="alert">
            <Icon name="alert" />
            <div className="banner-body">
              <p>{error}</p>
            </div>
          </div>
        )}

        {uploading ? (
          <div className="progress-row">
            <div
              className="progress"
              role="progressbar"
              aria-label="Upload progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(progress * 100)}
            >
              <span style={{ width: `${progress * 100}%` }} />
            </div>
            <span className="num">{progress >= 1 ? 'Starting…' : `${Math.round(progress * 100)}%`}</span>
            <button type="button" className="btn" onClick={() => abort.current?.abort()}>
              Cancel
            </button>
          </div>
        ) : (
          <div className="form-actions">
            <button className="btn btn-primary btn-lg" disabled={!file}>
              {error && file ? 'Try again' : 'Make notes'}
            </button>
          </div>
        )}
      </form>
    </div>
  )
}
