import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  root: resolve(__dirname, 'src/popup'),
  publicDir: false,
  // Use relative paths for browser extension compatibility
  base: './',
  resolve: {
    alias: {
      '@': resolve(__dirname, './src'),
    },
  },
  build: {
    outDir: resolve(__dirname, 'dist/popup'),
    emptyOutDir: true,
    // Disable code splitting for browser extension
    cssCodeSplit: false,
    rollupOptions: {
      input: resolve(__dirname, 'src/popup/index.html'),
      output: {
        // Keep all code in single files for extension compatibility
        entryFileNames: 'popup.js',
        chunkFileNames: 'popup.js',
        assetFileNames: (assetInfo) => {
          if (assetInfo.name?.endsWith('.css')) {
            return 'popup.css'
          }
          return '[name].[ext]'
        },
        // Inline dynamic imports
        inlineDynamicImports: true,
      },
    },
  },
})
