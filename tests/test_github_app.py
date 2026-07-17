"""Unit tests for the GitHub App client (GH2) — a fake GitHub transport, no network, no real key."""
import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
from github_app import (  # noqa: E402
    GitHubApp, GitHubError, _parse_deployment_attempt_annotations, _summarize_annotations,
)


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
                # Real GitHub returns both `sha` and base64 `content` — the fake mirrors both so
                # get_file_content (F3) can decode it, same as _put_file only ever used `sha`.
                return (200, {"sha": self.files[key]["sha"], "content": self.files[key]["content"]}) \
                    if key in self.files else (404, {})
            if method == "PUT":
                self._cid_file = self.files.get(key, {})
                self.files[key] = {"sha": f"blob-{len(self.files) + 1}", "content": body["content"],
                                   "had_sha": "sha" in body}
                return 200, {"content": {"sha": self.files[key]["sha"]}}
            if method == "DELETE":
                if key not in self.files:
                    return 404, {}
                if body.get("sha") != self.files[key]["sha"]:  # real GitHub 409s on a stale sha
                    return 409, {"message": "sha does not match"}
                del self.files[key]
                return 200, {"commit": {}}

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


def test_open_creates_a_brand_new_branch_when_patch_returns_422():
    # Regression: real GitHub returns 422 "Reference does not exist" (NOT 404) when PATCHing a ref
    # that doesn't exist. A per-space branch is brand-new, so the bot must create it, not error.
    fg = FakeGitHub()

    def transport(method, url, headers, body):
        path = url.split("api.github.com")[-1]
        if method == "PATCH" and "/git/refs/heads/" in path:  # mimic GitHub on a missing ref
            br = path.rsplit("/heads/", 1)[1]
            if br not in fg.refs:
                return 422, {"message": "Reference does not exist"}
        return fg.transport(method, url, headers, body)

    gh = GitHubApp(owner="o", repo="r", transport=transport, token_provider=lambda: "tok")
    pr = gh.open_or_update_promotion(branch="promote/s_new", path="src/genie/s_new.json",
                                     content="{}", title="t", body="b")
    assert pr["number"] == 1 and "promote/s_new" in fg.refs   # branch created, PR opened — no error


def test_open_commits_extra_files_alongside_the_artifact():
    # The per-space title sidecar (and any extra files) are committed to the same branch + PR.
    fg = FakeGitHub()
    _app(fg).open_or_update_promotion(
        branch="promote/x", path="src/genie/s.json", content="{}", title="t", body="b",
        extra_files={"src/genie/s.title": "My Space\n"})
    assert ("promote/x", "src/genie/s.json") in fg.files
    assert ("promote/x", "src/genie/s.title") in fg.files
    assert base64.b64decode(fg.files[("promote/x", "src/genie/s.title")]["content"]).decode() == "My Space\n"


# --- G9: remove_files — clearing a stale sidecar on a re-request (found live, PR #25) -----------


def test_remove_files_deletes_a_previously_committed_sidecar():
    fg = FakeGitHub()
    gh = _app(fg)
    gh.open_or_update_promotion(branch="promote/x", path="p", content="{}", title="t", body="b",
                                extra_files={"src/genie/x.mapping.json": '{"a": 1}'})
    assert ("promote/x", "src/genie/x.mapping.json") in fg.files

    gh.open_or_update_promotion(branch="promote/x", path="p", content="{}", title="t", body="b",
                                remove_files=["src/genie/x.mapping.json"])
    assert ("promote/x", "src/genie/x.mapping.json") not in fg.files


def test_remove_files_is_a_noop_for_a_path_never_committed():
    # Every request passes every optional sidecar path here, declared or not — must never error on
    # one that was never written (the common case: nothing was ever declared for this space).
    fg = FakeGitHub()
    pr = _app(fg).open_or_update_promotion(branch="promote/x", path="p", content="{}", title="t",
                                           body="b", remove_files=["src/genie/x.mapping.json"])
    assert pr["number"] == 1  # completed without error


def test_remove_files_does_not_touch_the_main_artifact_or_other_extra_files():
    fg = FakeGitHub()
    gh = _app(fg)
    gh.open_or_update_promotion(
        branch="promote/x", path="p", content="{}", title="t", body="b",
        extra_files={"src/genie/x.title": "T\n", "src/genie/x.mapping.json": "{}"})
    gh.open_or_update_promotion(branch="promote/x", path="p", content="{}", title="t", body="b",
                                remove_files=["src/genie/x.mapping.json"])
    assert ("promote/x", "p") in fg.files
    assert ("promote/x", "src/genie/x.title") in fg.files
    assert ("promote/x", "src/genie/x.mapping.json") not in fg.files


def test_get_file_content_returns_none_when_absent():
    fg = FakeGitHub()
    assert _app(fg).get_file_content("src/genie/s.mapping.json") is None


def test_get_file_content_reads_and_decodes_from_base_by_default():
    # F3: reading an existing sidecar off `main` (the default `ref`) so a caller can merge into it
    # rather than blindly overwrite. Seed the fake as if a prior PR had already merged the file.
    fg = FakeGitHub()
    fg.files[("main", "src/genie/s.mapping.json")] = {
        "sha": "sha1", "content": base64.b64encode(b'{"space_permissions": []}').decode()}
    got = _app(fg).get_file_content("src/genie/s.mapping.json")
    assert got == '{"space_permissions": []}'


