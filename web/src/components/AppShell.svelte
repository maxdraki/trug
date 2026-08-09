<script lang="ts">
  import { onMount } from 'svelte';
  import { fly } from 'svelte/transition';
  import { d, DUR } from '../lib/motion';
  import { api, getToken, probeAuthStatus, ApiError } from '../lib/api';
  import { OpQueue } from '../lib/opqueue';
  import { createStore } from '../lib/store.svelte';
  import { createDragController } from '../lib/drag.svelte';
  import { connectEvents } from '../lib/sse';
  import { staleHint, clearSnapshot } from '../lib/snapshot';
  import { requestPersistentStorage } from '../lib/storage';
  import { WALK_ORDER } from '../lib/walkOrder';
  import ListView from './ListView.svelte';
  import AddBar from './AddBar.svelte';
  import RecentsGrid from './RecentsGrid.svelte';
  import SettingsSheet from './SettingsSheet.svelte';
  import Toast from './Toast.svelte';
  import Icon from './Icon.svelte';
  import Logo from '../lib/Logo.svelte';

  const store = createStore({ api, queue: new OpQueue('trug'), walkOrder: WALK_ORDER });
  const drag = createDragController({
    getGroups: () => store.groups,
    // Error sink for a rejected reorder: an ApiError (the server refused the
    // move) surfaces as a toast; a network error keeps the offline-banner path
    // (store.reorder has already rolled the row back and flipped `online`).
    reorder: async (id, sortKey, category) => {
      try {
        await store.reorder(id, sortKey, category);
      } catch (err) {
        if (err instanceof ApiError) showDragError();
      }
    },
  });

  let query = $state('');
  let settingsOpen = $state(false);

  // A cookie (human) session has no stored bearer; a machine/legacy principal
  // does. Used to gate the machine-principal ownerless banner and Settings' own
  // cookie-only panels. Read once at mount — the credential can't change without
  // a reload.
  const cookieAuth = getToken() == null;

  // Machine principal on a still-claimable (zero-user) instance: signed in with a
  // bearer, but nobody has created the first account yet. A quiet nudge to open
  // the instance somewhere the passkey ceremony can run (secure context).
  let ownerless = $state(false);

  // Transient toast for a rejected drag-reorder.
  let dragError = $state<string | null>(null);
  let dragErrorTimer: ReturnType<typeof setTimeout> | undefined;
  function showDragError() {
    dragError = "Couldn't move that — try again";
    clearTimeout(dragErrorTimer);
    dragErrorTimer = setTimeout(() => (dragError = null), 4000);
  }
  // Measured footer height, published as --footer-h so docked toasts sit just
  // above the add-bar (safe-area padding is already included in the measurement).
  let footerH = $state(0);

  // Live viewport height in px, tracked through the VisualViewport API where
  // available. On phones the on-screen keyboard shrinks the *visual* viewport
  // while the *layout* viewport (100dvh, on browsers that don't honor
  // interactive-widget=resizes-content) can stay full-height — that mismatch
  // is what leaves the pill floating mid-screen with the list's top scrolled
  // out of reach. Sizing `.app` off this value keeps the whole column,
  // including the add-bar, inside whatever space the keyboard actually leaves.
  let viewportH = $state<number | null>(null);

  function syncViewportHeight() {
    if (typeof window === 'undefined') return;
    viewportH = window.visualViewport ? window.visualViewport.height : window.innerHeight;
  }

  // Seed the offline banner from the browser's initial connectivity so a cold
  // load while offline shows the banner before the first request resolves; the
  // store's `online` (fetch success/failure) then drives it thereafter.
  const offline = $derived(!store.online || (typeof navigator !== 'undefined' && !navigator.onLine));
  const queued = $derived(store.pendingIds.size);
  // Quiet "as of <relative time>" hint when the shelf is rendering from an
  // hour-plus-old snapshot the server hasn't yet reconciled (retires the moment
  // a refresh lands). null the rest of the time.
  const asOf = $derived(staleHint(store.snapshotAt, store.refreshed));
  // When the list is completely empty the empty-state ("Shelf's empty") owns the
  // screen; suppress the recents tray so it doesn't sit above that message.
  const listEmpty = $derived(store.groups.length === 0 && store.checked.length === 0);

  // Ring arrivals (SSE item_added with source 'ring') announce themselves in a
  // toast. Names landing within ~1s of the first are batched into one message.
  let ringToast = $state<string | null>(null);
  let ringBatch: string[] = [];
  let ringBatchTimer: ReturnType<typeof setTimeout> | null = null;
  let ringToastTimer: ReturnType<typeof setTimeout> | undefined;

  function flushRingToast() {
    ringBatchTimer = null;
    if (!ringBatch.length) return;
    ringToast = `🪄 from the ring: ${ringBatch.join(', ')}`;
    ringBatch = [];
    clearTimeout(ringToastTimer);
    ringToastTimer = setTimeout(() => (ringToast = null), 5000);
  }

  function onEvent(name: string, data: any) {
    if (name === 'item_added' && data?.source === 'ring' && typeof data.name === 'string') {
      ringBatch.push(data.name);
      if (!ringBatchTimer) ringBatchTimer = setTimeout(flushRingToast, 1000);
    }
    store.applyEvent(name, data);
  }

  // Normalised names already on the list, so the recents grid can hide them.
  const activeNames = $derived(
    new Set(
      store.groups
        .flatMap((g) => g.items)
        .map((i) => i.name.toLowerCase().split(/\s+/).filter(Boolean).join(' ')),
    ),
  );

  onMount(() => {
    store.refresh().catch(() => {});

    // Machine principal on a zero-user instance: surface the "no owner yet"
    // banner. Cookie (human) sessions are never in this state. Best-effort — a
    // failed probe just leaves the banner hidden.
    if (!cookieAuth) {
      api.auth
        .bootstrapState()
        .then((s) => (ownerless = s.claimable))
        .catch(() => {});
    }

    // Ask the browser to keep the snapshot + op-queue from being evicted under
    // disk pressure. Fire-and-forget; installed PWAs usually auto-grant, and a
    // denied tab still works (just evictable), so neither outcome shows UI.
    void requestPersistentStorage();

    syncViewportHeight();
    const vv = window.visualViewport;
    // visualViewport fires resize (keyboard open/close, pinch-zoom) and scroll
    // (the page shifting under the keyboard); window's resize is the fallback
    // for browsers without the API at all.
    if (vv) {
      vv.addEventListener('resize', syncViewportHeight);
      vv.addEventListener('scroll', syncViewportHeight);
    } else {
      window.addEventListener('resize', syncViewportHeight);
    }

    // Live stream: apply deltas, and refetch on every (re)connect since the
    // stream only carries changes made while we were connected.
    // The gate only mounts this shell once signed in, so the absence of a bearer
    // here means a cookie session — connect the stream cookie-authed.
    const disconnect = connectEvents(
      (name, data) => onEvent(name, data),
      getToken,
      () => store.refresh().catch(() => {}),
      () => cookieAuth,
      {
        // After a sustained run of failed reconnects, confirm we're still signed
        // in. A 401/403 (probeAuthStatus 'lost') means the session expired —
        // stop retrying and reload to re-assert the gate. A network blip stays
        // on the backoff ladder.
        checkStillAuthed: async () => (await probeAuthStatus()) !== 'lost',
        // Confirmed auth-loss: drop the cached list before reasserting the gate
        // so a signed-out device never boots into a stale household shelf.
        onAuthLost: () => {
          clearSnapshot();
          location.reload();
        },
      },
    );

    // Coming back to the tab may have missed events while hidden.
    const onVisible = () => {
      if (document.visibilityState === 'visible') store.refresh().catch(() => {});
    };
    // Regained network: flush queued ops, then refetch.
    const onOnline = () => store.retry().catch(() => {});

    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('online', onOnline);

    return () => {
      disconnect();
      store.dispose();
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('online', onOnline);
      if (vv) {
        vv.removeEventListener('resize', syncViewportHeight);
        vv.removeEventListener('scroll', syncViewportHeight);
      } else {
        window.removeEventListener('resize', syncViewportHeight);
      }
      // Cancel any pending ring batch/toast timers so they don't fire after
      // teardown (and touch state on a torn-down component).
      if (ringBatchTimer) clearTimeout(ringBatchTimer);
      clearTimeout(ringToastTimer);
      clearTimeout(dragErrorTimer);
    };
  });

  async function onUpdate(id: string, fields: { note?: string | null; category?: string }) {
    const updated = await api.update(id, fields);
    store.applyEvent('item_updated', updated);
  }
