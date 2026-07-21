import { describe, it, expect } from 'vitest';
import { phaseChip, PHASE_LABEL, EVENT_LABEL, statusBucket } from './status';

describe('phaseChip', () => {
  it('maps known phases to a PT label + semantic tone', () => {
    expect(phaseChip('deployed')).toEqual({ label: 'Implantado em produção', tone: 'success' });
    expect(phaseChip('checks_failed')).toEqual({ label: 'Checagens falharam', tone: 'destructive' });
    expect(phaseChip('deploy_failed')).toEqual({ label: 'Falha no deploy', tone: 'destructive' });
    expect(phaseChip('checks_running')).toEqual({ label: 'Checagens em execução', tone: 'warning' });
    expect(phaseChip('awaiting_approval')).toEqual({
      label: 'Aguardando aprovação da Plataforma',
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

describe('EVENT_LABEL', () => {
  it('has a PT label for the A3/F1 rehydrate audit event', () => {
    expect(EVENT_LABEL.rehydrated).toBe('Rehidratado para dev');
  });
});

describe('statusBucket', () => {
  it('maps each phase to one of the 4 filter buckets (S3/GR3)', () => {
    expect(statusBucket('deployed')).toBe('deployed');
    expect(statusBucket('merged')).toBe('merged');
    expect(statusBucket('checks_failed')).toBe('failed');
    expect(statusBucket('deploy_failed')).toBe('failed');
    expect(statusBucket('closed')).toBe('failed');
    expect(statusBucket('open')).toBe('open');
    expect(statusBucket('checks_running')).toBe('open');
    expect(statusBucket('awaiting_approval')).toBe('open');
    expect(statusBucket('deploying')).toBe('open');
  });

  it('falls back to "open" for null/undefined/unknown phases — never dropped from every filter', () => {
    expect(statusBucket(null)).toBe('open');
    expect(statusBucket(undefined)).toBe('open');
    expect(statusBucket('something_new')).toBe('open');
  });
});
