<script lang="ts" module>
  // Inline, license-safe SVG icons (Feather-style stroke icons are MIT; the GitHub mark is GitHub's
  // public logo path). Inlining avoids a runtime icon dependency. `currentColor` lets callers theme
  // via CSS `color`. The strings are static (no user input) so {@html} is safe here.
  export type IconName =
    | 'home'
    | 'grid'
    | 'git-branch'
    | 'plus-circle'
    | 'check-circle'
    | 'shield'
    | 'github'
    | 'menu'
    | 'close'
    | 'external';

  // Stroke icons share the svg stroke attributes; only their inner geometry differs.
  const STROKE: Record<Exclude<IconName, 'github'>, string> = {
    home: '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>',
    'git-branch':
      '<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
    'plus-circle':
      '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/>',
    'check-circle':
      '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    menu: '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>',
    close: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    external:
      '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
  };

  // GitHub's mark is a single filled path.
  const GITHUB =
    'M12 .5C5.7.5.5 5.7.5 12c0 5.1 3.3 9.4 7.9 10.9.6.1.8-.2.8-.5v-1.8c-3.2.7-3.9-1.5-3.9-1.5-.5-1.3-1.3-1.7-1.3-1.7-1.1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1 .1.8 1.7 2.6 1.2.1-.7.4-1.2.7-1.5-2.6-.3-5.3-1.3-5.3-5.8 0-1.3.5-2.3 1.2-3.1-.1-.3-.5-1.5.1-3.1 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0C17 4.6 18 4.9 18 4.9c.6 1.6.2 2.8.1 3.1.8.8 1.2 1.8 1.2 3.1 0 4.5-2.7 5.5-5.3 5.8.4.4.8 1.1.8 2.2v3.3c0 .3.2.6.8.5 4.6-1.5 7.9-5.8 7.9-10.9C23.5 5.7 18.3.5 12 .5z';
</script>

<script lang="ts">
  interface Props {
    name: IconName;
    size?: number | string;
    /** Decorative by default; pass a label to expose it to assistive tech. */
    label?: string;
  }
  let { name, size = 20, label }: Props = $props();
</script>

{#if name === 'github'}
  <svg
    class="icon"
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    role={label ? 'img' : 'presentation'}
    aria-hidden={label ? undefined : 'true'}
    aria-label={label}
  >
    <path d={GITHUB} />
  </svg>
{:else}
  <svg
    class="icon"
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    role={label ? 'img' : 'presentation'}
    aria-hidden={label ? undefined : 'true'}
    aria-label={label}
  >
    {@html STROKE[name]}
  </svg>
{/if}

<style>
  .icon {
    flex-shrink: 0;
    display: block;
  }
</style>
