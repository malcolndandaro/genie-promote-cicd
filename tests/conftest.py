"""Shared pytest fixtures — make the engine "unit" suite hermetic (TD1).

These tests advertise "no network, no live workspace" (see the docstrings in `test_engine_api.py`,
`test_roles_and_drift_api.py`, `test_rules_api.py`, `test_authz.py`, …): they fake
`authz.verify_identity` and inject fakes for the GitHub reader / dev transport, so no test *intends*
to touch a live Databricks workspace. But the databricks-sdk validates auth EAGERLY at construction
time, so a handler path can throw/hang on the ambient auth env BEFORE any injected fake is reached.
Concretely, two distinct construction points fire during these tests:

  1. ``databricks.sdk.core.Config()`` — as of databricks-sdk 0.111.0, even a bare ``Config()``
     performs OIDC host-metadata discovery over the network at construction
     (``Config.__init__`` -> ``_resolve_host_metadata`` -> ``oauth.get_host_metadata`` ->
     ``_BaseClient.do`` with retry/backoff) whenever a host is set, then validates the credential
     (``init_auth``). ``engine_api.main._verified_email`` (``Config().host``, main.py:344) and
     ``whoami`` (``Config().host``, main.py:427) build one on nearly every admin-gated request.
  2. ``WorkspaceClient()`` — built lazily by ``app_logic.github_app_factory()`` inside the status
     poll's ``reconcile`` (main.py:762) and by the OBO/read paths.

On the S9b CI box (``DATABRICKS_AUTH_TYPE=oauth-m2m`` + host + client_id + an INVALID
``DATABRICKS_CLIENT_SECRET``) construction raised ``ValueError: default auth: oauth-m2m:
invalid_request`` — the ~148 CI failures TD1 was filed for. Against any *unreachable* host it hangs
on the SDK's retry/backoff instead. Both mean the "unit" suite secretly depends on the runner's
credentials + reachability.

This conftest closes that hole for the WHOLE suite, at the transport layer only, so no test can
depend on live/valid auth or network — while changing NO production code and asserting nothing:

  1. Scrub every ambient ``DATABRICKS_*`` var (so nothing inherits the runner's creds/profile), then
     pin a dummy, well-formed PAT config (host + token + ``auth_type=pat`` + ``/dev/null`` config
     file). PAT auth only adds a header — it makes NO network call at construction — so ``Config()``
     / ``WorkspaceClient()`` build cleanly offline, and any handler that still constructs one
     resolves to this inert config instead of the runner's live one.
  2. Neutralize the SDK's eager host-metadata discovery (``Config._resolve_host_metadata``) — the one
     construction step that hits the network regardless of ``auth_type``. (It only back-fills
     account/workspace/discovery ids the hermetic tests never use.)
  3. Fail-fast the SDK's REST transport (``_BaseClient.do``). No test in this suite performs a real
     Databricks API call — every SDK interaction is a fake/injected object (grep-verified). But a
     handler path that constructs a *real* client and then calls the API without the test having
     mocked it (e.g. the status poll's ``reconcile`` -> ``github_app_factory()`` ->
     ``secrets.get_secret`` when that specific test didn't stub the factory) would otherwise make a
     real request. Against the dummy host that request is unreachable and the SDK retries it for its
     full ~300s budget — turning a would-be fast failure into a hang. Raising here makes any such
     ESCAPED call fail immediately; the production code already treats a client/API hiccup as
     non-fatal (``reconcile``'s ``try/except`` -> empty facts; ``_run_drift_check``'s guard ->
     ``unknown_*`` findings), which is exactly the graceful-degradation path those tests assert on,
     so the fast failure is swallowed identically to the CI env's fast ``ValueError``.

Tests that specifically exercise ``Config().host`` (whoami's ``prod_host``) or a live-shaped SDK
object already monkeypatch the symbol in the module under test (``engine_api.Config`` /
``app_logic.Config`` / ``authz.WorkspaceClient``), which fully shadows the real SDK — so they are
unaffected by anything set here.
"""
import os

import pytest
from databricks.sdk import _base_client
from databricks.sdk.core import Config


def _blocked_do(self, method, url, *args, **kwargs):
    """Stand-in for ``_BaseClient.do`` — fail immediately instead of making a real request (which,
    against the dummy test host, would retry for the SDK's full timeout budget and hang the suite).
    Only ESCAPED calls (a real client the test forgot to stub) reach this; the handlers that build
    such clients already degrade gracefully on the exception."""
    raise RuntimeError(
        f"Databricks SDK network call blocked in tests (TD1 hermetic conftest): {method} {url}. "
        "A handler constructed a real client without the test stubbing its factory/transport."
    )


@pytest.fixture(autouse=True)
def _hermetic_databricks_auth(monkeypatch):
    """Autouse: isolate every test from live/ambient Databricks auth + network (TD1).

    All patches are reverted automatically after each test by pytest's ``monkeypatch``, so nothing
    leaks between the suite and a real environment.
    """
    # 1. Remove ALL ambient DATABRICKS_* vars so no test inherits the runner's creds or profile.
    for key in list(os.environ):
        if key.startswith("DATABRICKS_"):
            monkeypatch.delenv(key, raising=False)

    # 2. Pin a dummy, well-formed PAT config. PAT auth makes no network call at construction, so
    #    Config()/WorkspaceClient() build cleanly offline; the bogus host is inert because discovery
    #    is neutralized (below) and the transport is blocked (below).
    monkeypatch.setenv("DATABRICKS_HOST", "https://test.invalid")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dummy-pat-token-for-tests")
    monkeypatch.setenv("DATABRICKS_AUTH_TYPE", "pat")
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", "/dev/null")

    # 3. Neutralize the SDK's eager host-metadata discovery (network at construction, any auth_type).
    monkeypatch.setattr(Config, "_resolve_host_metadata", lambda self: None)

    # 4. Fail-fast any ESCAPED real Databricks API call (see module docstring).
    monkeypatch.setattr(_base_client._BaseClient, "do", _blocked_do)

    yield
