<script lang="ts">
  import { fly } from 'svelte/transition';
  import { flip } from 'svelte/animate';
  import type { Item } from '../lib/types';
  import type { Store } from '../lib/store.svelte';
  import AisleGroup from './AisleGroup.svelte';
  import ItemRow from './ItemRow.svelte';
  import ItemSheet from './ItemSheet.svelte';
  import EmptyState from './EmptyState.svelte';
  import Toast from './Toast.svelte';
  import Icon from './Icon.svelte';
  import { d, DUR, STAGGER, receiveItem, keyOf } from '../lib/motion';

  import type { DragController } from '../lib/drag.svelte';

  let {
    store,
    walkOrder,
    drag,
    onUpdate,
  }: {
    store: Store;
    walkOrder: string[];
    drag?: DragController;
    onUpdate: (id: string, fields: { note?: string | null; category?: string }) => Promise<void> | void;
  } = $props();

  let editing = $state<Item | null>(null);
  let undo = $state<{ items: Item[]; timer: ReturnType<typeof setTimeout> } | null>(null);
  // A single swipe-commit undo (delete or to-basket), mirroring the clear-checked
  // undo: a 5s window with an action that reverses the store mutation.
  let swipeUndo = $state<{ text: string; onUndo: () => void; timer: ReturnType<typeof setTimeout> } | null>(null);
  // True only while a "clear checked" batch is flying out, so the staggered
  // outro delay applies to a bulk clear but a single uncheck/delete gets 0.
  let clearing = $state(false);
  let clearingTimer: ReturnType<typeof setTimeout> | undefined;

  const empty = $derived(store.groups.length === 0 && store.checked.length === 0);

  function toggle(id: string) {
    store.toggle(id);
  }
  function remove(id: string) {
    store.remove(id);
  }
  function open(item: Item) {
    editing = item;
  }

  function clearUndo() {
    if (undo) clearTimeout(undo.timer);
    undo = null;
  }

  function clearChecked() {
    const snapshot = store.checked.slice();
    if (!snapshot.length) return;
    // Flag the batch so the outro reads a staggered delay, then release it once
    // the last row has flown out.
    clearing = true;
    clearTimeout(clearingTimer);
    clearingTimer = setTimeout(
      () => (clearing = false),
      d(DUR.fly) + snapshot.length * STAGGER + 50,
    );
    store.clearChecked();
    clearUndo();
    undo = {
      items: snapshot,
      timer: setTimeout(() => {
        undo = null;
      }, 5000),
    };
  }

  function undoClear() {
    if (!undo) return;
    for (const it of undo.items) store.add(it.name, it.note ?? undefined);
    clearUndo();
  }

  function showSwipeUndo(text: string, onUndo: () => void) {
    if (swipeUndo) clearTimeout(swipeUndo.timer);
    swipeUndo = { text, onUndo, timer: setTimeout(() => (swipeUndo = null), 5000) };
  }
  function runSwipeUndo() {
    if (!swipeUndo) return;
    clearTimeout(swipeUndo.timer);
    swipeUndo.onUndo();
    swipeUndo = null;
  }

  // Left-swipe: delete, with a 5s undo. Undo re-adds by name/note through the
  // normal add path — its category re-resolves via the tier-0 / catalog lookup
  // and it gets a fresh id, so this restores the item, not the exact prior row.
  function swipeDelete(item: Item) {
    const { name } = item;
    const note = item.note ?? undefined;
    store.remove(item.id);
    showSwipeUndo(`Deleted ${name}`, () => store.add(name, note));
  }
  // Right-swipe (active rows only): send to the basket via the same toggle the
  // check-off uses; undo toggles the (same id) row back to active.
  function swipeBasket(item: Item) {
    store.toggle(item.id);
    showSwipeUndo('In the basket', () => store.toggle(item.id));
  }
</script>

