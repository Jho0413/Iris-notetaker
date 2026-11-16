import { Link } from 'react-router-dom'
import { BACK_TO_LIBRARY, RETRY, UNREACHABLE } from '../lib/messages'
import { Icon } from './Icon'

export function ErrorPanel(props: { page?: boolean; title?: string; detail?: string; onRetry?: () => void }) {
  return (
    <div className={props.page ? 'page error-panel' : 'error-panel'} role={props.onRetry ? 'alert' : undefined}>
      <h2>{props.title ?? UNREACHABLE}</h2>
      {props.detail && <p>{props.detail}</p>}
      {props.onRetry ? (
        <button type="button" className="btn" onClick={props.onRetry}>
          <Icon name="retry" /> {RETRY}
        </button>
      ) : (
        <Link className="btn" to="/">
          {BACK_TO_LIBRARY}
        </Link>
      )}
    </div>
  )
}
