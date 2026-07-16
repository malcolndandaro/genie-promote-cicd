# Keep GitHub as the pilot's deep integration module; neutralize the domain before adding adapters

**Status:** accepted (2026-07-16)

## Context

The pilot runs on GitHub and GitHub Actions, while CERC may later choose Azure DevOps. A generic
`CICDProvider` interface looks future-friendly, but there is only one production provider today.
Designing Azure-shaped methods without an Azure implementation would expose guesses as interface
and make the module shallower, not more portable.

The current `GitHubApp` already centralizes App auth, branch/file updates, PRs, comments, checks,
environment deployments and audit actors behind injected HTTP/token dependencies. The problem is
not scattered REST calls; it is that GitHub-shaped dictionaries and PR-only vocabulary leak into
the domain/store/UI.

## Decision

### Provider-neutral now

- A Promotion is 1:1 with a **Change Request**, whose pilot adapter is a GitHub pull request.
- Persist a provider discriminator, opaque external id, external URL, content revision and engine
  revision. `pr_number` remains only as Phase-1 compatibility data.
- Use canonical values for `PromotionObservation`, `DeploymentAttempt`, stages, checks, actors and
  phases. The rest of the app never parses GitHub check-run/deployment payloads.
- One Deployment Attempt pins both the content SHA and engine SHA; the PR check and approved deploy
  must run the same pair.

### Keep behind the GitHub module

- GitHub App JWT/installation token mechanics;
- branch naming, file commits/removals and pull-request upsert;
- review comments and links;
- check-run/annotation aggregation;
- branch protection and Environment reviewer inspection;
- deploy-run and approving-actor translation.

The GitHub implementation returns canonical domain values, not raw GitHub dictionaries. Tests use
its existing fake transport and assert through this public interface.

### Do not build yet

- no generic `CICDProvider` base class/protocol;
- no Azure DevOps adapter, auth model or YAML;
- no lowest-common-denominator interface for GitHub and hypothetical Azure features;
- no generic administration surface for provider-specific branch/environment policy.

When a second production provider is actually selected, extract the now-proven canonical interface
and implement two adapters. Until then, one production adapter does not justify a speculative seam.

### Immutable cross-repo engine revision

The content repo records an engine lock (commit SHA or immutable release tag). Its workflows read
that lock and checkout the same engine revision in PR checks and deploy. KIP changes the engine lock
through a reviewed content-repo PR. Workflows never silently consume engine `main`, eliminating a
class of “checks passed with one engine, deploy ran with another” failures.

## Consequences

- Azure DevOps remains a future adapter exercise, but the domain/store/UI no longer need a PR-shaped
  rewrite first.
- GitHub retains high leverage and locality as one deep module instead of being wrapped in shallow
  pass-through layers.
- Provider-specific governance inspection can remain GitHub-only during the pilot.
- The content repo gains an explicit engine upgrade operation owned by KIP.
- A future second adapter is the trigger for introducing a real provider seam.
