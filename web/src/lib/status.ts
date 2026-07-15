/** Presentation for the live promotion phases (GH3) — label + semantic badge tone. */
import type { PromotePhase } from './api';

export const PHASE_LABEL: Record<PromotePhase, string> = {
  open: 'Aguardando merge',
  checks_running: 'Checagens em execução',
  checks_failed: 'Checagens falharam',
  awaiting_approval: 'Aguardando aprovação do Steward',
  deploying: 'Implantando…',
  deployed: 'Implantado em produção',
  deploy_failed: 'Falha no deploy',
  merged: 'Merge concluído',
  closed: 'PR fechado',
};

/** PT labels for the durable audit-trail event types (LB4). */
export const EVENT_LABEL: Record<string, string> = {
  requested: 'Promoção solicitada',
  re_reviewed: 'Revisão refeita',
  pr_opened: 'PR aberto',
  pr_review_approved: 'Revisão do PR aprovada',
  merged: 'Merge para main',
  deploy_approved: 'Deploy aprovado (gate liberado)',
  deployed: 'Implantado em produção',
  failed: 'Falha',
  closed: 'PR fechado (abandonado)',
  // A3/F1: app-direct (no git PR) — recurs each time dev is reseeded from this promotion.
  rehydrated: 'Rehidratado para dev',
};

export type BadgeTone = 'neutral' | 'accent' | 'success' | 'warning' | 'destructive';

export function phaseTone(phase: PromotePhase): BadgeTone {
  switch (phase) {
    case 'deployed':
      return 'success';
    case 'checks_failed':
    case 'deploy_failed':
      return 'destructive';
    case 'checks_running':
    case 'awaiting_approval':
      return 'warning';
    case 'deploying':
      return 'accent';
    default:
      return 'neutral';
  }
}

/**
 * One-call chip presentation for a promotion's phase string — the stored `current_phase` (LB5) or the
 * live `live_status.phase` (GH3), which arrive as plain strings. Returns a PT label + semantic tone,
 * with a graceful fallback for null/unknown so an unexpected backend value never renders blank or
 * throws. Single source of truth for the StatusChip + the history/home lists.
 */
export function phaseChip(phase: string | null | undefined): { label: string; tone: BadgeTone } {
  if (!phase) return { label: '—', tone: 'neutral' };
  return { label: PHASE_LABEL[phase as PromotePhase] ?? phase, tone: phaseTone(phase as PromotePhase) };
}

/** S3 (merged "Meus espaços" page, GR3): the cross-space status filter's 4 buckets — open/
 * merged/failed/deployed. `checks_failed`/`deploy_failed`/`closed` all read as "failed" for
 * filtering purposes (didn't make it), even though `checks_failed` isn't always terminal (a
 * fix can still be pushed) — the filter is about "does this need attention right now", not
 * strict terminality. Unrecognized/null phases fall into "open" (never silently dropped from
 * every filter). */
export type StatusBucket = 'open' | 'merged' | 'failed' | 'deployed';

/** The authoritative governance actor for an audit event: GitHub login when present, else the
 * display-only app email (OBO), else "sistema". Shared by `AuditTrail.svelte` (per-promotion) and
 * the standalone Audit page (S4/GR4, cross-promotion) so both render actors identically. */
export interface ActorDisplay {
  who: string;
  kind: 'github' | 'app' | 'system';
}

export function actorDisplay(e: {
  actor_github_login: string | null;
  actor_app_email: string | null;
}): ActorDisplay {
  if (e.actor_github_login) return { who: e.actor_github_login, kind: 'github' };
  if (e.actor_app_email) return { who: e.actor_app_email, kind: 'app' };
  return { who: 'sistema', kind: 'system' };
}

export function statusBucket(phase: string | null | undefined): StatusBucket {
  switch (phase) {
    case 'deployed':
      return 'deployed';
    case 'checks_failed':
    case 'deploy_failed':
    case 'closed':
      return 'failed';
    case 'merged':
      return 'merged';
    default:
      return 'open'; // open, checks_running, awaiting_approval, deploying, null/unknown
  }
}
