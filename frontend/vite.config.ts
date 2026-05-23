import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // Bind all interfaces so VS Code dev-container port forwarding (IPv4) reaches
    // Vite; otherwise it binds IPv6-only (::1) and forwarded connections stall.
    host: true,
    // Proxy the API's exact prefixes to FastAPI; everything else (incl. the
    // SPA's /admin/* routes) falls through to Vite's index.html. Note: the API
    // also owns /admin/db-credentials (M3.5) — not proxied here because M3.3's UI
    // doesn't call it; add it explicitly (not the bare /admin prefix, which would
    // shadow the admin SPA routes) when a view needs it.
    proxy: {
      '/surveys': 'http://127.0.0.1:8000',
      '/auth': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test-setup.ts'],
  },
});
