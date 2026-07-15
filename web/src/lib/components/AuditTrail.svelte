<script lang="ts">
  import Badge from './Badge.svelte';
  import { EVENT_LABEL, actorDisplay } from '../status';
  import type { AuditEvent } from '../api';

  interface Props {
    events: AuditEvent[];
  }
  let { events }: Props = $props();

  function when(e: AuditEvent): string {
    const iso = e.github_event_at ?? e.occurred_at;
    const d = new Date(iso);
    return isNaN(d.getTime()) ? iso : d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
  }
</script>

<section class="audit">
  <h3 class="audit__heading">Trilha de auditoria</h3>
  {#if events.length === 0}
    <p class="muted text-sm">Nenhum evento ainda.</p>
  {:else}
    <ol class="audit__list">
      {#each events as e (e.seq)}
        {@const a = actorDisplay(e)}
        <li class="audit__item">
          <span class="audit__dot" aria-hidden="true"></span>
          <div class="audit__body">
            <span class="audit__type">{EVENT_LABEL[e.event_type] ?? e.event_type}</span>
            <span class="audit__meta">
              <Badge tone={a.kind === 'github' ? 'accent' : 'neutral'}>{a.who}</Badge>
              {#if a.kind === 'app'}<span class="muted text-xs">(somente exibição)</span>{/if}
              <time class="muted text-xs">{when(e)}</time>
            </span>
          </div>
        </li>
      {/each}
    </ol>
  {/if}
  <p class="muted text-xs">
    Identidade de governança vem do GitHub (autoritativa); o e-mail (OBO) é somente exibição.
  </p>
</section>

<style>
  .audit {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .audit__heading {
    font-size: 0.95rem;
    margin: 0;
  }
  .audit__list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    border-left: 2px solid var(--border);
    padding-left: var(--space-4);
  }
  .audit__item {
    position: relative;
    display: flex;
    gap: var(--space-3);
  }
  .audit__dot {
    position: absolute;
    left: calc(-1 * var(--space-4) - 5px);
    top: 0.35rem;
    width: 0.55rem;
    height: 0.55rem;
    border-radius: 50%;
    background: var(--accent);
  }
  .audit__body {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .audit__type {
    font-weight: 600;
    font-size: 0.9rem;
  }
  .audit__meta {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
</style>
