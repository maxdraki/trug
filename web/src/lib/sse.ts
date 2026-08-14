/**
 * Live-sync client over Server-Sent Events.
 *
 * The server publishes item_added / item_updated / item_removed / list_cleared
 * frames on `/api/events`, which accepts the bearer token as a `?token=` query
 * param (EventSource cannot set an Authorization header).
 *
 * On any transport error the current EventSource is closed and a reconnect is
 * scheduled with exponential backoff (1s, doubling, capped at 30s). A successful
 * open resets the backoff and fires `onConnect` so callers can refetch — the
 * stream only carries deltas, so a full refresh reconciles anything missed while
 * disconnected.
 *
 * Waiting for a token (before the gate is passed) is NOT a connection error, so
 * it polls at a fixed short interval and never advances the backoff ladder —
 * otherwise pasting a token after a few empty polls would strand the first real
 * connection attempt behind a 30s delay.
 */

const EVENT_NAMES = [
  'item_added',
  'item_updated',
  'item_removed',
  'list_cleared',
  // Not a list change at all — the store ignores it. It exists so a device
  // showing the "Frequently added" tray learns that a shortcut was forgotten
  // somewhere else, instead of offering it until the next reload.
  'catalog_forgotten',
] as const;

const BACKOFF_START_MS = 1_000;
const BACKOFF_CAP_MS = 30_000;
const TOKEN_POLL_MS = 1_000;
const PROBE_AFTER_FAILURES = 3;

/**
 * Decide whether a run of failed connects is long enough to be worth an auth
 * probe. Pure so the threshold behaviour is unit-testable without a live stream.
 */
export function shouldProbeAuth(
  consecutiveFailures: number,
  threshold: number = PROBE_AFTER_FAILURES,
): boolean {
  return consecutiveFailures >= threshold;
}

/**
 * Optional auth-loss handling for the stream. After a run of failed connects,
 * `checkStillAuthed` is consulted; resolving `false` means a CONFIRMED auth loss
 * (a 401/403 — NOT a network blip), on which retrying stops and `onAuthLost`
 * fires so the caller can re-assert the sign-in gate.
 */
export interface AuthProbeOptions {
  checkStillAuthed?: () => Promise<boolean>;
  onAuthLost?: () => void;
  probeThreshold?: number;
}

export function connectEvents(
  onEvent: (name: string, data: any) => void,
  getToken: () => string | null,
  onConnect?: () => void,
  /**
   * Human (cookie) sessions have no bearer to pass as `?token=`; the session
   * cookie rides along automatically on a same-origin EventSource. When this
   * returns true and there is no bearer, connect to the plain `/api/events`.
   */
  cookieAuth?: () => boolean,
  auth?: AuthProbeOptions,
): () => void {
  let es: EventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let backoff = BACKOFF_START_MS;
  let closed = false;
  let failures = 0;
  let probing = false;

  function scheduleIn(delay: number): void {
    if (closed || timer !== null) return;
    timer = setTimeout(() => {
      timer = null;
      open();
    }, delay);
  }

  function scheduleRetry(): void {
    const delay = backoff;
    backoff = Math.min(backoff * 2, BACKOFF_CAP_MS);
    scheduleIn(delay);
  }

  function wire(source: EventSource): void {
    es = source;

    source.onopen = () => {
      backoff = BACKOFF_START_MS; // healthy connection: reset the backoff ladder
      failures = 0; // and the failed-connect run
      onConnect?.();
    };

    for (const name of EVENT_NAMES) {
      source.addEventListener(name, (e) => {
        try {
          onEvent(name, JSON.parse((e as MessageEvent).data));
        } catch {
          /* ignore malformed frames */
        }
      });
    }

    source.onerror = () => {
      source.close();
      if (es === source) es = null;
      failures++;
      // After a sustained run of failures, probe whether we're still signed in.
      // A confirmed auth loss stops the loop and re-asserts the gate; a network
      // blip (inconclusive) just keeps backing off.
      if (auth?.checkStillAuthed && shouldProbeAuth(failures, auth.probeThreshold) && !probing) {
        probing = true;
        auth.checkStillAuthed().then((stillAuthed) => {
          probing = false;
          if (closed) return;
          if (!stillAuthed) {
            closed = true;
            if (timer !== null) {
              clearTimeout(timer);
              timer = null;
            }
            auth.onAuthLost?.();
            return;
          }
          scheduleRetry();
        });
        return;
      }
      scheduleRetry();
    };
  }

  function open(): void {
    if (closed) return;
    const token = getToken();
    if (token) {
      wire(new EventSource(`/api/events?token=${encodeURIComponent(token)}`));
      return;
    }
    if (cookieAuth?.()) {
      // Cookie-authed human: no query token — the cookie authenticates the stream.
      wire(new EventSource('/api/events'));
      return;
    }
    // No auth yet (e.g. before the gate is passed) — poll at a fixed short
    // interval, leaving the backoff ladder untouched for real failures.
    scheduleIn(TOKEN_POLL_MS);
  }

  open();

  return function disconnect(): void {
    closed = true;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    if (es !== null) {
      es.close();
      es = null;
    }
  };
}
