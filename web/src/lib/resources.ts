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
    enabled: true,
  },
};

export function kindMeta(kind: ResourceKind): ResourceKindMeta {
  return RESOURCE_KINDS[kind];
}

/** Whether a kind string the backend sent is one this client knows how to render. Used to skip an
 * unknown kind rather than crash the list — a newer engine may return a kind an older SPA predates. */
export function isKnownKind(kind: string): kind is ResourceKind {
  return kind in RESOURCE_KINDS;
}

/**
 * Map the engine's discriminated `/resources` DTO (`{id,title,kind,env}`) to a resource.
 *
 * This is the shape the registry was designed for: the engine now tags each resource with its own
 * kind, so the client no longer assigns one. An unknown kind is dropped by the caller
 * (`getResources`) via `isKnownKind`.
 */
export function dtoToResource(dto: {
  id: string;
  title: string;
  kind: ResourceKind;
  env?: 'dev' | 'prod';
}): PromotableResource {
  return { id: dto.id, title: dto.title, kind: dto.kind, env: dto.env ?? 'dev' };
}

/**
 * Map the legacy `/spaces` DTO (`{space_id,title}`) to a kind-tagged resource.
 *
 * Retained for the back-compat `/spaces` endpoint (and its tests); `dtoToResource` above is the path
 * the app uses now. `/api/spaces` lists the caller's DEV authoring spaces, so everything here is
 * `env: 'dev'` — the env badge makes that origin obvious next to the kind badge.
 */
export function spaceToResource(dto: { space_id: string; title: string }): PromotableResource {
  return { id: dto.space_id, title: dto.title, kind: 'genie_space', env: 'dev' };
}
