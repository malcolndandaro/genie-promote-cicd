/**
 * Deep-link URL builders — pure so they're unit-testable without a DOM/fetch (mirrors `route.ts`/
 * `status.ts`'s split of pure logic from their Svelte consumers).
 *
 * Today only the Genie Space "room" URL, used by G5's rehydrate success state to link straight to
 * the Space just (re-)created in dev — other resource kinds can add their own builder here as they
 * gain a deep-link (see `resources.ts`'s kind registry).
 */

/** The Genie room URL for a Space on a given workspace host. `host` may be a bare hostname or a
 * full `https://…` URL (as `APP_DEV_HOST` is configured either way) — normalized to bare host with
 * no trailing slash before building the path. */
export function genieSpaceUrl(host: string, spaceId: string): string {
  const bareHost = host.trim().replace(/^https?:\/\//, '').replace(/\/+$/, '');
  return `https://${bareHost}/genie/rooms/${spaceId}`;
}
