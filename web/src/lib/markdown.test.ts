import { describe, it, expect } from 'vitest';
import { parseInline, parseBlocks } from './components/Markdown.svelte';

// The safe inline-markdown renderer used for KA advisory findings (bold, inline code, lists).
// These test the pure parser; the Svelte component just maps the tree to <strong>/<code>/<li>.

describe('parseInline', () => {
  it('splits bold and inline code, keeping surrounding text', () => {
    expect(parseInline('use `prod_recebiveis`, **não** dev')).toEqual([
      { kind: 'text', value: 'use ' },
      { kind: 'code', value: 'prod_recebiveis' },
      { kind: 'text', value: ', ' },
      { kind: 'bold', value: 'não' },
      { kind: 'text', value: ' dev' },
    ]);
  });

  it('leaves plain text untouched (no markers)', () => {
    expect(parseInline('texto simples')).toEqual([{ kind: 'text', value: 'texto simples' }]);
  });

  it('treats a lone/odd marker as literal text', () => {
    expect(parseInline('2 * 3 = 6')).toEqual([{ kind: 'text', value: '2 * 3 = 6' }]);
  });
});

describe('parseBlocks', () => {
  it('groups a paragraph and joins wrapped lines', () => {
    const b = parseBlocks('linha um\nlinha dois');
    expect(b).toHaveLength(1);
    expect(b[0].type).toBe('p');
    expect(b[0]).toMatchObject({ spans: [{ kind: 'text', value: 'linha um linha dois' }] });
  });

  it('parses an ordered list into merged items', () => {
    const b = parseBlocks('**Pontos:**\n1. Primeiro\n2. Segundo');
    expect(b.map((x) => x.type)).toEqual(['p', 'ol']);
    const ol = b[1] as { type: 'ol'; items: unknown[] };
    expect(ol.items).toHaveLength(2);
  });

  it('parses an unordered list (- or *)', () => {
    const b = parseBlocks('- um\n* dois');
    expect(b).toHaveLength(1);
    expect(b[0].type).toBe('ul');
    expect((b[0] as { items: unknown[] }).items).toHaveLength(2);
  });

  it('separates blocks on a blank line', () => {
    const b = parseBlocks('parágrafo um\n\nparágrafo dois');
    expect(b.map((x) => x.type)).toEqual(['p', 'p']);
  });

  it('renders the KA-style answer shape (bold headings + numbered list + code)', () => {
    const src = '**Conformidade:** catálogo `prod_recebiveis` ok.\n\n**Atenção:**\n1. **Benchmark**: mín 2\n2. **Grants**: SELECT';
    const b = parseBlocks(src);
    expect(b.map((x) => x.type)).toEqual(['p', 'p', 'ol']);
    expect((b[2] as { items: unknown[] }).items).toHaveLength(2);
  });

  it('is safe on empty/undefined input', () => {
    expect(parseBlocks('')).toEqual([]);
    // @ts-expect-error — defensive: undefined must not throw
    expect(parseBlocks(undefined)).toEqual([]);
  });
});
