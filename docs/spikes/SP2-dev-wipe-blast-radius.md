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

## What is NOT knowable from here (and why it doesn't matter)

Whether the ~7-day event is (a) a **data/resource reset of a persistent workspace** (same host
`fevm-cerc-mlops-dev…`, contents reset) or (b) a **full teardown + redeploy** (new workspace id)
cannot be determined without the FEVM platform docs/owner or watching a cycle. There are signals for
both (the host looks stable → (a); FEVM "days remaining" + vending SPs → (b)).

**It doesn't change the design.** Model (b) is strictly more destructive than (a), and the mitigation
for (b) (re-assert all workspace-local state idempotently) fully covers (a). So we design for (b).

## Blast radius — the engineering model to build against

| State | Scope | Survives a wipe? | Consequence |
|---|---|---|---|
| Dev SP **identity** (`applicationId` + SCIM id) | Account | **Yes** | The SP need not be re-created; reuse-or-create like `provision_ci.sh`. |
| Dev SP **OAuth M2M secret** | Account (on the SP) | **Yes** | The stored credential stays valid; no secret rotation forced by the wipe. |
| Regional **metastore** | Account/region | **Yes** | UC namespace root persists. |
| SP **workspace access assignment** | Workspace | **Assume NO** | SP may lose the ability to even reach dev → re-assign every cycle. |
| SP **workspace permissions** (warehouse `CAN_USE`, app `CAN_USE`, cluster) | Workspace | **Assume NO** | Re-grant every cycle. |
| **UC grants** on `dev_recebiveis` (`USE_CATALOG/SCHEMA`, `SELECT`, Genie space perms) | Metastore, but tied to the catalog | **Assume NO** | If the catalog is dropped/recreated by the wipe, its grants vanish → re-assert after the seed recreates the catalog. |
| Authored **Genie Spaces** in dev | Workspace | **NO** (confirmed) | Recovered via A3 rehydrate. |

## Implication for A2 (dev-SP bootstrap) — the actionable output

The dev-reader/writer SP bootstrap must be **fully idempotent and re-runnable as a self-heal**, not a
one-time provision. Concretely, `scripts/provision_dev_sp.sh` (net-new — no existing script covers the
**dev** side; `provision_ci.sh` covers only the prod CI SP):

- **Reuse-or-create** the SP by display name (mirror `provision_ci.sh`, capturing both
  `applicationId` for UC grants and the numeric SCIM id for the secrets API — they are not
  interchangeable).
- **Re-assert every workspace-local grant on every run** (idempotent, additive updates so a re-run is
  a no-op when state is intact): workspace access assignment, warehouse `CAN_USE`, and the UC grants
  the SP needs for Genie read/write. Use additive permission updates (not set/replace) as
  `provision_ci.sh` already documents for warehouses.
- **Config-driven per ADR-0004** — no hardcoded workspace/customer; a second customer changes config
  only. Safe to run repeatedly and after each wipe (schedule it, or make it a documented post-wipe
  step, or trigger it from the rehydrate precondition below).
- Note the SP is **inherently broad** (Genie has no per-Space delegation) → keep it least-privilege at
  the workspace level and lean on A2's `assert_can_access` app-layer guard, and add audit/anomaly
  monitoring (per the PRD).

## Implication for A3 (rehydrate preconditions)

Rehydrate is exactly the operation that runs **right after** a wipe (reseed dev), so it is the most
likely victim of a stale/de-authorized SP. A3 must:

- **Precondition-check** the dev SP's reachability + required grants **before** attempting
  `create_space`/`update_space`, and on failure return a clear, actionable error — *"dev environment
  was wiped; run `provision_dev_sp.sh` to re-bootstrap the dev service principal"* — **not** a
  confusing mid-operation 403/permission error (the issue's named risk: "fails confusingly exactly
  when it's most needed").
- Preferably **self-heal**: allow rehydrate to (re-)invoke the idempotent bootstrap, or clearly
  document the manual re-bootstrap step as a rehydrate precondition.
- Assume `dev_recebiveis` may need `seed_recebiveis.py` to have run first (the rehydrated Space's SQL
  targets `dev_recebiveis.*`); surface that as a precondition too.

## Recommendation

**Proceed with A2/A3.** No open decision blocks estimation: build the dev-SP bootstrap idempotent +
self-healing, and gate rehydrate on an explicit dev-SP precondition check. If a definitive blast-radius
answer is later wanted, confirm the FEVM lifecycle model with the FE-VM platform owners — but it is not
required to build A2/A3 safely.
