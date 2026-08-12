// Node 26 ships an experimental global `localStorage` that shadows the one
// jsdom would provide: it resolves to `undefined` unless `--localstorage-file`
// is passed. Install a minimal, spec-shaped, Map-backed replacement so tests
// that persist state behave as they would in a browser.
class MemoryStorage implements Storage {
  #store = new Map<string, string>();
  get length(): number {
    return this.#store.size;
  }
  clear(): void {
    this.#store.clear();
  }
  getItem(key: string): string | null {
    return this.#store.has(key) ? this.#store.get(key)! : null;
  }
  key(index: number): string | null {
    return [...this.#store.keys()][index] ?? null;
  }
  removeItem(key: string): void {
    this.#store.delete(key);
  }
  setItem(key: string, value: string): void {
    this.#store.set(key, String(value));
  }
}

Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  value: new MemoryStorage(),
});

// jsdom does not implement matchMedia; Svelte's motion/reactivity primitives
// query prefers-reduced-motion at import. Provide a spec-shaped default (no
// reduced motion) that individual tests can override or delete.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList,
  });
}

// jsdom implements neither the Web Animations API nor scrollIntoView; Svelte 5
// drives `transition:`/`animate:` directives through `element.animate`, so any
// component that mounts one (e.g. an aisle's row FLIP) throws in jsdom without a
// stub. Provide a no-op animation that reports itself already finished.
if (typeof Element !== 'undefined' && typeof Element.prototype.animate !== 'function') {
  Object.defineProperty(Element.prototype, 'animate', {
    configurable: true,
    writable: true,
    value: () =>
      ({
        cancel() {},
        finish() {},
        play() {},
        pause() {},
        commitStyles() {},
        addEventListener() {},
        removeEventListener() {},
        onfinish: null,
        oncancel: null,
        currentTime: 0,
        startTime: 0,
        playState: 'finished',
        finished: Promise.resolve(),
      }) as unknown as Animation,
  });
}

// Same gap, other end: Svelte asks a node for its running animations before it
// starts an outro (so an interrupted transition can be reversed rather than
// stacked). jsdom implements neither, and the missing `getAnimations` surfaces
// as an unhandled TypeError from inside the transition machinery rather than as
// a test failure — nothing is animating in jsdom, so an empty list is the
// truthful answer.
if (typeof Element !== 'undefined' && typeof Element.prototype.getAnimations !== 'function') {
  Object.defineProperty(Element.prototype, 'getAnimations', {
    configurable: true,
    writable: true,
    value: () => [],
  });
}

// @testing-library/svelte only auto-registers its afterEach cleanup when Vitest
// globals are enabled. This project uses explicit imports, so unmount rendered
// components between tests here to stop the DOM accumulating across cases.
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/svelte';
afterEach(() => cleanup());
