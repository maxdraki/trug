import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createSpeech, SPEECH_DISABLED_KEY, type SpeechErrorKind } from './speech.svelte';

/**
 * A stand-in for `SpeechRecognition`. The real thing needs a microphone, a user
 * gesture and (on Chrome) a round trip to Google's recogniser, so every test
 * here drives this instead and injects it via `opts.ctor`.
 *
 * It mirrors the shape the wrapper actually touches: the four configuration
 * properties, the three handler properties, `start`/`stop`/`abort`, and helpers
 * that emit the events in the same shape the browser does.
 */
class FakeRecognition {
  /** Every recogniser constructed, newest last — lets a test assert how many. */
  static instances: FakeRecognition[] = [];

  continuous: boolean | undefined;
  interimResults: boolean | undefined;
  maxAlternatives: number | undefined;
  lang: string | undefined;

  onresult: ((event: unknown) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onend: ((event: unknown) => void) | null = null;

  starts = 0;
  stops = 0;
  aborts = 0;

  constructor() {
    FakeRecognition.instances.push(this);
  }

  static get last(): FakeRecognition {
    const rec = FakeRecognition.instances.at(-1);
    if (!rec) throw new Error('no recogniser was constructed');
    return rec;
  }

  start(): void {
    this.starts += 1;
  }
  stop(): void {
    this.stops += 1;
  }
  abort(): void {
    this.aborts += 1;
  }

  /**
   * Emit a `result` event shaped like the browser's: `results` is an array-like
   * of results, each an array-like of alternatives carrying `.transcript`, with
   * `.isFinal` on the result itself and a `resultIndex` cursor on the event.
   */
  emitResult(transcript: string, isFinal: boolean, resultIndex = 0): void {
    const alternatives = Object.assign([{ transcript, confidence: 0.9 }], { length: 1 });
    const result = Object.assign(alternatives, { isFinal });
    const results = Object.assign([result], { length: 1 });
    this.onresult?.({ resultIndex, results });
  }

  emitError(error: string): void {
    this.onerror?.({ error });
  }

  emitEnd(): void {
    this.onend?.({});
  }
}

/** A spec-shaped, Map-backed Storage that never throws. */
function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    key: (i: number) => [...map.keys()][i] ?? null,
    removeItem: (k: string) => void map.delete(k),
    setItem: (k: string, v: string) => void map.set(k, String(v)),
  } as Storage;
}

/** A Storage that throws on every access, as Safari's private mode used to. */
function hostileStorage(): Storage {
  const boom = () => {
    throw new DOMException('denied', 'SecurityError');
  };
  return {
    get length(): number {
      return boom();
    },
    clear: boom,
    getItem: boom,
    key: boom,
    removeItem: boom,
    setItem: boom,
  } as unknown as Storage;
}

function make(over: Partial<Parameters<typeof createSpeech>[0]> = {}) {
  const onResult = vi.fn();
  const onError = vi.fn<(kind: SpeechErrorKind) => void>();
  const storage = memoryStorage();
  const speech = createSpeech({
    onResult,
    onError,
    lang: 'en-GB',
    ctor: FakeRecognition,
    storage,
    ...over,
  });
  return { speech, onResult, onError, storage };
}

beforeEach(() => {
  FakeRecognition.instances = [];
});

