/**
 * Promotion flow state — a small reactive state machine shared across the screens.
 *
 * Using a class with `$state` fields (per Svelte best practice) lets SV2 (select + request),
 * SV3 (render the review) and SV4 (steward approval) all read/drive one source of truth.
 *
 * Phases today: idle → reviewing → reviewed (or error). The flow is intentionally modeled as a
 * machine so the FUTURE git integration drops in as new phases without reshaping callers:
 *   reviewed → requesting_pr → pr_open → approved (PR approved by steward) → deploying → deployed
 * SV4 adds the in-app approval; the git-backed phases replace the in-app approve when wired.
 */
import type { PromotableResource, Review } from './types';
import { postReview } from './api';

export type PromotionPhase = 'idle' | 'reviewing' | 'reviewed' | 'error';

export class Promotion {
  resource = $state<PromotableResource | null>(null);
  phase = $state<PromotionPhase>('idle');
  review = $state<Review | null>(null);
  error = $state<string | null>(null);

  /** The selected resource id (or '' for none) — convenient for binding a Select. */
  get selectedId(): string {
    return this.resource?.id ?? '';
  }

  /** Pick a resource. A new selection invalidates any prior verdict so it can't mislead. */
  select(resource: PromotableResource | null): void {
    this.resource = resource;
    this.review = null;
    this.error = null;
    this.phase = 'idle';
  }

  /** Run the full promotion review for the selected resource (OBO export + app-SP reviewer). */
  async requestReview(): Promise<void> {
    if (!this.resource || this.phase === 'reviewing') return; // no double-submit
    const id = this.resource.id;
    this.phase = 'reviewing';
    this.review = null;
    this.error = null;
    try {
      const review = await postReview(id);
      if (this.resource?.id !== id) return; // selection changed mid-flight — drop the stale result
      this.review = review;
      this.phase = 'reviewed';
    } catch (e) {
      if (this.resource?.id !== id) return;
      this.error = e instanceof Error ? e.message : String(e);
      this.phase = 'error';
    }
  }
}
