# SP1 — Genie create/import API spike (findings)

**Verdict: GO.** A Genie Space can be created/imported programmatically from a `serialized_space`
payload, and `benchmarks.questions` **round-trip losslessly through create** — including their ids.
A3 (prod→dev rehydrate) can build on this primitive without re-authoring benchmarks.

- **Date:** 2026-07-02
- **Verified against:** dev workspace (`cerc-mlops-dev`), `databricks-sdk==0.111.0`, CLI `v1.4.0`
- **Method:** author → export → create → re-export → compare, then update/overwrite + second-create,
  all against a **throwaway dev Space that was trashed afterwards** (no prod mutation).

## Background — why this was a risk

The repo has **never created or imported a Genie Space live**: every write goes through the
declarative DABs `genie_spaces` resource (`file_path` → pre-rendered `serialized_space`), and
`create_space()` was referenced only in an `app_logic.py` docstring — the function did not exist.
The prior project lore (`CLAUDE.md` "gotchas") stated *"benchmark questions are UI-authored… no CLI
to create."* F1/A3 needs a live create/import primitive, so it had to be proven before A3 is
estimated.

## The primitive exists (SDK 0.111.0)

`WorkspaceClient.genie` exposes the full lifecycle:

| Call | Signature | Role in A3 |
|---|---|---|
| `get_space` | `get_space(space_id, *, include_serialized_space=None) -> GenieSpace` | Export the prod Space. `include_serialized_space=True` **requires CAN EDIT** on the space. |
| `create_space` | `create_space(warehouse_id, serialized_space, *, description=None, parent_path=None, title=None) -> GenieSpace` | **Create-new** rehydrate: mints a brand-new dev Space from the (rebound) payload. |
| `update_space` | `update_space(space_id, *, serialized_space=None, warehouse_id=None, title=None, description=None, parent_path=None, etag=None) -> GenieSpace` | **Overwrite** rehydrate: full-replacement of an existing dev Space. |
| `trash_space` | `trash_space(space_id)` | Cleanup / undo. |

There is **no** import-by-logical-key call: create always mints a new `space_id`.

## Evidence — benchmarks round-trip losslessly

Source: dev demo Space `01f16e8322661161a83f7d1f2a1bec14` ("Receivables and Merchant Data"),
which now carries **24** `benchmarks.questions` (not 2 — it has grown since the old notes).

`serialized_space` top-level shape: `{version, config, data_sources, instructions, benchmarks}`.
`benchmarks.questions[]` = `{id, question:[...], answer:[{format:"SQL", content:[...]}]}`.

Round-trip result (export → `create_space` → re-export → compare):

```
count_match=True  question_text_match=True  sql_match=True  ids_preserved=True
```

All 24 benchmark ids, question texts, and SQL answers came back **byte-identical**. The old
"benchmarks are UI-only, not importable" gotcha is **obsolete for this SDK** — record the correction.
`config` and `version` (the other two top-level keys) were present in the payload and survived
create + re-export unchanged, with no create-time validation error.

## Evidence — rebind-then-create (A3's actual code path)

The identity round-trip above pushed the payload back **unmodified**. A3 never does that: on every
invocation it exports a **prod** Space and runs `pre_render.rebind(prod_→dev_)` before creating in
dev. So the spike was re-run through the real rebind function to prove that path:

`export dev demo → rebind dev_→prod_ (simulate a prod-shaped export) → pre_render.rebind(prod_→dev_)
→ create_space in dev → re-export`. Result:

```
rebound_dev tables: ['dev_recebiveis.diamond.dim_arranjo', 'dim_cedente', 'fato_recebiveis']
prod_ leaked into rebound payload? False        dev_ present? True
create accepted rebound payload -> re-export tables all dev_recebiveis.* (no prod_recebiveis)
benchmarks count 24    first benchmark SQL references dev_recebiveis (not prod_)
top-level keys survived: [version, config, data_sources, instructions, benchmarks]
```

So `create_space` accepts a `pre_render`-rebound payload and the resulting Space is **structurally**
dev-bound (all `data_sources` identifiers + benchmark SQL point at `dev_recebiveis`). **Caveat
(untested):** Genie does **not** validate SQL/catalog references at ingestion, so a *semantically*
broken rebind would still return `200 OK`. This spike proves the payload is structurally correct and
accepted; A3 must still confirm the rehydrated Space actually **answers** a benchmark against dev data
(run one benchmark question e2e) before calling rehydrate proven — see "Known gaps" below.

## Create vs. overwrite semantics

- **`create_space` is not identity-idempotent.** A second `create_space` with the *same* payload
  produced a **different** `space_id` (`…ed4812…` vs `…f01510…`). Re-running create N times yields N
  Spaces. So "rehydrate again" must NOT loop on create.
