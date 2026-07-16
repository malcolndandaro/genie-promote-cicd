<script lang="ts" module>
  // A tiny, SAFE markdown renderer for the subset the Knowledge Assistants emit (bold, inline code,
  // ordered/unordered lists, paragraphs). Deliberately NOT a full markdown lib and NEVER `{@html}`:
  // the input is model output, so we tokenize into a typed tree and render it with plain Svelte
  // markup — there is no HTML-injection surface at all. Anything unrecognized falls through as text.

  type Inline = { kind: 'text' | 'bold' | 'code'; value: string };
  type Block =
    | { type: 'p'; spans: Inline[] }
    | { type: 'ul'; items: Inline[][] }
    | { type: 'ol'; items: Inline[][] };

  // Split one line into inline spans: **bold** and `code`. A lone/odd marker stays literal text.
  export function parseInline(text: string): Inline[] {
    const spans: Inline[] = [];
    // Alternate on ** ... ** and ` ... ` (non-greedy); everything else is text.
    const re = /\*\*([^*]+)\*\*|`([^`]+)`/g;
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) spans.push({ kind: 'text', value: text.slice(last, m.index) });
      if (m[1] !== undefined) spans.push({ kind: 'bold', value: m[1] });
      else if (m[2] !== undefined) spans.push({ kind: 'code', value: m[2] });
      last = m.index + m[0].length;
    }
    if (last < text.length) spans.push({ kind: 'text', value: text.slice(last) });
    return spans;
  }

  // Group lines into paragraphs and lists. A blank line separates blocks. Lines starting with
  // `- `/`* ` are unordered items; `<n>. ` are ordered items. Consecutive same-kind items merge.
  export function parseBlocks(src: string): Block[] {
    const lines = (src ?? '').replace(/\r\n/g, '\n').split('\n');
    const blocks: Block[] = [];
    let para: string[] = [];
    const flushPara = () => {
      if (para.length) {
        blocks.push({ type: 'p', spans: parseInline(para.join(' ').trim()) });
        para = [];
      }
    };
    for (const raw of lines) {
      const line = raw.trim();
      if (!line) { flushPara(); continue; }
      const ol = line.match(/^\d+\.\s+(.*)$/);
      const ul = line.match(/^[-*]\s+(.*)$/);
      if (ol) {
        flushPara();
        const prev = blocks[blocks.length - 1];
        if (prev && prev.type === 'ol') prev.items.push(parseInline(ol[1]));
        else blocks.push({ type: 'ol', items: [parseInline(ol[1])] });
      } else if (ul) {
        flushPara();
        const prev = blocks[blocks.length - 1];
        if (prev && prev.type === 'ul') prev.items.push(parseInline(ul[1]));
        else blocks.push({ type: 'ul', items: [parseInline(ul[1])] });
      } else {
        para.push(line);
      }
    }
    flushPara();
    return blocks;
  }
</script>

<script lang="ts">
  interface Props {
    /** The markdown-ish text (typically a KA advisory answer). */
    text: string;
  }
  let { text }: Props = $props();
  let blocks = $derived(parseBlocks(text));
</script>

<div class="md">
  {#each blocks as block (block)}
    {#if block.type === 'p'}
      <p>{#each block.spans as s}{#if s.kind === 'bold'}<strong>{s.value}</strong>{:else if s.kind === 'code'}<code>{s.value}</code>{:else}{s.value}{/if}{/each}</p>
    {:else if block.type === 'ul'}
      <ul>{#each block.items as item}<li>{#each item as s}{#if s.kind === 'bold'}<strong>{s.value}</strong>{:else if s.kind === 'code'}<code>{s.value}</code>{:else}{s.value}{/if}{/each}</li>{/each}</ul>
    {:else}
      <ol>{#each block.items as item}<li>{#each item as s}{#if s.kind === 'bold'}<strong>{s.value}</strong>{:else if s.kind === 'code'}<code>{s.value}</code>{:else}{s.value}{/if}{/each}</li>{/each}</ol>
    {/if}
  {/each}
</div>

<style>
  .md {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .md :global(p) {
    margin: 0;
  }
  .md :global(ul),
  .md :global(ol) {
    margin: 0;
    padding-left: 1.25rem;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  .md :global(code) {
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.85em;
    background: var(--surface-inset, rgba(0, 0, 0, 0.06));
    padding: 0.05rem 0.3rem;
    border-radius: var(--radius-sm);
  }
</style>
