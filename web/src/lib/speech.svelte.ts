/**
 * Dictation for the add bar: a thin, injectable wrapper over the Web Speech
 * API's `SpeechRecognition`, exposing one phrase at a time as trimmed text.
 *
 * Why detection is BEHAVIOURAL, not `if (window.webkitSpeechRecognition)`.
 * WebKit leaves the constructor exposed in contexts where the API is dead — a
 * WKWebView, and an installed (home-screen) web app on iOS — and only reveals
 * the truth at `start()`, which fires `onerror` with `service-not-allowed`
 * (WebKit bug 239816). A presence check therefore renders a mic button that can
 * never work. So the constructor's existence is treated as necessary but not
 * sufficient: a `service-not-allowed` is recorded as a device-level verdict
 * (persisted under {@link SPEECH_DISABLED_KEY}), `supported` flips to false and
 * the button disappears — but the verdict EXPIRES, because the same error also
 * covers conditions that are merely temporary. See {@link SPEECH_DISABLED_DAYS}.
 * `not-allowed` is deliberately NOT persisted at all — that is the user
 * declining the permission prompt, which they can grant on the next tap.
 *
 * Platform reality as of 2026:
 * - Chrome 139+ ships it unprefixed and can run on-device; before that, and
 *   still by default for many locales, recognition is server-side — which is
 *   why `network` is a first-class error kind.
 * - Safari 14.1 / iOS 14.5+ support it under the `webkit` prefix only, and NOT
 *   in an installed web app on iOS (see above).
 * - Firefox 142 has it behind `media.webspeech.recognition.enable`, off by
 *   default, so most Firefox users simply get no mic button.
 *
 * Chrome's `phrases` contextual biasing (which would let us prime the recogniser
 * with the household's usual items) is deliberately deferred to v2.
 */

export type SpeechErrorKind =
  /** The user declined the microphone permission. Recoverable — ask again. */
  | 'not-allowed'
  /** The engine refused outright (installed iOS web app, WKWebView). Fatal. */
  | 'service-not-allowed'
  /** The recogniser heard nothing. */
  | 'no-speech'
  /** The recogniser was unreachable — Chrome's default engine is server-side. */
  | 'network'
  | 'other';

/** localStorage key holding the "this device cannot dictate" verdict. */
export const SPEECH_DISABLED_KEY = 'trug_speech_off';

/**
 * How long a `service-not-allowed` verdict is trusted before the mic is offered
 * again. It is NOT forever, deliberately. On an installed iOS web app the
 * refusal really is permanent, and re-probing costs one dead tap a fortnight.
 * But Chrome raises the same error for conditions that are merely temporary —
 * the OS microphone permission switched off for the browser, an enterprise
 * policy, an unreachable speech endpoint — and a single one of those should not
 * silently remove the feature for good with no way back short of clearing site
 * data. Expiry is what makes the wrong guess survivable.
 */
export const SPEECH_DISABLED_DAYS = 14;

export interface Speech {
  /** Constructor exists AND this device has not been permanently ruled out. */
  readonly supported: boolean;
  readonly listening: boolean;
  /** Live interim transcript while listening; '' otherwise. */
  readonly interim: string;
  start(): void;
  stop(): void;
  dispose(): void;
}

export interface SpeechOptions {
  onResult: (text: string) => void;
  onError?: (kind: SpeechErrorKind) => void;
  lang?: string;
  /** Injectable for tests; defaults to window.SpeechRecognition ?? webkit-prefixed. */
  ctor?: any;
  /** Injectable for tests; defaults to localStorage. */
  storage?: Storage;
  /** Injectable clock, so the verdict's expiry is testable. Defaults to Date.now. */
  now?: () => number;
}

/** The error strings we surface as themselves; everything else is 'other'. */
const KNOWN_ERRORS = new Set<string>([
  'not-allowed',
  'service-not-allowed',
  'no-speech',
  'network',
]);

function toKind(error: unknown): SpeechErrorKind {
  return typeof error === 'string' && KNOWN_ERRORS.has(error)
    ? (error as SpeechErrorKind)
    : 'other';
}

function defaultCtor(): any {
  if (typeof window === 'undefined') return undefined;
  const w = window as any;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition;
}

/**
 * `localStorage` is a getter that throws outright in some privacy modes, so even
 * naming it needs a guard. A storage we cannot read is treated as "not disabled":
 * the worst case is a mic button that fails once per session, which beats hiding
 * a working feature from everyone whose browser locks storage down.
 */
function defaultStorage(): Storage | undefined {
  try {
    return typeof localStorage === 'undefined' ? undefined : localStorage;
  } catch {
    return undefined;
  }
}

const DAY_MS = 24 * 60 * 60 * 1000;

/** True while a stored verdict is still within {@link SPEECH_DISABLED_DAYS}. */
function readDisabled(storage: Storage | undefined, now: number): boolean {
  let stamp: string | null = null;
  try {
    stamp = storage?.getItem(SPEECH_DISABLED_KEY) ?? null;
  } catch {
    return false;
  }
  if (stamp === null) return false;
  const written = Number(stamp);
  // An unparseable value is treated as expired rather than as forever: a
  // corrupted key must not be the thing that costs someone the feature.
  if (!Number.isFinite(written)) return false;
  return now - written < SPEECH_DISABLED_DAYS * DAY_MS;
}