def test_get_file_content_reads_from_an_explicit_ref():
    fg = FakeGitHub()
    fg.files[("promote/x", "p")] = {"sha": "s", "content": base64.b64encode(b"branch content").decode()}
    assert _app(fg).get_file_content("p", ref="promote/x") == "branch content"
    assert _app(fg).get_file_content("p", ref="main") is None


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


def test_no_op_promotion_when_content_matches_base_opens_no_pr():
    # The reported case: the space is already in prod byte-identical (its serialized_space + title
    # sidecar match `main`). Promoting would create an EMPTY PR that triggers no CI. Detect it and
    # return {no_change: True} WITHOUT opening a PR or touching the branch.
    fg = FakeGitHub()
    fg.files[("main", "src/genie/s.json")] = {"sha": "b1", "content": base64.b64encode(b"{}").decode()}
    fg.files[("main", "src/genie/s.title")] = {"sha": "b2", "content": base64.b64encode(b"My Space\n").decode()}
    pr = _app(fg).open_or_update_promotion(
        branch="promote/x", path="src/genie/s.json", content="{}", title="t", body="b",
        extra_files={"src/genie/s.title": "My Space\n"})
    assert pr == {"no_change": True}
    assert len(fg.pulls) == 0                       # no PR opened
    assert "promote/x" not in fg.refs               # branch never created/touched
    assert ("promote/x", "src/genie/s.json") not in fg.files


def test_content_differing_from_base_still_opens_a_pr():
    # Guard the no-op detection doesn't over-fire: if the artifact differs from base (a real change),
    # the normal PR flow runs even when the title sidecar happens to match.
    fg = FakeGitHub()
    fg.files[("main", "src/genie/s.json")] = {"sha": "b1", "content": base64.b64encode(b"{}").decode()}
    pr = _app(fg).open_or_update_promotion(
        branch="promote/x", path="src/genie/s.json", content='{"changed": true}', title="t", body="b")
    assert pr["number"] == 1                         # a real PR opened
    assert ("promote/x", "src/genie/s.json") in fg.files


def test_no_op_detection_fails_open_on_a_github_read_error():
    # If the base read errors (not 200/404), we must NOT suppress the promotion — fall through to the
    # normal PR flow rather than risk silently dropping a real change on a transient GitHub hiccup.
    fg = FakeGitHub()

    def transport(method, url, headers, body):
        path = url.split("api.github.com")[-1]
        if method == "GET" and "/contents/" in path and "ref=main" in path:
            return 500, {"message": "boom"}
        return fg.transport(method, url, headers, body)

    gh = GitHubApp(owner="o", repo="r", transport=transport, token_provider=lambda: "tok")
    pr = gh.open_or_update_promotion(branch="promote/x", path="src/genie/s.json", content="{}",
                                     title="t", body="b")
    assert pr["number"] == 1  # fell through to the normal flow, didn't return no_change


def test_no_branch_reset_while_a_pr_is_open():
    # Second request with the PR open updates the file in place — no branch reset (PATCH).
    fg = FakeGitHub()
    gh = _app(fg)
    gh.open_or_update_promotion(branch="b", path="p", content="a", title="t", body="b")
    fg.calls.clear()
    gh.open_or_update_promotion(branch="b", path="p", content="b", title="t", body="b")
    assert not any(m == "PATCH" for m, _ in fg.calls)  # no reset while the PR is open


def test_post_review_comment_posts_a_new_comment_every_time():
    # A re-request must NOT erase the prior round's findings via an in-place PATCH (stakeholder
    # decision, found live) — two sequential requests must produce TWO comments, seq 1 then 2.
    fg = FakeGitHub()
    gh = _app(fg)
    pr = gh.open_or_update_promotion(branch="b", path="p", content="x", title="t", body="b")
    r1 = gh.post_review_comment(pr["number"], "<!--m-->", "<!--m-->\nfirst")
    r2 = gh.post_review_comment(pr["number"], "<!--m-->", "<!--m-->\nsecond")
    assert r1["seq"] == 1 and r2["seq"] == 2
    assert r2["id"] != r1["id"]                                       # a NEW comment, not the same one
    assert len(fg.comments[pr["number"]]) == 2                        # history, not one mutated comment
    bodies = [c["body"] for c in fg.comments[pr["number"]]]
    assert bodies[0].startswith("<!--m-->\n### 🧞 Revisão #1")
    assert bodies[0].endswith("first")
    assert bodies[1].startswith("<!--m-->\n### 🧞 Revisão #2")
    assert bodies[1].endswith("second")
    assert all("<!--m-->" in b for b in bodies)  # marker survives on EVERY review comment


def test_post_review_comment_header_uses_the_injected_clock():
    fg = FakeGitHub()
    gh = GitHubApp(owner="o", repo="r", transport=fg.transport, token_provider=lambda: "tok",
                   now=lambda: 1783976400.0)  # 2026-07-13 21:00:00 UTC
    pr = gh.open_or_update_promotion(branch="b", path="p", content="x", title="t", body="b")
    r = gh.post_review_comment(pr["number"], "<!--m-->", "<!--m-->\nbody")
    body = fg.comments[pr["number"]][0]["body"]
    assert "2026-07-13 21:00 UTC" in body
    assert r["seq"] == 1


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


