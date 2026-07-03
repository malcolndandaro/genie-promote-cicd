"""GitHub App (bot) client — the deep module the app uses for all mechanical GitHub ops (GH2/GH3).

Encapsulates GitHub App auth + the REST calls behind a small interface:
  - open_or_update_promotion(branch, path, content, title, body) -> {number, html_url}
  - upsert_comment(number, marker, body) -> {id}            (one comment, updated in place)
  - get_status(number) -> {...}                              (GH3)

Auth: mints a short-lived App JWT (RS256) from the App id + private key, exchanges it for an
installation token, and caches it until just before expiry. The human (OBO) requester is attributed
in PR/comment CONTENT — the bot never impersonates anyone.

Testability: the HTTP transport and the token provider are injectable, so the open/update/upsert
logic is unit-tested against a fake GitHub (no network, no real key) — mirrors the engine's
injectable-client pattern.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

_API = "https://api.github.com"
Transport = Callable[[str, str, dict, dict | None], "tuple[int, dict | None]"]


class GitHubError(RuntimeError):
    def __init__(self, status: int, label: str, body: dict | None):
        super().__init__(f"GitHub {label} -> HTTP {status}")
        self.status = status
        self.body = body


_NO_DEPLOY = {"status": "none", "conclusion": None, "waiting_approval": False, "run_url": None,
              "run_id": None, "approver": None}


def _aggregate_checks(runs: list) -> str:
    """Aggregate PR check-runs into one verdict: none / pending / success / failure."""
    if not runs:
        return "none"
    if any(r.get("status") != "completed" for r in runs):
        return "pending"
    conclusions = [r.get("conclusion") for r in runs]
    if any(c == "action_required" for c in conclusions):  # a check awaiting a human, not a failure
        return "pending"
    if any(c not in ("success", "neutral", "skipped") for c in conclusions):
        return "failure"
    return "success"


def _derive_phase(pr_state: str | None, merged: bool, checks: str, deploy: dict) -> str:
    """A single phase the UI maps to a badge — reflects where the promotion actually is."""
    if merged:
        if deploy["waiting_approval"]:
            return "awaiting_approval"
        if deploy["status"] in ("queued", "in_progress"):
            return "deploying"
        if deploy["status"] == "completed":
            return "deployed" if deploy["conclusion"] == "success" else "deploy_failed"
        return "merged"
    if pr_state == "closed":
        return "closed"
    if checks == "pending":
        return "checks_running"
    if checks == "failure":
        return "checks_failed"
    return "open"  # checks success or not-yet-started (runner idle)


def _urllib_transport(method: str, url: str, headers: dict, body: dict | None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt else None)
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        return e.code, (json.loads(txt) if txt else None)


class GitHubApp:
    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        base: str = "main",
        app_id: str | None = None,
        installation_id: str | None = None,
        private_key: str | None = None,
        transport: Transport | None = None,
        token_provider: Callable[[], str] | None = None,
        now: Callable[[], float] | None = None,
    ):
        self.owner, self.repo, self.base = owner, repo, base
        self._app_id, self._installation_id, self._private_key = app_id, installation_id, private_key
        self._transport = transport or _urllib_transport
        self._token_provider = token_provider or self._mint_installation_token
        self._now = now or time.time
        self._cached: tuple[str, float] | None = None

    # --- auth ---------------------------------------------------------------
    def _mint_installation_token(self) -> str:
        import jwt  # local import: only the deployed app + default provider need PyJWT

        now = int(self._now())
        app_jwt = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self._app_id},
                             self._private_key, algorithm="RS256")
        status, body = self._transport(
            "POST", f"{_API}/app/installations/{self._installation_id}/access_tokens",
            self._headers(app_jwt), None)
        if status not in (200, 201) or not body:
            raise GitHubError(status, "mint installation token", body)
        return body["token"]

    def _token(self) -> str:
        if self._cached and self._cached[1] - 60 > self._now():
            return self._cached[0]
        tok = self._token_provider()
        self._cached = (tok, self._now() + 3000)  # tokens last ~1h; refresh well before
        return tok

    @staticmethod
    def _headers(token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "genie-promote-bot",
            "Content-Type": "application/json",
        }

    def _api(self, method: str, path: str, label: str, body: dict | None = None,
             ok: tuple[int, ...] = (200, 201)):
        status, data = self._transport(method, f"{_API}{path}", self._headers(self._token()), body)
        if status not in ok:
            raise GitHubError(status, label, data)
        return status, data

    def _repo_path(self, suffix: str) -> str:
        return f"/repos/{self.owner}/{self.repo}{suffix}"

    # --- promotion PR -------------------------------------------------------
    def open_or_update_promotion(self, *, branch: str, path: str, content: str,
                                 title: str, body: str, extra_files: dict | None = None) -> dict:
        """Write the artifact (+ any extra_files, e.g. a per-space title sidecar) to the promotion
        branch and open-or-reuse the PR. Idempotent: while a PR is open, re-requesting updates the
        same branch + PR (no spam). When there's NO open PR (first request, or a prior promotion
        merged), the branch is reset to base first so the new PR has a clean diff (avoids the
        stale-branch / 'no commits between' edge)."""
        pr = self._find_open_pr(branch)
        if pr is None:
            self._reset_branch_to_base(branch)
        self._put_file(branch, path, content, f"promote: update {path}")
        for p, c in (extra_files or {}).items():
            self._put_file(branch, p, c, f"promote: update {p}")
        return pr if pr is not None else self._create_pr(branch, title, body)

    def get_file_content(self, path: str, *, ref: str | None = None) -> str | None:
        """Read a committed file's raw text content (decoded from the GitHub contents API's base64
        body), or None if absent on `ref` (defaults to `self.base`, i.e. `main`). Used by callers
        that need to MERGE into an existing committed artifact (e.g. F3 adding a principal to an
        already-committed AccessSpec sidecar) rather than blindly overwrite it via
        `open_or_update_promotion`."""
        ref = ref or self.base
        status, body = self._transport(
            "GET", f"{_API}{self._repo_path(f'/contents/{path}')}?ref={ref}",
            self._headers(self._token()), None)
        if status == 404:
            return None
        if status != 200 or not body:
            raise GitHubError(status, "get file content", body)
        return base64.b64decode(body["content"]).decode()

    def _reset_branch_to_base(self, branch: str) -> None:
        """Point the branch at the base sha — creating it if absent (a fresh per-space branch) or
        force-resetting it if it exists. Check existence with a GET first: GitHub returns 404 on a
        missing ref for GET but 422 'Reference does not exist' on PATCH, so relying on the PATCH error
        code mis-handles a brand-new branch (the per-space case)."""
        _, base_ref = self._api("GET", self._repo_path(f"/git/ref/heads/{self.base}"), "get base ref")
        sha = base_ref["object"]["sha"]
        exists, _ = self._transport(
            "GET", f"{_API}{self._repo_path(f'/git/ref/heads/{branch}')}", self._headers(self._token()), None)
        if exists == 200:  # branch exists -> force-reset to base for a clean diff
            status, body = self._transport(
                "PATCH", f"{_API}{self._repo_path(f'/git/refs/heads/{branch}')}",
                self._headers(self._token()), {"sha": sha, "force": True})
            if status != 200:
                raise GitHubError(status, "reset branch", body)
        else:  # branch absent -> create it
            self._api("POST", self._repo_path("/git/refs"), "create branch",
                      {"ref": f"refs/heads/{branch}", "sha": sha})

    def _put_file(self, branch: str, path: str, content: str, message: str) -> None:
        # If the file already exists on the branch we must pass its blob sha to update it.
        status, existing = self._transport(
            "GET", f"{_API}{self._repo_path(f'/contents/{path}')}?ref={branch}",
            self._headers(self._token()), None)
        if status not in (200, 404):  # only 404 means "absent"; surface real errors, don't 422 blindly
            raise GitHubError(status, "get file", existing)
        payload = {"message": message, "branch": branch,
                   "content": base64.b64encode(content.encode()).decode()}
        if status == 200 and existing:
            payload["sha"] = existing["sha"]
        self._api("PUT", self._repo_path(f"/contents/{path}"), "put file", payload)

    def _find_open_pr(self, branch: str) -> dict | None:
        # The promotion branch is bot-owned, so the first open PR for it is ours.
        _, prs = self._api("GET", self._repo_path(f"/pulls?head={self.owner}:{branch}&state=open"),
                           "list pulls")
        if prs:
            return {"number": prs[0]["number"], "html_url": prs[0]["html_url"]}
        return None

    def _create_pr(self, branch: str, title: str, body: str) -> dict:
        _, pr = self._api("POST", self._repo_path("/pulls"), "create pull",
                          {"title": title, "head": branch, "base": self.base, "body": body})
        return {"number": pr["number"], "html_url": pr["html_url"]}

    # --- live status (GH3): reflect, never assert ---------------------------
    def get_status(self, number: int) -> dict:
        """Read the live promotion state from GitHub (as the bot): PR check conclusion, merge
        state, and — once merged — the prod deploy run + whether its Environment gate is waiting.
        Reflects GitHub; it never asserts a deploy that didn't happen."""
        _, pr = self._api("GET", self._repo_path(f"/pulls/{number}"), "get pull")
        head_sha = pr["head"]["sha"]
        merged = bool(pr.get("merged"))
        _, cr = self._api("GET", self._repo_path(f"/commits/{head_sha}/check-runs"), "check runs")
        checks = _aggregate_checks((cr or {}).get("check_runs", []))
        deploy = self._deploy_status(pr.get("merge_commit_sha")) if merged else dict(_NO_DEPLOY)
        # A merged PR was approvable by definition; otherwise read the live PR-review decision.
        review_decision = "approved" if merged else self._pr_review_decision(number)
        return {
            "pr_state": pr.get("state"), "merged": merged, "checks": checks,
            "review_decision": review_decision,
            "deploy": deploy, "pr_url": pr.get("html_url"),
            "phase": _derive_phase(pr.get("state"), merged, checks, deploy),
        }

    def _pr_review_decision(self, number: int) -> str:
        """The PR's merge-approval gate, read from its reviews. Tolerant: any API hiccup or an empty
        review list reads as `review_required` (the safe default — never asserts an approval).

        Reviews arrive in chronological order; we keep each reviewer's LATEST decisive state. A
        standing CHANGES_REQUESTED from anyone blocks; else any standing APPROVED clears it; else
        it's still required. Only APPROVED/CHANGES_REQUESTED decide; a DISMISSED review CLEARS that
        reviewer's prior decisive state (a dismissed approval no longer counts as standing approval);
        COMMENTED/PENDING are ignored."""
        status, reviews = self._transport(
            "GET", f"{_API}{self._repo_path(f'/pulls/{number}/reviews')}",
            self._headers(self._token()), None)
        if status != 200 or not reviews:
            return "review_required"
        latest: dict[str, str] = {}
        for r in reviews:
            login = (r.get("user") or {}).get("login")
            state = r.get("state")
            if not login:
                continue
            if state in ("APPROVED", "CHANGES_REQUESTED"):
                latest[login] = state  # later entry wins -> latest decisive state per reviewer
            elif state == "DISMISSED":
                latest.pop(login, None)  # a dismissed review clears that reviewer's prior decision
        states = latest.values()
        if "CHANGES_REQUESTED" in states:
            return "changes_requested"
        if "APPROVED" in states:
            return "approved"
        return "review_required"

    def _deploy_status(self, merge_sha: str | None = None) -> dict:
        """THIS promotion's `deploy` run + whether its gate is waiting. Correlated to the PR's
        merge commit so a concurrent/unrelated push to base can't be mistaken for our deploy;
        falls back to the latest `deploy` run only when no merge-sha match is found."""
        _, runs = self._api(
            "GET", self._repo_path(f"/actions/runs?branch={self.base}&event=push&per_page=20"),
            "actions runs")
        deploys = [r for r in (runs or {}).get("workflow_runs", [])
                   if "/deploy.yml" in (r.get("path") or "") or r.get("name") == "deploy"]
        wf = None
        if merge_sha:
            wf = next((r for r in deploys if r.get("head_sha") == merge_sha), None)
        if wf is None:
            wf = deploys[0] if deploys else None  # fallback: most recent deploy run
        if not wf:
            return dict(_NO_DEPLOY)
        status = wf.get("status")  # queued | in_progress | completed | waiting (gate pending)
        conclusion = wf.get("conclusion")
        run_id = wf.get("id")
        # Who released the gate (GH4 + LB4's deploy_approved attribution) — fetch once the deploy has
        # COMPLETED (success OR failure), so the Steward's identity is captured for the audit even on
        # a failed deploy. Only on `completed` (not every polling tick), so /approvals isn't hammered.
        approver = self._deploy_approver(run_id) if run_id and status == "completed" else None
        return {"status": status, "conclusion": conclusion,
                "waiting_approval": status == "waiting", "run_url": wf.get("html_url"),
                "run_id": run_id, "approver": approver}

    def audit_facts(self, number: int) -> dict:
        """GitHub-sourced governance identities + timestamps for the durable audit trail (LB4).

        Cold path: reconcile calls this ONLY when it is about to append a `merged`/`pr_review_approved`
        event (a real transition, idempotent) — never on the hot 5s status poll — so the extra reads
        don't tax the poll. Returns the PR's merger + the latest approving reviewer (each with its
        GitHub login + timestamp). The deploy gate's approver comes from `get_status` (already read).
        Tolerant: any field GitHub doesn't expose is None."""
        _, pr = self._api("GET", self._repo_path(f"/pulls/{number}"), "get pull (audit)")
        merged_by = (pr.get("merged_by") or {}).get("login")
        merged_at = pr.get("merged_at")
        approver, approved_at = None, None
        status, reviews = self._transport(
            "GET", f"{_API}{self._repo_path(f'/pulls/{number}/reviews')}",
            self._headers(self._token()), None)
        if status == 200 and reviews:
            latest: dict[str, tuple] = {}  # reviewer -> (state, submitted_at), latest wins
            for r in reviews:
                login = (r.get("user") or {}).get("login")
                state = r.get("state")
                if not login:
                    continue
                if state in ("APPROVED", "CHANGES_REQUESTED"):
                    latest[login] = (state, r.get("submitted_at"))
                elif state == "DISMISSED":
                    latest.pop(login, None)
            for login, (state, at) in latest.items():
                if state == "APPROVED":
                    approver, approved_at = login, at  # an approval stands -> attribute it
        return {"merged_by": merged_by, "merged_at": merged_at,
                "review_approver": approver, "review_approved_at": approved_at}

    def _deploy_approver(self, run_id: int) -> str | None:
        """The GitHub login that approved the deployment (the Steward) — read from the run's
        approvals. Best-effort: the bot reads it; SoD itself is enforced by the Environment gate."""
        status, body = self._transport(
            "GET", f"{_API}{self._repo_path(f'/actions/runs/{run_id}/approvals')}",
            self._headers(self._token()), None)
        if status != 200 or not body:
            return None
        for a in body:
            if a.get("state") == "approved":
                return (a.get("user") or {}).get("login")
        return None

    # --- F5 Phase 1: READ-ONLY gate introspection (drift detection) ---------
    #
    # These two accessors are the ENTIRE GitHub-write-adjacent surface `github_drift.py` uses. Both
    # are GET-only — no `administration:write` scope is requested or required. Phase 2 (writing
    # these gates) is explicitly out of scope (see genie_reviewer/github_drift.py's docstring).

    def get_environment_reviewers(self, environment: str) -> list[str]:
        """The GitHub logins configured as required reviewers on a deployment Environment (e.g.
        'prod') — what actually gates `deploy.yml`. Requires `administration:read` (or repo admin)
        on the classic Environments API; a 404 means the Environment doesn't exist (returns an
        empty list, not an error — "no environment" and "no reviewers" both mean nobody is
        specifically gated by name). Any OTHER failure (403 missing scope, 5xx, etc.) raises
        `GitHubError` so the caller (`github_drift.check_drift`) can surface it as `unknown_*`
        rather than silently reporting an empty (and therefore falsely "no drift") reviewer list."""
        status, body = self._transport(
            "GET", f"{_API}{self._repo_path(f'/environments/{environment}')}",
            self._headers(self._token()), None)
        if status == 404:
            return []
        if status != 200 or not body:
            raise GitHubError(status, "get environment", body)
        logins: list[str] = []
        for rule in (body.get("protection_rules") or []):
            if rule.get("type") != "required_reviewers":
                continue
            for reviewer in (rule.get("reviewers") or []):
                r = reviewer.get("reviewer") or {}
                login = r.get("login")
                if login:
                    logins.append(login)
        return logins

    def get_branch_protection(self, branch: str) -> Optional[dict]:
        """The branch protection settings for `branch` (e.g. 'main'), or None if the branch has NO
        protection at all (a 404 from this endpoint — a legitimate, common state, not an error).
        Any OTHER failure raises `GitHubError` (surfaced as `unknown_branch_protection` by the
        caller) so an unreachable/unscoped read is never mistaken for "no protection configured"."""
        status, body = self._transport(
            "GET", f"{_API}{self._repo_path(f'/branches/{branch}/protection')}",
            self._headers(self._token()), None)
        if status == 404:
            return None
        if status != 200 or not body:
            raise GitHubError(status, "get branch protection", body)
        return body

    # --- comment (one canonical, updated in place) --------------------------
    def upsert_comment(self, number: int, marker: str, body: str) -> dict:
        _, comments = self._api("GET", self._repo_path(f"/issues/{number}/comments"), "list comments")
        for c in comments or []:
            if marker in (c.get("body") or ""):
                _, updated = self._api("PATCH", self._repo_path(f"/issues/comments/{c['id']}"),
                                       "update comment", {"body": body})
                return {"id": updated["id"], "updated": True}
        _, created = self._api("POST", self._repo_path(f"/issues/{number}/comments"),
                               "create comment", {"body": body})
        return {"id": created["id"], "updated": False}
