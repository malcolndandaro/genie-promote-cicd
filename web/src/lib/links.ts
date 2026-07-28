/**
 * Deep-link URL builders — pure so they're unit-testable without a DOM/fetch (mirrors `route.ts`/
 * `status.ts`'s split of pure logic from their Svelte consumers).
 *
 * One builder per resource kind that has a deep-link, plus `resourceUrl` which dispatches on the
 * kind so callers never branch (see `resources.ts`'s kind registry).
 */
import type { ResourceKind } from './types';

/** `host` may be a bare hostname or a full `https://…` URL (both shapes appear in `APP_DEV_HOST` /
 * `prod_host`), so normalize to a bare host with no trailing slash before building any path. */
function bare(host: string): string {
  return host.trim().replace(/^https?:\/\//, '').replace(/\/+$/, '');
}

/** The Genie room URL for a Space on a given workspace host. */
export function genieSpaceUrl(host: string, spaceId: string): string {
  return `https://${bare(host)}/genie/rooms/${spaceId}`;
}

/** The PUBLISHED view of an AI/BI (Lakeview) dashboard — what a business consumer should open.
 *
 * `/published` is deliberate: the draft view is the authoring surface, while the promotion pipeline
 * publishes the dashboard for consumers. Linking to the draft would show an editor a viewer may not
 * even have permission to open. */
export function dashboardUrl(host: string, dashboardId: string): string {
  return `https://${bare(host)}/dashboardsv3/${dashboardId}/published`;
}

/** The deep-link for any resource kind. Returns null for a kind with no known link (so a caller can
 * simply hide the affordance rather than render a broken one). */
export function resourceUrl(host: string | null | undefined, kind: ResourceKind,
                            resourceId: string | null | undefined): string | null {
  if (!host || !resourceId) return null;
  if (kind === 'genie_space') return genieSpaceUrl(host, resourceId);
  if (kind === 'dashboard') return dashboardUrl(host, resourceId);
  return null;
}
