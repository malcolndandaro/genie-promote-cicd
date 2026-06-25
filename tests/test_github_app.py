"""Unit tests for the GitHub App client (GH2) — a fake GitHub transport, no network, no real key."""
import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
from github_app import GitHubApp, GitHubError  # noqa: E402


class FakeGitHub:
    """Minimal in-memory GitHub the transport talks to (refs, files, pulls, comments)."""

    def __init__(self):
        self.refs = {"main": "sha-main"}
        self.files: dict = {}      # (branch, path) -> {sha, content}
        self.pulls: list = []      # {number, head, html_url}
        self.comments: dict = {}   # number -> [{id, body}]
        self._pr = 0
        self._cid = 0
        self.calls: list = []

    def transport(self, method, url, headers, body):
        path = url.split("api.github.com", 1)[-1]
        self.calls.append((method, path))

        if path.endswith("/access_tokens") and method == "POST":
            return 201, {"token": "ghs_fake"}

        if "/git/ref/heads/" in path and method == "GET":
            branch = path.rsplit("/heads/", 1)[1]
            return (200, {"object": {"sha": self.refs[branch]}}) if branch in self.refs else (404, {})

        if path.endswith("/git/refs") and method == "POST":
            self.refs[body["ref"].split("refs/heads/")[1]] = body["sha"]
            return 201, {"ref": body["ref"]}

        if "/git/refs/heads/" in path and method == "PATCH":
            branch = path.rsplit("/heads/", 1)[1]
            if branch in self.refs:
                self.refs[branch] = body["sha"]
                return 200, {"object": {"sha": body["sha"]}}
            return 404, {}

        if "/contents/" in path:
            fp = path.split("/contents/", 1)[1]
            if "?" in fp:
                fp, q = fp.split("?", 1)
                branch = q.split("ref=")[1]
            else:
                branch = body["branch"]
            key = (branch, fp)
            if method == "GET":
                return (200, {"sha": self.files[key]["sha"]}) if key in self.files else (404, {})
            if method == "PUT":
                self._cid_file = self.files.get(key, {})
                self.files[key] = {"sha": f"blob-{len(self.files) + 1}", "content": body["content"],
                                   "had_sha": "sha" in body}
                return 200, {"content": {"sha": self.files[key]["sha"]}}

        if path.split("?")[0].endswith("/pulls"):
            if method == "GET":
                branch = path.split("head=")[1].split("&")[0].split(":")[1]
                return 200, [p for p in self.pulls if p["head"] == branch]
            if method == "POST":
                self._pr += 1
                pr = {"number": self._pr, "head": body["head"],
                      "html_url": f"https://github.com/o/r/pull/{self._pr}"}
                self.pulls.append(pr)
                self.comments[self._pr] = []
                return 201, pr

        if "/issues/" in path and path.endswith("/comments"):
            num = int(path.split("/issues/")[1].split("/comments")[0])
            if method == "GET":
                return 200, self.comments.get(num, [])
            if method == "POST":
                self._cid += 1
                c = {"id": self._cid, "body": body["body"]}
                self.comments.setdefault(num, []).append(c)
                return 201, c

        if "/issues/comments/" in path and method == "PATCH":
            cid = int(path.rsplit("/", 1)[1])
            for lst in self.comments.values():
                for c in lst:
                    if c["id"] == cid:
                        c["body"] = body["body"]
                        return 200, c
            return 404, {}

        return 500, {"message": f"unhandled {method} {path}"}


def _app(fg):
    return GitHubApp(owner="o", repo="r", transport=fg.transport, token_provider=lambda: "tok")


def test_open_creates_branch_file_and_pr():
    fg = FakeGitHub()
    pr = _app(fg).open_or_update_promotion(branch="promote/x", path="src/genie/s.json",
                                           content="{}", title="t", body="b")
    assert pr == {"number": 1, "html_url": "https://github.com/o/r/pull/1"}
    assert "promote/x" in fg.refs
    assert ("promote/x", "src/genie/s.json") in fg.files
    assert len(fg.pulls) == 1


def test_idempotent_reuses_pr_and_updates_file_in_place():
    fg = FakeGitHub()
    gh = _app(fg)
    gh.open_or_update_promotion(branch="promote/x", path="p", content="aaa", title="t", body="b")
    pr2 = gh.open_or_update_promotion(branch="promote/x", path="p", content="bbb", title="t", body="b")
    assert pr2["number"] == 1            # same PR reused
    assert len(fg.pulls) == 1            # no second PR opened
    f = fg.files[("promote/x", "p")]
    assert base64.b64decode(f["content"]).decode() == "bbb"  # file updated
    assert f["had_sha"] is True          # update passed the existing blob sha


def test_resets_stale_branch_to_base_when_no_open_pr():
    # A prior promotion merged: the branch still exists but no PR is open. The next request must
    # reset it to base (clean diff) and open a fresh PR — not reuse the stale branch.
    fg = FakeGitHub()
    fg.refs["promote/x"] = "stale-sha"
    pr = _app(fg).open_or_update_promotion(branch="promote/x", path="p", content="x",
                                           title="t", body="b")
    assert fg.refs["promote/x"] == "sha-main"  # reset to base
    assert pr["number"] == 1                    # a new PR was opened
    assert any(m == "PATCH" and "/git/refs/heads/promote/x" in p for m, p in fg.calls)


def test_no_branch_reset_while_a_pr_is_open():
    # Second request with the PR open updates the file in place — no branch reset (PATCH).
    fg = FakeGitHub()
    gh = _app(fg)
    gh.open_or_update_promotion(branch="b", path="p", content="a", title="t", body="b")
    fg.calls.clear()
    gh.open_or_update_promotion(branch="b", path="p", content="b", title="t", body="b")
    assert not any(m == "PATCH" for m, _ in fg.calls)  # no reset while the PR is open


def test_upsert_comment_creates_then_updates_one_comment():
    fg = FakeGitHub()
    gh = _app(fg)
    pr = gh.open_or_update_promotion(branch="b", path="p", content="x", title="t", body="b")
    r1 = gh.upsert_comment(pr["number"], "<!--m-->", "<!--m-->\nfirst")
    r2 = gh.upsert_comment(pr["number"], "<!--m-->", "<!--m-->\nsecond")
    assert r1["updated"] is False and r2["updated"] is True and r2["id"] == r1["id"]
    assert len(fg.comments[pr["number"]]) == 1                       # one comment, updated in place
    assert fg.comments[pr["number"]][0]["body"].endswith("second")


def test_github_error_raised_on_non_2xx():
    fg = FakeGitHub()

    def failing(method, url, headers, body):
        if method == "POST" and url.split("api.github.com")[-1].endswith("/pulls"):
            return 422, {"message": "validation failed"}
        return fg.transport(method, url, headers, body)

    gh = GitHubApp(owner="o", repo="r", transport=failing, token_provider=lambda: "tok")
    with pytest.raises(GitHubError) as e:
        gh.open_or_update_promotion(branch="b", path="p", content="x", title="t", body="b")
    assert e.value.status == 422


def test_installation_token_is_cached_across_calls():
    fg = FakeGitHub()
    n = {"c": 0}

    def provider():
        n["c"] += 1
        return "tok"

    gh = GitHubApp(owner="o", repo="r", transport=fg.transport, token_provider=provider,
                   now=lambda: 1000.0)
    pr = gh.open_or_update_promotion(branch="b", path="p", content="x", title="t", body="b")
    gh.upsert_comment(pr["number"], "<!--m-->", "<!--m-->\nx")
    assert n["c"] == 1  # token minted once, then cached (not re-minted per request)
