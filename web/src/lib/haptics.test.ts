import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  HAPTICS_KEY,
  hapticsEnabled,
  setHapticsEnabled,
  isMachineSource,
  tickOnRemoteAdd,
} from './haptics';

/** A Storage stand-in that throws on every access (Safari private browsing). */
const hostileStorage = {
  getItem() {
    throw new DOMException('denied');
  },
  setItem() {
    throw new DOMException('denied');
  },
  removeItem() {
    throw new DOMException('denied');
  },
  clear() {},
  key: () => null,
  length: 0,
} as unknown as Storage;

describe('haptics preference', () => {
  beforeEach(() => localStorage.clear());

  it('defaults to on when the key is absent', () => {
    expect(hapticsEnabled()).toBe(true);
  });

  it('persists off as the string "off" and reads back false', () => {
    setHapticsEnabled(false);
    expect(localStorage.getItem(HAPTICS_KEY)).toBe('off');
    expect(hapticsEnabled()).toBe(false);
  });

  it('treats any value other than "off" as enabled', () => {
    localStorage.setItem(HAPTICS_KEY, 'nonsense');
    expect(hapticsEnabled()).toBe(true);
  });

  it('turning it back on clears the off state', () => {
    setHapticsEnabled(false);
    setHapticsEnabled(true);
    expect(hapticsEnabled()).toBe(true);
  });

  it('survives storage that throws, defaulting to on', () => {
    expect(() => setHapticsEnabled(false, hostileStorage)).not.toThrow();
    expect(hapticsEnabled(hostileStorage)).toBe(true);
  });
});

describe('isMachineSource', () => {
  it('is true only for ring and mcp', () => {
    expect(isMachineSource('ring')).toBe(true);
    expect(isMachineSource('mcp')).toBe(true);
    expect(isMachineSource('pwa')).toBe(false);
    expect(isMachineSource(null)).toBe(false);
    expect(isMachineSource(undefined)).toBe(false);
    expect(isMachineSource('something-else')).toBe(false);
  });
});

describe('tickOnRemoteAdd', () => {
  beforeEach(() => localStorage.clear());

  it('pulses for 15ms on a ring add', () => {
    const vibrate = vi.fn(() => true);
    expect(tickOnRemoteAdd({ source: 'ring' }, { navigator: { vibrate } })).toBe(true);
    expect(vibrate).toHaveBeenCalledWith(15);
  });

  it('pulses for an mcp add', () => {
    const vibrate = vi.fn(() => true);
    expect(tickOnRemoteAdd({ source: 'mcp' }, { navigator: { vibrate } })).toBe(true);
  });

  it('stays quiet for a human add from the app', () => {
    const vibrate = vi.fn(() => true);
    expect(tickOnRemoteAdd({ source: 'pwa' }, { navigator: { vibrate } })).toBe(false);
    expect(tickOnRemoteAdd({ source: null }, { navigator: { vibrate } })).toBe(false);
    expect(vibrate).not.toHaveBeenCalled();
  });

  it('stays quiet when the preference is off', () => {
    setHapticsEnabled(false);
    const vibrate = vi.fn(() => true);
    expect(tickOnRemoteAdd({ source: 'ring' }, { navigator: { vibrate } })).toBe(false);
    expect(vibrate).not.toHaveBeenCalled();
  });

  it('returns false rather than throwing where vibrate does not exist', () => {
    expect(() => tickOnRemoteAdd({ source: 'ring' }, { navigator: {} as Navigator })).not.toThrow();
    expect(tickOnRemoteAdd({ source: 'ring' }, { navigator: {} as Navigator })).toBe(false);
  });

  it('returns false rather than throwing when vibrate itself throws', () => {
    const vibrate = vi.fn(() => {
      throw new Error('not allowed');
    }) as unknown as Navigator['vibrate'];
    expect(tickOnRemoteAdd({ source: 'ring' }, { navigator: { vibrate } })).toBe(false);
  });

  it('reports the call, not the platform verdict, when vibrate returns false', () => {
    // The W3C API returns false when the UA declines (no sticky activation, a
    // hidden document). The call still happened, and that is what we report —
    // there is nothing the caller could usefully do about the refusal.
    const vibrate = vi.fn(() => false);
    expect(tickOnRemoteAdd({ source: 'ring' }, { navigator: { vibrate } })).toBe(true);
  });
});
