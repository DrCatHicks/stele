import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // Bind all interfaces so VS Code dev-container port forwarding (IPv4) reaches
    // Vite; otherwise it binds IPv6-only (::1) and forwarded connections stall.
    host: true,
    // Proxy the API's exact prefixes to FastAPI; everything else (incl. the
    // SPA's /admin/* routes) falls through to Vite's index.html. Proxy keys are
    // matched as path PREFIXES, so list the precise API subpaths — never a prefix
    // that also covers an SPA route. In particular proxy '/admin/pii/free-text'
    // (the reviewer API), NOT '/admin/pii', which would also swallow the SPA route
    // '/admin/pii-review' and 404 it on hard refresh. /admin/db-credentials (M3.5)
    // stays unproxied until a view calls it. /respondents is the withdrawal trigger.
    proxy: {
      '/surveys': 'http://127.0.0.1:8000',
      '/auth': 'http://127.0.0.1:8000',
      '/respondents': 'http://127.0.0.1:8000',
      '/admin/withdrawals': 'http://127.0.0.1:8000',
      '/admin/pii/free-text': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test-setup.ts'],
  },
});
