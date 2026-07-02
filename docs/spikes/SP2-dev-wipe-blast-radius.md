# SP2 — Dev-wipe blast-radius spike (findings)

**Bottom line:** the dev workspace is a **field-eng "vending-machine" (FEVM) workspace with a bounded
lifetime** (~7 days). The exact wipe blast radius is **not authoritatively documented** and can't be
proven from here without observing a cycle — so **A2 must design for the worst case**: treat
everything *workspace-local* (the dev SP's workspace access + permissions + UC grants) as **ephemeral
and re-assert it idempotently every cycle**. Account-level state (the SP identity, its OAuth secret,
the regional shared metastore) **survives**. This is safe under both the "data-reset-in-place" and the
"full teardown+redeploy" models, so the ambiguity does **not** block A2's estimate.

- **Date:** 2026-07-02
- **Verified against:** dev workspace (`cerc-mlops-dev`), read-only.
- **No mutation** — investigation only.

## What is confirmed

1. **FEVM-provisioned, bounded-lifetime workspace.** The dev workspace carries the FE vending-machine
   management identities — read-only SP list shows `vending-machine-sp` and `fevm-sage-sp` alongside
   the app SPs. The FE-VM deployment skill's own phrasing ("find an existing FE-VM workspace with **at
   least 7 days remaining**") confirms FEVM workspaces run on an **expiry countdown**; the "~7-day dev
   wipe" is that FEVM lifecycle event, not an app feature. (Analogous FE sandboxes reap resources on a
   schedule — e.g. the AWS One-Env sandbox "reaps resources over the weekend.")
2. **Genie Spaces authored in dev do NOT survive the wipe.** This is stated in the project context and
   is the entire reason F1/A3 (rehydrate) exists. So the wipe removes at least workspace-scoped assets.
3. **UC *data* is recreated idempotently.** `src/setup/seed_recebiveis.py` rebuilds the
   `dev_recebiveis` schemas + `diamond` tables via `CREATE SCHEMA/TABLE IF NOT EXISTS` — it is the
   existing self-heal for the *data* layer after a wipe.
4. **The metastore is account-level and shared.** Confirmed live: dev's metastore is
   `616f89c2-6a5b-4106-9253-2e6f81df10e4` (`metastore_aws_us_west_2`) — the same regional metastore
   prod uses. A metastore is **not** destroyed by a workspace wipe.

## Empirical signal — persistence observed (a cheap read-only check)

A read-only cross-reference gives real evidence, not just inference: the dev demo Space
`01f16e8322661161a83f7d1f2a1bec14` is the exact id recorded in the project notes on **2026-06-25**.
Today (**2026-07-02**, ~7 days later) it **still exists in dev and has been incrementally edited** (it
now carries 24 benchmark questions vs. the "2" recorded then) — i.e. the same Space object was
*edited*, not *destroyed + recreated*. (Decoding a creation timestamp from the time-ordered id was
inconclusive — the ids don't use a standard UUIDv7 ms-epoch — so the project-record cross-reference is
the reliable signal.)

