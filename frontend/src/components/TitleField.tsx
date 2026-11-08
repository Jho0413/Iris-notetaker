import { useEffect, useId, useRef, useState } from 'react'
import { nameToSave, RENAME_HINT, renameFailure, TITLE_MAX_CHARS } from '../session/rename'

/**
 * A session's name, being edited in place: the title stays text, in its own type and position,
 * with an indigo underline (frontend/DESIGN.md, "Renaming"). Enter or leaving the field saves,
 * Esc cancels; an empty or unchanged name keeps the old one. A failed save says so under the
 * field and keeps the text.
 *
 * `onSave` returns the response status (0 if Iris never answered); 200 ends editing.
 */
export function TitleField(props: {
  title: string
  onSave: (name: string) => Promise<number>
  onDone: () => void
}) {
  const [draft, setDraft] = useState(props.title)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const busy = useRef(false) // Enter then blur must not save twice
  const hintId = useId()

  useEffect(() => {
    input.current?.focus()
    input.current?.select()
  }, [])

  async function commit() {
    if (busy.current) return
    const name = nameToSave(draft, props.title)
    if (name === null) return props.onDone()
    busy.current = true
    setSaving(true)
    setError(null)
    const status = await props.onSave(name)
    busy.current = false
    setSaving(false)
    if (status === 200) return props.onDone()
    setError(renameFailure(status))
    input.current?.focus()
  }

  const hint = error ?? (saving ? 'Saving…' : RENAME_HINT)
  return (
    <>
      <input
        ref={input}
        className="title-field"
        value={draft}
        maxLength={TITLE_MAX_CHARS}
        size={Math.max(10, draft.length + 1)} // where field-sizing isn't supported
        aria-label="Session name"
        aria-describedby={hintId}
        aria-invalid={error ? true : undefined}
        readOnly={saving}
        onChange={(e) => {
          setDraft(e.target.value)
          setError(null)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            void commit()
          } else if (e.key === 'Escape') {
            e.preventDefault()
            busy.current = true // leaving the field now cancels, it doesn't save
            props.onDone()
          }
        }}
        onBlur={() => void commit()}
      />
      <p id={hintId} className={`edit-meta${error ? ' err' : ''}`} role="status">
        {hint}
      </p>
    </>
  )
}
