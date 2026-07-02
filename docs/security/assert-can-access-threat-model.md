# Threat model — `authz.assert_can_access` (A2)

**Component:** `app/authz.py::assert_can_access` (+ its companion `verify_identity`).
**Status:** accepted (2026-07-02), companion to `docs/adr/0006-app-in-prod-cross-workspace-reach.md`.
**Why this gets its own document:** the PRD and ADR-0006 both call this out as *the* highest-risk
new component in the Genie Governance Console — a confused-deputy control replacing Databricks'
own native OBO enforcement, which normally does this job for free. A unit-test suite proves the
function behaves as specified; this document is where we reason about whether the *specification
itself* is sound.

## 1. What problem this solves

Before the Genie Governance Console, the app lived in the **dev** workspace and read Genie Spaces
**on behalf of the signed-in user** via OBO (`x-forwarded-access-token`). Access control was free:
Databricks itself refused an OBO call against a Space the user couldn't see. There was no
opportunity for a confused deputy, because there was no deputy — the platform enforced the
boundary natively, per request, using its own trusted identity resolution.

ADR-0006 relocates the app to **prod** (a durable control plane immune to the dev workspace's
~7-day wipe). OBO tokens are scoped to the workspace that issued them — **a token minted by prod's
proxy cannot authenticate against dev**. So every dev-touching operation (reading an authored
Space at promotion time; later, F1's write-back) must now run as a **standing dev-reader/writer
service principal** instead of the user. That SP is deliberately broad: Genie has no per-Space
delegation, so any credential capable of listing dev Spaces at all can reach **every** dev Space in
the workspace. `assert_can_access` is the application-layer control that stands in for the
platform-layer control we just gave up — it is what makes it safe to let the SP be the sole
cross-workspace credential.

## 2. Trust boundary

```
 ┌─────────────────────────┐        ┌──────────────────────────────┐
 │  Browser / SPA           │        │  Databricks Apps proxy        │
 │  (fully untrusted input) │──HTTP─▶│  (injects x-forwarded-*,      │
 └─────────────────────────┘        │   including the OBO token)    │
                                     └───────────────┬───────────────┘
                                                      │ in-process (same app/origin)
                                                      ▼
                              ┌────────────────────────────────────────┐
                              │  engine_api/main.py (prod, trusted)     │
                              │   _verified_email() -> authz.verify_    │
                              │   identity(OBO token)                  │
                              └───────────────┬──────────────────────--┘
                                               │ VerifiedIdentity (user_name, groups)
                                               ▼
                              ┌────────────────────────────────────────┐
                              │  authz.assert_can_access(identity,      │
                              │    space_id, transport=dev_sp_client)   │
                              │   - live ACL read AS the dev SP         │
                              │   - decision made from `identity` only  │
                              └───────────────┬──────────────────────--┘
                                               │ (dev SP is TRANSPORT only)
                                               ▼
                                     ┌──────────────────────┐
                                     │  DEV workspace         │
                                     │  (ephemeral, ~7-day    │
                                     │   wipe, SP2)           │
                                     └──────────────────────┘
```

**What is trusted, and why:**
- The OBO token itself (`x-forwarded-access-token`) is trusted as *transport-authenticated* — the
  Databricks Apps proxy strips and re-injects `x-forwarded-*` headers on a genuinely proxied
  request, so a browser client cannot forge it (this was already the accepted trust boundary for
  the pre-Console app's OBO reads).
- The **verified identity** derived from that token via `WorkspaceClient(token=...)
  .current_user.me()` is trusted, because it is not merely forwarded — the WORKSPACE ITSELF is
  asked "whose token is this," which fails (raises) for an expired/revoked/forged token rather than
  silently returning attacker-chosen data.
- The dev-reader/writer SP's own credentials (client id/secret) are trusted as a transport
  mechanism only — see §3, "why the SP's own reach is never itself authorization."

**What is explicitly NOT trusted as an authorization input:**
- `x-forwarded-email` — the previous (broken) control. It IS proxy-injected and hard to forge on a
  genuinely proxied request, but it was never a platform-verified assertion of identity in the way
  `current_user.me()` is (there is no strip-and-re-inject guarantee documented for it the way there
  is for the OBO token's use as a bearer credential); more importantly, treating a *display* header
  as an *authorization* input is a design smell independent of how spoofable it happens to be
  today — a future proxy change, a local/test misconfiguration, or a bug in this app is one line
  away from trusting attacker-controlled data. A2 replaces this with a positively-verified identity
  so that "is this header trustworthy" is no longer a question a security reviewer has to answer.
- Anything the dev-reader/writer SP itself is separately entitled to do (§3).

## 3. The confused-deputy risk, named

**The deputy:** the dev-reader/writer SP. It is *more* privileged than most of its callers (it can
list/read/write every dev Genie Space), and it acts *on behalf of* requests originated by
less-privileged callers (any authenticated app user).

**The classic failure mode this guards against:** an authenticated-but-unauthorized app user asks
the app to export or overwrite a dev Space they have no business touching. If the app naively used
the SP's own broad reach as the access check ("the SP can read it, so the read succeeds"), *every*
signed-in user would effectively inherit the SP's full reach — the textbook confused-deputy bug.

**Why "the SP is scoped to Genie APIs only" is not suffient on its own:** ADR-0006 already commits
to scoping the SP narrowly (no UC/warehouse/other grants) as a *platform*-layer compensating
control. That limits blast radius if the SP's credentials leak, but it does **nothing** to stop a
legitimate, authenticated app user from asking the app to act on a Space that isn't theirs — the SP
being "only Genie-scoped" doesn't distinguish between dev Spaces at all. `assert_can_access` is the
**application**-layer control that makes the per-Space distinction the platform-layer scoping
cannot express.

**Design principle enforced by the guard's own code (see `app/authz.py` docstrings):**
`assert_can_access`'s decision is derived **entirely** from the `identity` argument (a
platform-verified `VerifiedIdentity`) checked against the target Space's real ACL. The `transport`
argument (the SP's `WorkspaceClient`) is used *only* to make the read-only `permissions.get` API
call — it never contributes to the yes/no decision. This is the load-bearing invariant: if a future
change ever made the decision depend on what the SP itself is allowed to do (e.g., "if the SP can
reach it, allow it"), the guard would degrade back into the confused-deputy bug it exists to
prevent. Any PR touching this function should be reviewed against this invariant specifically.

## 4. Failure modes and how each is handled

| Failure | Handling | Rationale |
|---|---|---|
| ACL read raises a `DatabricksError` (space not found, SP lacks CAN_EDIT, dev mid-wipe, transient 5xx) | `AccessDenied` raised — deny | Fail-closed: an error resolving the ACL must never be interpreted as "no restriction exists." |
| ACL read raises an unrecognized exception (network timeout, unexpected client bug) | `AccessDenied` raised — deny | Same as above; the guard has a catch-all `except Exception`, not just `DatabricksError`, precisely so an unknown failure mode cannot silently degrade to fail-open. |
| ACL read succeeds but returns an empty/malformed ACL | Deny (no matching entry found) | The absence of a grant is treated the same as a denial-worthy state; there is no implicit "no ACL = open" branch anywhere in the function. |
| Caller's OBO token doesn't verify (`verify_identity` raises) | Caller never reaches `assert_can_access` — the calling endpoint already denies/treats as unauthenticated | See `engine_api.main._verified_email`: a verification failure returns `None`, and every caller of that helper treats `None` as "no elevated access," never as "trust something else instead." |
| Caller belongs to a group that has access, but the group name doesn't match exactly (case, aliasing) | Deny | `_principal_names`/set-intersection is exact-match by design — no fuzzy/normalized matching that could accidentally widen a grant. A real mismatch here is a data-quality bug to fix at the source (the platform's own group naming), not something the guard should paper over. |
| Space genuinely has multiple ACL entries, one denying and one granting the same principal | Grant wins (`_grants_access` short-circuits on the first accepting entry) | Genie/Databricks permissions are additive-grant-only (no explicit deny entries in this API), so this scenario cannot occur via the modeled ACL shape; noted here so a future change to the permission model revisits this assumption. |
| The guard is called without ever awaiting/completing (e.g., an async cancellation mid-check) | N/A — the function is synchronous and raises-or-returns; there is no partial-completion state to leave the caller in | By construction: no cache is written, no side effect happens on a partial read, so there is nothing to leave inconsistent. |

## 5. Why never-cached (not just "fail closed")

A cache would reintroduce exactly the problem this control exists to fix: a Requester's access to
a dev Space can change **between actions in the same session** — an admin can revoke a grant, or
(more likely operationally) the dev workspace wipes and comes back with a stale/absent ACL. If the
guard cached "identity X may access space Y" for even a short TTL, a revoked user would continue to
be treated as authorized until the cache expired — a textbook "false sense of control," which is
the PRD's own words for what this design explicitly rejects. The cost of never caching is one extra
live API call per guarded action; given the operation frequency (promotion review/export, one-time
per action, not a hot loop), this is judged the correct trade-off.

## 6. Residual risks (accepted, not eliminated)

1. **The SP remains a high-value target.** Its OAuth credentials, if exfiltrated, let an attacker
   call the Genie APIs directly against dev, bypassing this app (and therefore this guard) entirely
   — `assert_can_access` protects the app's own API surface, not the SP's credentials themselves.
   Mitigation: `scripts/provision_dev_sp.sh` documents an audit/anomaly-monitoring expectation
   (§"AUDIT/ANOMALY MONITORING" in that script's own output), and the SP is scoped to Genie APIs
   only (no UC/warehouse grants) so a leak's blast radius is bounded — but this is a platform-layer
   mitigation, not something `assert_can_access` itself can close.
2. **Group membership resolution is only as fresh as the OBO token's own `current_user.me()` call.**
   `verify_identity` reads groups from the SAME `me()` response used to resolve `user_name` — this
   is a real API call made at guard-check time (not cached across requests), so a just-added group
   membership is visible on the very next action. The residual risk is the platform's own eventual
   consistency for group membership changes, which is outside this component's control.
3. **The guard authorizes "may touch this Space," not a finer-grained action distinction.**
   `assert_can_access` today answers a single yes/no per Space (read-export and write-overwrite are
   gated identically, via `_ACCESS_LEVELS` accepting CAN_VIEW..CAN_MANAGE alike). If a future
   feature needs to distinguish "may read" from "may overwrite" at the ACL-level-granularity, the
   function's `_ACCESS_LEVELS` set should be parameterized per call site rather than assuming today's
   single threshold — flagged here so a reviewer of a later PR knows this was a deliberate v1
   simplification, not an oversight.
4. **No rate limiting / anomaly detection inside the guard itself.** A compromised but validly
   authenticated caller could enumerate every dev Space id and learn (via the guard's own
   allow/deny) which ones they don't have access to — this is an authorization oracle, not a data
   leak (the guard reveals no ACL contents, only allow/deny), but it is a minor information
   disclosure. Accepted for v1; if this becomes a concern, rate-limiting belongs at the endpoint
   layer (`engine_api/main.py`), not inside the guard.
5. **`is_admin` derivation depends on the SAME `verify_identity` call succeeding.** If dev/verify
   temporarily can't resolve identity (e.g., during an outage of the app's OWN prod workspace,
   which is what issues/validates the token, not dev), admin-gated features fail closed too (no
   admin access) rather than degrading to "trust the display header." This is the correct
   fail-closed choice, but it does mean a prod identity-resolution outage takes down admin features
   along with everything else guarded by verified identity — accepted, since the alternative (fall
   back to the header) reintroduces the exact control this replaces.

## 7. What would make this guard unsafe (a checklist for future reviewers)

A change to `assert_can_access` (or its callers) should be treated as security-sensitive if it:
- Makes the allow/deny decision depend on anything the **SP** (the `transport` argument) is
  entitled to do, rather than purely on the **verified identity** argument.
- Introduces any caching of the guard's result across more than a single call.
- Adds a fallback path that uses `x-forwarded-email` (or any other unverified header) when
  `verify_identity` fails, instead of propagating the denial.
- Changes any exception handler in `assert_can_access` from "deny" to "allow" or "retry-then-allow."
- Adds a new dev-touching code path (F2/F3/F4/F5, A3) that does NOT call this same guard — the PRD
  and ADR-0006 both require this to remain the **single** reused guard; a parallel/bespoke check is
  a regression even if it happens to be correct on its own.
