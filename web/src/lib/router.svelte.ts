/**
 * Reactive hash router (UI1) — a thin runes wrapper over the pure primitives in `route.ts`.
 *
 * Owns the single reactive `route` the shell renders from. It mirrors `window.location.hash`:
 * deep-links/reloads land on the right screen, browser back/forward work, and plain `<a href="#/…">`
 * links in the sidebar navigate for free (the hashchange listener updates `route`). Keeping the URL
 * authoritative means there is no second source of truth to keep in sync. The URL is also
 * canonicalized — an empty or unknown hash (`#/bogus`) is rewritten to the default route's hash so
 * the address bar always reflects the screen actually shown.
 */
import { parseHash, formatHash, DEFAULT_ROUTE, type Route, type RouteId } from './route';

export class Router {
  route = $state<Route>({ ...DEFAULT_ROUTE });

  constructor() {
    if (typeof window === 'undefined') return; // SSR/test-import guard
    this._sync();
    // Register the listener in an $effect so it's cleaned up with the owning component (no leak, and
    // no duplicate listeners across HMR). Valid because the Router is constructed during component
    // init. Falls back to a bare listener if constructed outside a component (e.g. a unit test).
    try {
      $effect(() => {
        window.addEventListener('hashchange', this._sync);
        return () => window.removeEventListener('hashchange', this._sync);
      });
    } catch {
      window.addEventListener('hashchange', this._sync);
    }
  }

  /** Read the hash → set `route` → canonicalize the URL if it isn't already the route's hash. */
  private _sync = (): void => {
    const parsed = parseHash(window.location.hash);
    this.route = parsed;
    const canonical = formatHash(parsed);
    if (window.location.hash !== canonical) history.replaceState(null, '', canonical);
  };

  /** Navigate to a route. Updates the hash (→ hashchange → _sync); a same-hash nav sets state directly. */
  navigate(id: RouteId, param?: string): void {
    const next: Route = param ? { id, param } : { id };
    const h = formatHash(next);
    if (typeof window === 'undefined') {
      this.route = next;
      return;
    }
    if (window.location.hash !== h) window.location.hash = h;
    else this.route = next;
  }

  destroy(): void {
    if (typeof window !== 'undefined') window.removeEventListener('hashchange', this._sync);
  }
}
