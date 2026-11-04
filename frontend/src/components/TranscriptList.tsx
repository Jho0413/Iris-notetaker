import { formatClock, type Segment } from '../session/state'

/** Transcript segments with start times. */
export function TranscriptList({ segments }: { segments: Segment[] }) {
  return (
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
  )
}
