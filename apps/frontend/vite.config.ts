/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    rollupOptions: {
      output: {
        // Three.js is ~600kB and only the chat tab's context meter needs it.
        // Splitting it out keeps the configuration tab from paying for a WebGL
        // renderer it never instantiates.
        manualChunks: {
          three: ['three'],
          mui: ['@mui/material', '@mui/icons-material'],
          vendor: ['react', 'react-dom', '@reduxjs/toolkit', 'react-redux'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // Proxied in dev so the browser sees one origin and SSE is not subject to
      // a preflight on every reconnect.
      '/api': {
        target: process.env.VITE_API_BASE_URL ?? 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