**Interpretation:** in practice the dev workspace behaves as **persistent / reset-in-place** (model
(a), the *milder* blast radius) within at least a ~7-day window — the "authored Spaces vanish"
framing is looser than literal (workspaces appear to be extended, or the wipe is less aggressive /
hasn't fired). This means the dev SP's identity and grants likely **do** survive in practice.

## What is still NOT knowable — and why it doesn't change the design

We still can't *prove* the wipe never does a full teardown+redeploy (model (b), new `workspace_id`)
without FEVM platform-owner confirmation or watching an actual teardown. **It doesn't matter for the
build.** Model (b) is strictly more destructive than (a), and the mitigation for (b) (re-assert all
workspace-local state idempotently) fully covers (a). So we design for (b) and get (a) for free — the
empirical persistence above just means the self-heal will usually be a no-op, which is exactly how an
idempotent bootstrap should behave.

## Blast radius — the engineering model to build against

| State | Scope | Survives a wipe? | Consequence |
|---|---|---|---|
| Dev SP **identity** (`applicationId` + SCIM id) | Account | **Yes** | The SP need not be re-created; reuse-or-create like `provision_ci.sh`. |
| Dev SP **OAuth M2M secret** | Account (on the SP) | **Yes** | The stored credential stays valid; no secret rotation forced by the wipe. |
| Regional **metastore** | Account/region | **Yes** | UC namespace root persists. |
| SP **workspace access assignment** (permission-assignment binding, keyed to `workspace_id`) | Workspace | Model (a): likely YES · Model (b): **NO** | The assignment references a specific `workspace_id`; a redeploy (new id) orphans it. Re-assert every cycle (no-op if intact). |
| SP **workspace permissions** (warehouse `CAN_USE`, app `CAN_USE`, cluster) | Workspace | **Assume NO** | Re-grant every cycle. |
| **Catalog object** `dev_recebiveis` + its **UC privilege grants** (`USE_CATALOG/SCHEMA`, `SELECT`) | Metastore securable | Survive **unless the catalog is dropped/recreated** | Grants are keyed to the securable; a dropped+recreated catalog = a fresh empty ACL. NB: `seed_recebiveis.py` does `CREATE SCHEMA/TABLE IF NOT EXISTS` but **never `CREATE CATALOG`** — it assumes the catalog exists → re-assert grants after any catalog recreation. |
| **Workspace↔catalog binding** (`dev_recebiveis` is `isolation_mode=ISOLATED`) | Workspace binding | **Assume severed** | Even with catalog + grants intact, an unbound catalog is unreachable from dev (`CATALOG_NOT_FOUND`-class errors). `seed_recebiveis.py` sets this binding **best-effort/non-fatal**, so `provision_dev_sp.sh` must re-check/re-apply the binding — not just re-grant privileges. |
| Authored **Genie Space ACLs** in dev (CAN_EDIT/CAN_MANAGE, create-space entitlement) | Workspace | **Assume NO** | Distinct permission surface (see A2 note) — re-assert. |
| Authored **Genie Spaces** in dev | Workspace | **NO** (confirmed) | Recovered via A3 rehydrate. |

## Implication for A2 (dev-SP bootstrap) — the actionable output

The dev-reader/writer SP bootstrap must be **fully idempotent and re-runnable as a self-heal**, not a
one-time provision. Concretely, `scripts/provision_dev_sp.sh` (net-new — no existing script covers the
**dev** side; `provision_ci.sh` covers only the prod CI SP):

- **Reuse-or-create** the SP by display name (borrow `provision_ci.sh`'s pattern, capturing both
  `applicationId` for UC grants and the numeric SCIM id for the secrets API — they are not
  interchangeable). **The SP must be a plain team-created account SP** (like the existing
  `cerc-mlops-cicd` / `genie-workbench`), **NOT** one minted by the FEVM vending machine (the
  `vending-machine-sp` / `fevm-sage-sp` identities) — if FEVM owns it, FEVM may reap it on teardown
  and the "identity survives" assumption breaks.
- **`provision_ci.sh` only *partially* generalizes** — do not assume it's a template:
  - It grants **UC catalog privileges + warehouse `CAN_USE`** only. The dev SP additionally needs the
    **Genie permission surface** — a workspace-level Genie/create-space entitlement and CAN_EDIT/
    CAN_MANAGE on the Spaces it reads/writes — which `provision_ci.sh` never touches and for which
    there is **no prior art in the repo**. This is the part A2/A3 must get right; budget for it.
  - `provision_ci.sh`'s **secret-minting is NOT idempotent** — it mints a *new* OAuth secret every
    run, silently invalidating the previously-stored one. For a **weekly self-heal** that is a real
    operational trap: `provision_dev_sp.sh` must **mint a secret only if one isn't already valid**
    (or explicitly rotate + re-propagate to the app config), never mint-on-every-run.
- **Re-assert every workspace-local grant on every run** (idempotent, additive updates so a re-run is
  a no-op when state is intact): workspace access assignment, warehouse `CAN_USE`, the Genie ACLs,
  **and re-apply the `dev_recebiveis` workspace↔catalog binding** (not just the privilege grants —
  see the blast-radius table). Use additive permission updates (not set/replace).
- **Config-driven per ADR-0004** — no hardcoded workspace/customer; a second customer changes config
  only. Safe to run repeatedly and after each wipe (schedule it, or make it a documented post-wipe
  step, or trigger it from the rehydrate precondition below).
- Note the SP is **inherently broad** (Genie has no per-Space delegation) → keep it least-privilege at
  the workspace level and lean on A2's `assert_can_access` app-layer guard, and add audit/anomaly
  monitoring (per the PRD).

## Implication for A3 (rehydrate preconditions)

Rehydrate is exactly the operation that runs **right after** a wipe (reseed dev), so it is the most
likely victim of a stale/de-authorized SP. A3 must:

- **Precondition-check the prod-side read first.** A3's flow is export-from-prod → rebind →
  create/overwrite-in-dev. Before anything, the caller must be able to **read/export the source prod
  Space** (A3's own AC: "the caller must be able to access the source prod Space"). Gate this via
  A2's guard on the **prod** side; a caller who can't read the source Space gets a permission error
  **at export**, not a dev-bootstrap message.
- **Then precondition-check** the dev SP's reachability + required grants + the `dev_recebiveis`
  binding **before** `create_space`/`update_space`.
- **Disambiguate the two failure classes — do not collapse them** (collapsing them recreates the very
  "fails confusingly" bug this spike exists to prevent):
  - *SP unreachable / ungranted / catalog unbound* → actionable **re-bootstrap** message: *"dev
    environment was wiped; run `provision_dev_sp.sh`."*
  - *SP is fine but A2's `assert_can_access` denies the calling user* → a normal **403**, NOT a
    bootstrap prompt (the guard is working as designed).
- Preferably **self-heal**: allow rehydrate to (re-)invoke the idempotent bootstrap for the first
  class only, or clearly document the manual re-bootstrap step.
- Assume `dev_recebiveis` may need `seed_recebiveis.py` to have run first (the rehydrated Space's SQL
  targets `dev_recebiveis.*`); surface that as a precondition too.
- **Cross-spike note (SP1):** `create_space` mints a new id and isn't identity-idempotent. Under the
  worst-case teardown model there is **no surviving dev Space to overwrite**, so post-wipe the
  create-vs-overwrite branch always resolves to **create**; "overwrite" is only meaningful when a
  prior dev Space survived (model (a), the observed case). A3 must resolve the target accordingly.

## Recommendation

**Proceed with A2/A3.** No open decision blocks estimation: build the dev-SP bootstrap idempotent +
self-healing, and gate rehydrate on an explicit dev-SP precondition check. If a definitive blast-radius
answer is later wanted, confirm the FEVM lifecycle model with the FE-VM platform owners — but it is not
required to build A2/A3 safely.
