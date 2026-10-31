import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend runs separately on :8000; proxy /api so the UI and API share an origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
