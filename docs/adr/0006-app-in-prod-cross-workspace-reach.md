# The app relocates to PROD as a durable control plane and reaches INTO dev; supersedes ADR-0002's "dev front door" framing

**Status:** accepted (2026-07-02)

## Context

ADR-0002 framed the app as living in the **dev** workspace: a zero-deploy-rights front door that
reads dev, opens PRs, and lets a distinct SP `bundle deploy` into prod. That was correct for a
one-way promotion tool. The Genie Governance Console PRD (`.scratch/genie-governance-console/PRD.md`)
turns the app into a **two-way governance control plane** — durable audit, roles, access requests,
and (later, F1) pulling a promoted Space back into dev. None of that survives if the app's own
state lives in dev, because **dev is a field-eng vending-machine (FEVM) workspace wiped on a bounded
~7-day cycle** (confirmed by SP2). A control plane that gets wiped every week isn't a control plane.

Two spikes gated this decision before it was estimated:
- **SP1** (`.scratch/genie-governance-console/issues/done/SP1-genie-create-api-spike.md`): GO — the
  Genie create/import API round-trips a `serialized_space` **including `benchmarks.questions`**
  losslessly (24/24, ids preserved); overwrite uses `update_space(..., etag=...)`; create mints new
  ids (not identity-idempotent). This unblocks F1 (a later slice) but doesn't change A1.
- **SP2** (`.scratch/genie-governance-console/issues/done/SP2-dev-wipe-blast-radius-spike.md`):
  account-level identity (an SP + its OAuth secret, the shared metastore) **survives** the wipe;
  **workspace-local** state (an SP's workspace access/permissions, UC grants) is **ephemeral** and
  must be re-asserted. This directly shapes the dev-SP bootstrap (A2) and rehydrate preconditions
  (A3) — see Decision 4 below.

## Decision

1. **Relocate the app + its Lakebase instance to the `prod` DABs target.** Prod is the durable
   control plane going forward. This is mechanically a near cut-paste (host already resolves from
   `DATABRICKS_HOST`/profile, never the bundle file — ADR-0004; the `database_instances`→`apps`
   deploy-ordering edge is preserved verbatim). Dev keeps only the `setup` job (idempotent seed data).
   **Supersedes ADR-0002's "app lives in dev" framing** — ADR-0002's SoD decision (Requester ≠
   Approver ≠ deploying SP; the app never itself runs `bundle deploy`) is UNCHANGED and still holds.

2. **Cross-workspace client factory, one tested place.** `app/app_logic.py::_client(scope=...)`
   generalizes the old 3-way selector (OBO / local profile / app SP) into a 4th branch:
   - `scope="prod"` (default): the workspace the app runs in — now prod. OBO via
     `user_token`, a local CLI `profile`, or the app's own SP (unchanged behavior/back-compat).
   - `scope="dev-sp"`: the DEV workspace, reached via a **standing dev-reader/writer service
     principal** — host from `APP_DEV_HOST` (config-driven, ADR-0004), credentials read at call
     time from the `APP_DEV_SP_SECRET_SCOPE` secret scope (never a bundle variable, never in code).

   **This reverses ADR-0002's implicit assumption that no long-running service holds cross-env
   write rights** (the CI/CD SP was the only such identity, and it only ever ran from short-lived
   GitHub Actions jobs). The dev-reader/writer SP is a **standing, long-running credential with
   dev-side Genie-API write reach**, held by an always-on app. We accept this explicitly:
   - It is **named and scoped** to the Genie APIs only (no UC/warehouse/other grants) — a
     compensating application-layer control, since Genie itself has no per-Space delegation (the
     SP can reach every dev Space by platform necessity).
   - It is **audit-distinguishable**: every dev-touching action records the live, OBO-verified
     acting identity (A2) distinctly from the fact that the SP performed the call — so an incident
     review can tell "a verified user triggered this via the SP" from "the SP was misused."
   - The **real security boundary is A2's live, fail-closed per-user authorization guard**, not the
     SP's own restraint — this ADR names the trust relationship; A2 builds the control that makes
     it safe. Until A2 lands, the dev-sp client path in A1 is wired but not exercised (no SP exists
     to authenticate as yet).

3. **[STAKEHOLDER — CONSIDERED, NOT ADOPTED] A dedicated 3rd control-plane workspace.** Raised in
   review as an alternative that would decouple the governance app's uptime/blast-radius from prod
   and generalize better to a future 3-tier (dev/staging/prod) customer. **Recorded here per the
   PRD's requirement; prod-hosting is the default and we proceed with it** — the 3-reviewer team
   judged prod-hosting acceptable, and a 3rd workspace adds an environment to provision/secure/pay
   for with no customer asking for it yet. Rationale to revisit this if/when a customer's topology
   genuinely needs the isolation (e.g. prod itself has a stricter change-freeze than the governance
   console should be subject to): a 3rd workspace removes the app from prod's blast radius entirely
   and would let the client factory add a 3rd scope (`scope="prod-remote-sp"`) with the same shape
   as `scope="dev-sp"` — the factory design already generalizes to N remote workspaces, not just one,
   so this pivot is cheap later if warranted. **[STAKEHOLDER]: confirm prod-hosting vs. a dedicated
   control-plane workspace** — non-blocking; work proceeds on prod-hosting.

4. **Dev-SP bootstrap must be idempotent self-heal, not one-time provisioning (per SP2).** Because
   workspace-local grants are ephemeral across the dev wipe but the SP's account-level identity
   survives, A2's `scripts/provision_dev_sp.sh` must re-assert the dev SP's workspace access +
   Genie-scoped permissions on every run (mirroring `provision_ci.sh`'s idempotent-ish pattern, but
   net-new — no prior equivalent exists for a dev-side SP). A3's rehydrate flow must
   precondition-check the dev SP before calling create/update and fail with an actionable
   "re-bootstrap the dev SP" message rather than a confusing raw API error, since a rehydrate is
   exactly the moment a fresh-wiped dev's SP grants are most likely to be stale.

5. **Fresh-customer bootstrap order.** For a NEW customer/deployment, the prod `apps` +
   `database_instances` resources (this ADR) must deploy — and the app must start successfully
   against a migrated, empty Lakebase — **before** any governance feature (access requests, admin
   console, roles) is usable. The dev-SP bootstrap (A2) is a separate, later bootstrap step scoped
   to unlocking F1/A3, not a precondition of A1's app-in-prod milestone.

6. **Dev-Lakebase-state cutover: fresh start, no migration.** The dev workspace's prior Lakebase
   instance (`genie-promote`, dev target) held **disposable demo state** — promotions/audit records
   accumulated during the single-workspace build, against a workspace that is itself wiped
   periodically. Prod's new Lakebase instance starts **empty**; idempotent startup migrations
   (ADR-0005) create the schema fresh. No data migration is performed or planned — there is nothing
   in the dev store worth carrying forward, and building a one-time migration path for throwaway
   demo data isn't justified. The dev Lakebase instance itself is decommissioned when the `dev`
   target's `database_instances`/`apps` resources are removed from a bundle deploy (this ADR).

7. **Prod Lakebase direct-access control.** The prod Lakebase instance now holds the governance
   system's most sensitive data — who requested/approved what, the full audit trail, (later)
   access-request and role state. This is **separate from the app's own role-gating** (which governs
   what a user sees *through the app*): it is about who may connect to the Postgres instance
   **directly**, bypassing the app entirely. Decision: **only the app's own service principal** (for
   normal operation) **and a named, config-driven admin allowlist** (`lakebase_direct_access_admins`
   bundle variable, comma-separated emails/SP names — empty by default) may connect directly.
   Enforcement is via Lakebase's own Postgres role grants (`CAN_CONNECT_AND_CREATE`/read-only
   variants), applied out-of-band from the bundle today (Lakebase does not yet expose per-principal
   connect grants as a DABs-declared resource) — this is a **documented manual step** until the
   platform adds one; the config variable exists now so the intended set is versioned and reviewable
   even though its enforcement isn't yet fully declarative. No one queries the instance directly for
   routine operation; direct access is an operator/incident-response escape hatch, not a workflow.

