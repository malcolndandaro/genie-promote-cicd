<script lang="ts">
  interface Props {
    height?: string;
    width?: string;
  }
  let { height = '2.5rem', width = '100%' }: Props = $props();
</script>

<span class="skeleton" style:height style:width aria-hidden="true"></span>

<style>
  .skeleton {
    display: block;
    border-radius: var(--radius-sm);
    background: linear-gradient(
      90deg,
      var(--skeleton-base) 25%,
      var(--skeleton-highlight) 37%,
      var(--skeleton-base) 63%
    );
    background-size: 400% 100%;
    animation: shimmer 1.4s ease infinite;
  }
  @keyframes shimmer {
    0% {
      background-position: 100% 0;
    }
    100% {
      background-position: 0 0;
    }
  }
</style>
