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
from typing import Callable

_API = "https://api.github.com"
Transport = Callable[[str, str, dict, dict | None], "tuple[int, dict | None]"]


class GitHubError(RuntimeError):
    def __init__(self, status: int, label: str, body: dict | None):
        super().__init__(f"GitHub {label} -> HTTP {status}")
        self.status = status
        self.body = body


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
                                 title: str, body: str) -> dict:
        """Write the artifact to the promotion branch and open-or-reuse the PR. Idempotent: while a
        PR is open, re-requesting updates the same branch + PR (no spam). When there's NO open PR
        (first request, or a prior promotion merged), the branch is reset to base first so the new
        PR has a clean diff (avoids the stale-branch / 'no commits between' edge)."""
        pr = self._find_open_pr(branch)
        if pr is None:
            self._reset_branch_to_base(branch)
        self._put_file(branch, path, content, f"promote: update {path}")
        return pr if pr is not None else self._create_pr(branch, title, body)

    def _reset_branch_to_base(self, branch: str) -> None:
        """Point the branch at the base sha (force), creating it if absent — a fresh start."""
        _, base_ref = self._api("GET", self._repo_path(f"/git/ref/heads/{self.base}"), "get base ref")
        sha = base_ref["object"]["sha"]
        status, body = self._transport(
            "PATCH", f"{_API}{self._repo_path(f'/git/refs/heads/{branch}')}",
            self._headers(self._token()), {"sha": sha, "force": True})
        if status == 200:
            return
        if status == 404:  # branch doesn't exist yet -> create it
            self._api("POST", self._repo_path("/git/refs"), "create branch",
                      {"ref": f"refs/heads/{branch}", "sha": sha})
            return
        raise GitHubError(status, "reset branch", body)

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
