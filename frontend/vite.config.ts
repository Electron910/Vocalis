import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0', // Bind to all interfaces for RunPod
    port: 3000,
    open: false, // Don't auto-open browser in RunPod
    proxy: {
      // Proxy WebSocket connections to backend
      '/ws': {
        target: 'ws://0.0.0.0:8000',
        ws: true,
        changeOrigin: true,
        secure: false, // Allow self-signed certificates in RunPod
      },
      // Proxy REST API calls to backend
      '/api': {
        target: 'http://0.0.0.0:8000',
        changeOrigin: true,
        secure: false, // Allow self-signed certificates in RunPod
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
