#!/usr/bin/env python3
"""Non-mutating pilot readiness verifier and redacted evidence manifest generator.

The default command runs every offline feedback loop, then requires a small non-secret live-evidence
file. Missing provider evidence is an explicit NO-GO; ``--offline-only`` lets CI prove the offline
floor while keeping those prerequisites visible as PENDING.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

ACTIVE_ENGINE_PATHS = (
    ".github",
    "app",
    "engine_api",
    "genie_reviewer",
    "scripts",
    "web/src",
    "handbook",
    "README.md",
    "SETUP.md",
    "CONTEXT.md",
    "docs/PILOT-GO-NO-GO.md",
    "docs/security",
)
ACTIVE_CONTENT_PATHS = (".github", "src", "tests")
TEXT_SUFFIXES = {".md", ".py", ".sh", ".ts", ".svelte", ".yml", ".yaml", ".json", ".txt"}
RETIRED_LITERALS = (
    "." + "access" + ".json",
    "Access" + "Spec",
    "uc_" + "principals",
    "CAN_" + "VIEW",
    "GRANT" + "-01",
    "consumer_" + "group",
    "access_" + "request",
    "is_" + "approver",
    "check_" + "grants.py",
    "apply_" + "access.py",
)

SCENARIO_TESTS = {
    "content_blocker": "tests/test_app_logic.py::test_blocked_review_opens_draft_pr_and_applies_label",
    "missing_select_advisory": "tests/test_audience_check.py::test_missing_select_is_informational_terraform_guidance",
    "preflight_zero_mutation": "tests/test_deploy_attempt.py::test_preflight_failure_has_zero_mutation_and_operational_evidence",
    "partial_attempt": "tests/test_deploy_attempt.py::test_mid_deploy_failure_is_partial_and_records_completed_stages_and_targets",
    "idempotent_replay": "tests/test_deploy_attempt.py::test_retry_restarts_at_preflight_and_idempotently_converges",
    "acl_preservation": "tests/test_reconcile_audience.py::test_desired_acl_removes_only_previous_can_run_and_preserves_stronger_unrelated_entries",
    "authz_denial": "tests/test_authz.py::test_assert_can_access_denies_a_requester_who_does_not_own_the_space",
    "no_op": "tests/test_github_app.py::test_no_op_promotion_when_content_matches_base_opens_no_pr",
    "audit_idempotency": "tests/test_reconcile.py::test_idempotent_no_new_events_on_unchanged_status",
}

LIVE_REQUIREMENTS = {
    "github_app": "GitHub App installation and token/read-write smoke test from the live App",
    "required_checks": "strict content-main required checks with admin enforcement",
    "prod_environment_gate": "prod Environment Steward reviewer and self-review prevention",
    "runner": "content runner online and harmless job completed",
    "app_runtime": "one Medium App lifecycle and runtime/cost evidence",
    "databricks_identities": "App, dev transport and CI identity permission smoke tests",
    "r1_r15_rehearsal": "human/provider R1-R15 evidence pack",
    "signoff": "KIP and GestOps affirmative sign-off",
}
SENSITIVE_KEY_PARTS = ("token", "secret", "private_key", "authorization", "password", "credential")


@dataclasses.dataclass
class Check:
    check_id: str
    status: str
    detail: str
    duration_seconds: float | None = None


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def _iter_text_files(root: Path, relative_paths: tuple[str, ...]):
    for relative in relative_paths:
        target = root / relative
        if not target.exists():
            continue
        paths = [target] if target.is_file() else target.rglob("*")
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if any(part in {".git", "build", "node_modules", "test-results"} for part in path.parts):
                continue
            yield path


def scan_retired_literals(root: Path, relative_paths: tuple[str, ...]) -> list[str]:
    findings: list[str] = []
    for path in _iter_text_files(root, relative_paths):
        text = path.read_text(encoding="utf-8", errors="replace")
        for literal in RETIRED_LITERALS:
            for line_no, line in enumerate(text.splitlines(), 1):
                if literal.casefold() in line.casefold():
                    findings.append(f"{path.relative_to(root)}:{line_no}: {literal}")
    return findings


def check_lock(engine_root: Path, content_root: Path) -> tuple[bool, dict[str, str]]:
    engine_revision = _git(engine_root, "rev-parse", "HEAD")
    content_revision = _git(content_root, "rev-parse", "HEAD")
    locked_revision = (content_root / "engine.lock").read_text(encoding="utf-8").strip()
    valid = bool(re.fullmatch(r"[0-9a-f]{40}", locked_revision))
    return valid and locked_revision == engine_revision, {
        "engine_revision": engine_revision,
        "content_revision": content_revision,
        "content_engine_lock": locked_revision,
    }


def check_schema_contract() -> list[str]:
    import promotion_store
    import roles_store
    import reset_demo_ledger

    errors: list[str] = []
    create_promotion = promotion_store.MIGRATIONS[0]
    for required in ("audience_spec", "change_provider", "external_id", "external_url"):
        if required not in create_promotion:
            errors.append(f"canonical promotions schema missing {required}")
    for retired in ("pr_" + "number", "pr_" + "url", "access_" + "spec"):
        if retired in create_promotion:
            errors.append(f"fresh promotions schema still contains {retired}")
    sql = "\n".join(promotion_store.MIGRATIONS)
    for required in ("deployment_attempts", "rehydrate_events", "DROP COLUMN IF EXISTS pr_number"):
        if required not in sql:
            errors.append(f"promotion migration missing {required}")
    if sql.find("ADD COLUMN IF NOT EXISTS external_id") > sql.find("DROP COLUMN IF EXISTS pr_number"):
        errors.append("provider-neutral identity is not expanded before provider column contraction")
    role_sql = "\n".join(roles_store.MIGRATIONS)
    if "CHECK (role IN ('steward', 'admin'))" not in role_sql:
        errors.append("roles schema is not constrained to steward/admin")
    if set(reset_demo_ledger.LEDGER_TABLES) & set(reset_demo_ledger.PRESERVED_CONFIG_TABLES):
        errors.append("demo reset overlaps preserved operational configuration")
    return errors


def check_workflow_guardrails(engine_root: Path, content_root: Path) -> list[str]:
    errors: list[str] = []
    pr = (content_root / ".github/workflows/pr-checks.yml").read_text(encoding="utf-8")
    deploy = (content_root / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    engine_ci = (engine_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    required_pr = (
        "scripts/check_audience.py",
        "scripts/check_eval.py",
        "scripts/check_eval_run.py",
        "databricks bundle validate --strict",
        "ref: ${{ steps.engine.outputs.sha }}",
    )
    required_deploy = (
        "environment: prod",
        "cancel-in-progress: false",
        "scripts/deploy_attempt.py",
        "ref: ${{ steps.engine.outputs.sha }}",
    )
    for marker in required_pr:
        if marker not in pr:
            errors.append(f"pr-checks missing {marker}")
    for marker in required_deploy:
        if marker not in deploy:
            errors.append(f"deploy workflow missing {marker}")
    if "name: bundle validate (prod)" not in engine_ci:
        errors.append("engine CI lost the protected check name")
    return errors


def check_scenario_tests_exist(engine_root: Path) -> list[str]:
    errors: list[str] = []
    for scenario, node_id in SCENARIO_TESTS.items():
        file_part, test_name = node_id.split("::", 1)
        path = engine_root / file_part
        if not path.exists() or f"def {test_name}(" not in path.read_text(encoding="utf-8"):
            errors.append(f"{scenario}: missing {node_id}")
    return errors


def _contains_sensitive_key(value) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if any(part in str(key).casefold() for part in SENSITIVE_KEY_PARTS):
                return True
            if _contains_sensitive_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(child) for child in value)
    return False


def _safe_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def load_live_evidence(path: Path | None) -> tuple[dict, list[Check]]:
    data = {}
    if path is not None and path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if _contains_sensitive_key(data):
            raise ValueError("live evidence contains a secret-like key; store links and status only")
    results: list[Check] = []
    redacted: dict[str, dict] = {}
    for key, description in LIVE_REQUIREMENTS.items():
        entry = data.get(key) if isinstance(data, dict) else None
        status = str((entry or {}).get("status") or "pending").lower()
        url = _safe_url((entry or {}).get("url"))
        note = str((entry or {}).get("note") or description)[:300]
        passed = status == "pass" and url is not None
        result_status = "PASS" if passed else "PENDING"
        results.append(Check(f"live:{key}", result_status, note))
        redacted[key] = {"status": result_status, "url": url, "note": note}
    return redacted, results


def run_command(check_id: str, command: list[str], cwd: Path) -> Check:
    print(f"\n[{check_id}] {' '.join(command)}", flush=True)
    started = time.monotonic()
    result = subprocess.run(command, cwd=cwd, check=False)
    duration = round(time.monotonic() - started, 3)
    return Check(
        check_id,
        "PASS" if result.returncode == 0 else "FAIL",
        f"exit={result.returncode}",
        duration,
    )


def _copy_overlay(engine_root: Path, content_root: Path, destination: Path) -> None:
    ignored = shutil.ignore_patterns(".git", "build", "node_modules", ".venv", "test-results")
    shutil.copytree(engine_root, destination, dirs_exist_ok=True, ignore=ignored)
    shutil.copytree(content_root / "src", destination / "src", dirs_exist_ok=True)


def execution_checks(engine_root: Path, content_root: Path) -> list[Check]:
    python = sys.executable
    checks = [
        run_command("scenarios", [python, "-m", "pytest", *SCENARIO_TESTS.values(), "-q"], engine_root),
        run_command("backend", [python, "-m", "pytest", "tests", "-q"], engine_root),
        run_command("content", [python, "-m", "pytest", "-q"], content_root),
        run_command("frontend-unit", ["npm", "run", "test:unit"], engine_root / "web"),
        run_command("frontend-check", ["npm", "run", "check"], engine_root / "web"),
        run_command("frontend-build", ["npm", "run", "build"], engine_root / "web"),
        run_command("frontend-smoke", ["npm", "run", "test:smoke"], engine_root / "web"),
        run_command("render-standalone", ["bash", "scripts/render.sh", "prod"], engine_root),
    ]
    with tempfile.TemporaryDirectory(prefix="genie-pilot-readiness-") as temp:
        overlay = Path(temp) / "overlay"
        _copy_overlay(engine_root, content_root, overlay)
        checks.append(run_command("render-content-overlay", ["bash", "scripts/render.sh", "prod"], overlay))
    return checks


def _check(check_id: str, errors: list[str]) -> Check:
    return Check(check_id, "FAIL" if errors else "PASS", "; ".join(errors) if errors else "ok")


def build_manifest(
    engine_root: Path,
    content_root: Path,
    live_evidence_path: Path | None,
    *,
    execute: bool,
    offline_only: bool,
) -> tuple[dict, int]:
    lock_ok, revisions = check_lock(engine_root, content_root)
    engine_retired = scan_retired_literals(engine_root, ACTIVE_ENGINE_PATHS)
    content_retired = scan_retired_literals(content_root, ACTIVE_CONTENT_PATHS)
    dirty_worktrees = []
    if _git(engine_root, "status", "--porcelain"):
        dirty_worktrees.append("engine worktree is dirty")
    if _git(content_root, "status", "--porcelain"):
        dirty_worktrees.append("content worktree is dirty")
    static_checks = [
        _check("legacy-absence", engine_retired + content_retired),
        _check("engine-content-lock", [] if lock_ok else ["content engine.lock != engine HEAD"]),
        _check("schema-migrations", check_schema_contract()),
        _check("workflow-guardrails", check_workflow_guardrails(engine_root, content_root)),
        _check("scenario-coverage", check_scenario_tests_exist(engine_root)),
        _check("clean-worktrees", dirty_worktrees),
    ]
    executed = execution_checks(engine_root, content_root) if execute else []
    live_redacted, live_checks = load_live_evidence(live_evidence_path)
    offline_checks = static_checks + executed
    offline_pass = all(check.status == "PASS" for check in offline_checks)
    live_pass = all(check.status == "PASS" for check in live_checks)
    if offline_pass and live_pass:
        decision = "GO-CANDIDATE"
        exit_code = 0
    elif offline_pass and offline_only:
        decision = "OFFLINE-PASS"
        exit_code = 0
    else:
        decision = "NO-GO"
        exit_code = 1
    manifest = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "decision": decision,
        "redaction_policy": "links have query/fragment removed; secret-like keys are rejected",
        "revisions": revisions,
        "expected_actor_surfaces": {
            "humans": ["Business User", "Steward", "Platform Admin"],
            "ownership": {"KIP": "runtime and maintenance", "GestOps": "adoption and guidance"},
            "machines": ["App service principal", "DEV transport service principal", "CI service principal", "GitHub App"],
        },
        "runtime_policy": {
            "app_size": "Medium",
            "instances": 1,
            "availability_window": "business days 08:00-18:00 BRT",
            "support": "best effort during pilot window",
            "lakebase": "CU_1 autoscaling floor with scale-to-zero",
        },
        "offline_checks": [dataclasses.asdict(check) for check in offline_checks],
        "scenario_tests": SCENARIO_TESTS,
        "live_prerequisites": live_redacted,
        "live_checks": [dataclasses.asdict(check) for check in live_checks],
        "human_checklist": "docs/PILOT-GO-NO-GO.md#mandatory-scenario-matrix",
    }
    return manifest, exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content-repo", type=Path, required=True)
    parser.add_argument("--live-evidence", type=Path)
    parser.add_argument("--evidence-out", type=Path, default=ROOT / "build/pilot-readiness-evidence.json")
    parser.add_argument("--offline-only", action="store_true")
    parser.add_argument("--skip-execution", action="store_true", help="static verifier tests only")
    args = parser.parse_args()
    content_root = args.content_repo.resolve()
    if not (content_root / "engine.lock").exists():
        parser.error("--content-repo must point to the content repository root")
    try:
        manifest, exit_code = build_manifest(
            ROOT,
            content_root,
            args.live_evidence.resolve() if args.live_evidence else None,
            execute=not args.skip_execution,
            offline_only=args.offline_only,
        )
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"NO-GO: readiness verifier could not complete: {exc}", file=sys.stderr)
        return 2
    args.evidence_out.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nDecision: {manifest['decision']}")
    print(f"Evidence: {args.evidence_out}")
    for check in manifest["offline_checks"] + manifest["live_checks"]:
        print(f"  {check['status']:7} {check['check_id']}: {check['detail']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
