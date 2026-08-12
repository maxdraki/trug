<script lang="ts">
  import type { CatalogEntry } from '../lib/types';
  import { ICONS } from '../lib/icons';
  import Icon from './Icon.svelte';

  let {
    search,
    onAdd,
    onQueryChange,
  }: {
    search: (q: string) => Promise<CatalogEntry[]>;
    onAdd: (name: string) => void;
    onQueryChange?: (q: string) => void;
  } = $props();

  let query = $state('');
  let suggestions = $state<CatalogEntry[]>([]);
  let timer: ReturnType<typeof setTimeout> | undefined;

  function onInput(e: Event) {
    query = (e.target as HTMLInputElement).value;
    onQueryChange?.(query);
    clearTimeout(timer);
    const q = query.trim();
    if (!q) {
      suggestions = [];
      return;
    }
    timer = setTimeout(async () => {
      try {
        const results = await search(q);
        // Ignore stale responses if the query moved on.
        if (query.trim() === q) suggestions = results;
      } catch {
        suggestions = [];
      }
    }, 120);
  }

  function commit(name: string) {
    const value = name.trim();
    if (!value) return;
    clearTimeout(timer);
    onAdd(value);
    query = '';
    suggestions = [];
    onQueryChange?.('');
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      const top = suggestions[0];
      commit(top ? top.display_name : query);
    } else if (e.key === 'Escape') {
      suggestions = [];
    }
  }
</script>

<div class="addbar">
  {#if suggestions.length}
    <ul class="dropdown" role="listbox">
      {#each suggestions as s (s.name_norm)}
        <li>
          <button
            type="button"
            role="option"
            aria-selected="false"
            onclick={() => commit(s.display_name)}
          >
            {#if s.icon && s.icon in ICONS}
              <span class="ico" aria-hidden="true"><Icon name={s.icon} size={17} stroke={1.75} /></span>
            {:else if s.icon && !/^[\x20-\x7e]+$/.test(s.icon)}
              <span class="ico" aria-hidden="true">{s.icon}</span>
            {/if}
            <span class="label">{s.display_name}</span>
          </button>
        </li>
      {/each}
    </ul>
  {/if}
  <input
    type="text"
    value={query}
    oninput={onInput}
    onkeydown={onKeydown}
    placeholder="Add an item…"
    aria-label="Add an item"
    autocapitalize="off"
    autocomplete="off"
  />
</div>

<style>
  /* Floating bar: a base-surface object lifted off the mantle column by a
     hairline and a whisper of shadow. Accent is spent here — the add action. */
  .addbar {
    position: relative;
    padding: 8px 12px calc(12px + env(safe-area-inset-bottom));
  }
  /* Accent CTA at rest: a whisper of the accent washed through the pill (bg +
     border tint + placeholder), so the add action reads as the one primary
     surface without shouting. Focus keeps the stronger full-accent border. */
  input {
    width: 100%;
    box-sizing: border-box;
    padding: 13px 16px;
    font-size: 16px;
    border-radius: var(--radius);
    border: var(--hairline);
    border-color: color-mix(in srgb, var(--accent) 30%, var(--ctp-surface0));
    background: color-mix(in srgb, var(--accent) 10%, var(--ctp-base));
    color: var(--ctp-text);
    box-shadow: var(--shadow-2);
    transition: border-color 120ms ease;
  }
  input::placeholder {
    color: color-mix(in srgb, var(--accent) 40%, var(--ctp-subtext0));
  }
  input:focus {
    outline: none;
    border-color: var(--accent);
  }
  .dropdown {
    position: absolute;
    left: 12px;
    right: 12px;
    bottom: calc(100% - 2px);
    margin: 0 0 8px;
    padding: 4px;
    list-style: none;
    /* dvh (not vh) so this shrinks with the keyboard-reduced viewport instead
       of a fixed fraction of the full device height — otherwise the list can
       ask for more room above the input than the keyboard has left it. */
    max-height: 45dvh;
    overflow-y: auto;
    background: var(--ctp-base);
    border: var(--hairline);
    border-radius: var(--radius);
    box-shadow: var(--shadow-2);
  }
  .dropdown li + li {
    border-top: var(--hairline);
  }
  .dropdown button {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 11px 12px;
    border: none;
    border-radius: calc(var(--radius) - 2px);
    background: none;
    color: var(--ctp-text);
    font-size: 15px;
    text-align: left;
    cursor: pointer;
  }
  .dropdown button:hover {
    background: var(--ctp-mantle);
  }
  .ico {
    display: inline-flex;
    color: var(--ctp-subtext1);
    font-size: 18px;
    line-height: 1;
  }
</style>
