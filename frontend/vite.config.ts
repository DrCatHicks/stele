import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // Bind all interfaces so VS Code dev-container port forwarding (IPv4) reaches
    // Vite; otherwise it binds IPv6-only (::1) and forwarded connections stall.
    host: true,
    // The API lives entirely under /api, so a single proxy entry covers it and
    // can never shadow an SPA route (all of which sit outside /api). Everything
    // else falls through to Vite's index.html for client-side routing.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test-setup.ts'],
  },
});
