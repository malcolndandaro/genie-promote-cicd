import { describe, it, expect } from 'vitest';
import { matchesQuery, filterOptions, toggleOption, isSelected, dedupeById, type PickerOption } from './picker';

const ANA: PickerOption = { id: 'u1', label: 'Ana Silva', sublabel: 'ana@x.com' };
const BOB: PickerOption = { id: 'u2', label: 'Bob Souza', sublabel: 'bob@x.com' };
const GROUP: PickerOption = { id: 'g1', label: 'grp_genie_receivables' };

describe('matchesQuery', () => {
  it('matches everything on a blank query', () => {
    expect(matchesQuery(ANA, '')).toBe(true);
    expect(matchesQuery(ANA, '   ')).toBe(true);
  });

  it('matches case-insensitively on the label', () => {
    expect(matchesQuery(ANA, 'ana')).toBe(true);
    expect(matchesQuery(ANA, 'ANA SILVA')).toBe(true);
  });

  it('matches on the sublabel too', () => {
    expect(matchesQuery(ANA, 'ana@x.com')).toBe(true);
  });

  it('tolerates a missing sublabel', () => {
    expect(matchesQuery(GROUP, 'genie')).toBe(true);
    expect(matchesQuery(GROUP, 'nope')).toBe(false);
  });

  it('does not match an unrelated query', () => {
    expect(matchesQuery(ANA, 'zzz')).toBe(false);
  });
});

describe('filterOptions', () => {
  it('returns every option for a blank query', () => {
    expect(filterOptions([ANA, BOB, GROUP], '')).toEqual([ANA, BOB, GROUP]);
  });

  it('narrows to the matching options', () => {
    expect(filterOptions([ANA, BOB, GROUP], 'bob')).toEqual([BOB]);
  });

  it('returns an empty array when nothing matches', () => {
    expect(filterOptions([ANA, BOB, GROUP], 'zzz')).toEqual([]);
  });
});

describe('toggleOption', () => {
  it('adds an option not yet selected', () => {
    expect(toggleOption([], ANA)).toEqual([ANA]);
    expect(toggleOption([ANA], BOB)).toEqual([ANA, BOB]);
  });

  it('removes an option already selected (by id)', () => {
    expect(toggleOption([ANA, BOB], ANA)).toEqual([BOB]);
  });

  it('never mutates the input array', () => {
    const selected = [ANA];
    const out = toggleOption(selected, BOB);
    expect(selected).toEqual([ANA]); // unchanged
    expect(out).not.toBe(selected); // new reference
  });
});

describe('isSelected', () => {
  it('is true only when the id is present in the selection', () => {
    expect(isSelected([ANA, BOB], ANA)).toBe(true);
    expect(isSelected([ANA], BOB)).toBe(false);
    expect(isSelected([], ANA)).toBe(false);
  });
});

describe('dedupeById', () => {
  it('keeps the first occurrence of each id', () => {
    const anaAgain: PickerOption = { id: 'u1', label: 'Ana (renamed)' };
    expect(dedupeById([ANA, BOB, anaAgain])).toEqual([ANA, BOB]);
  });

  it('is a no-op on an already-unique list', () => {
    expect(dedupeById([ANA, BOB, GROUP])).toEqual([ANA, BOB, GROUP]);
  });

  it('handles an empty list', () => {
    expect(dedupeById([])).toEqual([]);
  });
});
