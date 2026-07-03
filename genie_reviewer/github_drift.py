"""github_drift — F5 Phase 1: READ-ONLY comparison between the app's in-app role configuration and
GitHub's AUTHORITATIVE enforced gates (the prod Environment's required reviewers + the base
branch's protection rules).

GitHub stays the enforcement plane (ADR-0005's framing, extended): this module never writes
anything. It only READS two GitHub surfaces via the existing bot client (`GitHubApp`, extended
below with two read-only accessors) and diffs them against the app's role config (through the
email<->GitHub-username mapping `roles_store` holds), producing a small set of named divergence
classes an Admin/Steward can act on.

**Phase 2 (write-through) is explicitly OUT OF SCOPE** — see the PRD's "Out of Scope" section: that
would need `administration:write` (repo-admin-equivalent) bot scope, letting the app rewrite its
own SoD gates. This module never requests or uses that scope, and never calls a GitHub write
endpoint. If that ever changes, it needs its own security-scoped ADR/PRD — not a quiet addition
here.

**Graceful degradation (explicit acceptance criterion):** any GitHub read failure (missing scope,
network error, repo/environment not found, unexpected shape) must surface as an "unknown" status
for that gate, NEVER as "no drift" (an error must never look like a clean bill of health) and NEVER
as a raised exception that crashes the caller (an admin's Settings screen must render even when
GitHub is unreachable).
"""
from __future__ import annotations

import dataclasses
from typing import Callable, Optional, Protocol

# --- divergence classes ---------------------------------------------------------

# Each row in a drift report names ONE specific divergence, so the UI can render a concrete,
# actionable sentence rather than a vague "something's wrong":
#   steward_unmapped        — the app's configured Steward(s) have no GitHub-username mapping at
#                             all, so drift against the Environment reviewer can't even be checked.
#   steward_not_env_reviewer — the app's Steward IS mapped, but that GitHub username is NOT among
#                             the prod Environment's required reviewers (or the Environment has no
#                             required reviewers configured at all).
#   env_reviewer_not_steward — the Environment has a required reviewer whose GitHub username maps
#                             back (via roles_store) to an app email that is NOT a configured
#                             Steward/Admin — someone can release the deploy gate whom the app
#                             doesn't recognize as a governance role at all.
#   branch_protection_missing — the base branch has no protection / no required PR review at all.
#   unknown_environment      — the Environment's required-reviewers couldn't be read (surfaced as
#                             unknown, never silently "no drift").
#   unknown_branch_protection — same, for branch protection.
DIVERGENCE_CLASSES = (
    "steward_unmapped",
    "steward_not_env_reviewer",
    "env_reviewer_not_steward",
    "branch_protection_missing",
    "unknown_environment",
    "unknown_branch_protection",
)


@dataclasses.dataclass(frozen=True)
class DriftFinding:
    kind: str          # one of DIVERGENCE_CLASSES
    severity: str       # "warning" | "unknown" — never "error"/blocking; this is READ-ONLY visibility
    message: str        # PT, human-actionable (mirrors the reviewer's PT findings elsewhere)
    detail: dict


class GitHubReader(Protocol):
    """The narrow read-only surface `github_drift` needs from the bot client — deliberately NOT the
    whole `GitHubApp` (keeps this module's dependency small + trivially fakeable in tests)."""

    def get_environment_reviewers(self, environment: str) -> list[str]: ...
    def get_branch_protection(self, branch: str) -> Optional[dict]: ...


def _norm(name: Optional[str]) -> str:
    return (name or "").strip().lower()


