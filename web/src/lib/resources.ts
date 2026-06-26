/**
 * Resource-kind registry — the single extension point for new promotable Databricks resources.
 *
 * The promotion pipeline (review → gate → steward approval → deploy) is resource-agnostic. To add
 * a new kind (e.g. an AI/BI dashboard, a job, a pipeline), add an entry here and have the backend
 * tag its resources with the matching `kind`. The screens (list, tabs, review header) read this
 * registry, so no per-kind UI branching is needed.
 */
import type { ResourceKind, PromotableResource } from './types';

export interface ResourceKindMeta {
  /** Singular label, e.g. "Genie Space". */
  label: string;
  /** Plural label for tab/section headers, e.g. "Genie Spaces". */
  labelPlural: string;
  /** Short glyph used in lists/badges until we wire a proper icon set. */
  glyph: string;
  /** Whether authoring/promotion for this kind is live yet (vs. "em breve"). */
  enabled: boolean;
}

export const RESOURCE_KINDS: Record<ResourceKind, ResourceKindMeta> = {
  genie_space: {
    label: 'Genie Space',
    labelPlural: 'Genie Spaces',
    glyph: '✦',
    enabled: true,
  },
  dashboard: {
    label: 'Painel AI/BI',
    labelPlural: 'Painéis AI/BI',
    glyph: '▤',
    enabled: false, // backend support lands with a future slice; UI is already ready.
  },
};

export function kindMeta(kind: ResourceKind): ResourceKindMeta {
  return RESOURCE_KINDS[kind];
}

/**
 * Map the engine's `/spaces` DTO (`{space_id,title}`) to a kind-tagged resource.
 *
 * NOTE (future resource types): the client currently ASSIGNS `kind: 'genie_space'` because the
 * `/spaces` endpoint only returns Genie spaces. When AI/BI dashboards (and other resources) land,
 * the engine should emit a discriminated DTO (`{id, title, kind}`) and `getResources` should merge
 * the sources — the registry above already handles rendering N kinds; only *fetching* needs work.
 */
export function spaceToResource(dto: { space_id: string; title: string }): PromotableResource {
  return { id: dto.space_id, title: dto.title, kind: 'genie_space' };
}
