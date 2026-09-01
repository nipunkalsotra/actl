import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Fail loudly if 5173 is already taken instead of silently moving to
    // another port -- a drifted origin is exactly what breaks the backend's
    // CORS allow-list (main.py), which intentionally lists only 5173.
    strictPort: true,
  },
})
