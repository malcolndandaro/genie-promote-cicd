# Safe, resumable promotion deploys use preflight plus forward-only reconciliation

**Status:** accepted (2026-07-16)

## Context

A live promotion exposed a non-transactional failure mode: the bundle and UC mutations succeeded,
then an invalid Genie permission level (`CAN_VIEW`) failed, and the app service principal's
`CAN_MANAGE` assertion never ran. The run was red, but production had already changed.

Databricks bundle deploys and object-permission calls do not form one cross-service transaction.
Pretending they do would make retries and operator decisions unsafe. The pilot needs to prevent
known-invalid work before the first mutation, make every remaining mutation replayable, and report
partial state precisely when an external call still fails.

## Decision

### 1. Two preflights, zero production mutation

The PR check runs the normal preflight, and the approved deployment runs the same preflight again
against the exact merged content immediately before mutation. The second pass closes the time gap
between PR validation and the Steward gate.

Preflight includes:

- render plus strict environment/table allowlist;
- `databricks bundle validate --strict -t prod`;
- required title/mapping/AudienceSpec schema and stable-principal checks;
- only `CAN_RUN` as the derived audience ACL level;
- live target table/principal existence and duplicate-title checks;
- `AUDIENCE-01` effective-grant inspection (missing SELECT is informational);
- read-only lookup of the governed `system.certification_status` tag policy, including that it
  declares `certified` as an allowed value;
- authentication/capability checks where Databricks exposes a stable read-only check.

Invalid content/principals block before mutation. An API/auth/timeout that prevents preflight is an
operational failure, not an author finding, and also leaves production untouched.

### 2. Forward-only ordered mutation stages

After preflight succeeds:

1. `bundle_deploy` — deploy the rendered Space content;
2. `resolve_space` — resolve exactly one live Space and capture its id;
3. `assert_app_manage` — idempotently assert the app SP's technical `CAN_MANAGE`;
4. `reconcile_audience` — reconcile app-managed audience ACLs to AudienceSpec, deriving `CAN_RUN`;
5. `verify_live_state` — read back content identity, technical ACL and managed audience;
6. `certify_space` — read, create or update the Genie Space's
   `system.certification_status=certified` workspace-entity tag, then read it back with a bounded
   retry for workspace-tag propagation;
7. `complete` — only now report the deployment as deployed.

Audience reconciliation is the last access-changing stage; certification follows the content and ACL
verification in stage 5. The CI deploy service principal needs least-privilege `ASSIGN` on the governed
`system.certification_status` tag, granted through an account or individual tag policy;
workspace-admin alone is insufficient. Databricks exposes no stable non-mutating `ASSIGN` probe in
this flow: preflight verifies only the policy and allowed value, while absent `ASSIGN` fails honestly
at `certify_space`. No stage grants UC privileges.

### 3. Idempotent replay, not blind resume

Every mutation is safe to repeat. A retry starts with preflight and replays the ordered stages; it
does not trust a manually selected “resume from step N”. Bundle deploy is declarative, technical ACL
assertion is additive, audience reconciliation converges on the declared managed set, and
certification is a no-op when the Space is already certified (otherwise it converges by create or
update). Every approved full desired-set deployment re-certifies every managed rendered Space, so a
manual deletion or deprecation is temporary and is not a revocation control. Final readback proves
the target state; transient `NotFound` or stale values are retried for about 60 seconds, while an
exhausted readback still fails closed as `partial_failed`.

The workflow emits a stable stage id, completed-stage list and target identifiers as step output and
annotations. GitHub remains the live source of truth; Lakebase mirrors the Attempt/phase through the
existing reconcile path and never asserts success independently.

### 4. Honest partial failure and forward recovery

- Failure before `bundle_deploy`: `operational_failed`, `mutation_started=false`.
- Failure after a mutation: `partial_failed`, with `last_completed_stage`, failed stage, target id,
  actionable reason and run URL.
- Missing audience SELECT remains an advisory and never produces either failure state.

There is no automatic rollback across Databricks services. A retry converges forward. If content
itself must be reverted, a Steward approves a new deployment of the prior known-good content; the
system never guesses that rollback is safer than completing the desired state.

## Consequences

- The UI can distinguish author fixes from platform incidents and show exactly what production has
  already received.
- A mid-deploy failure may still leave partial state; the invariant is visibility + safe replay, not
  impossible transactional atomicity.
- Audience removal requires desired-state reconciliation of only app-managed principals; unrelated
  owners/ACLs are preserved.
- The content workflow and engine revision must be immutable within one Attempt; the integration
  boundary decision defines how that revision is selected.
- Permission levels are resource-specific. Generic DABs permission examples are not evidence for
  Genie ACL support; the pilot contract accepts only the proven Genie `CAN_RUN` value.
