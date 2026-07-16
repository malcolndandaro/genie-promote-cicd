"""GitHub App (bot) client — the deep module the app uses for all mechanical GitHub ops (GH2/GH3).

Encapsulates GitHub App auth + the REST calls behind a small interface:
  - open_or_update_promotion(branch, path, content, title, body) -> {number, html_url}
  - post_review_comment(number, marker, body) -> {id, seq}   (a NEW comment per review — history,
                                                              not one canonical comment mutated
                                                              in place; marker + seq/timestamp
                                                              header on every comment)
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


def _failed_check_runs(runs: list) -> list:
    """The completed runs that made `_aggregate_checks` report `failure` (mirrors its predicate)."""
    return [r for r in runs if r.get("status") == "completed"
            and r.get("conclusion") not in ("success", "neutral", "skipped")]


# G9: found live — the app's annotation fallback was picking up GitHub's OWN generic noise (a
# Node.js-version deprecation warning + the auto "Process completed with exit code N." failure
# annotation every non-zero step gets) instead of anything useful, because our checks (e.g.
# GRANT-01) weren't emitting real annotations at all — bare print()s never become one. Now that
# `scripts/check_grants.py` emits real `::error::`/`::warning::` annotations, this needs to prefer
# those over GitHub's own noise rather than just joining everything in annotation order.
_NOISE_PREFIXES = ("Process completed with exit code",)
_NOISE_SUBSTRINGS = ("Node.js 20 is deprecated",)


def _is_noise_annotation(message: str) -> bool:
    return message.startswith(_NOISE_PREFIXES) or any(s in message for s in _NOISE_SUBSTRINGS)


def _summarize_annotations(annotations: list) -> str:
    """Join a failing check-run's annotation messages into its summary text — preferring
    FAILURE-level annotations (our own `::error::` findings, e.g. GRANT-01's baseline BLOCKERs,
    report at this level; GitHub's warning-level deprecation notices don't) and filtering the
    well-known noise annotations, but ONLY when doing so still leaves something real to show —
    never filtering a run's summary down to nothing."""
    msgs = [a["message"].strip() for a in annotations if a.get("message")]
    if not msgs:
        return ""
    failures = [a["message"].strip() for a in annotations
                if a.get("annotation_level") == "failure" and a.get("message")]
    pool = failures or msgs  # prefer failure-level; fall back to every annotation otherwise
    filtered = [m for m in pool if not _is_noise_annotation(m)]
    return "\n".join(filtered or pool)  # never filter a real pool down to nothing


