import { createBrowserRouter, Link, NavLink, Outlet, RouterProvider } from 'react-router-dom'
import { useRecordFromMenuBar } from './capture/menuBar'
import { Icon, Mark, type IconName } from './components/Icon'
import { useTheme, type ThemeChoice } from './components/theme'
import { LibraryPage } from './pages/LibraryPage'
import { LivePage } from './pages/LivePage'
import { SessionPage } from './pages/SessionPage'
import { SettingsPage } from './pages/SettingsPage'
import { UploadPage } from './pages/UploadPage'

const THEMES: { id: ThemeChoice; icon: IconName; label: string }[] = [
  { id: 'system', icon: 'system', label: 'Use system theme' },
  { id: 'light', icon: 'sun', label: 'Light theme' },
  { id: 'dark', icon: 'moon', label: 'Dark theme' },
]

function ThemeSwitch() {
  const [theme, setTheme] = useTheme()
  return (
    <div className="theme-switch" role="group" aria-label="Theme">
      {THEMES.map((t) => (
        <button
          key={t.id}
          type="button"
          aria-pressed={theme === t.id}
          aria-label={t.label}
          title={t.label}
          onClick={() => setTheme(t.id)}
        >
          <Icon name={t.icon} />
        </button>
      ))}
    </div>
  )
}

function Layout() {
  useRecordFromMenuBar()
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <Link to="/" className="wordmark" aria-label="Iris, library">
          <Mark />
          Iris
        </Link>
        <nav aria-label="Main">
          <NavLink to="/" end className="navlink">
            Library
          </NavLink>
        </nav>
        <span className="spacer" />
        <div className="actions">
          <Link to="/upload" className="btn" aria-label="Upload">
            <Icon name="upload" /> <span className="btn-label">Upload</span>
          </Link>
          <Link to="/live" className="btn btn-primary" aria-label="Record live">
            <Icon name="record" /> <span className="btn-label">Record live</span>
          </Link>
          <NavLink to="/settings" className="btn btn-ghost btn-icon" aria-label="Settings" title="Settings">
            <Icon name="gear" />
          </NavLink>
          <ThemeSwitch />
        </div>
      </header>
      <main id="main" className="main" tabIndex={-1}>
        <Outlet />
      </main>
    </>
  )
}

function NotFound() {
  return (
    <div className="page error-panel">
      <h2>Page not found</h2>
      <p>There's nothing at this address.</p>
      <Link className="btn" to="/">
        Back to the library
      </Link>
    </div>
  )
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: '/', element: <LibraryPage /> },
      { path: '/upload', element: <UploadPage /> },
      { path: '/live', element: <LivePage /> },
      { path: '/sessions/:id', element: <SessionPage /> },
      { path: '/settings', element: <SettingsPage /> },
      { path: '*', element: <NotFound /> },
    ],
  },
])

export default function App() {
  return <RouterProvider router={router} />
}