def check_drift(
    *,
    stewards: list[str],
    admins: list[str],
    github_username_for: Callable[[str], Optional[str]],
    reader: GitHubReader,
    environment: str = "prod",
    branch: str = "main",
) -> list[DriftFinding]:
    """The pure(ish) comparison — takes the app's role config as plain data (already resolved by
    the caller via `roles_store`'s store-over-env precedence) plus a `reader` (the bot client, or a
    fake in tests) and returns every divergence found. Never raises: any failure reading GitHub is
    caught HERE and turned into an `unknown_*` finding, so a caller can render this list directly
    without its own try/except.

    ``stewards``/``admins`` are app EMAILS (already lowercased by the caller, following the existing
    `_admin_emails()` convention) — governance roles that are allowed to release the deploy gate.
    ``github_username_for`` resolves an app email to its mapped GitHub login (or None if unmapped),
    e.g. `roles_store.RolesStore.github_username_for`.
    """
    findings: list[DriftFinding] = []
    steward_set = {_norm(e) for e in stewards}
    admin_or_steward = {_norm(e) for e in (*stewards, *admins)}

    # --- Environment required-reviewers vs the app's Steward(s) --------------
    env_reviewers: Optional[list[str]] = None
    try:
        env_reviewers = reader.get_environment_reviewers(environment)
    except Exception as e:  # noqa: BLE001 — ANY failure degrades to "unknown", never "no drift"
        findings.append(DriftFinding(
            kind="unknown_environment", severity="unknown",
            message=(f"Não foi possível ler os aprovadores obrigatórios do Environment "
                     f"'{environment}' no GitHub — verifique a instalação/escopo do bot."),
            detail={"environment": environment, "error": str(e)}))

    if env_reviewers is not None:
        env_reviewer_logins = {_norm(r) for r in env_reviewers}
        # steward_unmapped / steward_not_env_reviewer: for each configured Steward, can we even
        # check drift (is there a GitHub mapping), and if so, is it actually the enforced reviewer?
        for steward_email in stewards:
            login = github_username_for(steward_email)
            if not login:
                findings.append(DriftFinding(
                    kind="steward_unmapped", severity="warning",
                    message=(f"O Steward configurado ({steward_email}) não tem usuário do GitHub "
                             f"mapeado — não é possível verificar se ele é o aprovador obrigatório "
                             f"do Environment '{environment}'."),
                    detail={"steward_email": steward_email, "environment": environment}))
                continue
            if _norm(login) not in env_reviewer_logins:
                findings.append(DriftFinding(
                    kind="steward_not_env_reviewer", severity="warning",
                    message=(f"O Steward configurado ({steward_email} -> @{login}) NÃO está entre "
                             f"os aprovadores obrigatórios do Environment '{environment}' no "
                             f"GitHub — o gate real pode divergir do papel configurado no app."),
                    detail={"steward_email": steward_email, "github_username": login,
                            "environment": environment, "env_reviewers": sorted(env_reviewer_logins)}))
        # env_reviewer_not_steward: the inverse direction — someone can release the gate whom the
        # app doesn't recognize as Steward/Admin at all (mapped back via the SAME roles_store).
        mapped_back = _reverse_map(admin_or_steward, github_username_for, stewards + admins)
        for login in env_reviewer_logins:
            if login not in mapped_back:
                findings.append(DriftFinding(
                    kind="env_reviewer_not_steward", severity="warning",
                    message=(f"O aprovador obrigatório @{login} do Environment '{environment}' no "
                             f"GitHub não corresponde a nenhum Steward/Admin configurado no app "
                             f"(ou não tem e-mail mapeado) — alguém pode liberar o deploy sem um "
                             f"papel de governança reconhecido."),
                    detail={"github_username": login, "environment": environment}))

    # --- Branch protection ----------------------------------------------------
    try:
        protection = reader.get_branch_protection(branch)
    except Exception as e:  # noqa: BLE001
        findings.append(DriftFinding(
            kind="unknown_branch_protection", severity="unknown",
            message=(f"Não foi possível ler a proteção do branch '{branch}' no GitHub — verifique "
                     f"a instalação/escopo do bot."),
            detail={"branch": branch, "error": str(e)}))
    else:
        if not protection or not protection.get("required_pull_request_reviews"):
            findings.append(DriftFinding(
                kind="branch_protection_missing", severity="warning",
                message=(f"O branch '{branch}' não exige revisão de PR — o merge não está "
                         f"protegido pela segregação de funções esperada."),
                detail={"branch": branch}))

    return findings


def _reverse_map(app_emails: set[str], github_username_for: Callable[[str], Optional[str]],
                  candidate_emails: list[str]) -> set[str]:
    """The set of GitHub logins (lowercased) that map back to a configured Steward/Admin email,
    built by resolving the FORWARD mapping for every candidate email (roles_store has no reverse
    index — this module doesn't need one, since the candidate set is always small: the configured
    role holders)."""
    out = set()
    for email in candidate_emails:
        if _norm(email) in app_emails:
            login = github_username_for(email)
            if login:
                out.add(_norm(login))
    return out


def summarize(findings: list[DriftFinding]) -> dict:
    """A small JSON-shaped summary for the API/UI: whether there's any (non-unknown) drift, any
    unknown/unreachable gate, and the findings themselves. Kept separate from `check_drift` so a
    caller that wants the raw findings (e.g. a future Phase 2 remediation flow) isn't forced through
    this summary shape."""
    has_drift = any(f.severity == "warning" for f in findings)
    has_unknown = any(f.severity == "unknown" for f in findings)
    return {
        "has_drift": has_drift,
        "has_unknown": has_unknown,
        "findings": [dataclasses.asdict(f) for f in findings],
    }
