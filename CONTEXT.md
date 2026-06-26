# Domain glossary — genie-promote

The shared language of the promotion accelerator. Terms here are meaningful to domain experts;
implementation details live in code + ADRs (`docs/adr/`).

## Core terms

- **Promotion** — one governed attempt to move a resource (today a Genie Space) from a dev workspace
  to prod, through: review → open PR → merge gate → deploy gate → deploy. **Grain: 1:1 with a PR.**
  Re-requesting while the PR is open stays the SAME Promotion (adds a new Review Snapshot + a
  `re-reviewed` audit event; PR updated in place). When the PR merges/closes the Promotion is
  terminal; a later promotion of the same resource is a NEW Promotion (new PR). A Promotion may carry
  multiple Review Snapshots over its life (latest shown; all retained for audit).

- **Resource** — a promotable Databricks artifact (Genie Space today; AI/BI dashboards and others are
  anticipated via a kind registry). The thing a Promotion promotes.

- **Requester** — the human who initiates a Promotion (the Author). In the app their identity is the
  OBO email, which is **display-only**; the *authoritative* requester identity is the GitHub actor on
  the PR/merge.

- **Steward** — the distinct approver under separation of duties (ADR-0002). Approves on GitHub (the
  PR review that allows the merge, and the prod deployment gate). The Steward is never the Requester;
  `prevent_self_review` enforces this on GitHub identity.

- **Review Snapshot** — the immutable reviewer result captured when a Promotion is requested:
  findings, the gate verdict, the eval result, and the pipeline timeline. It is the AI-generated
  assessment at a point in time. **Persisted with the Promotion** so reopening a Promotion renders the
  same result WITHOUT re-running the LLM reviewer. A re-review (after a fix) produces a new snapshot.

- **Promotion Phase** — where a Promotion currently is in the flow (open / checks running / PR review /
  merged / awaiting deploy approval / deploying / deployed / failed). **Derived live from GitHub** —
  it reflects GitHub state, never asserts it.

- **Audit Trail** — the append-only, ordered record of governance events for a Promotion (requested,
  PR opened, reviewed, merged, deploy approved, deployed, failed), each with actor + timestamp. The
  authoritative actors come from GitHub (the bot/the human GitHub logins), not the display-only OBO
  email.

## Lakebase's role (decided — see Q1; ADR pending)

Lakebase (Databricks Postgres) is a **durable index + append-only audit log + status cache** over the
promotion flow, and is a **HARD dependency** (the app requires it — history/recovery/audit are core,
not optional; no stateless fallback). It is provisioned **per deployment via DABs** (config-driven,
ADR-0004), the app connects **as its service principal via a short-lived Lakebase OAuth token** (no
static secret; an app-SP operation, never the user's OBO token), and the schema self-initializes via
**idempotent startup migrations**. **GitHub remains the source of truth** for live verdicts
(checks/merge/gate/deploy);
Lakebase never decides that a deploy happened — it records, recovers, and caches, and self-heals from
GitHub on the next read. This preserves the "reflect, never assert" guarantee while adding
persistence, per-user history, and a compliance-grade audit trail.

- **Reconcile** — the single server-side function that diffs live GitHub state against a Promotion's
  last-known phase, appends any new Audit Trail events (attributing GitHub-sourced identities), and
  updates the cached status. It is the only writer of audit events (idempotent). Runs **on every
  status read** (self-healing/backfill) and via a **scheduled reconciler** over non-terminal
  Promotions (completeness when no one is viewing). Webhooks → real-time push are a future option.