def _status_transport(pull, check_runs, workflow_runs, approvals=None, reviews=None, annotations=None,
                      jobs=None, revision_manifests=None):
    """`jobs` (Fix C): {run_id: [job, ...]} for `/actions/runs/{id}/jobs` — a job's own
    `check-runs/{id}/annotations` reuses the SAME `annotations` dict (keyed by check-run id, same
    as the PR-check-run path above; a job IS a check run on GitHub Actions)."""
    def t(method, url, headers, body):
        p = url.split("api.github.com")[-1]
        if p.endswith("/access_tokens"):
            return 201, {"token": "x"}
        if p.endswith("/annotations"):  # must precede the generic /check-runs match below (G8)
            run_id = int(p.rsplit("/", 2)[1])
            return 200, (annotations or {}).get(run_id, [])
        if "/reviews" in p:  # must precede the generic /pulls/{n} branch (path also has /pulls/)
            return 200, reviews or []
        if "/pulls/" in p and "/files" in p:
            return (200, [{"filename": "src/genie/space.revision.json"}]
                    if revision_manifests else [])
        if "/contents/src/genie/space.revision.json" in p:
            ref = p.split("ref=", 1)[1]
            payload = (revision_manifests or {}).get(ref)
            if payload is None:
                return 404, {}
            return 200, {
                "sha": f"blob-{ref}",
                "content": base64.b64encode(json.dumps(payload).encode()).decode(),
            }
        if "/pulls/" in p and p.split("/pulls/")[1].split("?")[0].isdigit():
            return 200, pull
        if "/check-runs" in p:
            return 200, {"check_runs": check_runs}
        if p.endswith("/jobs") and "/actions/runs/" in p:  # Fix C: precedes the generic /actions/runs match
            run_id = int(p.split("/actions/runs/")[1].split("/jobs")[0])
            return 200, {"jobs": (jobs or {}).get(run_id, [])}
        if "/approvals" in p:  # must precede the /actions/runs check (it's a sub-path)
            return 200, approvals or []
        if "/actions/runs" in p:
            return 200, {"workflow_runs": workflow_runs}
        return 500, {"message": f"unhandled {method} {p}"}

    return GitHubApp(owner="o", repo="r", transport=t, token_provider=lambda: "tok")


_OPEN_PR = {"number": 1, "state": "open", "merged": False, "head": {"sha": "s"}, "html_url": "u"}
_MERGED_PR = {"number": 1, "state": "closed", "merged": True, "head": {"sha": "s"}, "html_url": "u"}


def test_audit_facts_returns_github_merger_and_approver_with_timestamps():
    # LB4: the cold-path enrichment reconcile uses for GitHub-sourced governance identities.
    pull = {**_MERGED_PR, "merged_by": {"login": "PSPedro176"}, "merged_at": "2026-06-26T13:00:00Z"}
    reviews = [
        {"user": {"login": "someone"}, "state": "COMMENTED", "submitted_at": "2026-06-26T11:00:00Z"},
        {"user": {"login": "PSPedro176"}, "state": "APPROVED", "submitted_at": "2026-06-26T12:00:00Z"},
    ]
    facts = _status_transport(pull, [], [], reviews=reviews).audit_facts(1)
    assert facts == {"merged_by": "PSPedro176", "merged_at": "2026-06-26T13:00:00Z",
                     "review_approver": "PSPedro176", "review_approved_at": "2026-06-26T12:00:00Z"}


def test_audit_facts_tolerant_when_unmerged_and_no_approval():
    facts = _status_transport(_OPEN_PR, [], [], reviews=[]).audit_facts(1)
    assert facts == {"merged_by": None, "merged_at": None,
                     "review_approver": None, "review_approved_at": None}


def test_status_checks_running_when_open():
    s = _status_transport(_OPEN_PR, [{"status": "in_progress", "conclusion": None}], []).get_status(1)
    assert s["checks"] == "pending" and s["merged"] is False and s["phase"] == "checks_running"
    assert s["deploy"]["status"] == "none"


def test_status_checks_failed():
    s = _status_transport(_OPEN_PR, [{"status": "completed", "conclusion": "failure"}], []).get_status(1)
    assert s["checks"] == "failure" and s["phase"] == "checks_failed"


# --- G8: "why did it fail?" without leaving the app -------------------------


def test_checks_detail_none_when_checks_pass():
    s = _status_transport(_OPEN_PR, [{"status": "completed", "conclusion": "success"}], []).get_status(1)
    assert s["checks_detail"] is None


def test_checks_detail_uses_output_summary_when_present():
    run = {"id": 1, "name": "bundle validate (prod)", "status": "completed", "conclusion": "failure",
           "output": {"summary": "algo deu errado"}, "details_url": "https://x/1"}
    s = _status_transport(_OPEN_PR, [run], []).get_status(1)
    assert s["checks"] == "failure"
    assert s["checks_detail"] == [{"name": "bundle validate (prod)", "conclusion": "failure",
                                   "summary": "algo deu errado", "details_url": "https://x/1"}]


def test_checks_detail_falls_back_to_annotations_when_output_is_empty():
    # Bare `run:` steps do not populate `output`; the findings live in annotations.
    # `::error::` workflow-command annotations (failure-level) — and GitHub's OWN generic noise (the
    # step's auto "Process completed with exit code N." failure annotation) must be filtered out
    # when real content is present (this is now "the annotation-fallback test" — it must show noise
    # filtering, not just a plain join).
    run = {"id": 9, "name": "AUDIENCE-01 — validate the declared audience", "status": "completed",
           "conclusion": "failure", "output": {}, "html_url": "https://x/9"}
    ann = {9: [
        {"annotation_level": "failure", "message": "🔴 AUDIENCE-01 — promoção bloqueada (1 achado)"},
        {"annotation_level": "failure",
         "message": "'users' não tem SELECT em prod_recebiveis.diamond.dim_arranjo"},
        {"annotation_level": "failure", "message": "Process completed with exit code 1."},
    ]}
    s = _status_transport(_OPEN_PR, [run], [], annotations=ann).get_status(1)
    detail = s["checks_detail"][0]
    assert detail["summary"] == ("🔴 AUDIENCE-01 — promoção bloqueada (1 achado)\n"
                                 "'users' não tem SELECT em prod_recebiveis.diamond.dim_arranjo")
    assert "exit code" not in detail["summary"]  # GitHub's own noise annotation is filtered out
    assert detail["details_url"] == "https://x/9"  # falls back to html_url (no details_url given)