<div class="list">
  {#if empty}
    <EmptyState />
  {:else}
    <div class="shelf">
      {#each store.groups as group (group.category)}
        <AisleGroup
          category={group.category}
          items={group.items}
          pendingIds={store.pendingIds}
          {drag}
          onToggle={toggle}
          onRemove={remove}
          onOpen={open}
          onSwipeLeft={swipeDelete}
          onSwipeRight={swipeBasket}
        />
      {/each}
    </div>

    {#if store.checked.length}
      <section class="checked">
        <div class="checked-head">
          <h2>
            <span class="basket-ico"><Icon name="basket" size={18} stroke={1.75} /></span>
            in the basket
            <span class="count">{store.checked.length}</span>
          </h2>
          <button type="button" onclick={clearChecked}>Clear</button>
        </div>
        <div class="rows">
          {#each store.checked as item, i (item.id)}
            <div
              class="row-wrap"
              animate:flip={{ duration: d(DUR.flip) }}
              in:receiveItem={{ key: keyOf(item.name) }}
              out:fly={{ y: 8, duration: d(DUR.fly), delay: clearing ? d(i * STAGGER) : 0 }}
            >
              <ItemRow {item} onToggle={toggle} onRemove={remove} onOpen={open} onSwipeLeft={swipeDelete} pending={store.pendingIds.has(item.id)} />
            </div>
          {/each}
        </div>
      </section>
    {/if}
  {/if}
</div>

{#if swipeUndo}
  <Toast variant="neutral">
    <span>{swipeUndo.text}</span>
    <button class="undo-btn" type="button" onclick={runSwipeUndo}>Undo</button>
  </Toast>
{:else if undo}
  <Toast variant="neutral">
    <span>Cleared {undo.items.length} item{undo.items.length === 1 ? '' : 's'}</span>
    <button class="undo-btn" type="button" onclick={undoClear}>Undo</button>
  </Toast>
{/if}

{#if editing}
  {#key editing.id}
    <ItemSheet
      item={editing}
      {walkOrder}
      onSave={onUpdate}
      onRemove={remove}
      onClose={() => (editing = null)}
    />
  {/key}
{/if}

<style>
  .list {
    padding: 4px 0 16px;
  }
  /* One uninterrupted base surface for the whole active list. Radius survives
     here (the column-level container), not on the individual aisles — those
     dissolve into a continuous shelf, divided only by hairlines. */
  .shelf {
    background: var(--ctp-base);
    border: var(--hairline);
    border-radius: var(--radius);
    box-shadow: var(--shadow-1);
    overflow: hidden;
  }
  /* Hairline shelf-edge between adjacent aisles (each aisle is an <section>). */
  .shelf > :global(section + section) {
    border-top: var(--hairline);
  }
  /* The basket: the one signature moment. A recessed mantle-on-mantle card —
     hairline-edged, its icon the only accent in the list body. */
  .checked {
    margin-top: 16px;
    border: var(--hairline);
    border-radius: var(--radius);
    overflow: hidden;
  }
  .checked-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px 8px;
    border-bottom: var(--hairline);
  }
  .checked-head h2 {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    font-family: var(--font-display);
    font-size: 13px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    /* subtext1 clears 4.5:1 on base in both Latte and Mocha. */
    color: var(--ctp-subtext1);
  }
  .basket-ico {
    display: inline-flex;
    color: var(--accent);
  }
  .count {
    font-family: var(--font-display);
    font-variant-numeric: tabular-nums;
    color: var(--ctp-subtext0);
  }
  .checked-head button {
    background: none;
    border: none;
    border-radius: var(--radius);
    padding: 2px 6px;
    color: var(--ctp-subtext1);
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: color 120ms ease;
  }
  .checked-head button:hover {
    color: var(--ctp-text);
  }
  .rows {
    display: flex;
    flex-direction: column;
  }
  .rows > :global(.row-wrap + .row-wrap) {
    border-top: var(--hairline);
  }
  .undo-btn {
    flex: 0 0 auto;
    background: none;
    border: none;
    color: var(--accent);
    font-weight: 600;
    font-size: 15px;
    cursor: pointer;
  }
</style>
