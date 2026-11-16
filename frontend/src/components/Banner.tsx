import type { ReactNode } from 'react'
import { Icon, type IconName } from './Icon'

export function Banner(props: {
  tone: 'error' | 'warning' | 'info'
  icon?: IconName
  className?: string
  role?: 'alert' | 'status'
  details?: ReactNode
  end?: ReactNode
  children: ReactNode
}) {
  return (
    <div
      className={`banner banner-${props.tone}${props.className ? ` ${props.className}` : ''}`}
      role={props.role ?? (props.tone === 'error' ? 'alert' : 'status')}
    >
      <Icon name={props.icon ?? 'alert'} />
      <div className="banner-body">
        <p>{props.children}</p>
        {props.details}
      </div>
      {props.end}
    </div>
  )
}
