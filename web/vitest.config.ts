import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// Unit tests only. The Playwright end-to-end smokes live in `tests/*.spec.ts` and are run by
// `npm run test:smoke` (a browser + the built SPA) — keep vitest scoped to the pure `src/**/*.test.ts`
// specs so the two runners never collide. The svelte plugin lets future unit tests import `.svelte.ts`
// rune modules.
export default defineConfig({
  plugins: [svelte()],
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
});
