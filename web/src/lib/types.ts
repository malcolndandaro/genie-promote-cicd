/**
 * Shared domain types — the JSON contract the FastAPI engine returns.
 * Kept identical to the engine API so the Svelte client is a faithful port of the AppKit app.
 */

/** Who the platform forwarded (OBO) + the configured Steward (separation of duties). */
export interface Whoami {
  email: string | null;
  steward: string | null;
}

/**
 * A promotable resource the user owns. Today the engine only returns Genie spaces, but the UI
 * is modeled around a `kind` so AI/BI dashboards (and future Databricks resources) drop in
 * without reshaping the screens. See `resources.ts` for the kind registry.
 */
export type ResourceKind = 'genie_space' | 'dashboard';

export interface PromotableResource {
  id: string;
  title: string;
  kind: ResourceKind;
}

/** One reviewer finding (deterministic rule or LLM). */
export interface Finding {
  rule_id: string;
  severity: string;
  message: string;
  citation?: string;
  suggestion?: string;
}

export interface Gate {
  conclusion: string; // 'success' | 'failure' | (neutral)
  blocker_count: number;
  summary: string;
}

export interface EvalResult {
  status: string;
  summary: string;
  pass_rate?: number | null;
  n?: number;
}

/** Promotion-pipeline step status as emitted by `app_logic.build_timeline`. */
export type StepStatus = 'pass' | 'fail' | 'running' | 'pending';

export interface TimelineStep {
  key: string;
  label: string;
  status: StepStatus;
}

export interface Review {
  findings: Finding[];
  gate: Gate;
  eval: EvalResult;
  allowlist_violations: string[];
  consumer_group: string;
  timeline: TimelineStep[];
}
