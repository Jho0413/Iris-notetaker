import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import { LibraryPage } from './pages/LibraryPage'
import { LivePage } from './pages/LivePage'
import { SessionPage } from './pages/SessionPage'
import { UploadPage } from './pages/UploadPage'

export default function App() {
  return (
    <BrowserRouter>
      <nav className="nav">
        <NavLink to="/" className="brand">
          Iris
        </NavLink>
        <NavLink to="/" end>
          Library
        </NavLink>
        <NavLink to="/upload">Upload</NavLink>
        <NavLink to="/live">Live</NavLink>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<LibraryPage />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/live" element={<LivePage />} />
          <Route path="/sessions/:id" element={<SessionPage />} />
        </Routes>
      </main>
    </BrowserRouter>
  )
}
