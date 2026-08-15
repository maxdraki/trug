import { describe, it, expect, afterEach } from 'vitest';
import { readStored, writeStored } from './safeStorage';

/**
 * Replace `localStorage` with a property whose GETTER throws, which is what a
 * locked-down browser actually does. It is not that `getItem` fails — merely
 * naming `localStorage` raises a SecurityError, so any guard that wraps only
 * the call and not the lookup is no guard at all.
 */
function blockStorage(): void {
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    get() {
      throw new DOMException('The operation is insecure.', 'SecurityError');
    },
  });
}

const real = Object.getOwnPropertyDescriptor(window, 'localStorage')!;

afterEach(() => {
  Object.defineProperty(window, 'localStorage', real);
  localStorage.clear();
});

describe('safeStorage', () => {
  it('reads a stored value', () => {
    localStorage.setItem('trug_probe', 'mocha');
    expect(readStored('trug_probe')).toBe('mocha');
  });

  it('reads null for a key that was never written', () => {
    expect(readStored('trug_probe')).toBeNull();
  });

  it('writes a value', () => {
    writeStored('trug_probe', 'latte');
    expect(localStorage.getItem('trug_probe')).toBe('latte');
  });

  it('removes the key when the value is null', () => {
    localStorage.setItem('trug_probe', 'latte');
    writeStored('trug_probe', null);
    expect(localStorage.getItem('trug_probe')).toBeNull();
  });

  it('reads null instead of throwing when the browser blocks storage', () => {
    blockStorage();
    expect(() => readStored('trug_probe')).not.toThrow();
    expect(readStored('trug_probe')).toBeNull();
  });

  it('drops the write instead of throwing when the browser blocks storage', () => {
    blockStorage();
    expect(() => writeStored('trug_probe', 'latte')).not.toThrow();
  });

  it('drops the removal instead of throwing when the browser blocks storage', () => {
    blockStorage();
    expect(() => writeStored('trug_probe', null)).not.toThrow();
  });
});
