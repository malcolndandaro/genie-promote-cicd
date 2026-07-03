/**
 * Shared domain types — the JSON contract the FastAPI engine returns.
 * Kept identical to the engine API so the Svelte client is a faithful port of the AppKit app.
 */

/** Who the platform forwarded (OBO) + the configured Steward (SoD) + whether the caller is an
 * Admin/Steward (drives the LB5 history `scope=all` toggle; the server re-checks the role). */
export interface Whoami {
  email: string | null;
  steward: string | null;
  is_admin: boolean;
  /** The source/CI repo the header's GitHub link points to (config-driven; the SPA falls back to a
   * default if absent). */
  repo_url?: string | null;
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

/** One Genie Space permission entry the Requester declared (F2, system 1). */
export interface SpacePermissionEntry {
  principal: string;
  is_group: boolean;
  level: string; // 'CAN_RUN' | 'CAN_VIEW'
}

/** One UC SELECT-grant principal the Requester declared (F2, system 2). */
export interface AccessPrincipal {
  principal: string;
  is_group: boolean;
}

/**
 * The Requester's declared access for this promotion (F2). Models BOTH systems explicitly —
 * Genie Space permissions (who can open/run the Space) and UC data grants (who can SELECT the
 * underlying tables) — so the Steward reviews and approves exactly what will be enforced by the
 * governed CI pipeline (never applied app-direct). Empty arrays mean nothing was declared.
 */
export interface AccessSpec {
  space_permissions: SpacePermissionEntry[];
  uc_principals: AccessPrincipal[];
}

export interface Review {
  findings: Finding[];
  gate: Gate;
  eval: EvalResult;
  allowlist_violations: string[];
  consumer_group: string;
  timeline: TimelineStep[];
  /** The declared AccessSpec (F2) — present once the engine always returns it; optional here so an
   * older cached/recovered review payload (pre-F2) still type-checks. */
  access_spec?: AccessSpec;
}

/** F3: the self-service access-request state machine. `applied` is distinct from `approved` —
 * the grant is only actually queued once the governed sidecar PR has been opened. */
export type AccessRequestState = 'requested' | 'approved' | 'denied' | 'applied';

/** F3: one self-service access request — a user asking for access to a Space they can't use. */
export interface AccessRequest {
  id: string;
  space_id: string;
  space_title: string | null;
  /** The requester's platform-VERIFIED identity at request time (never a display header). */
  requester_email: string;
  note: string | null;
  want_space_permission: boolean;
  space_permission_level: string; // 'CAN_RUN' | 'CAN_VIEW'
  want_uc_select: boolean;
  state: AccessRequestState;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  /** The governed sidecar-update PR the approval opened (F2's apply path), once applied. */
  pr_number: number | null;
  pr_url: string | null;
  created_at: string;
  updated_at: string;
}

/** F3: one append-only access-request audit event. `actor_email` is ALWAYS the verified acting
 * identity (requester on `requested`, approver on `approved`/`denied`/`applied`/`apply_failed`). */
export interface AccessRequestAuditEvent {
  seq: number;
  event_type: string;
  occurred_at: string;
  actor_email: string;
  detail: Record<string, unknown> | null;
}
