# Lakebase is a hard-required index/audit/cache over GitHub-as-truth (not a write-model)

- Status: accepted
- Date: 2026-06-26

## Context

The promotion flow's live state (PR checks, merge, the prod Environment gate, the deploy run) lives
on GitHub, and the app was built "reflect, never assert" — it reads GitHub on demand and holds no
state. That gave correctness but no persistence: no recovery across reloads (a reload re-runs the
LLM reviewer), no per-user history, and no durable, queryable audit trail — which a production app
and an OSS accelerator for multiple customers both need.

Adding a database forces a choice: does it become the **source of truth** for promotion state (a
write-model that GitHub events sync into), or a **secondary store** that indexes/audits/caches over
GitHub-as-truth?

## Decision

Lakebase (Databricks Postgres) is a **durable index + append-only audit log + status cache**, and a
**hard dependency** of the app. **GitHub remains the source of truth** for live verdicts. Lakebase
never decides that a check/merge/approval/deploy happened — it records, recovers, caches, and
**self-heals from GitHub** via a single idempotent `reconcile` function (run on every status read and
by a scheduled reconciler). A Promotion is **1:1 with a PR**; its **Review Snapshot** is persisted so
reopening never re-runs the reviewer. Governance attribution in the audit uses **GitHub-sourced
identities** (the OBO email is display-only). Provisioned **per deployment via DABs**, app connects as
its **SP via Lakebase OAuth**, schema via **idempotent startup migrations** (ADR-0004 portable).

## Consequences

**Positive:** preserves "reflect, never assert" (no dual-source drift, no sync pipeline to reconcile
a write-model); recovery without re-running the LLM; complete, queryable, GitHub-attributed audit;
self-healing; portable + per-deployment isolated for the OSS accelerator.

**Negative / trade-offs:** Lakebase is now on the app's uptime path (hard dependency — fails fast if
misconfigured, by choice). The audit trail is a *mirror*, so without webhooks it can lag real time
between reconciles (mitigated by the scheduled reconciler; GitHub's own timeline remains the ultimate
record). Backfilled events are stamped at observation time (GitHub's own timestamp recorded when
available). Webhooks/real-time push are deferred to a future ADR.

**Alternatives rejected:** (a) **Lakebase as the write-model / source of truth** — needs webhook
ingestion + reconciliation against GitHub, reintroduces drift, and fights the proven model; (b)
**stay stateless** — no history/audit/recovery, fails the production + compliance bar; (c)
**enhancing-not-required** (degrade to stateless if the DB is down) — rejected: a half-stateful app
is harder to reason about than a hard dependency that fails loudly.
