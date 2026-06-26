import { describe, it, expect } from 'vitest';
import { phaseChip, PHASE_LABEL } from './status';

describe('phaseChip', () => {
  it('maps known phases to a PT label + semantic tone', () => {
    expect(phaseChip('deployed')).toEqual({ label: 'Implantado em produção', tone: 'success' });
    expect(phaseChip('checks_failed')).toEqual({ label: 'Checagens falharam', tone: 'destructive' });
    expect(phaseChip('deploy_failed')).toEqual({ label: 'Falha no deploy', tone: 'destructive' });
    expect(phaseChip('checks_running')).toEqual({ label: 'Checagens em execução', tone: 'warning' });
    expect(phaseChip('awaiting_approval')).toEqual({
      label: 'Aguardando aprovação do Steward',
      tone: 'warning',
    });
    expect(phaseChip('deploying')).toEqual({ label: 'Implantando…', tone: 'accent' });
    expect(phaseChip('open')).toEqual({ label: 'Aguardando merge', tone: 'neutral' });
    expect(phaseChip('merged')).toEqual({ label: 'Merge concluído', tone: 'neutral' });
    expect(phaseChip('closed')).toEqual({ label: 'PR fechado', tone: 'neutral' });
  });

  it('returns a non-empty label + valid tone for every known phase', () => {
    const tones = ['neutral', 'accent', 'success', 'warning', 'destructive'];
    for (const p of Object.keys(PHASE_LABEL)) {
      const c = phaseChip(p);
      expect(c.label.length).toBeGreaterThan(0);
      expect(tones).toContain(c.tone);
    }
  });

  it('falls back gracefully for null / undefined / unknown phases', () => {
    expect(phaseChip(null)).toEqual({ label: '—', tone: 'neutral' });
    expect(phaseChip(undefined)).toEqual({ label: '—', tone: 'neutral' });
    expect(phaseChip('')).toEqual({ label: '—', tone: 'neutral' });
    expect(phaseChip('something_new')).toEqual({ label: 'something_new', tone: 'neutral' });
  });
});
