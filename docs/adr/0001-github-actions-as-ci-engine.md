# GitHub Actions is the CI/CD engine, not Databricks Workflows

**Status:** accepted (2026-06-22)

Although this is a Databricks demo, the promotion pipeline (deterministic checks,
agent review, eval-run gate, `bundle deploy`) runs in **GitHub Actions** with a
real PR — not a Databricks Workflow/Job triggered by the app. We chose this
because Acme's platform gatekeeper (Davi) is a CI/CD/Terraform purist whose
reflex question is "is there a real PR?"; a textbook PR + branch-protection +
GitHub-Environment-gate flow is the credibility anchor, and it reuses the proven
`bimbo_demo` engine wholesale.

## Considered options
- **Databricks-native (app → Workflow)** — more self-contained, compute stays
  on-platform, fewer external failure points; rejected because it weakens the
  "real PR / real CI shop" story Davi needs and discards the bimbo reuse.
- **App-orchestrated deploy** — rejected: concentrates prod-write rights in a
  long-running app, violating separation of duties (see ADR-0002).

## Consequences
- Live-demo fragility (runner cold-start, OIDC/M2M auth, network) is the top
  operational risk → mitigate with a **pre-warmed self-hosted runner**
  (workspace IP-ACL blocks GitHub-hosted runners), pre-tested OAuth M2M, retries,
  and a **pre-recorded fallback clip**.
- Take-home note for any customer on Azure DevOps: the pattern translates; gaps
  are syntax, not capability (per `bimbo_demo/docs/ado-translation.md`).
