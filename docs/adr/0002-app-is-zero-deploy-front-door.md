# The App is a zero-deploy-rights front door; the SP deploys; 3-party SoD

**Status:** accepted (2026-06-22)

The App never writes prod. It opens a real PR (as a bot identity) and, on the
steward's in-app approval, calls the GitHub API to release the environment gate —
but the **only identity that runs `bundle deploy` into prod is a per-domain CI/CD
service principal**, post-merge. This preserves **3-party separation of duties**
(Requester ≠ Approver ≠ deploying SP), which is both good engineering and the
BCB-538 "documented access governance" answer. We chose it over an always-on app
that deploys directly because that would concentrate prod-write rights in a
long-running service and recreate the ~$20k/mo idle-Apps cost Acme is cutting.

## Consequences
- The App must be **scale-to-zero and thin** — a management/promotion surface, not
  a deployer and not a Genie-authoring rebuild.
- The PR is **real and human-approved** — "the agent only removes the typing"
  (the framing Davi accepted). Versioned, auditable, defensible.
- The App proxies *both* human roles (author requests + reads findings; steward
  approves) so non-technical users never touch GitHub — but GitHub remains the
  system of record.
