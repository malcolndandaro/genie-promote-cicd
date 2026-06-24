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
# catalogs like `main`/`samples` — see the S4 review.)
_REF3 = re.compile(r"`?([A-Za-z][\w]*)`?\.`?[A-Za-z_]\w*`?\.`?[A-Za-z_]\w*`?")


def find_violations(serialized: str, to_env: str, domain: str) -> list[str]:
    """Catalogs of any 3-part ref that aren't the target <to_env>_<domain> (true allowlist)."""
    target = f"{to_env}_{domain}".lower()
    return sorted({m.group(1) for m in _REF3.finditer(serialized) if m.group(1).lower() != target})


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