def _truncate(text: str, limit: int = 500) -> str:
    """Sane truncation for a check-run summary (G8) — never dump an unbounded log into the app."""
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


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
                                 title: str, body: str, extra_files: dict | None = None,
                                 remove_files: "list[str] | None" = None) -> dict:
        """Write the artifact (+ any extra_files, e.g. a per-space title sidecar) to the promotion
        branch and open-or-reuse the PR. Idempotent: while a PR is open, re-requesting updates the
        same branch + PR (no spam). When there's NO open PR (first request, or a prior promotion
        merged), the branch is reset to base first so the new PR has a clean diff (avoids the
        stale-branch / 'no commits between' edge).

        `remove_files` (G9, found live PR #25): sidecar paths to DELETE from the branch when THIS
        request no longer declares them. `extra_files` only ever UPSERTS — a prior round's
        `.access.json`/`.mapping.json` would otherwise survive on the branch forever once committed,
        even after the Requester clears the declaration, and CI would keep reading the stale
        sidecar. Deleting is 404-tolerant (`_delete_file_if_exists`), so this is always safe to pass
        unconditionally, including on a path that was never committed."""
        pr = self._find_open_pr(branch)
        if pr is None:
            self._reset_branch_to_base(branch)
        self._put_file(branch, path, content, f"promote: update {path}")
        for p, c in (extra_files or {}).items():
            self._put_file(branch, p, c, f"promote: update {p}")
        for p in (remove_files or []):
            self._delete_file_if_exists(branch, p, f"promote: clear {p}")
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

    def _delete_file_if_exists(self, branch: str, path: str, message: str) -> None:
        """DELETE a committed file from `branch`, if present. A 404 (never committed, or already
        cleared by a prior call) is a silent no-op — mirrors `_put_file`'s GET-then-act shape, but
        this must be safe to call UNCONDITIONALLY (every request passes every optional sidecar path
        here, declared or not)."""
        status, existing = self._transport(
            "GET", f"{_API}{self._repo_path(f'/contents/{path}')}?ref={branch}",
            self._headers(self._token()), None)
        if status == 404:
            return
        if status != 200 or not existing:
            raise GitHubError(status, "get file (for delete)", existing)
        self._api("DELETE", self._repo_path(f"/contents/{path}"), "delete file",
                  {"message": message, "sha": existing["sha"], "branch": branch})

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
        runs = (cr or {}).get("check_runs", [])
        checks = _aggregate_checks(runs)
        # G8: WHY it failed, in the app — no new bot permission (checks:read already covers both
        # calls below). Only fetched on a failing verdict, so the happy-path poll pays nothing extra.
        checks_detail = self._checks_detail(runs) if checks == "failure" else None
        deploy = self._deploy_status(pr.get("merge_commit_sha")) if merged else dict(_NO_DEPLOY)
        # Fix C: WHY the DEPLOY failed (e.g. apply_access.py crashing on real declared access) —
        # same "only on a failing verdict" gating as checks_detail, no new bot permission
        # (actions:read already covers /jobs; annotations reuse the checks_detail scope).
        deploy_detail = (self._deploy_detail(deploy["run_id"], deploy["run_url"])
                         if deploy["run_id"] and deploy["conclusion"] == "failure" else None)
        # A merged PR was approvable by definition; otherwise read the live PR-review decision.
        review_decision = "approved" if merged else self._pr_review_decision(number)
        return {
            "pr_state": pr.get("state"), "merged": merged, "checks": checks,
            "checks_detail": checks_detail,
            "review_decision": review_decision,
            "deploy": deploy, "deploy_detail": deploy_detail, "pr_url": pr.get("html_url"),
            "phase": _derive_phase(pr.get("state"), merged, checks, deploy),
        }

    def _checks_detail(self, runs: list) -> list[dict] | None:
        """PT-friendly detail per FAILING check run (G8): name, conclusion, a best-effort summary,
        and the GitHub link as a fallback. Degrades to `None` (never a partial/broken list) on any
        unexpected error — the status read itself must never fail because this enrichment did."""
        try:
            return [{
                "name": r.get("name") or "check",
                "conclusion": r.get("conclusion"),
                "summary": _truncate(self._check_run_summary(r)),
                "details_url": r.get("details_url") or r.get("html_url"),
            } for r in _failed_check_runs(runs)]
        except Exception:  # noqa: BLE001 — a detail-fetch hiccup must not break the status read
            return None

    def _check_run_summary(self, run: dict) -> str:
        """The most useful text GitHub has for this failing run. Bare `run:` steps (e.g. the
        GRANT-01/`bundle validate` gates, plain `print`/exit-code failures) rarely populate
        `output` — the CI's PT findings live in the job LOG, not the Checks API — so this falls
        back to the run's annotations, summarized (preferring real `::error::`/`::warning::`
        content over GitHub's own generic noise — `_summarize_annotations`); a run with neither
        yields "" and the caller's fallback is just name+conclusion+details_url."""
        output = run.get("output") or {}
        text = (output.get("summary") or output.get("title") or "").strip()
        if text:
            return text
        run_id = run.get("id")
        if not run_id:
            return ""
        try:
            _, annotations = self._api(
                "GET", self._repo_path(f"/check-runs/{run_id}/annotations"), "check run annotations")
        except GitHubError:
            return ""
        return _summarize_annotations(annotations or [])

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
        merge commit so a concurrent/unrelated push to base can't be mistaken for our deploy.

        When a `merge_sha` is known (the PR is merged) but NO deploy run matches it yet, report NO
        deploy (phase stays `merged`) instead of falling back to the latest run: right after a merge
        there is a window where GitHub hasn't created THIS promotion's deploy run yet (webhook +
        self-hosted-runner pickup latency), and the most-recent deploy run on `base` is a PRIOR
        promotion's completed+success run — falling back to it made `_derive_phase` read `deployed`
        (all steps green, "Implantado em produção") seconds before this promotion's gate even
        appeared, then correct back down to `awaiting_approval`. Worse, a poll in that window let
        `reconcile` persist a false terminal `deployed` + audit event. The latest-run fallback is
        kept ONLY for the no-merge-sha case (defensive; also what the legacy merged-PR tests exercise)."""
        _, runs = self._api(
            "GET", self._repo_path(f"/actions/runs?branch={self.base}&event=push&per_page=20"),
            "actions runs")
        deploys = [r for r in (runs or {}).get("workflow_runs", [])
                   if "/deploy.yml" in (r.get("path") or "") or r.get("name") == "deploy"]
        if merge_sha:
            wf = next((r for r in deploys if r.get("head_sha") == merge_sha), None)
            if wf is None:
                # Our run hasn't materialized yet — do NOT borrow a prior cycle's run (see docstring).
                return dict(_NO_DEPLOY)
        else:
            wf = deploys[0] if deploys else None  # no merge sha: latest-run fallback (defensive)
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

    def _deploy_detail(self, run_id: int, run_url: str | None) -> dict | None:
        """PT-friendly detail for a FAILED deploy run (Fix C — mirrors G8's `_checks_detail`, one
        level down: a deploy failure is a WORKFLOW JOB failing, not a PR check-run). Walks the
        run's jobs for the first step with `conclusion == "failure"`, and — best-effort — that
        job's own check-run annotations (a job IS a check run on GitHub Actions; `check_run_url`
        carries its id), summarized with the SAME noise filtering as `_check_run_summary`.
        Degrades to `None` on any hiccup — `get_status` must never fail because this enrichment
        did (same contract as `_checks_detail`)."""
        try:
            _, body = self._api(
                "GET", self._repo_path(f"/actions/runs/{run_id}/jobs"), "workflow run jobs")
            for job in (body or {}).get("jobs", []):
                failed_step = next(
                    (s for s in (job.get("steps") or []) if s.get("conclusion") == "failure"), None)
                if failed_step is None:
                    continue
                return {
                    "failed_step": failed_step.get("name") or job.get("name") or "step",
                    "summary": _truncate(self._job_annotations_summary(job)),
                    "details_url": job.get("html_url") or run_url,
                }
            return None
        except Exception:  # noqa: BLE001 — a detail-fetch hiccup must not break the status read
            return None

    def _job_annotations_summary(self, job: dict) -> str:
        """Best-effort annotations for a failing job, via its check-run id (parsed from
        `check_run_url`, e.g. `.../check-runs/12345`). "" if the job carries none or the fetch
        hiccups — the caller's fallback is just failed_step + details_url."""
        check_run_url = job.get("check_run_url") or ""
        run_id = check_run_url.rstrip("/").rsplit("/", 1)[-1]
        if not run_id:
            return ""
        try:
            _, annotations = self._api(
                "GET", self._repo_path(f"/check-runs/{run_id}/annotations"), "check run annotations")
        except GitHubError:
            return ""
        return _summarize_annotations(annotations or [])

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

    # --- comment (a NEW comment per review — history, never mutated in place) ----------------
    def post_review_comment(self, number: int, marker: str, body: str) -> dict:
        """POST a fresh comment for every review request — a re-request must NOT erase the prior
        round's findings via an in-place PATCH (stakeholder decision, found live: a re-review
        looked like nothing had happened). `body` already carries `marker` as its hidden first
        line (`render_promotion_comment`); this inserts a human-visible "Revisão #N — <timestamp>
        UTC" header right after it, so the PR timeline reads as a history, not a single mutating
        comment. `marker` still tags EVERY review comment (not just one) — a caller that needs
        "the current promotion comment" must scan for `marker` and take the LATEST match (by id
        or created_at), never assume there's exactly one."""
        _, comments = self._api("GET", self._repo_path(f"/issues/{number}/comments"), "list comments")
        seq = sum(1 for c in (comments or []) if marker in (c.get("body") or "")) + 1
        marker_line, _, rest = body.partition("\n")
        header = f"### 🧞 Revisão #{seq} — {self._utc_now_str()}"
        dated_body = f"{marker_line}\n{header}\n{rest.lstrip(chr(10))}"
        _, created = self._api("POST", self._repo_path(f"/issues/{number}/comments"),
                               "create comment", {"body": dated_body})
        return {"id": created["id"], "seq": seq}

    def _utc_now_str(self) -> str:
        """UTC timestamp for the review-history header, via the injectable clock (`self._now`) —
        same testability pattern as token caching, so a test can assert an exact header string."""
        return time.strftime("%Y-%m-%d %H:%M", time.gmtime(self._now())) + " UTC"
