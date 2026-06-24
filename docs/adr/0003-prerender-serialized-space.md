# A pre-render step over `serialized_space` is mandatory (DABs `${var}` doesn't reach inside it)

**Status:** accepted (2026-06-22)

DABs variable substitution does **not** interpolate inside the `serialized_space`
JSON that defines a Genie Space, so promotion cannot rely on `${var}` the way
`bimbo_demo` did for catalog-per-env. A small, deliberately-slim **CI pre-render
step (Jinja/`envsubst`) over the JSON is required** before `bundle deploy`:
swap `dev_<domain>.` → `prod_<domain>.` table prefixes and re-point `warehouse_id`
— nothing else. This is the single piece of bespoke logic in the whole system.

## Consequences
- Keep it minimal (prefix + warehouse only); the native `genie_spaces` DABs
  resource owns Space identity and permissions.
- A **deterministic identifier allowlist** (`^prod_<domain>\.`) must run after
  pre-render and **reject any `dev_`/`sbx_` leak**, including catalog names
  hard-coded inside certified/example SQL snippets.
- The native `genie_spaces` resource + Genie Management API round-trip is **≤weeks
  old** — every reliance on it is marked **[VERIFY in-console]** and must be
  soaked before Acme trusts it against the 538-prod estate.
- **Allowlist semantics (S4):** pre-render auto-rebinds the *same-domain* `dev_`→
  `prod_`, so a same-domain `dev_` ref does NOT survive to fail the allowlist —
  it is corrected by design. The deterministic allowlist (`pre_render.py find_violations`)
  is therefore the net for **foreign-env** (`sbx_`, uppercase, backtick-quoted) and
  **cross-domain / unrelated catalogs** (`main`, `samples`) on the rendered output.
  A *semantic* dev-leak that shouldn't be promoted at all is the **agent's** job
  (GRANT-01 / ENV-01, S5) — that's the human-judgment layer the deterministic gate
  can't replace.
