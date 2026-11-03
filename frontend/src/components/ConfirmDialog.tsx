import { useEffect, useRef } from 'react'

/**
 * A native <dialog> for confirming a destructive or disruptive action: it traps focus, closes on
 * Escape and returns focus to the opener by itself.
 */
export function ConfirmDialog(props: {
  open: boolean
  title: string
  body: string
  confirm: string
  cancel?: string
  danger?: boolean
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (props.open && !dialog.open) dialog.showModal()
    if (!props.open && dialog.open) dialog.close()
  }, [props.open])

  return (
    <dialog
      ref={ref}
      className="confirm"
      aria-labelledby="confirm-title"
      onCancel={(e) => {
        e.preventDefault()
        if (!props.busy) props.onCancel()
      }}
    >
      <h2 id="confirm-title">{props.title}</h2>
      <p>{props.body}</p>
      <div className="dialog-actions">
        <button type="button" className="btn" onClick={props.onCancel} disabled={props.busy} autoFocus>
          {props.cancel ?? 'Cancel'}
        </button>
        <button
          type="button"
          className={props.danger ? 'btn btn-stop' : 'btn btn-primary'}
          onClick={props.onConfirm}
          disabled={props.busy}
        >
          {props.confirm}
        </button>
      </div>
    </dialog>
  )
}