- **Overwrite = `update_space(space_id, serialized_space=…)`** — this replaces the target's
  serialized layout. **But `update_space` is PATCH, not PUT** (SDK source: `PATCH
  /api/2.0/genie/spaces/{id}`, and the request body is built only from the fields you pass —
  `if serialized_space is not None: body["serialized_space"]=…`, same for `title`/`warehouse_id`/
  `description`). So omitting `title`/`warehouse_id`/`description` **leaves them unchanged
  server-side** — only `serialized_space`'s own content is fully replaced. **A3's overwrite mode must
  explicitly pass every field it wants reset** (e.g. `title`, `warehouse_id`); it cannot rely on
  "send `serialized_space` and everything else follows."
- **Optimistic concurrency:** `update_space` accepts an optional `etag` (from a prior GET/UPDATE);
  when set, the update fails if the Space changed since — omit to overwrite unconditionally. A3
  should pass the etag it read to avoid clobbering a concurrent edit.
- **Warehouse binding is a separate parameter.** `create_space` **requires** `warehouse_id`; the
  `serialized_space` itself carries no warehouse (that's `${var.warehouse_id}` on the DABs resource
  today). So rehydrate must supply a **dev** `warehouse_id` explicitly (config-driven per ADR-0004),
  independent of the `prod_`→`dev_` identifier rebind.

## Implications for A3 (scope — no re-estimation blocker)

1. **No benchmark re-authoring.** Rehydrate carries `benchmarks.questions` across for free. Drop the
   contingency in A3's acceptance criterion ("if the API can't import benchmarks…") — it can.
2. **Two modes, both native:**
   - *create-new* → `create_space(dev_warehouse_id, rebound_serialized, title=…)`
   - *overwrite* → resolve the target dev `space_id`, then `update_space(space_id, serialized_space=rebound, warehouse_id=dev_warehouse_id, etag=…)`
3. **Target resolution is the guard's blast site.** Because create mints new ids and overwrite names
   an existing id, "overwrite" must resolve *which* dev Space — this is exactly where A2's single
   `assert_can_access` guard (verified identity, never a display header) must gate, so a caller can't
   clobber a Space they can't access. (A3 acceptance already calls for this.)
4. **Permissions (assumed, NOT verified in this spike):** the SDK docstring says
   `include_serialized_space=True` "Requires at least CAN EDIT permission on the space," matching
   Databricks' general convention — but this spike did **not** empirically test the negative case (a
   CAN VIEW-only credential getting a 403). A2 should **verify this empirically** before granting the
   dev/prod SP only CAN EDIT (vs. CAN MANAGE): the export leg needs read-with-serialized on the source
   **prod** Space; the standing dev-writer SP needs create/update in dev.
5. **Dev warehouse id** must be config-driven (ADR-0004); reuse the existing warehouse-id var pattern.
6. **Folder placement:** `parent_path` was omitted in this spike (throwaway Spaces landed in the
   caller's default location). A3 should **pin an explicit dev target folder** rather than rely on
   default placement — it affects discoverability and the dev-SP blast-radius (A2).

## Known gaps for A3 (proven-enough to build, verify these during A3)

The GO verdict stands — these do not change the primitive, but A3 must handle them:

1. **Overwrite is PATCH.** A3's overwrite path must pass every field it wants reset (title, warehouse),
   not assume `serialized_space` alone resets the whole resource (see "Create vs. overwrite semantics").
2. **Semantic validity is unproven.** Rebind-then-create is structurally correct + accepted, but Genie
   does no ingestion-time SQL/catalog validation. A3's e2e must run one benchmark against dev data to
   prove the rehydrated Space actually answers (not just that JSON round-trips).
3. **CAN EDIT requirement is assumed, not tested** — A2 must verify the exact permission the SP needs.
4. **Not exercised (bounded-risk, same-org dev/prod, small payload):** message attachments, cached
   sample values, payloads much larger than the 24-benchmark demo Space, and non-default `parent_path`.
   None are expected to break create, but A3 should not assume they were covered here.

## Reproduction

Against dev only, using the method table above: `get_space(SRC, include_serialized_space=True)` →
`create_space(warehouse_id=<dev_wh>, serialized_space=<payload>, title="…")` → re-`get_space` the new
id → compare `benchmarks.questions` → `trash_space(new_id)`. The throwaway spaces created during this
spike (`01f17659ed4812abb2c6f72ad7de5c4f`, `01f17659f015106ba8d35645aab0475d`, and the
rebind-create space `01f1765aba6b1ca3ae6742dcbd3cc0ae`) were all **trashed**.
