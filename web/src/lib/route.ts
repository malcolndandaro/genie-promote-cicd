/**
 * Pure hash-routing primitives for the app shell (UI1).
 *
 * Kept framework-free (no runes, no DOM) so it is trivially unit-testable: `parseHash` and
 * `formatHash` are inverses, unknown/empty input falls back to the default, and only `promocoes`
 * carries a path param (a promotion id, for the `#/promocoes/:id` deep-link). The reactive wrapper
 * that touches `window.location`/`hashchange` lives in `router.svelte.ts`.
 */
export type RouteId =
  | 'inicio'
  | 'espacos'
  | 'promocoes'
  | 'acesso'
  | 'rehidratar'
  | 'admin'
  | 'configuracoes';

export interface Route {
  id: RouteId;
  /** Optional path param — today only a promotion id for `#/promocoes/:id`. */
  param?: string;
}

export const ROUTE_IDS: readonly RouteId[] = [
  'inicio',
  'espacos',
  'promocoes',
  'acesso',
  'rehidratar',
  'admin',
  'configuracoes',
];

/** The default landing route — the business-user home/dashboard (UI4). */
export const DEFAULT_ROUTE: Route = { id: 'inicio' };

function isRouteId(s: string): s is RouteId {
  return (ROUTE_IDS as readonly string[]).includes(s);
}

/** Parse a location hash (`#/promocoes/abc`) into a Route. Unknown/empty → the default route. */
export function parseHash(hash: string): Route {
  const clean = (hash || '').replace(/^#\/?/, '').trim(); // '#/promocoes/abc' -> 'promocoes/abc'
  if (!clean) return { ...DEFAULT_ROUTE };
  const [id, ...rest] = clean.split('/');
  if (!isRouteId(id)) return { ...DEFAULT_ROUTE };
  const param = rest.join('/').trim() || undefined;
  // Only `promocoes` carries a param (a promotion id); ignore stray params on other routes.
  if (id === 'promocoes' && param) return { id, param: decodeURIComponent(param) };
  return { id };
}

/** Format a Route into a location hash (`{id:'promocoes',param:'abc'}` → `#/promocoes/abc`). */
export function formatHash(route: Route): string {
  if (route.id === 'promocoes' && route.param) {
    return `#/promocoes/${encodeURIComponent(route.param)}`;
  }
  return `#/${route.id}`;
}

/** Whether two routes point to the same place (id + param). */
export function sameRoute(a: Route, b: Route): boolean {
  return a.id === b.id && (a.param ?? '') === (b.param ?? '');
}
