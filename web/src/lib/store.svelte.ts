import { uuidv7 } from 'uuidv7';
import { ApiError } from './api';
import { tidyName, normaliseName } from './text';
import { readSnapshot, writeSnapshot } from './snapshot';
import type { api as realApi } from './api';
import type { OpQueue } from './opqueue';
import type { Item, Op } from './types';

export interface Group {
  category: string;
  items: Item[];
}

/**
 * Sort key for dropping a row between two neighbours (their current keys, or
 * undefined at an edge). First position → below the current min; last → above
 * the current max; empty target → a fresh epoch-seconds key (matches the
 * server's default so a recategorised row lands at the end of its new aisle).
 */
export function midpointSortKey(prev: number | undefined, next: number | undefined): number {
  if (prev !== undefined && next !== undefined) return (prev + next) / 2;
  if (next !== undefined) return next - 1;
  if (prev !== undefined) return prev + 1;
  return Date.now() / 1000;
}

/** Stable order within an aisle: sort_key, then created_at, then id. */
function bySortKey(a: Item, b: Item): number {
  if (a.sort_key !== b.sort_key) return a.sort_key - b.sort_key;
  if (a.created_at !== b.created_at) return a.created_at < b.created_at ? -1 : 1;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

export interface StoreDeps {
  api: typeof realApi;
  queue: OpQueue;
  /** Category display order for the walk-order groups. */
  walkOrder: string[];
  /**
   * Sink for failures the shopper has to be told about, because the change they
   * just made has been undone. Only storage faults reach here: a network
   * failure is normal service (the op is durably queued and the offline banner
   * explains it), but if the op could not be SAVED there is nothing left to
   * sync and the shelf has silently rolled back under their thumb.
   */
  onError?: (message: string) => void;
}

export interface Store {
  readonly groups: Group[];
  readonly checked: Item[];
  readonly online: boolean;
  readonly pendingIds: Set<string>;
  /** Epoch ms of the snapshot the shelf booted from, or null if none. */
  readonly snapshotAt: number | null;
  /** True once a `refresh()` has reconciled with the server this session. */
  readonly refreshed: boolean;
  refresh(): Promise<void>;
  /** Flush any queued ops, then refetch — used on reconnect / regained network. */
  retry(): Promise<void>;
  add(name: string, note?: string): Promise<void>;
  toggle(id: string): Promise<void>;
  remove(id: string): Promise<void>;
  /**
   * Persist a drag: set an item's sort_key (and optionally recategorise it),
   * optimistically applied and rolled back if the PATCH fails.
   */
  reorder(id: string, sortKey: number, category?: string): Promise<void>;
  clearChecked(): Promise<void>;
  applyEvent(name: string, data: any): void;
  /** Teardown hook; retained for API stability (currently a no-op). */
  dispose(): void;
}

/**
 * Optimistic list store layered over the durable op queue.
 *
 * State is held in runes so Svelte components track it. Arrays/Sets are
 * reassigned immutably on every mutation, which keeps the store equally
 * correct if runes are ever swapped for plain reactive fields.
 */
export function createStore(deps: StoreDeps): Store {
  const { api, queue, walkOrder, onError } = deps;

  let active = $state<Item[]>([]);
  let checked = $state<Item[]>([]);
  let online = $state(true);
  let pending = $state<Set<string>>(new Set());
  // Snapshot age the shelf booted from (for the quiet "as of …" hint), and
  // whether the server has been reached this session (which retires the hint).
  let snapshotAt = $state<number | null>(null);
  let refreshed = $state(false);

  // --- snapshot persistence (last-known list, readable with no signal) ---

  // Debounced writes: a burst of mutations (a drag, a rapid check-through)
  // collapses into a single localStorage write ~500ms after the list settles.
  let persistTimer: ReturnType<typeof setTimeout> | undefined;

  function persistNow(): void {
    clearTimeout(persistTimer);
    persistTimer = undefined;
    writeSnapshot([...active, ...checked]);
  }

  function schedulePersist(): void {
    clearTimeout(persistTimer);
    persistTimer = setTimeout(persistNow, 500);
  }

  // --- state helpers (immutable reassignment) ---

  function place(it: Item): void {
    active = active.filter((x) => x.id !== it.id);
    checked = checked.filter((x) => x.id !== it.id);
    if (it.status === 'checked') checked = [...checked, it];
    else active = [...active, it];
    schedulePersist();
  }

  function removeId(id: string): void {
    active = active.filter((x) => x.id !== id);
    checked = checked.filter((x) => x.id !== id);
    schedulePersist();
  }

  function find(id: string): Item | undefined {
    return active.find((x) => x.id === id) ?? checked.find((x) => x.id === id);
  }

  function addPending(id: string): void {
    const next = new Set(pending);
    next.add(id);
    pending = next;
  }

  function delPending(id: string): void {
    if (!pending.has(id)) return;
    const next = new Set(pending);
    next.delete(id);
    pending = next;
  }

  function optimisticRow(id: string, name: string, note: string | null, createdAt: string): Item {
    return {
      id,
      name,
      note,
      icon: null,
      category: 'Other',
      status: 'active',
      source: null,
      added_by: null,
      created_at: createdAt,
      checked_at: null,
      sort_key: Date.now() / 1000,
    };
  }

  // --- derived groups ---

  function computeGroups(): Group[] {
    // Bucket each active item under a single category key. Null and unknown
    // categories coalesce into 'Other' so `groups` never contains two entries
    // with the same key (components key their lists by category). Fresh adds
    // land directly in their aisle — the row's own arrival animation and brief
    // "new" glint signal that the add landed, so there is no separate pin group.
    const known = new Set(walkOrder);
    const keyFor = (i: Item): string => {
      const category = i.category ?? 'Other';
      return known.has(category) ? category : 'Other';
    };

    const buckets = new Map<string, Item[]>();
    for (const i of active) {
      const key = keyFor(i);
      let arr = buckets.get(key);
      if (!arr) buckets.set(key, (arr = []));
      arr.push(i);
    }

    const groups: Group[] = [];
    for (const cat of walkOrder) {
      const items = buckets.get(cat);
      if (items?.length) groups.push({ category: cat, items: items.sort(bySortKey) });
      buckets.delete(cat);
    }
    // When 'Other' is not part of the walk order, unknown/null items still land
    // in a single trailing 'Other' bucket handled here.
    const leftover = buckets.get('Other');
    if (leftover?.length) groups.push({ category: 'Other', items: leftover.sort(bySortKey) });
    return groups;
  }

  // --- reconciliation ---

  /**
   * The id of the row an `add` op actually put on screen. Usually the op's own
   * fresh uuid, but when `add()` found a local match it rendered THAT row and
   * marked its id pending, so the op carries the match's id alongside.
   */
  function localAddId(op: Op): string {
    return op.pendingId ?? op.item!.id;
  }

  async function reconcileAdd(op: Op, returned: Item): Promise<void> {
    const localId = localAddId(op);
    const sentId = op.item!.id;
    if (returned.id === localId) {
      place(returned);
      delPending(localId);
      return;
    }
    if (sentId !== localId) {
      // We rendered a LOCAL match and the server disagreed with it — folding
      // onto a third row, or creating a fresh one. Reconciliation below copes,
      // but the row we quietly moved on screen is one the server still has, so
      // the shelf is briefly wrong until the next refresh. Not silent.
      console.warn(
        `[trug] add for "${op.item!.name}" did not resolve onto the row shown` +
          ` (rendered ${localId}, sent ${sentId}, server returned ${returned.id})`,
      );
    }

    // Dedup swap: the server folded this add onto a row we had never seen (its
    // matching is broader than ours — plurals via the catalogue). Anything still
    // queued against the optimistic id — the check-off made in the aisle, a
    // delete — would name an item the server has never heard of, 404, and be
    // dropped: the item you put in the trolley silently comes back unchecked.
    // So repoint those ops at the id we were given. Ops already sent keep their
    // 404 drop; only the stale ids are fixed.
    //
    // Repoint BEFORE migrating the visible state, so the outcome is known
    // before anything is half-moved. The rewrite is IndexedDB work and can fail
    // on its own terms (quota, private browsing, an aborted transaction) — that
    // is a storage failure, not a network one, so it must not touch `online`:
    // claiming "you're offline, changes will sync" would be wrong about the
    // network AND wrong about the change having been saved. The add itself
    // succeeded, so we swallow the error (the queue dequeues the add) and log.
    let rewritten: Op[] = [];
    try {
      rewritten = await queue.rewriteItemId(localId, returned.id);
    } catch (err) {
      console.error(
        `[trug] could not repoint queued ops from ${localId} to ${returned.id};` +
          ' edits made offline against this item may be dropped on the next sync',
        err,
      );
    }
    // Migrate the view regardless. The server row is authoritative and is about
    // to arrive over SSE: leaving the optimistic row in place would let that
    // echo land as a SECOND copy of the same item, and strand the row at
    // "queued" forever. A stale-but-single row is healed by the next refresh.
    removeId(localId);
    if (sentId !== localId) removeId(sentId);
    place(returned);
    delPending(localId);
    delPending(sentId);
    delPending(returned.id);

    if (rewritten.length === 0) return;
    // Those ops have not flushed yet, so re-apply them over the server row (the
    // check stays visibly ticked even if the network drops again here) and keep
    // the id pending, which also suppresses the SSE echo of our own work.
    const ids = new Set(pending);
    for (const op of rewritten) applyOptimistic(op, ids);
    pending = ids;
  }

  // --- op execution (used by every flush) ---

  async function exec(op: Op): Promise<void> {
    // The `try` covers the REQUEST and nothing else. It used to wrap the local
    // bookkeeping below as well, so a throw from reconciliation — a malformed
    // payload, a bug in `place` — flipped `online` to false and raised "you're
    // offline, changes will sync" immediately after a request that had
    // demonstrably reached the server and succeeded.
    let applyLocally: () => void | Promise<void> = () => {};
    try {
      switch (op.kind) {
        case 'add': {
          const { item } = await api.addItem(op.item!);
          applyLocally = () => reconcileAdd(op, item);
          break;
        }
        case 'check':
        case 'uncheck': {
          const item = await api.setStatus(op.itemId!, op.kind === 'check' ? 'checked' : 'active');
          applyLocally = () => {
            place(item);
            delPending(op.itemId!);
          };
          break;
        }
        case 'delete': {
          await api.remove(op.itemId!);
          applyLocally = () => {
            removeId(op.itemId!);
            delPending(op.itemId!);
          };
          break;
        }
        case 'clear': {
          await api.clearChecked();
          applyLocally = () => {
            checked = [];
            schedulePersist();
          };
          break;
        }
      }
    } catch (err) {
      if (err instanceof ApiError) {
        // 404/409 => the queue drops the op; release its pending marker too.
        if (err.status === 404 || err.status === 409) {
          if (op.itemId) delPending(op.itemId);
          if (op.item) {
            delPending(op.item.id);
            delPending(localAddId(op));
          }
        }
      } else {
        online = false;
      }
      throw err;
    }

    online = true;
    try {
      await applyLocally();
    } catch (err) {
      // The server has already done the work, so the op is finished and must
      // NOT be replayed: swallow, but never quietly. The shelf may now be out
      // of step with the server until the next refresh — that is a bug to be
      // read in the console, not an offline state to be shown as a banner.
      console.error(
        `[trug] ${op.kind} for item ${op.itemId ?? op.item?.id ?? '(none)'} succeeded, but` +
          ' updating the local list failed; the shelf may be stale until the next refresh',
        err,
      );
    }
  }

  /**
   * Flush the queue to completion. Because a coalesced flush only sees the
   * in-flight run's snapshot, re-check pending() and flush again while we are
   * making progress and still online.
   */
  async function drain(): Promise<void> {
    for (;;) {
      const before = (await queue.pending()).length;
      if (before === 0) return;
      await queue.flush(exec);
      if (!online) return;
      const after = (await queue.pending()).length;
      if (after === 0 || after >= before) return;
    }
  }

  /**
   * The shared tail of every mutation: durably queue the op, then try to send
   * it. The optimistic change is ALREADY on screen when this is called, so a
   * rejected `enqueue` — quota, Safari private browsing, a blocked or corrupt
   * database — has to undo it: otherwise the row sits there wearing a "queued"
   * chip that is a lie (nothing is queued, nothing will ever be sent) and
   * quietly disappears at the next refresh. `clearChecked` was the worst of
   * these: the entire basket vanished locally with no `clear` op anywhere.
   *
   * Nothing is rethrown. Every caller is a UI event handler whose promise is
   * floated (ListView's row taps, AppShell's add-bar), so throwing would only
   * produce an unhandled rejection — the failure is handled here instead:
   * rolled back, logged, and told to the shopper through `onError`.
   */
  async function commit(op: Op, rollback: () => void, message: string): Promise<void> {
    try {
      await queue.enqueue(op);
    } catch (err) {
      rollback();
      console.error(
        `[trug] could not save the ${op.kind} for item ${op.itemId ?? op.item?.id ?? '(none)'}` +
          ' to the offline queue; the change has been undone',
        err,
      );
      onError?.(message);
      return;
    }
    try {
      await drain();
    } catch (err) {
      // The op is safely stored, so this is only a failed *attempt* to send:
      // the next drain (reconnect, SSE, the periodic retry) picks it up again.
      console.error(`[trug] could not sync after ${op.kind}; it stays queued`, err);
    }
  }

  // --- public mutations ---

  async function add(name: string, note?: string): Promise<void> {
    const id = uuidv7();
    // Mirror the server's tidy_name so the optimistic row already reads as it
    // will once reconciled — no case-flash when the server row lands.
    const display = tidyName(name);

    // Does this name already exist here? The server dedups on a normalised name
    // — an active match is returned untouched, a checked one is REACTIVATED in
    // place, keeping its own id, aisle and icon. Fabricating an optimistic row
    // anyway meant the name appeared twice (once struck through in the basket,
    // once fresh in `Other`), and then the reply destroyed that row and inserted
    // the real one somewhere else: three overlapping height changes for one
    // logical move, which is what read as the whole list jittering.
    //
    // So when we can see the match locally, move THAT row instead. Same id, so
    // the keyed each animates one row; real category, so no `Other` detour; and
    // because the id we mark pending is the one the server will echo over SSE,
    // the duplicate broadcast is suppressed too.
    //
    // The op still carries a fresh id. That is deliberate: the server's
    // id-idempotency check returns an existing row UNCHANGED, so reusing the
    // match's id would leave it checked. An unmatched fresh id falls through to
    // the reactivate branch, which is what bumps the catalog for "buy it again".
    const existing = [...active, ...checked].find((i) => normaliseName(i.name) === normaliseName(display));
    const optimisticId = existing ? existing.id : id;
    if (existing) {
      place({ ...existing, status: 'active', checked_at: null });
    } else {
      place(optimisticRow(id, display, note ?? null, new Date().toISOString()));
    }
    addPending(optimisticId);
    // `pendingId` records which row is on screen for this op — see its doc on
    // `Op` in types.ts. Stored with the op so a reload still knows.
    const op: Op = {
      opId: uuidv7(),
      kind: 'add',
      item: { id, name: display, ...(note != null ? { note } : {}) },
      pendingId: optimisticId,
      ts: new Date().toISOString(),
    };
    await commit(
      op,
      () => {
        // Undo exactly what was applied: a matched row goes back the way it
        // was (struck through in the basket, if that is where it came from);
        // a fabricated row goes away entirely.
        if (existing) place(existing);
        else removeId(id);
        delPending(optimisticId);
      },
      `Couldn't save “${display}” — it hasn't been added`,
    );
  }

  async function toggle(id: string): Promise<void> {
    const cur = find(id);
    if (!cur) return;
    const checking = cur.status === 'active';
    place({
      ...cur,
      status: checking ? 'checked' : 'active',
      checked_at: checking ? new Date().toISOString() : null,
    });
    addPending(id);
    await commit(
      {
        opId: uuidv7(),
        kind: checking ? 'check' : 'uncheck',
        itemId: id,
        ts: new Date().toISOString(),
      },
      () => {
        place(cur);
        delPending(id);
      },
      `Couldn't save that — “${cur.name}” is back as it was`,
    );
  }

  async function remove(id: string): Promise<void> {
    // Captured for the rollback. Undefined only if the row has already gone
    // (an SSE removal racing the tap); the delete is still queued either way,
    // since the server may well still have the item.
    const prev = find(id);
    removeId(id);
    addPending(id);
    await commit(
      { opId: uuidv7(), kind: 'delete', itemId: id, ts: new Date().toISOString() },
      () => {
        if (prev) place(prev);
        delPending(id);
      },
      `Couldn't save that — ${prev ? `“${prev.name}” is` : 'the item is'} still on the list`,
    );
  }

  async function reorder(id: string, sortKey: number, category?: string): Promise<void> {
    const prev = find(id);
    if (!prev) return;
    // Optimistically move the row; groups re-sort by sort_key so it settles
    // immediately (FLIP-animated by the view). Reorder is a live-only mutation:
    // it goes direct to the API rather than through the durable op queue, so it
    // needs the network — offline reorder is unsupported this round.
    place({ ...prev, sort_key: sortKey, ...(category != null ? { category } : {}) });
    try {
      const updated = await api.update(id, {
        sort_key: sortKey,
        ...(category != null ? { category } : {}),
      });
      online = true;
      place(updated);
    } catch (err) {
      place(prev); // rollback to the pre-drag position/category
      if (!(err instanceof ApiError)) online = false;
      throw err;
    }
  }

  async function clearChecked(): Promise<void> {
    const prev = checked;
    checked = [];
    schedulePersist();
    await commit(
      { opId: uuidv7(), kind: 'clear', ts: new Date().toISOString() },
      () => {
        checked = prev;
        schedulePersist();
      },
      "Couldn't clear the basket — nothing was cleared",
    );
  }

  // --- refresh: server wins, then re-apply queued ops on top ---

  function applyOptimistic(op: Op, ids: Set<string>): void {
    switch (op.kind) {
      case 'add': {
        // Re-apply against the row this op put on screen, not the id it sends:
        // when the op matched an existing row, that row is already here and
        // fabricating a second one under the op's fresh uuid would duplicate it.
        const id = localAddId(op);
        const { name, note } = op.item!;
        if (!find(id)) place(optimisticRow(id, name, note ?? null, op.ts));
        ids.add(id);
        break;
      }
      case 'check':
      case 'uncheck': {
        const cur = find(op.itemId!);
        if (cur) {
          const checking = op.kind === 'check';
          place({
            ...cur,
            status: checking ? 'checked' : 'active',
            checked_at: checking ? op.ts : null,
          });
        }
        ids.add(op.itemId!);
        break;
      }
      case 'delete':
        removeId(op.itemId!);
        ids.add(op.itemId!);
        break;
      case 'clear':
        checked = [];
        schedulePersist();
        break;
    }
  }

  async function refresh(): Promise<void> {
    let res;
    try {
      res = await api.list();
    } catch (err) {
      // A network failure (not an ApiError with a real status) means we're
      // offline: flip the flag so the banner shows on a cold offline boot, where
      // this failing refresh is the only network activity and navigator.onLine
      // can't always be trusted. The hydrated snapshot stays on screen.
      if (err instanceof ApiError) {
        // The server answered, and refused. `online` stays true — correctly, the
        // network is fine — so NOTHING on screen changes: the shelf keeps
        // showing a list that may be minutes or hours stale as though it were
        // current. There is no honest UI for "the server is broken" here, but
        // there must at least be a trace.
        console.error(
          `[trug] refresh failed: server answered ${err.status}.` +
            ' The list on screen may be stale.',
          err,
        );
      } else {
        online = false;
      }
      throw err;
    }
    online = true;

    const flat: Item[] = [];
    for (const cat of Object.keys(res.active)) flat.push(...res.active[cat]);
    active = flat;
    checked = res.checked;

    const ops = await queue.pending();
    const ids = new Set<string>();
    for (const op of ops) applyOptimistic(op, ids);
    pending = ids;

    // Server has been reached: retire the stale hint and persist the reconciled
    // list immediately (not debounced) so the freshest state survives a kill.
    refreshed = true;
    persistNow();
  }

  /**
   * Drain the durable queue and then refetch. Draining first lets pending
   * mutations land server-side before the refresh snapshot is taken, so
   * `refresh()` sees a consistent list and clears the reconciled pending ids.
   */
  async function retry(): Promise<void> {
    await drain();
    await refresh();
  }

  // --- SSE ingestion ---

  function applyEvent(name: string, data: any): void {
    switch (name) {
      case 'item_added':
      case 'item_updated': {
        const it = data as Item;
        if (pending.has(it.id)) return; // ignore echoes of our own pending ops
        place(it);
        break;
      }
      case 'item_removed':
        removeId((data as { id: string }).id);
        break;
      case 'list_cleared':
        checked = [];
        schedulePersist();
        break;
    }
  }

  // --- boot hydration: last-known snapshot renders before any network ---

  // Synchronously seed state from the persisted snapshot so the shelf paints
  // instantly on a cold, offline start — BEFORE App's onMount fires refresh().
  // refresh() then reconciles (server wins) when the network returns. Callers
  // gate whether a snapshot exists (it's cleared on sign-out / confirmed
  // auth-loss), so hydrating unconditionally here never reveals a stale list to
  // a signed-out user — there simply is no snapshot to load.
  const snap = readSnapshot();
  if (snap) {
    active = snap.items.filter((i) => i.status !== 'checked');
    checked = snap.items.filter((i) => i.status === 'checked');
    snapshotAt = snap.savedAt;
  }

  // Re-apply any queued offline ops on top of the hydrated snapshot. The queue
  // is IndexedDB-backed (async), so this settles a tick after first paint, but
  // it runs at boot — not only after refresh() — so pending edits (an offline
  // add, a check-off) show on the shelf before any network round-trip.
  void (async () => {
    const ops = await queue.pending();
    if (ops.length === 0) return;
    const ids = new Set<string>(pending);
    for (const op of ops) applyOptimistic(op, ids);
    pending = ids;
  })().catch((err) => {
    // Reading the queue failed at boot. The shelf still paints, from the
    // snapshot — but a cold start after an offline shop now shows the PRE-shop
    // list as though nothing had happened, with no "queued" chips. The ops are
    // still on disk and a later drain will send them, so this is a display
    // fault, not data loss; it is not something to shout at the shopper about
    // mid-boot, but it must not be invisible either.
    console.error(
      '[trug] could not re-apply queued offline edits at boot;' +
        ' the shelf may not show changes made while offline until they sync',
      err,
    );
  });

  return {
    get groups() {
      return computeGroups();
    },
    get checked() {
      return checked;
    },
    get online() {
      return online;
    },
    get pendingIds() {
      return pending;
    },
    get snapshotAt() {
      return snapshotAt;
    },
    get refreshed() {
      return refreshed;
    },
    refresh,
    retry,
    add,
    toggle,
    remove,
    reorder,
    clearChecked,
    applyEvent,
    dispose: () => clearTimeout(persistTimer),
  };
}
