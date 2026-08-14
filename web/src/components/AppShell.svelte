<script lang="ts">
  import { onMount } from 'svelte';
  import { DUR, EASE, rise } from '../lib/motion';
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

  const store = createStore({
    api,
    queue: new OpQueue('trug'),
    walkOrder: WALK_ORDER,
    // A change that could not be saved has already been rolled back on the
    // shelf; say so, through the same transient toast a refused drag uses.
    onError: (message) => showError(message),
  });
  const drag = createDragController({
    getGroups: () => store.groups,
    // Error sink for a rejected reorder: an ApiError (the server refused the
    // move) surfaces as a toast; a network error keeps the offline-banner path
    // (store.reorder has already rolled the row back and flipped `online`).
    reorder: async (id, sortKey, category) => {
      try {
        await store.reorder(id, sortKey, category);
      } catch (err) {
        if (err instanceof ApiError) showError("Couldn't move that — try again");
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

  // Transient toast for a change the app could not carry out: a rejected
  // drag-reorder, or a mutation whose op could not be saved to the offline
  // queue (both have already been rolled back on the shelf by the time we get
  // here, so the message says what state the list is now in).
  let errorToast = $state<string | null>(null);
  let errorToastTimer: ReturnType<typeof setTimeout> | undefined;
  function showError(message: string) {
    errorToast = message;
    clearTimeout(errorToastTimer);
    errorToastTimer = setTimeout(() => (errorToast = null), 4000);
  }

  /**
   * A refresh/retry that failed. Offline is the ordinary case and is already on
   * screen as the banner, and the store logs a server-side refusal itself — but
   * an empty catch would leave a genuinely surprising failure (a bug in
   * reconciliation, say) with nowhere to show at all.
   */
  function noteSyncFailure(err: unknown) {
    console.debug('[trug] refresh/retry did not complete', err);
  }

  // "Copy list": the outstanding items, one name per line in shelf display
  // order — the shape supermarket multisearch boxes accept as a paste. Success
  // swaps the icon to a check and toasts; a blocked clipboard gets an honest
  // failure toast (there's no selectable-text fallback up here in the header).
  // Plain-HTTP installs (LAN/Pi) have no Clipboard API at all — hide the button
  // there rather than render a permanently dead affordance. Static per load.
  const canCopy = typeof navigator !== 'undefined' && !!navigator.clipboard;
  let copied = $state(false);
  let copyToast = $state<{ text: string; variant: 'accent' | 'neutral' } | null>(null);
  let copiedTimer: ReturnType<typeof setTimeout> | undefined;
  let copyToastTimer: ReturnType<typeof setTimeout> | undefined;

  async function copyList() {
    // Read groups once — the getter recomputes per access. Empty is a race
    // guard only (last item checked between render and tap), not a real path:
    // the button is hidden whenever groups is empty.
    const names = store.groups.flatMap((g) => g.items).map((i) => i.name);
    if (!names.length) return;
    try {
      await navigator.clipboard.writeText(names.join('\n'));
      copied = true;
      clearTimeout(copiedTimer);
      copiedTimer = setTimeout(() => (copied = false), 1500);
      copyToast = { text: `Copied ${names.length} item${names.length === 1 ? '' : 's'}`, variant: 'accent' };
    } catch (err) {
      // Permission denied / document unfocused. Leave a trace for remote
      // debugging ("copy doesn't work on my phone") — the toast alone is mute.
      console.warn('copy list failed', err);
      copied = false;
      clearTimeout(copiedTimer);
      copyToast = { text: "Couldn't copy — clipboard blocked", variant: 'neutral' };
    }
    clearTimeout(copyToastTimer);
    copyToastTimer = setTimeout(() => (copyToast = null), 3000);
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

  /**
   * Tells the recents tray that the catalogue may have moved under it. The tray
   * is a fetch, not a subscription, so without this a shortcut deleted (or
   * forgotten) anywhere — including on this device — stays on screen until the
   * component next mounts: you delete the junk, watch the tray, and nothing
   * happens.
   *
   * Only the three events that CAN change the catalogue bump it. Adding creates
   * or bumps a row, deleting an item takes that row away whatever its count,
   * and a forget removes one outright; checking, unchecking, editing and
   * clearing the basket deliberately touch none of it.
   * That is the difference between "no requests at all during the walk round
   * the shop" (a check-off is the most repeated gesture there is) and one per
   * tap on a phone with two bars in a carpark.
   *
   * Coalesced over a second, so a ring capture that lands five names — or a
   * clear-out of several items — costs one refetch rather than five. The same
   * window the ring toast batches on.
   */
  let catalogRevision = $state(0);
  let catalogTimer: ReturnType<typeof setTimeout> | null = null;
  const CATALOG_COALESCE_MS = 1000;

  function catalogMayHaveChanged() {
    if (catalogTimer) return;
    catalogTimer = setTimeout(() => {
      catalogTimer = null;
      catalogRevision += 1;
    }, CATALOG_COALESCE_MS);
  }

  function onEvent(name: string, data: any) {
    if (name === 'item_added' && data?.source === 'ring' && typeof data.name === 'string') {
      ringBatch.push(data.name);
      if (!ringBatchTimer) ringBatchTimer = setTimeout(flushRingToast, 1000);
    }
    if (name === 'item_added' || name === 'item_removed' || name === 'catalog_forgotten') {
      catalogMayHaveChanged();
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
    store.refresh().catch(noteSyncFailure);

    // Machine principal on a zero-user instance: surface the "no owner yet"
    // banner. Cookie (human) sessions are never in this state. Best-effort — a
    // failed probe just leaves the banner hidden.
    if (!cookieAuth) {
      api.auth
        .bootstrapState()
        .then((s) => (ownerless = s.claimable))
        .catch((err) => console.debug('[trug] bootstrap-state probe failed', err));
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

    // Live stream: apply deltas, and drain + refetch on every (re)connect since
    // the stream only carries changes made while we were connected.
    // A stream that has just (re)connected IS the "the server is back" signal —
    // and it is the only one there is when the SERVER went away rather than the
    // device's network, since `window.online` cannot fire for a network that
    // never changed. So retry() here, not refresh(): refresh alone clears the
    // banner and leaves the queue sitting there undrained.
    // The gate only mounts this shell once signed in, so the absence of a bearer
    // here means a cookie session — connect the stream cookie-authed.
    // The stream carries nothing that happened while it was down, so every
    // connect is also a moment the tray may be out of date — the FIRST one
    // included. It used to be skipped, on the grounds that the tray had just
    // fetched at mount. But a cold start with no signal is precisely the case
    // where that mount fetch failed, and the first connect is precisely the
    // moment the network arrived: skipping it left the tray blank for the whole
    // session, since nothing else refetches it until an add or a delete. The
    // shell cannot see whether the tray's own fetch landed, and the cost of
    // assuming the worst is one coalesced GET per page load — beside the
    // store.retry() this callback already makes unconditionally.
    const disconnect = connectEvents(
      (name, data) => onEvent(name, data),
      getToken,
      () => {
        catalogMayHaveChanged();
        store.retry().catch(noteSyncFailure);
      },
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

    // Coming back to the tab may have missed events while hidden — and may also
    // be the moment a phone that was asleep in a pocket regains service, so
    // drain before refetching rather than after.
    const onVisible = () => {
      if (document.visibilityState === 'visible') store.retry().catch(noteSyncFailure);
    };
    // Regained network: flush queued ops, then refetch.
    const onOnline = () => store.retry().catch(noteSyncFailure);

    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('online', onOnline);

    // Last-resort recovery ticker. Every automatic drain above is EVENT-driven,
    // and each event can be absent exactly when it is needed: `online` cannot
    // fire when the device's network never changed (the server went away, not
    // the wifi), the stream may be stuck on its backoff ladder, and a shopper
    // walking the aisles never leaves or re-enters the tab. Without this, a
    // queue that stopped draining stays stopped until someone taps something.
    // It only runs while there is something to recover — offline, or ops still
    // queued — so a healthy shelf makes no unprompted requests at all.
    const RECOVERY_MS = 20_000;
    const recovery = setInterval(() => {
      if (!store.online || store.pendingIds.size > 0) store.retry().catch(noteSyncFailure);
    }, RECOVERY_MS);

    return () => {
      clearInterval(recovery);
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
      if (catalogTimer) clearTimeout(catalogTimer);
      clearTimeout(ringToastTimer);
      clearTimeout(errorToastTimer);
      clearTimeout(copiedTimer);
      clearTimeout(copyToastTimer);
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
      {#if canCopy && store.groups.length > 0}
        <button type="button" class="icon-btn" aria-label="Copy list" title="Copy list" onclick={copyList}>
          <Icon name={copied ? 'copy-check' : 'copy'} size={20} stroke={1.75} />
        </button>
      {/if}
      <button type="button" class="icon-btn" aria-label="Settings" onclick={() => (settingsOpen = true)}>
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
    <div
      class="offline-banner"
      role="status"
      in:rise={{ y: -12, duration: DUR.toastIn, easing: EASE.enter }}
      out:rise={{ y: -12, duration: DUR.toastOut, easing: EASE.exit }}
    >
      You're offline — changes are saved and will sync when you reconnect.
    </div>
  {/if}

  {#if asOf}
    <p class="as-of" role="status">{asOf}</p>
  {/if}

  <main>
    {#if !query.trim() && !listEmpty}
      <RecentsGrid
        top={api.top}
        {activeNames}
        onPick={(name) => store.add(name)}
        onForget={api.forget}
        onError={showError}
        revision={catalogRevision}
      />
    {/if}
    <ListView {store} walkOrder={WALK_ORDER} {drag} {onUpdate} />
  </main>

  <footer bind:clientHeight={footerH}>
    <AddBar search={api.search} onAdd={(name) => store.add(name)} onQueryChange={(q) => (query = q)} />
  </footer>

  {#if ringToast}
    <Toast variant="accent">{ringToast}</Toast>
  {/if}

  {#if errorToast}
    <Toast variant="neutral">{errorToast}</Toast>
  {/if}

  {#if copyToast}
    <Toast variant={copyToast.variant}>{copyToast.text}</Toast>
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
  .icon-btn {
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
  .icon-btn:hover {
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
    padding: 0 var(--app-gutter);
    /* Scroll anchoring fights the list's own animations. Checking an item, or
       re-adding one, changes the height of two containers at once; the browser
       then adjusts scrollTop to keep an anchor node still, which moves
       everything ELSE instead. That is the part people describe as the whole
       list jittering rather than one row moving. The list animates its own
       reflow, so it does not want the help. */
    overflow-anchor: none;
  }
  footer {
    flex: 0 0 auto;
    /* Stacking context above <main>: transformed rows (drag/swipe) form their
       own contexts and would otherwise paint over the typeahead dropdown. */
    position: relative;
    z-index: 40;
  }
</style>
