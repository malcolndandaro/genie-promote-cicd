/**
 * Pure logic for `Picker.svelte` (G1) — kept out of the component so it's unit-testable in Node
 * (the vitest config runs `environment: 'node'`, no DOM/component renderer), mirroring how
 * `route.ts`/`status.ts` split pure logic from their Svelte consumers. The component owns async
 * loading + keyboard/DOM state; everything here is a plain, side-effect-free function.
 */

/** One selectable option a Picker renders — deliberately generic (not `Principal`/space-shaped) so
 * the SAME component drives spaces, users, and groups; callers map their domain type into this. */
export interface PickerOption {
  id: string;
  label: string;
  sublabel?: string;
}

/** Case-insensitive substring match against label + sublabel — a CLIENT-side quick filter over an
 * already-loaded option set (e.g. a caller with a small static list that skips the round trip
 * entirely). The server-side search (when a picker's `search` fn hits an API) is the actual source
 * of truth for anything larger; this is a local fallback/complement, never a substitute for it. */
export function matchesQuery(option: PickerOption, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return option.label.toLowerCase().includes(q) || (option.sublabel ?? '').toLowerCase().includes(q);
}

export function filterOptions(options: PickerOption[], query: string): PickerOption[] {
  return options.filter((o) => matchesQuery(o, query));
}

/** Multi-select toggle: adds the option if not already selected (by id), else removes it. Always
 * returns a NEW array (never mutates `selected`) so a Svelte `$state` reassignment triggers
 * reactivity even when the caller happens to pass the same reference back in. */
export function toggleOption(selected: PickerOption[], option: PickerOption): PickerOption[] {
  const idx = selected.findIndex((o) => o.id === option.id);
  if (idx === -1) return [...selected, option];
  return selected.filter((o) => o.id !== option.id);
}

export function isSelected(selected: PickerOption[], option: PickerOption): boolean {
  return selected.some((o) => o.id === option.id);
}

/** De-dupe by id, keeping the FIRST occurrence — used when merging a fresh search page against
 * options that must stay pinned (e.g. the caller's already-selected chips). */
export function dedupeById(options: PickerOption[]): PickerOption[] {
  const seen = new Set<string>();
  const out: PickerOption[] = [];
  for (const o of options) {
    if (seen.has(o.id)) continue;
    seen.add(o.id);
    out.push(o);
  }
  return out;
}
