<script lang="ts">
  import Icon from './Icon.svelte';
  import type { IconName } from './Icon.svelte';

  // The governed dev→prod lifecycle, in plain Portuguese for a business user.
  const STEPS: { icon: IconName; title: string; desc: string }[] = [
    {
      icon: 'grid',
      title: 'Autoria no dev',
      desc: 'Você cria e ajusta o Genie Space no ambiente de desenvolvimento.',
    },
    {
      icon: 'git-branch',
      title: 'Revisão automatizada',
      desc: 'Um agente revisa contra o manual e roda checagens (grants, PII, eval).',
    },
    {
      icon: 'check-circle',
      title: 'Promoção governada',
      desc: 'O Steward aprova e o deploy para produção é feito de forma segura.',
    },
  ];
</script>

<ol class="flow" aria-label="Como funciona a promoção">
  {#each STEPS as s, i (s.title)}
    <li class="flow__step">
      <span class="flow__index">{i + 1}</span>
      <span class="flow__icon" aria-hidden="true"><Icon name={s.icon} size={22} /></span>
      <div class="flow__text">
        <p class="flow__title">{s.title}</p>
        <p class="flow__desc">{s.desc}</p>
      </div>
    </li>
  {/each}
</ol>

<style>
  .flow {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: var(--space-4);
    counter-reset: none;
  }
  .flow__step {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    padding: var(--space-4);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
  /* the connecting arrow between steps (desktop only) */
  .flow__step:not(:last-child)::after {
    content: '→';
    position: absolute;
    right: calc(-1 * var(--space-4) / 2 - 0.4rem);
    top: 50%;
    transform: translateY(-50%);
    color: var(--border-strong);
    font-size: 1.1rem;
    z-index: 1;
  }
  .flow__index {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.5rem;
    height: 1.5rem;
    border-radius: var(--radius-pill);
    background: var(--primary);
    color: var(--primary-foreground);
    font-size: 0.78rem;
    font-weight: 700;
  }
  .flow__icon {
    color: var(--accent-hover);
  }
  .flow__title {
    margin: 0;
    font-weight: 700;
    font-size: 0.98rem;
  }
  .flow__desc {
    margin: 0;
    font-size: 0.84rem;
    color: var(--muted-foreground);
    line-height: 1.45;
  }
  @media (max-width: 720px) {
    .flow {
      grid-template-columns: 1fr;
    }
    .flow__step:not(:last-child)::after {
      content: '↓';
      right: 50%;
      top: auto;
      bottom: calc(-1 * var(--space-4) / 2 - 0.4rem);
      transform: translateX(50%);
    }
  }
</style>
