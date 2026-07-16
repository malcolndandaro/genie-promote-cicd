# Internal pilot go/no-go rehearsal

This is the release bar for the simplified Genie Promotion App. Run it after the Phase-2 cleanup in
ADR-0009 and against the clean pilot Lakebase ledger. A green unit suite or a successful demo alone
is not a go decision: the rehearsal must prove the governed path, failure truthfulness, operations,
and ownership with live evidence.

## Decision rule

The pilot is **GO** only when every mandatory row below has evidence, KIP signs the technical
decision, and GestOps signs the governance/adoption decision. Either team can declare **NO-GO** in
its responsibility area. There is no “go with a known critical exception”.

KIP owns runtime, runner, secrets, incident recovery, maintenance and technical go/no-go. GestOps
owns user guidance, the Steward reviews/gates and governance go/no-go. Support is best-effort during
the published pilot window, not 24x7.

Recommended window: business days, 08:00–18:00 BRT. KIP starts/stops one Medium App (0.5 DBU/hour)
and may extend the window for a scheduled demo. Expected App compute usage is 5 DBU/day or 25
DBU/week; investigate above 30 DBU/week. Budget alerts inform KIP but do not stop the App.

## Current known live blocker

The live in-app promotion button is a NO-GO until the `genie-promote-bot` installation includes
`genie-spaces-content` and the current `github_app_id`, `github_installation_id`, and
`github_app_private_key` are present in the prod `genie_promote` secret scope. Token minting and a
read/write test against the content repo must pass from the App identity. CI-only success does not
satisfy this prerequisite.

## Rehearsal record

- Date/time (BRT):
- App URL/revision:
- Content revision:
- Engine revision/lock:
- Rehearsal Space id/title:
- Business User:
- GestOps Steward (different GitHub identity):
- KIP operator:
- GitHub Change Request and run URLs:
- Evidence folder/link:
- Decision: `GO | NO-GO`

No secret, token, private key or full auth header belongs in the evidence pack.

## Entry prerequisites

### Product and schema

- [ ] All ten Wayfinder decisions are resolved and Phase 2 in ADR-0009 is complete.
- [ ] `Meus Espaços`, `Aguardando minha revisão`, `Configurações`, and `Auditoria` are the only
      navigation surfaces; promotion detail remains available by deep link.
- [ ] No active code/workflow/content reference remains for `.access.json`, `AccessSpec`,
      `uc_principals`, `CAN_VIEW`, `GRANT-01`, `consumer_group`, Access Requests or Approver.
- [ ] Lakebase has `AudienceSpec` and Deployment Attempt persistence, no access-request tables,
      no `promotions.access_spec`, and roles allow only `steward`/`admin`.
- [ ] Demo promotion/review/audit/rehydrate rows were reset; operational role/rule/prompt/KA config
      was intentionally retained and reviewed.
- [ ] The content repo pins an immutable engine revision; PR checks and deploy resolve the same
      engine SHA and content SHA.

### GitHub governance

- [ ] The GitHub App prerequisite above passes from the live App.
- [ ] Content `main` requires `bundle validate (prod)` and `eval-run pass-rate (dev)`, with strict
      up-to-date checks and admin enforcement.
- [ ] The `prod` Environment requires the GestOps Steward gate; the Requester cannot provide their
      own Steward approval.
- [ ] The self-hosted content runner is online, uniquely labeled/scoped, has sufficient disk, and
      completes a harmless checkout/job before rehearsal.
- [ ] Workflow concurrency never cancels an approved run in progress.
- [ ] The engine lock bump process is owned by KIP and is itself reviewed in the content repo.

### Databricks identities and secrets

- [ ] App SP can read its secret scope, use the prod warehouse, query configured KAs, and hold
      technical `CAN_MANAGE` on deployed rehearsal Spaces.
- [ ] Dev transport SP can list/export/update only the required dev boundary; user authorization is
      still enforced per request by `assert_can_access`.
- [ ] CI SP can deploy/bind the App, inspect effective grants, and execute the staged deploy. No
      workflow or script can grant UC privileges.
- [ ] Secret creation/last-rotation date and KIP owner are recorded; token mint tests pass; revoke
      and replacement steps are known. Rotate immediately on suspected exposure and after the pilot
      if the credentials were shared for setup.
- [ ] No dead workspace/warehouse/SP id from the pre-migration environment appears in active vars,
      secrets, bundles or workflows.

### Runtime and support

- [ ] Exactly one Medium App is configured; horizontal scaling is not a pilot dependency.
- [ ] KIP has tested supported start and stop operations and confirmed configuration survives.
- [ ] A usage/budget alert reaches KIP; everyone understands that it is not a hard cost cap.
- [ ] The 08:00–18:00 BRT availability window and best-effort support expectation are published.
- [ ] GestOps has a one-page author guide and knows the escalation package: Space, Attempt id,
      decision headline, timestamp and exact run URL.
- [ ] KIP can pause new promotions, preserve evidence, resume an idempotent Attempt, and stop the
      App. The pause procedure does not revoke audit access or guess at rollback.

## Rehearsal actors and fixture

Use a dedicated, disposable Genie Space with at least two benchmarks and references to the three
approved `prod_recebiveis.diamond` tables. Give the Business User access to its Dev version. Use a
different GestOps GitHub identity for review and gate approval. Include:

- one audience principal with effective SELECT;
- one valid audience principal intentionally missing SELECT for the advisory scenario;
- one nonexistent/invalid principal for a rejected Preflight attempt;
- one unrelated owner/technical ACL that audience reconciliation must preserve.