</script>

<div
  class="app"
  style="--footer-h: {footerH}px; {viewportH != null ? `--viewport-h: ${viewportH}px;` : ''}"
>
  <header>
    <span class="brand">
      <span class="brand-mark"><Logo size={30} title="trug" /></span>
      <span class="wordmark">trug</span>
    </span>
    <div class="header-actions">
      {#if queued > 0}
        <span class="queued" title="Waiting to sync">{queued} queued</span>
      {/if}
      {#if offline}<span class="offline" title="Offline — changes will sync">offline</span>{/if}
      <button type="button" class="gear" aria-label="Settings" onclick={() => (settingsOpen = true)}>
        <Icon name="settings" size={20} stroke={1.75} />
      </button>
    </div>
  </header>

  {#if ownerless}
    <p class="ownerless" role="status">
      this instance has no owner yet — open it on HTTPS or localhost to create the first account.
    </p>
  {/if}

  {#if offline}
    <div class="offline-banner" role="status" transition:fly={{ y: -12, duration: d(DUR.fly) }}>
      You're offline — changes are saved and will sync when you reconnect.
    </div>
  {/if}

  {#if asOf}
    <p class="as-of" role="status">{asOf}</p>
  {/if}

  <main>
    {#if !query.trim() && !listEmpty}
      <RecentsGrid top={api.top} {activeNames} onPick={(name) => store.add(name)} />
    {/if}
    <ListView {store} walkOrder={WALK_ORDER} {drag} {onUpdate} />
  </main>

  <footer bind:clientHeight={footerH}>
    <AddBar search={api.search} onAdd={(name) => store.add(name)} onQueryChange={(q) => (query = q)} />
  </footer>

  {#if ringToast}
    <Toast variant="accent">{ringToast}</Toast>
  {/if}

  {#if dragError}
    <Toast variant="neutral">{dragError}</Toast>
  {/if}

  <SettingsSheet
    open={settingsOpen}
    {cookieAuth}
    onClose={() => (settingsOpen = false)}
  />
</div>

<style>
  .app {
    display: flex;
    flex-direction: column;
    height: 100svh;
    /* dvh tracks the keyboard on browsers honoring interactive-widget=resizes-
       content; --viewport-h (set from the VisualViewport API in AppShell.svelte)
       is the authoritative value where JS can measure it, since it also
       covers browsers where the layout viewport never resizes for the
       keyboard at all. */
    height: 100dvh;
    height: var(--viewport-h, 100dvh);
    max-width: 560px;
    margin: 0 auto;
    /* Ladder: crust page, mantle column, base cards. */
    background: var(--ctp-mantle);
  }
  /* Hairline frame on wide viewports so the column reads as a deliberate
     object against the crust page, not an unfinished layout. */
  @media (min-width: 592px) {
    .app {
      border-left: var(--hairline);
      border-right: var(--hairline);
    }
  }
  header {
    flex: 0 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 20px 12px;
  }
  .brand {
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }
  /* The mark carries the accent; the wordmark stays ink. */
  .brand-mark {
    display: inline-flex;
    color: var(--accent);
  }
  .wordmark {
    font-family: var(--font-display);
    font-size: 30px;
    font-weight: 500;
    letter-spacing: -0.03em;
    color: var(--ctp-text);
  }
  .header-actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .offline,
  .queued {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--ctp-subtext0);
    border: var(--hairline);
    padding: 3px 8px;
    border-radius: var(--radius);
  }
  .gear {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    padding: 0;
    border: none;
    border-radius: var(--radius);
    background: none;
    color: var(--ctp-subtext0);
    cursor: pointer;
    transition: color 120ms ease, background 120ms ease;
  }
  .gear:hover {
    background: var(--ctp-surface0);
    color: var(--ctp-text);
  }
  .offline-banner {
    flex: 0 0 auto;
    padding: 8px 20px;
    font-size: 13px;
    text-align: center;
    color: var(--ctp-subtext1);
    background: var(--ctp-base);
    border-top: var(--hairline);
    border-bottom: var(--hairline);
  }
  /* Quiet, no-accent "no owner yet" nudge for a machine principal on a still-
     claimable instance — a whisper under the header, same voice as .as-of. */
  .ownerless {
    flex: 0 0 auto;
    margin: 0;
    padding: 8px 20px;
    font-size: 13px;
    text-align: center;
    color: var(--ctp-subtext0);
    background: var(--ctp-base);
    border-top: var(--hairline);
    border-bottom: var(--hairline);
  }
  /* Quiet, no-accent "as of …" hint: a whisper under the header telling you how
     old the shelf you're reading is when it hasn't reconciled yet. */
  .as-of {
    flex: 0 0 auto;
    margin: 0;
    padding: 4px 20px 0;
    font-size: 12px;
    text-align: center;
    color: var(--ctp-subtext0);
  }
  main {
    flex: 1 1 auto;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
    padding: 0 16px;
  }
  footer {
    flex: 0 0 auto;
    /* Stacking context above <main>: transformed rows (drag/swipe) form their
       own contexts and would otherwise paint over the typeahead dropdown. */
    position: relative;
    z-index: 40;
  }
</style>
