import { describe, it, expect, afterEach, vi } from 'vitest';
import { requestPersistentStorage, storagePersisted } from './storage';

const original = Object.getOwnPropertyDescriptor(globalThis, 'navigator');

function setNavigator(value: unknown): void {
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value });
}

afterEach(() => {
  if (original) Object.defineProperty(globalThis, 'navigator', original);
});

describe('requestPersistentStorage', () => {
  it("returns 'granted' and records it when the browser grants persistence", async () => {
    setNavigator({ storage: { persist: vi.fn().mockResolvedValue(true) } });
    expect(await requestPersistentStorage()).toBe('granted');
    expect(storagePersisted()).toBe('granted');
  });

  it("returns 'denied' when the browser refuses", async () => {
    setNavigator({ storage: { persist: vi.fn().mockResolvedValue(false) } });
    expect(await requestPersistentStorage()).toBe('denied');
    expect(storagePersisted()).toBe('denied');
  });

  it("returns 'denied' when persist throws", async () => {
    setNavigator({ storage: { persist: vi.fn().mockRejectedValue(new Error('nope')) } });
    expect(await requestPersistentStorage()).toBe('denied');
  });

  it("returns 'unsupported' when the Storage Manager API is absent (Safari variations)", async () => {
    setNavigator({});
    expect(await requestPersistentStorage()).toBe('unsupported');
    expect(storagePersisted()).toBe('unsupported');
  });
});