Failure injection must be scoped to this rehearsal Space, approved by KIP, and removed before the
final clean run. Never test failure by weakening a shared customer Space. A temporary rehearsal
workflow may deliberately exit after `assert_app_manage`; it must still use the prod Environment
gate, emit the pinned revisions/stage evidence, and be deleted after the forward-recovery proof.

## Mandatory scenario matrix

| ID | Scenario | Required proof | Owner |
|---|---|---|---|
| R1 | Live App authentication and discovery | verified OBO identity; only personally accessible Dev Spaces; no trusted forwarded-email shortcut | KIP |
| R2 | Happy-path promotion | review ready → one Change Request → required checks → different Steward → prod gate → deployed/live readback | Business User + GestOps |
| R3 | Same-Space re-request | existing open Promotion/Change Request is reused, new immutable Review Snapshot appended, no duplicate branch/PR | Business User |
| R4 | Audience-only update | changed-space gates run; only managed `CAN_RUN` set converges; owner, app `CAN_MANAGE`, and unrelated ACLs remain | KIP |
| R5 | Missing SELECT | `AUDIENCE-01` names principal/table and Terraform queue as informational; merge/deploy can proceed; no UC grant occurs | GestOps |
| R6 | Content blocker | foreign/missing table or low benchmark is shown as author-fixable; no promotable Change Request or prod mutation proceeds | Business User |
| R7 | Final Preflight operational failure | provider/API failure is labeled operational; Attempt records `mutation_started=false`; production readback is unchanged | KIP |
| R8 | Partial deployment and recovery | exact completed/failed stages and targets shown; audience exposure remains last; replay starts at Preflight and converges forward | KIP |
| R9 | Evaluation gate | below-threshold eval blocks the protected merge with useful check annotation; fixed revision creates fresh passing evidence | GestOps |
| R10 | Prod → Dev export | contextual action authorizes both source and overwrite target, applies reviewed de-para, and creates actor/timestamp audit | Business User + KIP |
| R11 | Confused-deputy denial | user requests an inaccessible Space id; guard fails closed even though transport SP can reach it; no read/write/audit false success | KIP |
| R12 | No-op and recovery on reload | unchanged Space says `Nada a promover`; reload restores only the in-flight Promotion and never flashes stale `deployed` | Business User |
| R13 | Secret/runner interruption | user sees operational failure without blame; precise run/step/log link appears; GestOps escalates and KIP restores service | GestOps + KIP |
| R14 | Audit completeness | Requester, GitHub reviewer, deploy approver, revisions, Attempts, failures, recovery and export are queryable without duplicate milestones | KIP + GestOps |
| R15 | Runtime lifecycle/cost | start → use → stop works; stopped App has no App compute charge; weekly usage projection/alert is visible | KIP |

## Evidence required for each run

Capture links or redacted output for:

- app decision/status screen and exact Attempt id;
- Change Request, review actor, required check conclusions and Environment approver;
- content SHA and engine SHA from both PR check and deployment;
- Preflight conclusion, ordered stage outputs, live target Space id, failed/current stage if any;
- final Genie content identity, audience ACL readback and preserved technical/unrelated ACLs;
- an effective-grant inspection showing the advisory case without any grant mutation;
- Lakebase Promotion, Review Snapshot, Deployment Attempts and ordered Audit Trail;
- exact run/job/step log or annotation for failures;
- runner online/job evidence, App runtime state and usage alert evidence.

Timestamps must be BRT or carry an explicit timezone. Evidence must show actor identities without
copying tokens or secret values.

## Critical NO-GO conditions

Any one of these fails the release:

- the live App cannot create/update the content-repo Change Request;
- Requester and Steward are the same governance identity, or the prod gate can be bypassed;
- checks and deploy use different content/engine revisions;
- any active path accepts `CAN_VIEW`, mutates UC grants, or exposes the retired Access Request flow;
- a content/principal error reaches production mutation instead of Preflight;
- the app claims `deployed` without live readback, claims `produção não mudou` without proof, or
  hides a partial mutation;
- replay skips Preflight, duplicates destructive effects, drops unrelated ACLs, or cannot converge;
- `assert_can_access` can be bypassed through the dev transport SP;
- audit actors/stages are missing, duplicated or attributed from display-only identity;
- required runner, App, Lakebase, GitHub App or secret dependencies are unavailable;
- no KIP operator or GestOps Steward is available during the published window;
- App compute/runtime behavior exceeds the agreed policy with no understood cause or owner.

Cosmetic defects may be recorded for follow-up only if they do not obscure the decision, next
action, production-mutation truth, audit evidence or accessibility of a required control.

## Final clean run and sign-off

After all injected failures are recovered, remove temporary rehearsal hooks/branches, confirm the
engine lock again, and run R1–R5, R10, R12, R14 and R15 once more without intervention. This final
run is the pilot's first clean audit chain.

### KIP technical decision

- [ ] All technical prerequisites and R1–R15 evidence pass.
- [ ] Temporary failure hooks are absent from both default branches.
- [ ] Known operational risks have an owner and do not meet a critical NO-GO condition.
- Decision/name/time:

### GestOps governance/adoption decision

- [ ] Steward separation and required gates were observed live.
- [ ] Decision/next-action language is clear enough for first-line guidance.
- [ ] Author guide, availability window and escalation path are published.
- Decision/name/time:

Only two affirmative signatures change the result to **GO**. Otherwise the pilot remains paused,
the failed checklist item becomes the next implementation/operations ticket, and the rehearsal is
repeated from the affected prerequisite.
