import { describe, it, expect } from 'vitest';
import { parseHash, formatHash, sameRoute, DEFAULT_ROUTE, type Route } from './route';

describe('parseHash', () => {
  it('falls back to the default route for empty/missing hashes', () => {
    expect(parseHash('')).toEqual(DEFAULT_ROUTE);
    expect(parseHash('#')).toEqual(DEFAULT_ROUTE);
    expect(parseHash('#/')).toEqual(DEFAULT_ROUTE);
  });

  it('falls back to the default route for unknown ids', () => {
    expect(parseHash('#/bogus')).toEqual(DEFAULT_ROUTE);
    expect(parseHash('#/../etc')).toEqual(DEFAULT_ROUTE);
  });

  it('parses each known top-level route', () => {
    expect(parseHash('#/inicio')).toEqual({ id: 'inicio' });
    expect(parseHash('#/espacos')).toEqual({ id: 'espacos' });
    expect(parseHash('#/promocoes')).toEqual({ id: 'promocoes' });
    expect(parseHash('#/acesso')).toEqual({ id: 'acesso' });
    expect(parseHash('#/rehidratar')).toEqual({ id: 'rehidratar' });
    expect(parseHash('#/admin')).toEqual({ id: 'admin' });
    expect(parseHash('#/configuracoes')).toEqual({ id: 'configuracoes' });
  });

  it('parses a promotion id param on #/promocoes/:id', () => {
    expect(parseHash('#/promocoes/abc-123')).toEqual({ id: 'promocoes', param: 'abc-123' });
  });

  it('decodes an encoded promotion id', () => {
    expect(parseHash('#/promocoes/a%2Fb')).toEqual({ id: 'promocoes', param: 'a/b' });
  });

  it('ignores stray params on routes that do not take one', () => {
    expect(parseHash('#/espacos/whatever')).toEqual({ id: 'espacos' });
    expect(parseHash('#/inicio/x')).toEqual({ id: 'inicio' });
  });

  it('tolerates a leading hash with or without the slash', () => {
    expect(parseHash('#inicio')).toEqual({ id: 'inicio' });
    expect(parseHash('#/inicio')).toEqual({ id: 'inicio' });
  });
});

describe('formatHash', () => {
  it('formats top-level routes', () => {
    expect(formatHash({ id: 'inicio' })).toBe('#/inicio');
    expect(formatHash({ id: 'promocoes' })).toBe('#/promocoes');
  });

  it('formats a promotion deep-link with its id', () => {
    expect(formatHash({ id: 'promocoes', param: 'abc-123' })).toBe('#/promocoes/abc-123');
  });

  it('encodes an id with reserved characters', () => {
    expect(formatHash({ id: 'promocoes', param: 'a/b' })).toBe('#/promocoes/a%2Fb');
  });
});

describe('round-trip', () => {
  const routes: Route[] = [
    { id: 'inicio' },
    { id: 'espacos' },
    { id: 'promocoes' },
    { id: 'promocoes', param: 'p-9' },
    { id: 'acesso' },
    { id: 'rehidratar' },
    { id: 'admin' },
    { id: 'configuracoes' },
  ];
  it('parseHash(formatHash(r)) === r for every route', () => {
    for (const r of routes) expect(parseHash(formatHash(r))).toEqual(r);
  });
});

describe('sameRoute', () => {
  it('compares id and param', () => {
    expect(sameRoute({ id: 'promocoes' }, { id: 'promocoes' })).toBe(true);
    expect(sameRoute({ id: 'promocoes', param: 'a' }, { id: 'promocoes', param: 'a' })).toBe(true);
    expect(sameRoute({ id: 'promocoes', param: 'a' }, { id: 'promocoes', param: 'b' })).toBe(false);
    expect(sameRoute({ id: 'inicio' }, { id: 'espacos' })).toBe(false);
  });
});
