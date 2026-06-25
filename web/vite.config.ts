import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// The Svelte SPA is served by the FastAPI engine (same origin) in production, so all API calls
// are same-origin `/api/*`. In local dev, `vite dev` proxies them to the FastAPI app on :8000.
export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
});
