#!/usr/bin/env python3
"""Pre-render + identifier allowlist for Genie serialized_space promotion (S4, ADR-0003).

Pure and CONFIG-DRIVEN (ADR-0004): env prefixes and domain are arguments, never
literals — so the same tool promotes any domain across any workspace pair.

Two operations:
  render  — rebind <from_env>_<domain>  ->  <to_env>_<domain>  across the serialized_space
  check   — exit non-zero if any catalog ref isn't the target <to_env>_<domain>
            (catches a dev_/sbx_ leak, INCLUDING one buried inside example SQL)

The warehouse is intentionally NOT rewritten here: it's a per-target DABs variable
on the genie_space resource (${var.warehouse_id}); the serialized_space carries no
host/warehouse. So the only promotion transform over the JSON is the catalog rebind.
"""
from __future__ import annotations

import argparse
import json
import re
import sys


def rebind(serialized: str, from_env: str, to_env: str, domain: str) -> str:
    """Rebind <from_env>_<domain> -> <to_env>_<domain> across the serialized_space JSON."""
    pat = re.compile(rf"\b{re.escape(from_env)}_{re.escape(domain)}\b")
    return pat.sub(f"{to_env}_{domain}", serialized)


# A fully-qualified ref is catalog.schema.table, each part optionally backtick-quoted.
# The allowlist asserts the CATALOG of EVERY such ref equals the target
# <to_env>_<domain> — case-insensitive and backtick-aware. (A denylist on
# "<env>_<domain>." alone missed uppercase, backticked, and unrelated foreign
# catalogs like `main`/`samples` — see the S4 review.) Schema/table are now ALSO captured (groups
# 2/3, not just 1) so `find_refs` can report the full ref — this doesn't change what the pattern
# MATCHES, only what it captures, so `find_violations` (group(1)-only) is unaffected.
_REF3 = re.compile(r"`?([A-Za-z][\w]*)`?\.`?([A-Za-z_]\w*)`?\.`?([A-Za-z_]\w*)`?")


def find_violations(serialized: str, to_env: str, domain: str, *,
                     extra_allowed_catalogs: "set[str] | None" = None) -> list[str]:
    """Catalogs of any 3-part ref that aren't the target <to_env>_<domain> NOR one of
    `extra_allowed_catalogs` (true allowlist). The extra set exists for rehydrate's table de-para
    (G6): a per-table override may retarget a ref to a catalog other than the plain
    <to_env>_<domain> default, so the allowlist widens to include those CHOSEN catalogs — while
    still catching anything else unlisted, e.g. a stray <from_env>_<domain> that survived a rebind."""
    allowed = {f"{to_env}_{domain}".lower()} | {c.lower() for c in (extra_allowed_catalogs or ())}
    return sorted({m.group(1) for m in _REF3.finditer(serialized) if m.group(1).lower() not in allowed})


def find_refs(serialized: str) -> list[str]:
    """Every DISTINCT 3-part catalog.schema.table ref anywhere in the serialized_space (including a
    ref buried inside example/benchmark SQL text — the same grammar `find_violations` already
    parses), backtick-stripped, in FIRST-APPEARANCE order. Feeds the rehydrate-preview table de-para
    (G6): each ref returned here gets a plain `rebind`-ed default target the UI shows before letting
    the user override it."""
    seen: dict[str, None] = {}
    for m in _REF3.finditer(serialized):
        seen.setdefault(f"{m.group(1)}.{m.group(2)}.{m.group(3)}", None)
    return list(seen)


def _ref_pattern(ref: str) -> "re.Pattern[str]":
    """A regex matching `ref` (catalog.schema.table) exactly as `_REF3` would have matched it — each
    part OPTIONALLY backtick-quoted — so `apply_table_mapping` replaces a ref regardless of whether
    it appears plain or backtick-quoted (e.g. inside example/benchmark SQL text)."""
    catalog, schema, table = ref.split(".")
    return re.compile(rf"`?{re.escape(catalog)}`?\.`?{re.escape(schema)}`?\.`?{re.escape(table)}`?")


def apply_table_mapping(serialized: str, mapping: dict[str, str], *, from_env: str, to_env: str,
                         domain: str) -> str:
    """Apply a table de-para (G6/rehydrate) on top of an ALREADY `rebind`-ed serialized_space: each
    `mapping` key is the ORIGINAL SOURCE ref (e.g. a prod_ ref, matching the rehydrate-preview's
    `source` field) — this derives that ref's plain `rebind`-ed DEFAULT target and replaces THAT
    (not the source ref itself, which no longer appears in `serialized` post-rebind) with the user's
    chosen target. Replacement is TEXT SUBSTITUTION (the same technique `rebind` itself uses), so an
    occurrence buried inside example/benchmark SQL is caught too, not just a
    `data_sources.tables[].identifier`. Backtick-aware on the MATCH side (`_ref_pattern`); the
    replacement is written plain/unquoted — safe for the identifiers this accelerator's domains use
    (see `promotion_store._IDENT`), and simpler than preserving a per-part backtick style a future
    refinement could add if a customer's schema ever needs it."""
    out = serialized
    for source_ref, target_ref in mapping.items():
        default_target = rebind(source_ref, from_env, to_env, domain)
        out = _ref_pattern(default_target).sub(lambda _m, t=target_ref: t, out)
    return out


def render_file(in_path: str, out_path: str, from_env: str, to_env: str, domain: str) -> str:
    raw = open(in_path, encoding="utf-8").read()
    out = rebind(raw, from_env, to_env, domain)
    json.loads(out)  # the rebind must keep the JSON valid
    open(out_path, "w", encoding="utf-8").write(out)
    return out


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(description="Genie serialized_space pre-render + allowlist")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="rebind <from>_<domain> -> <to>_<domain>")
    r.add_argument("infile")
    r.add_argument("--from", dest="from_env", required=True)
    r.add_argument("--to", dest="to_env", required=True)
    r.add_argument("--domain", required=True)
    r.add_argument("--out", required=True)

    c = sub.add_parser("check", help="fail if any catalog ref isn't <to>_<domain>")
    c.add_argument("infile")
    c.add_argument("--to", dest="to_env", required=True)
    c.add_argument("--domain", required=True)

    a = p.parse_args(argv)
    if a.cmd == "render":
        out = render_file(a.infile, a.out, a.from_env, a.to_env, a.domain)
        leaks = find_violations(out, a.to_env, a.domain)
        if leaks:
            print(f"ERROR: post-render leaks remain: {leaks}", file=sys.stderr)
            return 2
        print(f"rendered {a.infile} -> {a.out} ({a.from_env}_{a.domain} -> {a.to_env}_{a.domain})")
        return 0
    # check
    raw = open(a.infile, encoding="utf-8").read()
    leaks = find_violations(raw, a.to_env, a.domain)
    if leaks:
        print(f"FAIL: {a.infile} references non-{a.to_env} catalogs: {leaks}", file=sys.stderr)
        return 1
    print(f"OK: {a.infile} references only {a.to_env}_{a.domain}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