# --- G9: _summarize_annotations — prefer failure-level, filter GitHub's own generic noise --------


def test_summarize_annotations_prefers_failure_level_over_warning_level():
    anns = [
        {"annotation_level": "warning", "message": "Node.js 20 is deprecated. See ..."},
        {"annotation_level": "failure", "message": "algo real quebrou"},
    ]
    assert _summarize_annotations(anns) == "algo real quebrou"


def test_summarize_annotations_filters_exit_code_noise_when_real_content_exists():
    anns = [
        {"annotation_level": "failure", "message": "achado real"},
        {"annotation_level": "failure", "message": "Process completed with exit code 1."},
    ]
    assert _summarize_annotations(anns) == "achado real"


def test_summarize_annotations_filters_node_deprecation_noise_when_real_content_exists():
    # No failure-level annotation at all here (a different check might only warn) — the noise
    # SUBSTRING filter must still work on its own, independent of the level preference.
    anns = [
        {"annotation_level": "warning", "message": "Node.js 20 is deprecated. See https://x for info."},
        {"annotation_level": "warning", "message": "aviso real"},
    ]
    assert _summarize_annotations(anns) == "aviso real"


def test_summarize_annotations_never_filters_down_to_nothing():
    # If EVERY annotation is noise, keep it anyway — better than an empty summary.
    anns = [{"annotation_level": "failure", "message": "Process completed with exit code 1."}]
    assert _summarize_annotations(anns) == "Process completed with exit code 1."


def test_summarize_annotations_empty_list_is_empty_string():
    assert _summarize_annotations([]) == ""


def test_checks_detail_final_fallback_when_no_output_and_no_annotations():
    run = {"id": 3, "name": "validate", "status": "completed", "conclusion": "failure",
           "details_url": "https://x/3"}
    s = _status_transport(_OPEN_PR, [run], [], annotations={3: []}).get_status(1)
    assert s["checks_detail"] == [{"name": "validate", "conclusion": "failure",
                                   "summary": "", "details_url": "https://x/3"}]


def test_checks_detail_only_covers_the_failing_runs():
    ok = {"id": 1, "name": "ok-check", "status": "completed", "conclusion": "success"}
    bad = {"id": 2, "name": "bad-check", "status": "completed", "conclusion": "failure",
           "output": {"summary": "quebrou"}, "details_url": "u"}
    s = _status_transport(_OPEN_PR, [ok, bad], []).get_status(1)
    assert [d["name"] for d in s["checks_detail"]] == ["bad-check"]


def test_checks_detail_truncates_a_long_summary():
    run = {"id": 4, "name": "validate", "status": "completed", "conclusion": "failure",
           "output": {"summary": "x" * 900}, "details_url": "u"}
    s = _status_transport(_OPEN_PR, [run], []).get_status(1)
    summary = s["checks_detail"][0]["summary"]
    assert len(summary) == 501 and summary.endswith("…")


def test_checks_detail_annotation_fetch_error_degrades_that_runs_summary_to_empty():
    # A hiccup reading ONE run's annotations must not break the whole checks_detail list — that run
    # just falls all the way to name+conclusion+details_url (empty summary).
    run = {"id": 5, "name": "validate", "status": "completed", "conclusion": "failure",
           "output": {}, "details_url": "u"}

    def t(method, url, headers, body):
        p = url.split("api.github.com")[-1]
        if p.endswith("/annotations"):
            return 500, {"message": "boom"}
        if p.endswith("/access_tokens"):
            return 201, {"token": "x"}
        if "/check-runs" in p:
            return 200, {"check_runs": [run]}
        if "/pulls/" in p and p.split("/pulls/")[1].split("?")[0].isdigit():
            return 200, _OPEN_PR
        if "/reviews" in p:
            return 200, []
        if "/actions/runs" in p:
            return 200, {"workflow_runs": []}
        return 500, {"message": f"unhandled {method} {p}"}

    s = GitHubApp(owner="o", repo="r", transport=t, token_provider=lambda: "tok").get_status(1)
    assert s["checks"] == "failure"
    assert s["checks_detail"] == [{"name": "validate", "conclusion": "failure",
                                   "summary": "", "details_url": "u"}]


def test_checks_detail_never_breaks_the_status_read_on_an_unexpected_error(monkeypatch):
    run = {"id": 1, "name": "x", "status": "completed", "conclusion": "failure"}
    gh = _status_transport(_OPEN_PR, [run], [])
    monkeypatch.setattr(gh, "_check_run_summary",
                        lambda run: (_ for _ in ()).throw(RuntimeError("boom")))
    s = gh.get_status(1)
    assert s["checks"] == "failure"  # the status read itself is unharmed
    assert s["checks_detail"] is None  # the enrichment degrades wholesale, not partially


