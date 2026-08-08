<script lang="ts">
  import { Spring } from 'svelte/motion';
  import { flip } from 'svelte/animate';
  import type { CatalogEntry } from '../lib/types';
  import { d, DUR, PRESS_SPRING, sendItem, keyOf, reducedMotion } from '../lib/motion';
  import { resolveIcon } from '../lib/resolveIcon';
  import Icon from './Icon.svelte';

  let {
    top,
    activeNames = new Set<string>(),
    onPick,
  }: {
    /** Fetches the most-added catalogue entries (typically `api.top`). */
    top: (n: number) => Promise<CatalogEntry[]>;
    /** Normalised names already on the list, hidden from the grid. */
    activeNames?: Set<string>;
    onPick: (displayName: string) => void;
  } = $props();

  let entries = $state<CatalogEntry[]>([]);

  $effect(() => {
    let cancelled = false;
    top(24)
      .then((res) => {
        if (!cancelled) entries = res;
      })
      .catch(() => {
        if (!cancelled) entries = [];
      });
    return () => {
      cancelled = true;
    };
  });

  const visible = $derived(entries.filter((e) => !activeNames.has(e.name_norm)));

  // Press-scale spring: the pressed cell dips and springs back to rest.
  const press = new Spring(1, PRESS_SPRING);
  let pressed = $state<string | null>(null);

  function pressDown(key: string) {
    if (reducedMotion()) return;
    pressed = key;
    press.set(0.92, { instant: true });
  }
  async function pressUp() {
    if (pressed === null) return;
    const key = pressed;
    // Keep the transform bound to this cell while the spring settles back to
    // rest — clearing `pressed` immediately would unbind mid-flight and snap.
    await press.set(1);
    if (pressed === key) pressed = null;
  }
</script>

{#if visible.length}
  <h2 class="tray-head">Frequently added</h2>
  <div class="tray">
    {#each visible as e (e.name_norm)}
      {@const icon = resolveIcon(e.icon, e.display_name)}
      <button
        class="cell"
        type="button"
        animate:flip={{ duration: d(DUR.flip) }}
        out:sendItem={{ key: keyOf(e.display_name) }}
        style={pressed === e.name_norm ? `transform: scale(${press.current})` : ''}
        onpointerdown={() => pressDown(e.name_norm)}
        onpointerup={pressUp}
        onpointerleave={pressUp}
        onpointercancel={pressUp}
        onclick={() => onPick(e.display_name)}
      >
        {#if icon.kind === 'line'}
          <span class="ico" aria-hidden="true"><Icon name={icon.slug} size={18} stroke={1.75} /></span>
        {:else if icon.kind === 'glyph'}
          <span class="ico glyph" aria-hidden="true">{icon.glyph}</span>
        {:else}
          <span class="ico mono" aria-hidden="true">{(e.display_name.trim()[0] ?? '?').toUpperCase()}</span>
        {/if}
        <span class="label">{e.display_name}</span>
      </button>
    {/each}
  </div>
{/if}

<style>
  .tray-head {
    margin: 0;
    padding: 4px 4px 8px;
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--ctp-subtext0);
  }
  /* A slim tray, not a shelf card: one horizontally scrolling row of quiet
     icon-first tiles. */
  .tray {
    display: flex;
    gap: 8px;
    padding: 0 0 12px;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .tray::-webkit-scrollbar {
    display: none;
  }
  .cell {
    flex: 0 0 auto;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    width: 76px;
    padding: 10px 6px 8px;
    border: var(--hairline);
    border-radius: var(--radius);
    background: var(--ctp-base);
    color: var(--ctp-subtext0);
    cursor: pointer;
    transition: color 120ms ease, border-color 120ms ease;
  }
  .cell:hover {
    border-color: var(--ctp-surface1);
    color: var(--ctp-text);
  }
  /* Recents chips are "things you'll likely need" — same accent family as the
     active-row and shelf-label icons. Monogram fallbacks (.ico.mono) keep
     lavender. */
  .ico {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 20px;
    color: var(--accent);
  }
  .ico.glyph {
    font-size: 18px;
    line-height: 1;
  }
  /* Monograms get the same lavender-tinted chip square as list rows, so an
     entry without a resolvable icon reads as a chip, not a bare letter. */
  .ico.mono {
    width: 24px;
    height: 24px;
    border-radius: 7px;
    background: color-mix(in srgb, var(--ctp-lavender) 16%, var(--ctp-base));
    font-family: var(--font-display);
    font-size: 13px;
    font-weight: 500;
    color: var(--ctp-lavender);
  }
  .label {
    font-size: 11px;
    text-align: center;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }
</style>
