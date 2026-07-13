import { describe, it, expect } from 'vitest';
import { Promotion } from './promotion.svelte';
import type { AccessSpec, PromotableResource } from './types';

// G9 (found live, PR #25): re-requesting the SAME space silently carried the PREVIOUS
// AccessSpec/prod-title/table-mapping declaration — select() never reset them, and the
// confirmation-panel remount keyed off resource id (unchanged for a same-space reselect) never
// gave the declaration forms a fresh start either. These cover the model-level fix directly (no
// DOM/component needed — select() is a plain method on a small reactive state class).

const SPEC: AccessSpec = { space_permissions: [], uc_principals: [{ principal: 'users', is_group: true }] };
const SPACE_A: PromotableResource = { id: 'sp1', title: 'Recebíveis', kind: 'genie_space' };
const SPACE_B: PromotableResource = { id: 'sp2', title: 'Outro espaço', kind: 'genie_space' };

describe('Promotion.select', () => {
  it('clears a prior declaration when re-selecting a DIFFERENT space', () => {
    const p = new Promotion();
    p.select(SPACE_A);
    p.pendingAccessSpec = SPEC;
    p.pendingProdTitle = 'Nome customizado';
    p.pendingTableMapping = { 'dev_x.a.b': 'prod_x.a.b2' };

    p.select(SPACE_B);
    expect(p.pendingAccessSpec).toBeUndefined();
    expect(p.pendingProdTitle).toBeUndefined();
    expect(p.pendingTableMapping).toBeUndefined();
  });

  it('ALSO clears a prior declaration when re-selecting the SAME space (the live bug)', () => {
    const p = new Promotion();
    p.select(SPACE_A);
    p.pendingAccessSpec = SPEC;
    p.pendingProdTitle = 'Nome customizado';
    p.pendingTableMapping = { 'dev_x.a.b': 'prod_x.a.b2' };

    p.select(SPACE_A); // re-select the SAME resource id
    expect(p.pendingAccessSpec).toBeUndefined();
    expect(p.pendingProdTitle).toBeUndefined();
    expect(p.pendingTableMapping).toBeUndefined();
  });

  it('increments selectionSeq on every select(), same-space included — MeusEspacos keys the ' +
    'confirmation panel off this so its declaration forms always remount fresh', () => {
    const p = new Promotion();
    const start = p.selectionSeq;
    p.select(SPACE_A);
    p.select(SPACE_A);
    p.select(SPACE_B);
    expect(p.selectionSeq).toBe(start + 3);
  });

  it('deselecting (select(null)) also clears any pending declaration', () => {
    const p = new Promotion();
    p.select(SPACE_A);
    p.pendingAccessSpec = SPEC;
    p.select(null);
    expect(p.pendingAccessSpec).toBeUndefined();
    expect(p.resource).toBeNull();
  });
});
