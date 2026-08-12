<script lang="ts">
  import type { Snippet } from 'svelte';
  import { DUR, EASE, rise } from '../lib/motion';

  let {
    variant = 'neutral',
    children,
  }: {
    /** 'accent' draws an accent left border (e.g. ring arrivals); 'neutral' for
        undo and other system toasts. */
    variant?: 'accent' | 'neutral';
    children: Snippet;
  } = $props();
</script>

<!-- In slower than out: it arrives with something to say, and leaves once it
     has been read. -->
<div
  class="toast {variant}"
  role="status"
  in:rise={{ y: 12, duration: DUR.toastIn, easing: EASE.enter }}
  out:rise={{ y: 12, duration: DUR.toastOut, easing: EASE.exit }}
>
  {@render children()}
</div>

<style>
  /* Docked just above the footer add-bar (whose measured height is exposed as
     --footer-h on .app), so a toast never covers interactive rows mid-list. */
  .toast {
    position: fixed;
    left: 16px;
    right: 16px;
    bottom: calc(var(--footer-h, 88px) + 10px);
    max-width: 480px;
    margin: 0 auto;
    z-index: 60;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 16px;
    font-size: 14px;
    border-radius: var(--radius);
    border: var(--hairline);
    border-left: 2px solid var(--ctp-overlay0);
    background: var(--ctp-base);
    color: var(--ctp-text);
    box-shadow: var(--shadow-2);
  }
  .toast.accent {
    border-left-color: var(--accent);
    justify-content: center;
    text-align: center;
    font-weight: 500;
  }
</style>
