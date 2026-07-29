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


def dashboard_sql_text(doc: "dict | str") -> str:
    """Every part of an AI/BI dashboard that is NOT prose — i.e. everything ENV-01 must still gate.

    The deterministic catalog allowlist must be STRUCTURAL for a dashboard, not textual. `_REF3` is a
    whole-document regex, and a dashboard legitimately contains prose: probed against a real dev
    dashboard, `find_violations` over the raw file matched **`en.wikipedia.org`** (a markdown link in a
    `multilineTextboxSpec` widget) and reported catalog `en` as a foreign-catalog ENV-01 BLOCKER — a
    false positive, since no query runs from a text widget.

    IMPLEMENTED AS A DENYLIST OF PROSE, NOT AN ALLOWLIST OF QUERY FIELDS. The first version of this
    function concatenated only `datasets[].queryLines`, which was a real hole: a dataset's
    `parameters[].defaultSelection.values.values[].value` is FREE TEXT that reaches the query engine
    via `IDENTIFIER(:param)` (verified live — `EXPLAIN SELECT * FROM IDENTIFIER('samples.nyctaxi.trips')`
    resolves the foreign catalog), so a dev/foreign catalog placed there passed ENV-01 untouched. An
    allowlist of known query fields keeps leaking as the `.lvdash.json` schema grows; a denylist of the
    one construct that is genuinely non-data (text/markdown widgets) fails in the SAFE direction —
    an unrecognized new field is scanned by default.

    So: strip every `*TextboxSpec` subtree, then return the rest of the document as text. A
    `dev_`/`sbx_`/cross-domain catalog anywhere that can influence a query — dataset SQL, a parameter
    default, a widget-level override, any field added by a future schema version — is still caught.

    A catalog name in a text widget's PROSE (the same probe found a heading reading
    `# cerc_mlops_dev_catalog.inference.inference_scores Monitoring`) is still REBOUND by the
    whole-document `rebind`, so the promoted dashboard reads correctly in prod, and is reported as an
    advisory DASH-04 finding by `dashboard_check` — never a BLOCKER.

    Accepts a parsed dict or a raw JSON string. An UNPARSEABLE string is returned VERBATIM so it is
    still scanned (fail closed) rather than silently yielding an empty scan.
    """
    if isinstance(doc, str):
        try:
            parsed = json.loads(doc)
        except (ValueError, TypeError):
            # Cannot understand the document -> scan it whole rather than scan nothing.
            return doc
        doc = parsed
    if not isinstance(doc, dict):
        return json.dumps(doc, ensure_ascii=False) if doc is not None else ""
    return json.dumps(_strip_prose(doc), ensure_ascii=False)


def _is_prose_key(key: object) -> bool:
    """Whether a dict key holds a text/markdown widget spec (the one genuinely non-data construct).

    Matches any `*TextboxSpec` (real exports use `multilineTextboxSpec`) so a renamed or additional
    text widget type is still treated as prose.
    """
    return isinstance(key, str) and key.endswith("TextboxSpec")


def _strip_prose(node):
    """Deep-copy ``node`` with every text/markdown widget spec removed."""
    if isinstance(node, dict):
        return {k: _strip_prose(v) for k, v in node.items() if not _is_prose_key(k)}
    if isinstance(node, list):
        return [_strip_prose(item) for item in node]
    return node


def dashboard_prose_text(doc: "dict | str") -> str:
    """The NON-data text of a dashboard — today its text/markdown widget lines.

    The counterpart to `dashboard_sql_text`: this is the text a leftover catalog reference is only a
    DOCUMENTATION defect in, which `dashboard_check` reports as an advisory DASH-04 rather than a
    BLOCKER. Kept here (beside the SQL extractor) so both halves of the "where does a dashboard's
    text live" question are answered in one place.
    """
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except (ValueError, TypeError):
            return ""
    if not isinstance(doc, dict):
        return ""
    parts: list[str] = []

    def _walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                # `multilineTextboxSpec.lines` is the shape real exports use; match any *TextboxSpec
                # so a future/renamed text widget still contributes its prose.
                if key.endswith("TextboxSpec") and isinstance(value, dict):
                    lines = value.get("lines")
                    if isinstance(lines, list):
                        parts.append("\n".join(str(line) for line in lines))
                    elif isinstance(lines, str):
                        parts.append(lines)
                else:
                    _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(doc.get("pages") or [])
    return "\n".join(parts)


def scan_text(serialized: str, *, sql_only: bool) -> str:
    """The text the deterministic catalog allowlist should scan for one artifact.

    `sql_only=False` (Genie) scans the WHOLE serialized document, unchanged — a Genie Space carries
    no prose widgets and its example/benchmark SQL must stay in scope. `sql_only=True` (dashboard)
    narrows to `dashboard_sql_text`. Callers pass `ResourceKind.sql_only_ref_scan`, so the choice is
    a registry fact rather than a branch at each call site.
    """
    return dashboard_sql_text(serialized) if sql_only else serialized


