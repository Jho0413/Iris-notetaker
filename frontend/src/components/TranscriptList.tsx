import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { formatClock, type Segment } from '../session/state'
import { Icon } from './Icon'

/**
 * Transcript segments with start times. With `follow`, the list keeps the newest line in view
 * only while the reader is already at the bottom; otherwise it offers "Jump to latest".
 */
export function TranscriptList({ segments, follow = false }: { segments: Segment[]; follow?: boolean }) {
  const box = useRef<HTMLDivElement>(null)
  const [atBottom, setAtBottom] = useState(true)

  useLayoutEffect(() => {
    const el = box.current
    if (follow && el && atBottom) el.scrollTop = el.scrollHeight
  }, [segments, follow, atBottom])

  useEffect(() => {
    const el = box.current
    if (!follow || !el) return
    const onScroll = () => setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 24)
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [follow])

  return (
    <div ref={box} className={follow ? 'live-transcript' : undefined}>
      <ol className="transcript">
        {segments.map((s, i) => (
          <li key={`${s.start}-${i}`}>
            <time className="num" dateTime={`PT${Math.floor(s.start)}S`}>
              {formatClock(s.start)}
            </time>
            <p>{s.text.trim()}</p>
          </li>
        ))}
      </ol>
      {follow && !atBottom && (
        <div className="jump">
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => box.current?.scrollTo({ top: box.current.scrollHeight })}
          >
            <Icon name="arrowDown" /> Jump to latest
          </button>
        </div>
      )}
    </div>
  )
}
