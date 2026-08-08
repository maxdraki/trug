<script lang="ts">
  import { untrack } from 'svelte';
  import type { Item } from '../lib/types';

  let {
    item,
    walkOrder,
    onSave,
    onRemove,
    onClose,
  }: {
    item: Item;
    walkOrder: string[];
    onSave: (id: string, fields: { note?: string | null; category?: string }) => Promise<void> | void;
    onRemove: (id: string) => void;
    onClose: () => void;
  } = $props();

  // Seed the editable fields once from the item; the sheet is re-mounted per
  // item (keyed by id in ListView), so we intentionally capture initial values.
  let note = $state(untrack(() => item.note ?? ''));
  let category = $state(untrack(() => item.category ?? 'Other'));
  let saving = $state(false);

  async function save() {
    saving = true;
    try {
      await onSave(item.id, { note: note.trim() || null, category });
      onClose();
    } finally {
      saving = false;
    }
  }
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && onClose()} />

<div
  class="backdrop"
  role="button"
  tabindex="-1"
  aria-label="Close"
  onclick={onClose}
  onkeydown={(e) => e.key === 'Escape' && onClose()}
></div>
<div class="sheet" role="dialog" aria-modal="true" aria-label="Edit {item.name}">
  <div class="grip" aria-hidden="true"></div>
  <h2>{item.name}</h2>

  <label>
    <span>Note</span>
    <input type="text" bind:value={note} placeholder="e.g. the big one" />
  </label>

  <label>
    <span>Category</span>
    <select bind:value={category}>
      {#each walkOrder as cat (cat)}
        <option value={cat}>{cat}</option>
      {/each}
    </select>
  </label>

  <div class="actions">
    <button class="danger" type="button" onclick={() => { onRemove(item.id); onClose(); }}>Remove</button>
    <div class="spacer"></div>
    <button class="ghost" type="button" onclick={onClose}>Cancel</button>
    <button class="primary" type="button" onclick={save} disabled={saving}>Save</button>
  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    /* Above the footer add-bar so it swallows pointer events while the modal is
       open — the footer input can no longer intercept clicks on the sheet. */
    z-index: 200;
    background: color-mix(in srgb, var(--shadow-tint) 45%, transparent);
    border: none;
  }
  .sheet {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 201;
    max-width: 640px;
    margin: 0 auto;
    /* Never taller than the viewport, and scroll internally so the action row
       stays reachable even on short screens / with the keyboard up. */
    max-height: calc(100svh - 24px);
    overflow-y: auto;
    padding: 8px 20px calc(24px + env(safe-area-inset-bottom));
    background: var(--ctp-base);
    border-radius: var(--radius) var(--radius) 0 0;
    border-top: var(--hairline);
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .grip {
    width: 32px;
    height: 3px;
    border-radius: 2px;
    background: var(--ctp-surface1);
    margin: 8px auto 0;
  }
  h2 {
    margin: 0;
    font-family: var(--font-display);
    font-size: 19px;
    font-weight: 500;
    letter-spacing: -0.01em;
    color: var(--ctp-text);
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  label span {
    font-size: 13px;
    color: var(--ctp-subtext0);
  }
  input,
  select {
    padding: 12px 14px;
    font-size: 16px;
    border-radius: var(--radius);
    border: var(--hairline);
    background: var(--ctp-mantle);
    color: var(--ctp-text);
  }
  input:focus,
  select:focus {
    outline: none;
    border-color: var(--accent);
  }
  .actions {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 4px;
  }
  .spacer {
    flex: 1 1 auto;
  }
  button {
    padding: 11px 18px;
    font-size: 15px;
    border-radius: var(--radius);
    border: none;
    cursor: pointer;
  }
  .primary {
    background: var(--accent);
    color: var(--ctp-crust);
    font-weight: 600;
  }
  .primary:disabled {
    opacity: 0.6;
  }
  .ghost {
    background: var(--ctp-surface0);
    color: var(--ctp-text);
  }
  .danger {
    background: none;
    color: var(--ctp-red);
  }
</style>