def test_status_open_when_checks_pass_pre_merge():
    s = _status_transport(_OPEN_PR, [{"status": "completed", "conclusion": "success"}], []).get_status(1)
    assert s["checks"] == "success" and s["phase"] == "open"


def test_status_review_decision_approved():
    s = _status_transport(
        _OPEN_PR, [{"status": "completed", "conclusion": "success"}], [],
        reviews=[{"user": {"login": "steward-gh"}, "state": "APPROVED"}],
    ).get_status(1)
    assert s["review_decision"] == "approved"


def test_status_review_decision_changes_requested_uses_latest_per_user():
    # The same reviewer first APPROVED, then later requested changes — the LATEST state wins.
    s = _status_transport(
        _OPEN_PR, [{"status": "completed", "conclusion": "success"}], [],
        reviews=[
            {"user": {"login": "steward-gh"}, "state": "APPROVED"},
            {"user": {"login": "steward-gh"}, "state": "CHANGES_REQUESTED"},
        ],
    ).get_status(1)
    assert s["review_decision"] == "changes_requested"


def test_status_review_decision_dismissed_clears_prior_approval():
    # An APPROVED then a later DISMISSED from the SAME user — the dismissal clears the approval.
    s = _status_transport(
        _OPEN_PR, [{"status": "completed", "conclusion": "success"}], [],
        reviews=[
            {"user": {"login": "steward-gh"}, "state": "APPROVED"},
            {"user": {"login": "steward-gh"}, "state": "DISMISSED"},
        ],
    ).get_status(1)
    assert s["review_decision"] == "review_required"


def test_status_review_decision_required_when_no_reviews():
    s = _status_transport(
        _OPEN_PR, [{"status": "completed", "conclusion": "success"}], [], reviews=[],
    ).get_status(1)
    assert s["review_decision"] == "review_required"


def test_status_merged_pr_review_decision_is_approved():
    # A merged PR was approvable by definition — skip the reviews call and report approved.
    s = _status_transport(_MERGED_PR, [{"status": "completed", "conclusion": "success"}], []).get_status(1)
    assert s["review_decision"] == "approved"


def test_status_awaiting_approval_after_merge():
    s = _status_transport(
        _MERGED_PR, [{"status": "completed", "conclusion": "success"}],
        [{"name": "deploy", "status": "waiting", "conclusion": None, "html_url": "r", "id": 9}],
    ).get_status(1)
    assert s["merged"] and s["deploy"]["waiting_approval"] and s["phase"] == "awaiting_approval"


def test_status_deployed_after_successful_run():
    s = _status_transport(
        _MERGED_PR, [{"status": "completed", "conclusion": "success"}],
        [{"name": "deploy", "status": "completed", "conclusion": "success", "html_url": "r", "id": 9}],
    ).get_status(1)
    assert s["phase"] == "deployed"


def test_status_deploying_when_run_in_progress():
    s = _status_transport(
        _MERGED_PR, [{"status": "completed", "conclusion": "success"}],
        [{"name": "deploy", "status": "in_progress", "conclusion": None, "html_url": "r", "id": 9}],
    ).get_status(1)
    assert s["phase"] == "deploying"


def test_status_deployed_reports_approver_login():
    s = _status_transport(
        _MERGED_PR, [{"status": "completed", "conclusion": "success"}],
        [{"name": "deploy", "status": "completed", "conclusion": "success", "html_url": "r", "id": 9}],
        approvals=[{"state": "approved", "user": {"login": "pedro-gh"}}],
    ).get_status(1)
    assert s["phase"] == "deployed" and s["deploy"]["approver"] == "pedro-gh"


def test_status_deploy_failed():
    s = _status_transport(
        _MERGED_PR, [{"status": "completed", "conclusion": "success"}],
        [{"name": "deploy", "status": "completed", "conclusion": "failure", "html_url": "r", "id": 9}],
    ).get_status(1)
    assert s["phase"] == "deploy_failed"


def test_deploy_steps_preserve_github_job_names_order_and_statuses_one_to_one():
    jobs = {9: [{
        "id": 101, "name": "Promote to prod (Steward approval)",
        "status": "completed", "conclusion": "success",
        "html_url": "https://github.com/o/r/actions/runs/9/jobs/101",
        "check_run_url": "https://api.github.com/repos/o/r/check-runs/101",
        "steps": [
            {"name": "Set up job", "status": "completed", "conclusion": "success", "number": 1},
            {"name": "Safe staged deployment (preflight + forward-only reconciliation)",
             "status": "completed", "conclusion": "success", "number": 9},
            {"name": "Post Checkout content (merged main)",
             "status": "completed", "conclusion": "skipped", "number": 10},
        ],
    }]}
    s = _status_transport(
        _MERGED_PR, [{"status": "completed", "conclusion": "success"}],
        [{"name": "deploy", "status": "completed", "conclusion": "success",
          "html_url": "r", "id": 9}], jobs=jobs,
    ).get_status(1)
    assert s["deploy"]["steps"] == [
        {"name": "Set up job", "status": "completed", "conclusion": "success", "number": 1,
         "job_name": "Promote to prod (Steward approval)",
         "details_url": "https://github.com/o/r/actions/runs/9/jobs/101"},
        {"name": "Safe staged deployment (preflight + forward-only reconciliation)",
         "status": "completed", "conclusion": "success", "number": 9,
         "job_name": "Promote to prod (Steward approval)",
         "details_url": "https://github.com/o/r/actions/runs/9/jobs/101"},
        {"name": "Post Checkout content (merged main)", "status": "completed",
         "conclusion": "skipped", "number": 10,
         "job_name": "Promote to prod (Steward approval)",
         "details_url": "https://github.com/o/r/actions/runs/9/jobs/101"},
    ]