function writeDisabled(storage: Storage | undefined, now: number): void {
  try {
    storage?.setItem(SPEECH_DISABLED_KEY, String(now));
  } catch {
    // The verdict is still honoured for the life of this page via `available`;
    // it just won't survive a reload. Nothing here is worth failing an add over.
  }
}

export function createSpeech(opts: SpeechOptions): Speech {
  const storage = 'storage' in opts ? opts.storage : defaultStorage();
  const ctor = 'ctor' in opts ? opts.ctor : defaultCtor();
  const lang = opts.lang || (typeof navigator === 'undefined' ? '' : navigator.language) || 'en-GB';
  const now = opts.now ?? (() => Date.now());

  let available = $state(Boolean(ctor) && !readDisabled(storage, now()));
  let listening = $state(false);
  let interim = $state('');

  /** The in-flight recogniser, or null when idle. */
  let rec: any = null;
  let disposed = false;

  /** Drop every handler so a late event from an abandoned recogniser is inert. */
  function detach(target: any): void {
    if (!target) return;
    target.onresult = null;
    target.onerror = null;
    target.onend = null;
  }

  function settle(): void {
    listening = false;
    interim = '';
    detach(rec);
    rec = null;
  }

  function handleResult(event: any): void {
    let finalText = '';
    let pending = '';
    const results = event?.results ?? [];
    for (let i = event?.resultIndex ?? 0; i < results.length; i += 1) {
      const result = results[i];
      const transcript = result?.[0]?.transcript ?? '';
      if (result?.isFinal) finalText += transcript;
      else pending += transcript;
    }

    const text = finalText.trim();
    if (text) {
      // One phrase per session (`continuous = false`), so one delivery: clear the
      // interim first, then hand the text over exactly once.
      interim = '';
      // This runs inside the browser's event dispatch, so a synchronous throw
      // downstream would escape into it: the console would show an uncaught
      // error, `onend` would settle the button back to idle a moment later, and
      // the user would watch the mic switch itself off having heard them
      // perfectly. Report it as an error they can see instead.
      try {
        opts.onResult(text);
      } catch (err) {
        console.error('[trug] the dictated phrase could not be added', err);
        opts.onError?.('other');
      }
    } else if (finalText) {
      // A final result that was only whitespace — heard, but nothing to add.
      interim = '';
    } else {
      interim = pending;
    }
  }

  function handleError(event: any): void {
    const kind = toKind(event?.error);
    if (kind === 'service-not-allowed') {
      // The engine refused outright — an installed iOS web app or a WKWebView,
      // or a browser whose microphone the OS has switched off. Stand down and
      // remember it, but only for a while (see SPEECH_DISABLED_DAYS), and say
      // so in the console: this is the one error that removes the button, so a
      // silent record of it is what someone debugging will look for first.
      available = false;
      writeDisabled(storage, now());
      console.warn('[trug] speech recognition refused by this browser; hiding the mic');
    }
    // `onerror` and `onend` both fire for one failure. Settling here detaches the
    // handlers, so the trailing `onend` lands on a dead object and cannot report
    // the same failure twice.
    settle();
    opts.onError?.(kind);
  }

  function start(): void {
    if (disposed || !available || listening) return;

    // A previous session can still be winding down: `stop()` leaves its handlers
    // attached on purpose, because the engine finalises after a stop and that
    // last transcript is the user's words. But once a NEW phrase is starting,
    // the old session's `end` must not land on it — settling the wrapper would
    // switch the button off with the mic still open. So retire it outright.
    if (rec) {
      const stale = rec;
      rec = null;
      detach(stale);
      try {
        stale.abort();
      } catch {
        // Already finished; nothing to abort.
      }
    }

    let next: any;
    try {
      next = new ctor();
      next.continuous = false;
      next.interimResults = true;
      next.maxAlternatives = 1;
      next.lang = lang;
      next.onresult = handleResult;
      next.onerror = handleError;
      next.onend = settle;
      rec = next;
      listening = true;
      interim = '';
      next.start();
    } catch (err) {
      // `start()` throws InvalidStateError if the engine is already running, and
      // constructors can throw in locked-down contexts. Either way the session
      // never began, so surface it rather than leaving the button stuck on.
      //
      // Logged because this catch is wide enough to swallow a fault of our own —
      // a non-constructor on `window.SpeechRecognition` planted by an extension,
      // say — and without a line here the button would fail identically on every
      // tap forever with nothing anywhere to say why.
      console.error('[trug] speech recogniser failed to start', err);
      detach(next);
      rec = null;
      listening = false;
      interim = '';
      opts.onError?.('other');
    }
  }

  function stop(): void {
    const target = rec;
    if (!target) return;
    // `listening` drops now rather than waiting for `onend`: the tap that stopped
    // the mic should turn the button off in the same frame, and `settle()` on the
    // engine's own `end` event is idempotent.
    listening = false;
    interim = '';
    try {
      target.stop();
    } catch {
      // Already stopped or torn down — the state above is what the UI reads.
    }
  }

  function dispose(): void {
    disposed = true;
    const target = rec;
    detach(target);
    rec = null;
    listening = false;
    interim = '';
    try {
      target?.abort();
    } catch {
      // Nothing left to abort.
    }
  }

  return {
    get supported() {
      return available;
    },
    get listening() {
      return listening;
    },
    get interim() {
      return interim;
    },
    start,
    stop,
    dispose,
  };
}
