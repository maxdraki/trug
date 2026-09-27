<script lang="ts">
  import type { Snippet } from 'svelte';
  import { DUR, EASE, rise } from '../lib/motion';

  let {
    variant = 'neutral',
    children,
  }: {
    /** 'accent' draws an accent left border (ring arrivals); 'undo' is the
        inverse snackbar that offers something back; 'neutral' for everything
        else the app has to say. */
    variant?: 'accent' | 'neutral' | 'undo';
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
  /* Undo, as Material's snackbar: the surface inverted, so it reads as the one
     thing on screen that is not part of the list — a light bar in the dark
     flavours, a dark one in Latte. The old undo notice was a base card with a
     hairline, the same stuff as the shelf, and at a glance it was one more row.
     No stripe and no hairline: the inversion is the edge. */
  .toast.undo {
    border: none;
    background: var(--ctp-text);
    color: var(--ctp-base);
  }
  /* The action. Its styles lived in each component that raised an undo, as two
     identical copies; they live with the notice now. The accent's inverse ink
     reads at 4.5:1 on the inverted ground (theme.css --inverse-ink-*), as
     Material's inverse primary does. Measured in lib/accentInk.test.ts. */
  .toast.undo :global(button) {
    flex: 0 0 auto;
    margin: -8px -8px -8px 0;
    padding: 8px 10px;
    background: none;
    border: none;
    border-radius: var(--radius);
    color: var(--accent-inverse-ink);
    font: inherit;
    font-weight: 600;
    font-size: 15px;
    cursor: pointer;
  }
  /* The app's focus ring is the accent, and on this inverted ground the accent
     all but vanishes: 1.0–1.5:1 in the dark flavours, under 2.7:1 in Latte,
     where a focus indicator needs 3:1. The tray moves focus onto Undo on
     purpose after a keyboard "forget", so that is exactly the moment the ring
     cannot be allowed to disappear. The inverse ink already reads here by
     construction. */
  .toast.undo :global(button:focus-visible) {
    outline-color: var(--accent-inverse-ink);
  }
</style>
