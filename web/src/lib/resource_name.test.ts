import { describe, expect, it } from 'vitest';
import { isUsableName, resourceName } from './resource_name';

// This mirrors `genie_reviewer/business_area.resource_name`, and the two MUST agree: the UI shows the
// author the exact path the promotion will commit to, and the engine re-derives it on submit. These
// cases are the same ones pinned on the Python side.
describe('resourceName — the client mirror of the engine derivation', () => {
  it('folds accents and collapses separators', () => {
    expect(resourceName('Painel de Recebíveis — Volume por Bandeira')).toBe(
      'painel_de_recebiveis_volume_por_bandeira',
    );
  });

  it('strips punctuation without leaving double or edge underscores', () => {
    expect(resourceName('Volume/Bandeira (2026)')).toBe('volume_bandeira_2026');
    expect(resourceName('Ação & Risco')).toBe('acao_risco');
  });

  it('caps the length so a long title stays an addressable path segment', () => {
    expect(resourceName('a'.repeat(80)).length).toBeLessThanOrEqual(48);
  });

  it('flags a title that yields no usable name', () => {
    // A path segment must start with a letter — the engine refuses these outright.
    expect(isUsableName(resourceName('123'))).toBe(false);
    expect(isUsableName(resourceName('   '))).toBe(false);
    expect(isUsableName(resourceName('Risco'))).toBe(true);
  });
});
