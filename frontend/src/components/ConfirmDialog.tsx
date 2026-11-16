import { useEffect, useRef } from 'react'
import type { Blocker } from 'react-router-dom'
import { KEEP_EDITING } from '../lib/messages'

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
      <div className={props.danger ? 'dialog-actions dialog-actions-apart' : 'dialog-actions'}>
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

export function LeaveWithoutSavingDialog(props: { blocker: Blocker; body: string }) {
  return (
    <ConfirmDialog
      open={props.blocker.state === 'blocked'}
      title="Leave without saving?"
      body={props.body}
      confirm="Leave"
      cancel={KEEP_EDITING}
      onConfirm={() => props.blocker.proceed?.()}
      onCancel={() => props.blocker.reset?.()}
    />
  )
}
