# Domain glossary — genie-promote

The shared language of the promotion accelerator. Terms here are meaningful to domain experts;
implementation details live in code + ADRs (`docs/adr/`).

## Core terms

- **Promotion** — one governed attempt to move a resource (today a Genie Space) from a dev workspace
  to prod, through: review → Change Request → merge gate → deploy gate → deploy. **Grain: 1:1 with a
  Change Request.** Re-requesting while it is open stays the SAME Promotion (adds a new Review
  Snapshot + a `re-reviewed` audit event; the Change Request is updated in place). Once it
  merges/closes the Promotion is terminal; a later promotion of the same resource is a NEW
  Promotion. A Promotion may carry multiple Review Snapshots over its life.

- **Change Request** — the provider-owned, human-reviewed change unit for a Promotion. In the pilot
  it is a GitHub pull request; a future provider can supply an equivalent without changing Promotion
  semantics. The app stores a provider discriminator, opaque external id, URL and immutable content
  + engine revisions rather than making a PR number the domain identity.

- **Resource** — a promotable Databricks artifact (Genie Space today; AI/BI dashboards and others are
  anticipated via a kind registry). The thing a Promotion promotes.

- **Requester** — the human who initiates a Promotion (the Author). In the app their identity is the
  OBO email, which is **display-only**; the authoritative governance identity is the Change Request
  provider's actor (a GitHub actor in the pilot).

- **Steward** — the distinct reviewer under separation of duties (ADR-0002). Approves on GitHub (the
  PR review that allows the merge, and the prod deployment gate). The Steward is never the Requester;
  `prevent_self_review` enforces this on GitHub identity. In-app, Steward is one of three **Roles**
  (below) resolved from the caller's platform-verified identity — there is no persona/self-declared
  toggle.

- **Role** (Steward / Admin) — an assignment that grants an app capability in addition to the
  implicit Business User experience. Steward monitors promotions and approves through the Change
  Request provider; Admin maintains app configuration and audit visibility.

- **App Maintainer (KIP)** — the team accountable for keeping the Promotion App operational and for
  its maintenance and future improvements. This is product/runtime ownership, distinct from deciding
  whether a specific Promotion should be approved.

- **Adoption Owner (GestOps)** — the team accountable for publicizing the Promotion App and guiding
  business users in how to use it. GestOps owns adoption and first-line usage orientation, not the
  app's code/runtime maintenance.

- **Review Snapshot** — the immutable reviewer result captured when a Promotion is requested:
  findings, the gate verdict, the eval result, and the pipeline timeline. It is the AI-generated
  assessment at a point in time. **Persisted with the Promotion** so reopening a Promotion renders the
  same result WITHOUT re-running the LLM reviewer. A re-review (after a fix) produces a new snapshot.

- **Promotion Phase** — where a Promotion currently is in the flow (open / checks running / PR review /
  merged / awaiting deploy approval / deploying / deployed / failed). **Derived live from GitHub** —
  it reflects GitHub state, never asserts it.

- **Deployment Attempt** — one approved execution of a Promotion's production workflow. A Promotion
  can have multiple Attempts when an operational failure is retried; each Attempt records its exact
  revision, completed stages and target identifiers. GitHub is authoritative for its live state.

- **Preflight** — the mutation-free validation of the exact content, audience, identities and live
  target assumptions an Attempt needs. It runs both on the PR and immediately after approval; any
  failure here guarantees that production was not changed by that Attempt.

- **Partial Deployment** — an Attempt that failed after at least one production mutation. It is
  neither “not deployed” nor “deployed”: its completed stages and failed stage are shown explicitly,
  and recovery replays the idempotent flow forward from Preflight.

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

## Cross-workspace identity (ADR-0006)

The app runs in **prod** (a durable control plane) but every author's Genie Space lives in **dev** —
so a family of terms exists for how the app reaches across that boundary safely.

- **dev-reader/writer SP** — the standing service principal the app uses to actually TRANSPORT every
  dev-side Genie call (list/export/rehydrate). Necessary because OBO cannot span workspaces once the
  app is prod-hosted — there is no forwarded dev token to act "as the user" with. Scoped to the
  Genie APIs plus the minimum UC/warehouse reach an export needs to function (warehouse `CAN_USE`,
  `USE_CATALOG`/`USE_SCHEMA` on `dev_<domain>` — no `SELECT`, no other catalogs); provisioned +
  re-asserted idempotently by `scripts/provision_dev_sp.sh` (a **self-heal**, since dev's
  workspace-local grants — but not the SP's account-level identity — are lost on dev's periodic wipe).
