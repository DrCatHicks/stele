import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // Bind all interfaces so VS Code dev-container port forwarding (IPv4) reaches
    // Vite; otherwise it binds IPv6-only (::1) and forwarded connections stall.
    host: true,
    // Proxy the API's exact prefixes to FastAPI; everything else (incl. the
    // SPA's /admin/* routes) falls through to Vite's index.html. The API owns a
    // few paths under /admin — list them individually (never the bare /admin
    // prefix, which would shadow the admin SPA routes). /admin/db-credentials
    // (M3.5) stays unproxied until a view calls it; M3.4 adds the GDPR audit and
    // PII-review endpoints. /respondents is the withdrawal-trigger endpoint.
    proxy: {
      '/surveys': 'http://127.0.0.1:8000',
      '/auth': 'http://127.0.0.1:8000',
      '/respondents': 'http://127.0.0.1:8000',
      '/admin/withdrawals': 'http://127.0.0.1:8000',
      '/admin/pii': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test-setup.ts'],
  },
});
