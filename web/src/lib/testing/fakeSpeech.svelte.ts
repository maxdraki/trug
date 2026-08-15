import { vi } from 'vitest';
import type { Speech, SpeechErrorKind } from '../speech.svelte';

/**
 * Test-only stand-in for the speech recogniser wrapper.
 *
 * It lives in a `.svelte.ts` module for the same reason `movingStore` does:
 * `listening` and `interim` have to be `$state`. The real wrapper backs them
 * with runes, so a component that renders the live transcript re-renders as the
 * words arrive. Behind a plain closure variable it would not, and the test that
 * says "the words appear as you speak" would pass against a DOM that never
 * updated — which is to say it would pass with the feature deleted.
 *
 * `emitResult`/`emitError` drive the callbacks the component registered at
 * construction, so the wiring under test is the real wiring.
 */
export interface FakeSpeech {
  speech: Speech;
  /** Pass as the `createSpeech` prop; captures the component's callbacks. */
  create: (opts: {
    onResult: (text: string) => void;
    onError?: (kind: SpeechErrorKind) => void;
  }) => Speech;
  emitInterim(text: string): void;
  emitResult(text: string): void;
  emitError(kind: SpeechErrorKind): void;
  /**
   * The device turning out to be deaf mid-session: the real wrapper flips
   * `supported` to false on `service-not-allowed` and the button goes for good.
   * `supported` is inside the reactive object for the same reason the other two
   * are — a component that reads it once into a local would keep rendering a
   * button that can never work, and only a reactive fake catches that.
   */
  emitFatal(): void;
}

export function fakeSpeech(overrides: { supported?: boolean } = {}): FakeSpeech {
  const live = $state({
    listening: false,
    interim: '',
    supported: overrides.supported ?? true,
  });

  let onResult: (text: string) => void = () => {};
  let onError: (kind: SpeechErrorKind) => void = () => {};

  const speech: Speech = {
    get supported() {
      return live.supported;
    },
    get listening() {
      return live.listening;
    },
    get interim() {
      return live.interim;
    },
    start: vi.fn(() => {
      live.listening = true;
    }),
    stop: vi.fn(() => {
      live.listening = false;
      live.interim = '';
    }),
    dispose: vi.fn(),
  };

  return {
    speech,
    create: (opts) => {
      onResult = opts.onResult;
      if (opts.onError) onError = opts.onError;
      return speech;
    },
    emitInterim(text: string) {
      live.listening = true;
      live.interim = text;
    },
    emitResult(text: string) {
      live.listening = false;
      live.interim = '';
      onResult(text);
    },
    emitError(kind: SpeechErrorKind) {
      live.listening = false;
      live.interim = '';
      onError(kind);
    },
    emitFatal() {
      live.listening = false;
      live.interim = '';
      live.supported = false;
      onError('service-not-allowed');
    },
  };
}