# --- Fix C: "why did the DEPLOY fail?" without leaving the app ------------------------------


def test_deploy_detail_none_when_deploy_succeeds():
    # Only fetched on a failing conclusion — the happy-path poll pays nothing extra (mirrors G8).
    s = _status_transport(
        _MERGED_PR, [{"status": "completed", "conclusion": "success"}],
        [{"name": "deploy", "status": "completed", "conclusion": "success", "html_url": "r", "id": 9}],
    ).get_status(1)
    assert s["deploy_detail"] is None


def test_hot_status_poll_skips_deployment_jobs_until_evidence_is_requested():
    jobs = {9: [{
        "id": 101, "name": "deploy", "html_url": "https://x/job",
        "steps": [{"name": "Expensive evidence", "status": "completed",
                   "conclusion": "success", "number": 1}],
    }]}
    gh = _status_transport(
        _MERGED_PR, [{"status": "completed", "conclusion": "success"}],
        [{"name": "deploy", "status": "completed", "conclusion": "success",
          "html_url": "r", "id": 9}], jobs=jobs,
    )

    lean = gh.get_status(1, include_deployment_evidence=False)
    detailed = gh.get_status(1, include_deployment_evidence=True)

    assert lean["phase"] == "deployed"
    assert lean["deploy"]["steps"] == []
    assert detailed["deploy"]["steps"][0]["name"] == "Expensive evidence"


def test_deploy_detail_reports_the_first_failing_step_and_its_annotations():
    # Root-cause regression scenario: apply_access.py crashed on a raw-dict SDK call (Fix A) —
    # this is what a business user would have seen in the app instead of a bare "Falha".
    jobs = {9: [{
        "id": 101, "name": "deploy", "status": "completed", "conclusion": "failure",
        "html_url": "https://github.com/o/r/actions/runs/9/jobs/101",
        "check_run_url": "https://api.github.com/repos/o/r/check-runs/101",
        "steps": [
            {"name": "Checkout", "status": "completed", "conclusion": "success", "number": 1},
            {"name": "Apply declared access", "status": "completed", "conclusion": "failure", "number": 5},
        ],
    }]}
    annotations = {101: [{"annotation_level": "failure",
                          "message": "AttributeError: 'dict' object has no attribute 'as_dict'"}]}
    s = _status_transport(
        _MERGED_PR, [{"status": "completed", "conclusion": "success"}],
        [{"name": "deploy", "status": "completed", "conclusion": "failure", "html_url": "r", "id": 9}],
        jobs=jobs, annotations=annotations,
    ).get_status(1)
    assert s["phase"] == "deploy_failed"
    assert s["deploy_detail"] == {
        "failed_step": "Apply declared access",
        "summary": "AttributeError: 'dict' object has no attribute 'as_dict'",
        "details_url": "https://github.com/o/r/actions/runs/9/jobs/101",
    }


def test_deploy_detail_falls_back_to_the_run_url_when_the_job_has_no_html_url():
    jobs = {9: [{"id": 101, "name": "deploy", "conclusion": "failure", "check_run_url": "",
                "steps": [{"name": "Deploy", "status": "completed", "conclusion": "failure"}]}]}
    s = _status_transport(
        _MERGED_PR, [{"status": "completed", "conclusion": "success"}],
        [{"name": "deploy", "status": "completed", "conclusion": "failure",
          "html_url": "https://run-url", "id": 9}],
        jobs=jobs,
    ).get_status(1)
    assert s["deploy_detail"]["details_url"] == "https://run-url"
    assert s["deploy_detail"]["summary"] == ""  # no check_run_url -> no annotations attempted


def test_deploy_detail_none_when_no_step_has_conclusion_failure():
    # The run's own conclusion is `failure` but no individual step literally is (e.g. a cancelled
    # step) — degrade to no detail rather than pointing at the wrong step.
    jobs = {9: [{"id": 101, "name": "deploy", "conclusion": "cancelled",
                "steps": [{"name": "Apply declared access", "status": "completed",
                          "conclusion": "cancelled"}]}]}
    s = _status_transport(
        _MERGED_PR, [{"status": "completed", "conclusion": "success"}],
        [{"name": "deploy", "status": "completed", "conclusion": "failure", "html_url": "r", "id": 9}],
        jobs=jobs,
    ).get_status(1)
    assert s["deploy_detail"] is None


def test_deploy_detail_annotation_fetch_error_degrades_summary_to_empty():
    # A hiccup reading the job's annotations must not drop the whole detail — it just falls back
    # to failed_step + details_url with an empty summary (mirrors checks_detail's own test).
    jobs = {9: [{
        "id": 101, "name": "deploy", "conclusion": "failure", "html_url": "https://x/job",
        "check_run_url": "https://api.github.com/repos/o/r/check-runs/101",
        "steps": [{"name": "Apply declared access", "status": "completed", "conclusion": "failure"}],
    }]}

    def t(method, url, headers, body):
        p = url.split("api.github.com")[-1]
        if p.endswith("/annotations"):
            return 500, {"message": "boom"}
        if p.endswith("/access_tokens"):
            return 201, {"token": "x"}
        if "/pulls/" in p and p.split("/pulls/")[1].split("?")[0].isdigit():
            return 200, _MERGED_PR
        if "/reviews" in p:
            return 200, []
        if "/check-runs" in p:
            return 200, {"check_runs": [{"status": "completed", "conclusion": "success"}]}
        if p.endswith("/jobs") and "/actions/runs/" in p:
            return 200, {"jobs": jobs[9]}
        if "/approvals" in p:
            return 200, []
        if "/actions/runs" in p:
            return 200, {"workflow_runs": [
                {"name": "deploy", "status": "completed", "conclusion": "failure", "html_url": "r", "id": 9}]}
        return 500, {"message": f"unhandled {method} {p}"}

    s = GitHubApp(owner="o", repo="r", transport=t, token_provider=lambda: "tok").get_status(1)
    assert s["deploy_detail"] == {
        "failed_step": "Apply declared access", "summary": "", "details_url": "https://x/job"}