8. **Least privilege for the Business Author.** A Requester authoring/promoting Genie Spaces needs
   `CAN_USE` on the `genie-promote-app` App resource **only** — no broad prod catalog/schema grant.
   This is standard Databricks Apps OBO (the app's own OAuth scopes carry the user's Genie
   permissions through to the Genie API calls it makes on their behalf); no new mechanism is
   introduced. Applied as a workspace-level App permission post-deploy (not bundle-declared here, to
   avoid the bundle clobbering a platform-managed ACL on redeploy — see README for the exact command).

## Consequences

**Positive:** governance state survives the dev wipe; the client factory generalizes cleanly to a
future 3rd remote workspace if ever needed; the SP2 finding is designed for up front (self-heal, not
a demo-day surprise); the trust relationship the design relies on is named and its compensating
control (A2) is explicit rather than assumed.

**Negative / trade-offs:** a standing, long-running SP with dev-side write reach is a genuinely new
class of risk this system didn't have before (accepted, Decision 2); prod Lakebase direct-access
enforcement is a manual step today, not a DABs-declared one (Decision 7); the 3rd-workspace
alternative (Decision 3) is deferred without a trigger condition beyond "a customer asks."

**Alternatives rejected:** (a) **app stays in dev, reaches prod** — fails outright, the wipe erases
the app + its state, which is the exact problem this ADR solves; (b) **Lakebase migration from the
old dev instance** — not worth building for disposable demo data (Decision 6); (c) **a 3rd
control-plane workspace now** — deferred, not rejected outright (Decision 3).
