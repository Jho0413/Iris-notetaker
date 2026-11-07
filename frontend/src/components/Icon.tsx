// One small icon set, drawn in one stroke weight (24px grid, 1.75 stroke, round joins).
const PATHS = {
  check: 'M5 12.5l4.5 4.5L19 7.5',
  pending: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z',
  spinner: 'M21 12a9 9 0 1 1-6.2-8.56',
  alert: 'M12 8v5M12 16.5v.01M10.3 3.9 2.6 17.4A2 2 0 0 0 4.3 20.4h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
  stop: 'M6.5 6.5h11v11h-11z',
  x: 'M6 6l12 12M18 6 6 18',
  upload: 'M12 15V4M7.5 8.5 12 4l4.5 4.5M5 15v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3',
  download: 'M12 4v11M7.5 10.5 12 15l4.5-4.5M5 15v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3',
  record: 'M12 19a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM12 14.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
  mic: 'M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3zM18.5 11.5a6.5 6.5 0 0 1-13 0M12 18v3',
  screen: 'M4 5h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM9 21h6M12 17v4',
  sun: 'M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM12 2.5v2M12 19.5v2M4.6 4.6 6 6M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4',
  moon: 'M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z',
  system: 'M4 5h16a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM2 20h20',
  retry: 'M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6',
  trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3',
  chevron: 'M9 6l6 6-6 6',
  file: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5zM14 3v5h5',
  stopped: 'M8 12h8',
  pencil: 'M4 20h4L18.5 9.5a2.1 2.1 0 0 0-3-3L5 17v3zM13.5 7.5l3 3',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  // Editing the notes (the block toolbar and its menus).
  grip: 'M9 5a1 1 0 1 0 0 2 1 1 0 0 0 0-2zM15 5a1 1 0 1 0 0 2 1 1 0 0 0 0-2zM9 11a1 1 0 1 0 0 2 1 1 0 0 0 0-2zM15 11a1 1 0 1 0 0 2 1 1 0 0 0 0-2zM9 17a1 1 0 1 0 0 2 1 1 0 0 0 0-2zM15 17a1 1 0 1 0 0 2 1 1 0 0 0 0-2z',
  plus: 'M12 5v14M5 12h14',
  up: 'M12 19V5M6 11l6-6 6 6',
  down: 'M12 5v14M6 13l6 6 6-6',
  text: 'M5 6h14M5 12h14M5 18h9',
  heading: 'M6 4v16M18 4v16M6 12h12',
  box: 'M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z',
  stepIcon: 'M8 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM8 15v5M8 4v5M14 12h6',
  sigma: 'M18 5H6l6 7-6 7h12',
  image: 'M4 5h16v14H4zM8.5 8.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zM20 16l-5-5-8 8',
  lock: 'M7 11V8a5 5 0 0 1 10 0v3M6 11h12a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1z',
} as const

export type IconName = keyof typeof PATHS

export function Icon({ name, label }: { name: IconName; label?: string }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <path d={PATHS[name]} />
    </svg>
  )
}

/** The Iris mark: an eye's iris, the accent ring around a solid pupil. */
export function Mark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <circle cx="12" cy="12" r="3.5" fill="currentColor" />
    </svg>
  )
}