def test_deploy_detail_none_when_the_jobs_fetch_errors():
    # The jobs endpoint itself 500s — degrade to no detail, never break the status read.
    def t(method, url, headers, body):
        p = url.split("api.github.com")[-1]
        if p.endswith("/jobs") and "/actions/runs/" in p:
            return 500, {"message": "boom"}
        if p.endswith("/access_tokens"):
            return 201, {"token": "x"}
        if "/pulls/" in p and p.split("/pulls/")[1].split("?")[0].isdigit():
            return 200, _MERGED_PR
        if "/reviews" in p:
            return 200, []
        if "/check-runs" in p:
            return 200, {"check_runs": [{"status": "completed", "conclusion": "success"}]}
        if "/approvals" in p:
            return 200, []
        if "/actions/runs" in p:
            return 200, {"workflow_runs": [
                {"name": "deploy", "status": "completed", "conclusion": "failure", "html_url": "r", "id": 9}]}
        return 500, {"message": f"unhandled {method} {p}"}

    s = GitHubApp(owner="o", repo="r", transport=t, token_provider=lambda: "tok").get_status(1)
    assert s["phase"] == "deploy_failed"
    assert s["deploy_detail"] is None


def test_deploy_enrichment_never_breaks_the_status_read_on_an_unexpected_error():
    jobs = {9: [{
        "id": 101, "name": "deploy", "conclusion": "failure", "html_url": "u",
        "check_run_url": "https://api.github.com/repos/o/r/check-runs/101",
        # Malformed provider payload forces the deep enrichment module's catch-all path.
        "steps": ["not-a-step-object"],
    }]}
    gh = _status_transport(
        _MERGED_PR, [{"status": "completed", "conclusion": "success"}],
        [{"name": "deploy", "status": "completed", "conclusion": "failure", "html_url": "r", "id": 9}],
        jobs=jobs,
    )
    s = gh.get_status(1)
    assert s["phase"] == "deploy_failed"   # the status read itself is unharmed
    assert s["deploy_detail"] is None      # the enrichment degrades wholesale, not partially
    assert s["deploy"]["steps"] == []


def test_status_closed_not_merged():
    pr = {"number": 1, "state": "closed", "merged": False, "head": {"sha": "s"}, "html_url": "u"}
    s = _status_transport(pr, [{"status": "completed", "conclusion": "success"}], []).get_status(1)
    assert s["phase"] == "closed"


def test_status_merged_no_deploy_run_yet():
    # Merged but the push hasn't spawned a deploy run (runner idle) — honest "merged", no false deploy.
    s = _status_transport(_MERGED_PR, [{"status": "completed", "conclusion": "success"}], []).get_status(1)
    assert s["deploy"]["status"] == "none" and s["phase"] == "merged"


def test_deploy_run_matched_by_merge_commit_sha_not_just_latest():
    # A concurrent/unrelated deploy run is the latest; ours is correlated by merge_commit_sha.
    pr = {"number": 1, "state": "closed", "merged": True, "head": {"sha": "s"},
          "merge_commit_sha": "MINE", "html_url": "u"}
    runs = [
        {"name": "deploy", "status": "completed", "conclusion": "failure", "html_url": "other",
         "id": 1, "head_sha": "OTHER"},
        {"name": "deploy", "status": "waiting", "conclusion": None, "html_url": "mine",
         "id": 2, "head_sha": "MINE"},
    ]
    s = _status_transport(pr, [{"status": "completed", "conclusion": "success"}], runs).get_status(1)
    assert s["deploy"]["run_url"] == "mine" and s["phase"] == "awaiting_approval"


def test_status_is_canonical_and_rejects_deployment_revision_mismatch():
    approved = {"content_revision": "b" * 64, "engine_revision": "a" * 40}
    observed = {"content_revision": "c" * 64, "engine_revision": "a" * 40}
    pr = {**_MERGED_PR, "merge_commit_sha": "MINE", "merged_by": {"login": "pedro"}}
    runs = [{
        "name": "deploy", "status": "completed", "conclusion": "success",
        "html_url": "run", "id": 9, "head_sha": "MINE",
    }]
    gh = _status_transport(
        pr,
        [{"name": "bundle validate", "status": "completed", "conclusion": "success"}],
        runs,
        approvals=[{"state": "approved", "user": {"login": "pedro"}}],
        revision_manifests={
            "s": {"version": 1, "revisions": approved},
            "MINE": {"version": 1, "revisions": observed},
        },
    )
    status = gh.get_status(1, approved_revisions=approved)
    assert status["provider"] == "github"
    assert status["external_id"] == "1"
    assert status["actors"]["merged_by"] == "pedro"
    assert status["revisions"] == approved
    assert status["deployment"]["rejected"] is True
    assert status["phase"] == "revision_mismatch"
    assert status["deploy"] == status["deployment"]  # legacy alias is canonical, never raw