def yaml_scalar(value: str) -> str:
    """Quote an arbitrary string as a SAFE single-line YAML double-quoted scalar.

    `render.sh` builds the generated bundle resources as text, and a resource's `display_name`/`title`
    comes from an author-supplied `.title` sidecar. Escaping only `"` with `sed` was exploitable: a
    title could close the quote and inject a SIBLING KEY into the resource. Confirmed reachable —
    a crafted title produced `embed_credentials: true` in the resolved bundle, which would publish a
    dashboard with the publisher's credentials embedded and turn promotion into data access (exactly
    what ADR-0009 retired). A bare trailing backslash could also make the YAML unparseable.

    So the sidecar text is never interpolated raw: every control character and every YAML-significant
    character is escaped here, and newlines are collapsed, guaranteeing a single scalar on one line
    that cannot terminate early.
    """
    out = []
    for ch in value:
        if ch in ("\n", "\r", "\t"):
            out.append(" ")          # collapse to a space: a display name is single-line by nature
        elif ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(" ")          # drop other control characters entirely
        else:
            out.append(ch)
    return '"' + "".join(out).strip() + '"'


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


def apply_mapping_file(in_path: str, mapping_path: str, from_env: str, to_env: str, domain: str) -> str:
    """G7 (promotion's table de-para): apply `mapping_path`'s JSON (source-env ref -> target ref) to
    an ALREADY `render_file`-ed file, IN PLACE. Thin CLI wrapper over `apply_table_mapping` — the
    caller (render.sh) runs this AFTER `render` and BEFORE `check`, so the strict allowlist still
    catches a mapped target outside `<to_env>_<domain>` (ENV-01), same as an un-mapped leak."""
    mapping = json.load(open(mapping_path, encoding="utf-8"))
    raw = open(in_path, encoding="utf-8").read()
    out = apply_table_mapping(raw, mapping, from_env=from_env, to_env=to_env, domain=domain)
    json.loads(out)  # the mapping must keep the JSON valid
    open(in_path, "w", encoding="utf-8").write(out)
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
    # Same choice as `check` (and the same default), because `render` re-checks for post-rebind leaks:
    # without this a dashboard would pass the narrowed `check` but still fail here on a markdown URL.
    r.add_argument("--scan", choices=("all", "dashboard-sql"), default="all",
                   help="text to scan for post-render leaks (default: all = whole document)")

    m = sub.add_parser("apply-mapping",
                       help="apply a table de-para (mapping.json) to an already-rendered file, in place")
    m.add_argument("infile")
    m.add_argument("--mapping", required=True, help="path to a mapping.json (source-env ref -> target ref)")
    m.add_argument("--from", dest="from_env", required=True)
    m.add_argument("--to", dest="to_env", required=True)
    m.add_argument("--domain", required=True)

    y = sub.add_parser("yaml-scalar",
                       help="quote a sidecar's text as a safe single-line YAML scalar (render.sh)")
    y.add_argument("infile", help="the sidecar file whose contents to quote")

    c = sub.add_parser("check", help="fail if any catalog ref isn't <to>_<domain>")
    c.add_argument("infile")
    c.add_argument("--to", dest="to_env", required=True)
    c.add_argument("--domain", required=True)
    # Which text to scan. `all` (default) is the pre-existing whole-document behaviour and stays the
    # default so every current caller is unchanged. `dashboard-sql` narrows the scan to
    # `datasets[].queryLines` — required for an AI/BI dashboard, whose markdown/text widgets would
    # otherwise produce false foreign-catalog BLOCKERs (see `dashboard_sql_text`).
    c.add_argument("--scan", choices=("all", "dashboard-sql"), default="all",
                   help="text to scan for catalog refs (default: all = whole document)")

    a = p.parse_args(argv)
    if a.cmd == "render":
        out = render_file(a.infile, a.out, a.from_env, a.to_env, a.domain)
        leaks = find_violations(scan_text(out, sql_only=(a.scan == "dashboard-sql")),
                                a.to_env, a.domain)
        if leaks:
            print(f"ERROR: post-render leaks remain: {leaks}", file=sys.stderr)
            return 2
        print(f"rendered {a.infile} -> {a.out} ({a.from_env}_{a.domain} -> {a.to_env}_{a.domain})")
        return 0
    if a.cmd == "yaml-scalar":
        text = open(a.infile, encoding="utf-8").read()
        if not text.strip():
            print(f"ERROR: {a.infile} is empty — a display name is required", file=sys.stderr)
            return 2
        print(yaml_scalar(text))
        return 0
    if a.cmd == "apply-mapping":
        mapping = json.load(open(a.mapping, encoding="utf-8"))
        apply_mapping_file(a.infile, a.mapping, a.from_env, a.to_env, a.domain)
        print(f"applied mapping ({len(mapping)} override(s)) from {a.mapping} to {a.infile}")
        return 0
    # check
    raw = open(a.infile, encoding="utf-8").read()
    scanned = scan_text(raw, sql_only=(a.scan == "dashboard-sql"))
    leaks = find_violations(scanned, a.to_env, a.domain)
    if leaks:
        print(f"FAIL: {a.infile} references non-{a.to_env} catalogs: {leaks}", file=sys.stderr)
        return 1
    where = "dataset SQL" if a.scan == "dashboard-sql" else "all refs"
    print(f"OK: {a.infile} references only {a.to_env}_{a.domain} ({where})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
