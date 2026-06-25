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
