import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3000,
    proxy: {
      '/ws': {
        target: `ws://${process.env.HOST || 'localhost'}:8000`,
        ws: true,
        changeOrigin: true,
        secure: false,
      },
      '/api': {
        target: `http://${process.env.HOST || 'localhost'}:8000`,
        changeOrigin: true,
        secure: false,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  define: {
    'import.meta.env.VITE_VISION_ENABLED': false,
  },
})
