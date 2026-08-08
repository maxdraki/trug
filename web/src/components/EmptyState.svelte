<script lang="ts">
  import Logo from '../lib/Logo.svelte';

  let { message = "Shelf's empty" }: { message?: string } = $props();
</script>

<div class="empty">
  <span class="mark"><Logo size={44} /></span>
  <p>{message}</p>
  <p class="hint">add the first thing below</p>
</div>

<style>
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    padding: 88px 24px;
    text-align: center;
  }
  .mark {
    display: inline-flex;
    color: var(--ctp-overlay0);
    margin-bottom: 8px;
  }
  p {
    margin: 0;
    font-family: var(--font-display);
    font-size: 17px;
    font-weight: 500;
    letter-spacing: -0.01em;
    color: var(--ctp-subtext1);
  }
  .hint {
    font-family: inherit;
    font-size: 13px;
    font-weight: 400;
    color: var(--ctp-overlay0);
  }
</style>
