<script lang="ts">
  import Icon from './Icon.svelte';
  import type { IconName } from './Icon.svelte';

  // The governed dev→prod lifecycle, in plain Portuguese for a business user.
  const STEPS: { icon: IconName; title: string; desc: string }[] = [
    {
      icon: 'grid',
      title: 'Escolher',
      desc: 'Selecione o Space no Dev',
    },
    {
      icon: 'git-branch',
      title: 'Checks',
      desc: 'Qualidade, acesso e eval',
    },
    {
      icon: 'check-circle',
      title: 'Steward',
      desc: 'Revisão independente',
    },
    {
      icon: 'external',
      title: 'Produção',
      desc: 'Deploy rastreável',
    },
  ];
</script>

<ol class="flow" aria-label="Como funciona a promoção">
  {#each STEPS as s, i (s.title)}
    <li class="flow__step">
      <span class="flow__marker">
        <span class="flow__index">0{i + 1}</span>
        <span class="flow__icon" aria-hidden="true"><Icon name={s.icon} size={17} /></span>
      </span>
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
    grid-template-columns: repeat(4, minmax(8rem, 1fr));
    gap: 0;
    counter-reset: none;
  }
  .flow__step {
    position: relative;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    gap: var(--space-2);
    min-height: 4.25rem;
    padding: var(--space-2) var(--space-4);
    border-left: 1px solid var(--border);
  }
  .flow__step::after {
    content: '';
    position: absolute;
    right: -0.25rem;
    top: 50%;
    width: 0.5rem;
    height: 0.5rem;
    transform: translateY(-50%) rotate(45deg);
    border-top: 1px solid var(--border-strong);
    border-right: 1px solid var(--border-strong);
    background: var(--surface);
    z-index: 2;
  }
  .flow__step:last-child::after { display: none; }
  .flow__marker { display: flex; flex-direction: column; align-items: center; gap: 0.2rem; }
  .flow__index {
    color: var(--muted-foreground);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.57rem;
    font-weight: 800;
    letter-spacing: 0.08em;
  }
  .flow__icon {
    display: grid;
    place-items: center;
    width: 1.8rem;
    height: 1.8rem;
    border-radius: 50%;
    background: var(--accent-soft);
    color: var(--accent-hover);
  }
  .flow__title {
    margin: 0;
    font-weight: 700;
    font-size: 0.78rem;
    line-height: 1.2;
  }
  .flow__desc {
    margin: 0;
    margin-top: 0.18rem;
    font-size: 0.66rem;
    color: var(--muted-foreground);
    line-height: 1.45;
  }
  @media (max-width: 768px) {
    .flow {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      border-top: 1px solid var(--border);
    }
    .flow__step { border-bottom: 1px solid var(--border); }
    .flow__step::after { display: none; }
  }
</style>