def test_deployment_attempt_annotation_parser_chooses_latest_sequence_and_rejects_noise():
    def annotation(sequence, state="running"):
        payload = {
            "attempt_id": "github:9:1", "run_attempt": 1, "sequence": sequence,
            "terminal_state": state, "completed_stages": ["preflight"],
        }
        return {"message": "DEPLOY_ATTEMPT:" + json.dumps(payload)}

    found = _parse_deployment_attempt_annotations([
        {"message": "Process completed with exit code 1."}, annotation(1), annotation(4),
        annotation(99, "invented"),
    ])
    assert found["sequence"] == 4 and found["terminal_state"] == "running"


def test_just_merged_window_does_not_borrow_a_prior_completed_deploy_run():
    # The bug: right after merge, GitHub hasn't created THIS promotion's deploy run yet, so the only
    # deploy run on `main` is a PRIOR promotion's completed+success one (different head_sha). The
    # merge_sha match fails; we must NOT fall back to that stale run (which read as `deployed` — all
    # steps green — before the gate even appeared). Phase must stay `merged` until our run shows.
    pr = {"number": 1, "state": "closed", "merged": True, "head": {"sha": "s"},
          "merge_commit_sha": "MINE", "html_url": "u"}
    runs = [
        {"name": "deploy", "status": "completed", "conclusion": "success", "html_url": "prior",
         "id": 9, "head_sha": "OTHER"},
    ]
    s = _status_transport(pr, [{"status": "completed", "conclusion": "success"}], runs).get_status(1)
    assert s["deploy"]["status"] == "none"
    assert s["phase"] == "merged"  # NOT "deployed" — the stale run must not be borrowed


def test_aggregate_checks_edges():
    from github_app import _aggregate_checks
    assert _aggregate_checks([]) == "none"
    assert _aggregate_checks([{"status": "completed", "conclusion": "action_required"}]) == "pending"
    assert _aggregate_checks([{"status": "completed", "conclusion": "neutral"},
                              {"status": "completed", "conclusion": "skipped"}]) == "success"
    assert _aggregate_checks([{"status": "completed", "conclusion": "success"},
                              {"status": "completed", "conclusion": "failure"}]) == "failure"


def test_installation_token_is_cached_across_calls():
    fg = FakeGitHub()
    n = {"c": 0}

    def provider():
        n["c"] += 1
        return "tok"

    gh = GitHubApp(owner="o", repo="r", transport=fg.transport, token_provider=provider,
                   now=lambda: 1000.0)
    pr = gh.open_or_update_promotion(branch="b", path="p", content="x", title="t", body="b")
    gh.post_review_comment(pr["number"], "<!--m-->", "<!--m-->\nx")
    assert n["c"] == 1  # token minted once, then cached (not re-minted per request)


# --- F5 Phase 1: READ-ONLY gate introspection (drift detection) -------------------------------


def _reader_transport(env_response=None, protection_response=None):
    """A minimal fake transport for the two new read-only accessors — (status, body) per path,
    independent of the FakeGitHub fixture above (these two calls don't touch refs/files/pulls)."""

    def transport(method, url, headers, body):
        path = url.split("api.github.com", 1)[-1]
        if "/environments/" in path:
            return env_response
        if "/branches/" in path and path.endswith("/protection"):
            return protection_response
        return 500, {"message": f"unhandled {method} {path}"}

    return transport


def test_get_environment_reviewers_extracts_logins():
    body = {"protection_rules": [
        {"type": "required_reviewers", "reviewers": [
            {"reviewer": {"login": "pedro-gh"}}, {"reviewer": {"login": "malcoln-gh"}}]},
        {"type": "wait_timer"},  # a non-reviewer rule must be ignored, not crash
    ]}
    gh = GitHubApp(owner="o", repo="r", transport=_reader_transport(env_response=(200, body)),
                  token_provider=lambda: "tok")
    assert gh.get_environment_reviewers("prod") == ["pedro-gh", "malcoln-gh"]


def test_get_environment_reviewers_missing_environment_is_empty_not_error():
    gh = GitHubApp(owner="o", repo="r", transport=_reader_transport(env_response=(404, {})),
                  token_provider=lambda: "tok")
    assert gh.get_environment_reviewers("prod") == []


def test_get_environment_reviewers_raises_on_other_failures():
    gh = GitHubApp(owner="o", repo="r",
                  transport=_reader_transport(env_response=(403, {"message": "missing scope"})),
                  token_provider=lambda: "tok")
    with pytest.raises(GitHubError):
        gh.get_environment_reviewers("prod")


def test_get_branch_protection_returns_none_when_absent():
    gh = GitHubApp(owner="o", repo="r", transport=_reader_transport(protection_response=(404, {})),
                  token_provider=lambda: "tok")
    assert gh.get_branch_protection("main") is None


def test_get_branch_protection_returns_body_when_present():
    body = {"required_pull_request_reviews": {"required_approving_review_count": 1}}
    gh = GitHubApp(owner="o", repo="r", transport=_reader_transport(protection_response=(200, body)),
                  token_provider=lambda: "tok")
    assert gh.get_branch_protection("main") == body


def test_get_branch_protection_raises_on_other_failures():
    gh = GitHubApp(owner="o", repo="r",
                  transport=_reader_transport(protection_response=(500, {"message": "boom"})),
                  token_provider=lambda: "tok")
    with pytest.raises(GitHubError):
        gh.get_branch_protection("main")