describe('createSpeech', () => {
  it('is unsupported when no recogniser constructor exists', () => {
    const { speech, onError } = make({ ctor: undefined });
    expect(speech.supported).toBe(false);
    speech.start();
    expect(FakeRecognition.instances).toHaveLength(0);
    expect(speech.listening).toBe(false);
    expect(onError).not.toHaveBeenCalled();
  });

  it('configures the recogniser for a single dictated phrase', () => {
    const { speech } = make();
    expect(speech.supported).toBe(true);
    speech.start();

    const rec = FakeRecognition.last;
    expect(rec.continuous).toBe(false);
    expect(rec.interimResults).toBe(true);
    expect(rec.maxAlternatives).toBe(1);
    expect(rec.lang).toBe('en-GB');
    expect(rec.starts).toBe(1);
  });

  it('falls back to the navigator language when no lang is given', () => {
    const { speech } = make({ lang: undefined });
    speech.start();
    expect(FakeRecognition.last.lang).toBe(navigator.language || 'en-GB');
  });

  it('delivers a final transcript once, trimmed', () => {
    const { speech, onResult } = make();
    speech.start();
    FakeRecognition.last.emitResult('  two pints of milk  ', true);

    expect(onResult).toHaveBeenCalledTimes(1);
    expect(onResult).toHaveBeenCalledWith('two pints of milk');
    expect(speech.interim).toBe('');
  });

  it('shows an interim transcript without delivering it', () => {
    const { speech, onResult } = make();
    speech.start();
    FakeRecognition.last.emitResult('two pi', false);

    expect(onResult).not.toHaveBeenCalled();
    expect(speech.interim).toBe('two pi');
  });

  it('is listening between start() and the end event', () => {
    const { speech } = make();
    expect(speech.listening).toBe(false);
    speech.start();
    expect(speech.listening).toBe(true);
    FakeRecognition.last.emitEnd();
    expect(speech.listening).toBe(false);
  });

  it('ignores start() while already listening', () => {
    const { speech } = make();
    speech.start();
    speech.start();
    expect(FakeRecognition.instances).toHaveLength(1);
    expect(FakeRecognition.last.starts).toBe(1);
  });

  it('stop() stops the recogniser and clears the interim transcript', () => {
    const { speech } = make();
    speech.start();
    FakeRecognition.last.emitResult('two pi', false);
    expect(speech.interim).toBe('two pi');

    speech.stop();
    expect(FakeRecognition.last.stops).toBe(1);
    expect(speech.interim).toBe('');
  });

  it('drops a blank final transcript', () => {
    const { speech, onResult } = make();
    speech.start();
    FakeRecognition.last.emitResult('   ', true);
    expect(onResult).not.toHaveBeenCalled();
  });

  it('permanently disables itself when the engine refuses the service', () => {
    const { speech, onError, storage } = make();
    speech.start();
    FakeRecognition.last.emitError('service-not-allowed');

    expect(onError).toHaveBeenCalledWith('service-not-allowed');
    expect(speech.supported).toBe(false);
    expect(speech.listening).toBe(false);
    expect(storage.getItem(SPEECH_DISABLED_KEY)).not.toBeNull();

    // A later session on the same device must not even try again.
    FakeRecognition.instances = [];
    const next = createSpeech({ onResult: vi.fn(), ctor: FakeRecognition, storage });
    expect(next.supported).toBe(false);
    next.start();
    expect(FakeRecognition.instances).toHaveLength(0);
  });

  it('keeps support after a declined permission prompt', () => {
    const { speech, onError, storage } = make();
    speech.start();
    FakeRecognition.last.emitError('not-allowed');

    expect(onError).toHaveBeenCalledWith('not-allowed');
    expect(speech.supported).toBe(true);
    expect(speech.listening).toBe(false);
    expect(storage.getItem(SPEECH_DISABLED_KEY)).toBeNull();

    speech.start();
    expect(FakeRecognition.instances).toHaveLength(2);
  });

  it.each([
    ['no-speech', 'no-speech'],
    ['network', 'network'],
    ['audio-capture', 'other'],
    ['aborted', 'other'],
    ['bad-grammar', 'other'],
  ])('maps the %s error to %s and settles', (raw, kind) => {
    const { speech, onError } = make();
    speech.start();
    FakeRecognition.last.emitResult('two pi', false);
    FakeRecognition.last.emitError(raw);

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith(kind);
    expect(speech.listening).toBe(false);
    expect(speech.interim).toBe('');
  });

  it('does not report an error twice when end follows error', () => {
    const { speech, onError } = make();
    speech.start();
    const rec = FakeRecognition.last;
    rec.emitError('no-speech');
    rec.emitEnd();

    expect(onError).toHaveBeenCalledTimes(1);
    expect(speech.listening).toBe(false);
  });

  it('dispose() aborts in flight recognition and silences every callback', () => {
    const { speech, onResult, onError } = make();
    speech.start();
    const rec = FakeRecognition.last;

    speech.dispose();
    expect(rec.aborts).toBe(1);
    expect(speech.listening).toBe(false);
    expect(speech.interim).toBe('');

    rec.emitResult('too late', true);
    rec.emitError('network');
    rec.emitEnd();
    expect(onResult).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();

    speech.start();
    expect(FakeRecognition.instances).toHaveLength(1);
  });

  it('treats a storage that throws as "not disabled"', () => {
    const storage = hostileStorage();
    const { speech, onError } = make({ storage });
    expect(speech.supported).toBe(true);

    speech.start();
    // Persisting the fatal flag must not escape as an exception either.
    expect(() => FakeRecognition.last.emitError('service-not-allowed')).not.toThrow();
    expect(onError).toHaveBeenCalledWith('service-not-allowed');
    expect(speech.supported).toBe(false);
  });

  // Tapping the mic off and straight back on is one gesture a person really
  // makes. `stop()` only ASKS the engine to stop; its `end` event lands a beat
  // later, by which time a second recogniser may already be listening — and if
  // that late event is allowed to settle the wrapper, it tears down a session
  // that is still running. The button then reads "off" with the mic still open.
  it('ignores the end event of a recogniser that has already been replaced', () => {
    const { speech } = make();
    speech.start();
    const first = FakeRecognition.last;

    speech.stop();
    speech.start();
    const second = FakeRecognition.last;
    expect(second).not.toBe(first);

    first.emitEnd();

    expect(speech.listening).toBe(true);
  });

  // The flip side, and the reason `stop()` does not simply detach: the engine
  // FINALISES on stop rather than discarding, so the words spoken before the
  // tap still arrive. Dropping them would lose an add with no error anywhere.
  it('still adds the words when the mic is tapped off mid-phrase', () => {
    const { speech, onResult } = make();
    speech.start();
    const rec = FakeRecognition.last;

    speech.stop();
    rec.emitResult('coffee', true);

    expect(onResult).toHaveBeenCalledWith('coffee');
  });

  it('still delivers the transcript after a stop-and-restart', () => {
    const { speech, onResult } = make();
    speech.start();
    const first = FakeRecognition.last;

    speech.stop();
    speech.start();
    const second = FakeRecognition.last;
    first.emitEnd();

    second.emitResult('coffee', true);

    expect(onResult).toHaveBeenCalledWith('coffee');
  });

  // A phrase heard and then dropped with no trace is the worst outcome this
  // module can produce, and the handoff runs inside the browser's own event
  // dispatch, where a throw would escape rather than reach anything of ours.
  it('reports an error when the heard phrase cannot be handed on', () => {
    const onResult = vi.fn(() => {
      throw new TypeError('the add path blew up');
    });
    const onError = vi.fn<(kind: SpeechErrorKind) => void>();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const speech = createSpeech({
      onResult,
      onError,
      ctor: FakeRecognition,
      storage: memoryStorage(),
    });

    speech.start();
    expect(() => FakeRecognition.last.emitResult('coffee', true)).not.toThrow();

    expect(onError).toHaveBeenCalledWith('other');
  });

  describe('the fatal verdict expires', () => {
    const DAY = 24 * 60 * 60 * 1000;

    it('is still honoured the next day', () => {
      const storage = memoryStorage();
      const first = createSpeech({
        onResult: vi.fn(),
        ctor: FakeRecognition,
        storage,
        now: () => 0,
      });
      vi.spyOn(console, 'warn').mockImplementation(() => {});
      first.start();
      FakeRecognition.last.emitError('service-not-allowed');

      const later = createSpeech({
        onResult: vi.fn(),
        ctor: FakeRecognition,
        storage,
        now: () => DAY,
      });
      expect(later.supported).toBe(false);
    });

    // Chrome raises `service-not-allowed` for temporary conditions too — the OS
    // mic switched off for the browser, say. One of those must not cost the
    // feature permanently, with no route back but clearing site data.
    it('offers the mic again after a fortnight', () => {
      const storage = memoryStorage();
      const first = createSpeech({
        onResult: vi.fn(),
        ctor: FakeRecognition,
        storage,
        now: () => 0,
      });
      vi.spyOn(console, 'warn').mockImplementation(() => {});
      first.start();
      FakeRecognition.last.emitError('service-not-allowed');

      const later = createSpeech({
        onResult: vi.fn(),
        ctor: FakeRecognition,
        storage,
        now: () => 15 * DAY,
      });
      expect(later.supported).toBe(true);
    });

    it('treats a corrupted verdict as expired rather than as forever', () => {
      const storage = memoryStorage();
      storage.setItem(SPEECH_DISABLED_KEY, 'not-a-number');

      const speech = createSpeech({
        onResult: vi.fn(),
        ctor: FakeRecognition,
        storage,
        now: () => 0,
      });
      expect(speech.supported).toBe(true);
    });
  });

  it('reports a recogniser that throws on start() as an error, not a hang', () => {
    class ThrowingRecognition extends FakeRecognition {
      override start(): void {
        throw new DOMException('already started', 'InvalidStateError');
      }
    }
    const { speech, onError } = make({ ctor: ThrowingRecognition });
    speech.start();

    expect(onError).toHaveBeenCalledWith('other');
    expect(speech.listening).toBe(false);
  });
});