- **Confused-deputy risk** — because the dev-reader/writer SP can reach *every* dev Space by platform
  necessity (Genie has no per-Space delegation), it alone is not a safe authorization boundary: any
  authenticated app user could otherwise ask the app to touch any dev Space via the SP's reach.
- **Verified identity** — the platform-attested identity behind a caller's OBO token, resolved by
  asking the workspace itself "whose token is this" (`authz.verify_identity` →
  `WorkspaceClient(token=...).current_user.me()`) rather than trusting the proxy-forwarded
  `x-forwarded-email` header as an authorization input (display-only, easily-named-but-never-trusted
  distinction). Built fresh per request — never cached beyond it, so a revoked token stops
  authorizing immediately.
- **`assert_can_access`** — the single, fail-closed, never-cached, per-user guard (`app/authz.py`)
  that decides whether a Verified Identity may touch a given dev Genie Space, checked via the dev SP
  as pure transport (never as the authorization source itself). This is the compensating control that
  makes the dev-reader/writer SP's broad reach safe — see
  `docs/security/assert-can-access-threat-model.md`.

## Access & governance

- **AudienceSpec / Público do Space** — the authoritative desired set of users and groups who should
  be able to run the promoted Space. It is committed as `<slug>.audience.json`; entries carry no
  permission level because the pipeline always derives Genie `CAN_RUN`. It never requests or applies
  UC grants. The pipeline reconciles only audience ACLs previously managed by this declaration and
  preserves owners, technical identities and unrelated ACLs.
- **AUDIENCE-01** — the deterministic, validation-only check of whether every AudienceSpec principal
  has effective `SELECT` on every rendered production table. Missing `SELECT` is informational and
  points to CERC's Terraform access queue; it never blocks promotion. Invalid tables/principals
  block preflight, while inability to query the grants API is an operational failure, not an author
  finding. There is no static baseline consumer group.
- **Technical Space access** — app, dev-transport and CI identities receive the minimum technical
  permissions their jobs require (`CAN_MANAGE` where a live ACL/export/update needs it). These grants
  are operational controls, never members of AudienceSpec. The prod CI SP temporarily retains UC
  `MANAGE` during the pilot solely to inspect effective grants; no app or pipeline path mutates UC.
- **Rehydrate** — pulling an already-promoted **prod** Genie Space back into **dev**, with no git PR
  (A3/G6; `app/rehydrate.py`). Works for ANY prod Space the caller can access, not only ones this app
  promoted (the prod store starts empty per ADR-0006). Gated at BOTH blast sites with
  `assert_can_access` — the source (prod) read, and, on overwrite, the target (dev) Space — so a
  caller can never clobber a dev Space they personally can't reach even though the standing dev SP
  could. Produces either an `audit_events` "rehydrated" row (when linked to a Promotion) or a
  standalone **Rehydrate Event** (below) when it isn't.
- **Rehydrate Event** — a standalone audit row (`promotion_store.RehydrateEvent` /
  `rehydrate_events` table) for a Rehydrate with no linkable Promotion — no FK, but the same
  acting-identity + "the dev SP holds a broad standing grant" facts as a Promotion-linked one.
  Surfaced with the other admin audit evidence in **Auditoria**.
- **Table de-para** — an optional, explicit remap of a source table reference to a different target
  (source ref → desired ref), reviewed by the caller before they commit. Two directions, two
  enforcement points: **rehydrate's** de-para (G6) widens Rehydrate's OWN dev-side allowlist to the
  catalogs the caller chose (refusing outright a target that resolves back to prod); **promotion's**
  de-para (G7, a `.mapping.json` sidecar) is only ever a DECLARATION in the app — CI's `render.sh`
  applies it, and the prod allowlist stays UNWIDENED there, so a mapped target outside
  `prod_<domain>` still fails `pr-checks` exactly like an un-mapped leak would.
- **Rule override** — an admin-configured change to the reviewer's rule set (G2, **Configurações**
  screen): either override severity/content/citation on one of the 9 hardcoded handbook rules, or add
  a fully custom rule alongside them. Persisted in Lakebase (`rule_overrides` + an append-only
  `rule_audit_events`); merged purely (`rules_config.effective_rules`) into the LLM's prompt context.
  AUDIENCE-01 is deliberately NOT configurable this way (CI owns the authoritative check and has no
  Lakebase access); EVAL-01's benchmark-count floor is the one deterministic exception with a tunable
  parameter (`min_benchmarks`).
