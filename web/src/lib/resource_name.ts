/**
 * Derive the path-safe resource NAME from a human production title — the client-side mirror of
 * `genie_reviewer/business_area.resource_name`.
 *
 * Duplicated deliberately, and kept tiny: the UI must show the author the EXACT path their promotion
 * will commit to (`src/dashboards/<area>/<name>/`) before they confirm, which needs the derivation
 * synchronously. The engine remains the authority — it re-derives on submit and refuses a title that
 * yields nothing usable, so a drift between the two shows up as a rejected promotion, never as a
 * wrong path.
 */

/** `Painel de Recebíveis — Volume por Bandeira` -> `painel_de_recebiveis_volume_por_bandeira`. */
export function resourceName(title: string): string {
  const folded = (title ?? '')
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '') // strip combining marks: Recebíveis -> Recebiveis
    .toLowerCase();
  return folded
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/_{2,}/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 48)
    .replace(/^_+|_+$/g, '');
}

/** Whether a title yields a usable name (must contain a letter to start with). */
export function isUsableName(name: string): boolean {
  return /^[a-z]/.test(name);
}
