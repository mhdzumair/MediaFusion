import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/app/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/static': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/streaming_provider': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/poster': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/encrypt-user-data': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      }
    },
  },
  build: {
    outDir: 'dist',
  },
})
