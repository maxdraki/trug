import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { connectEvents, shouldProbeAuth } from './sse';

type Listener = (e: MessageEvent) => void;

/** Minimal EventSource stand-in that lets tests drive open/error/message. */
class MockEventSource {
  static instances: MockEventSource[] = [];

  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  listeners = new Map<string, Listener[]>();

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(name: string, fn: Listener): void {
    const arr = this.listeners.get(name) ?? [];
    arr.push(fn);
    this.listeners.set(name, arr);
  }

  close(): void {
    this.closed = true;
  }

  // --- test drivers ---
  emitOpen(): void {
    this.onopen?.();
  }

  emitError(): void {
    this.onerror?.();
  }

  emit(name: string, data: unknown): void {
    for (const fn of this.listeners.get(name) ?? []) {
      fn({ data: JSON.stringify(data) } as MessageEvent);
    }
  }

  static get last(): MockEventSource {
    return MockEventSource.instances[MockEventSource.instances.length - 1];
  }
}

describe('connectEvents', () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal('EventSource', MockEventSource as unknown as typeof EventSource);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('opens /api/events with the token and registers the four listeners', () => {
    const onEvent = vi.fn();
    connectEvents(onEvent, () => 'tok-1');

    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.last.url).toBe('/api/events?token=tok-1');
    expect([...MockEventSource.last.listeners.keys()].sort()).toEqual([
      'item_added',
      'item_removed',
      'item_updated',
      'list_cleared',
    ]);
  });

  it('parses frames and forwards (name, data) to onEvent', () => {
    const onEvent = vi.fn();
    connectEvents(onEvent, () => 'tok-1');

    MockEventSource.last.emit('item_added', { id: 'x', name: 'Milk' });
    expect(onEvent).toHaveBeenCalledWith('item_added', { id: 'x', name: 'Milk' });
  });

  it('swallows malformed JSON frames without throwing', () => {
    const onEvent = vi.fn();
    connectEvents(onEvent, () => 'tok-1');
    const es = MockEventSource.last;
    // Bypass emit()'s JSON.stringify to deliver a bad payload.
    es.listeners.get('item_added')![0]({ data: '{not json' } as MessageEvent);
    expect(onEvent).not.toHaveBeenCalled();
  });

  it('fires onConnect on (re)connect', () => {
    const onConnect = vi.fn();
    connectEvents(vi.fn(), () => 'tok-1', onConnect);
    MockEventSource.last.emitOpen();
    expect(onConnect).toHaveBeenCalledTimes(1);
  });

  it('reconnects with exponential backoff capped at 30s', () => {
    connectEvents(vi.fn(), () => 'tok-1');
    expect(MockEventSource.instances).toHaveLength(1);

    const expected = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000];
    for (let i = 0; i < expected.length; i++) {
      MockEventSource.last.emitError();
      expect(MockEventSource.last.closed).toBe(true);
      // Not yet reconnected before the delay elapses.
      const count = MockEventSource.instances.length;
      vi.advanceTimersByTime(expected[i] - 1);
      expect(MockEventSource.instances).toHaveLength(count);
      vi.advanceTimersByTime(1);
      expect(MockEventSource.instances).toHaveLength(count + 1);
    }
  });

  it('resets the backoff after a successful open', () => {
    connectEvents(vi.fn(), () => 'tok-1');

    MockEventSource.last.emitError();
    vi.advanceTimersByTime(1_000); // first reconnect
    MockEventSource.last.emitOpen(); // healthy again -> resets ladder

    const count = MockEventSource.instances.length;
    MockEventSource.last.emitError();
    vi.advanceTimersByTime(1_000); // back to the 1s rung
    expect(MockEventSource.instances).toHaveLength(count + 1);
  });

  it('polls at a fixed 1s while no token, without advancing the backoff ladder', () => {
    let token: string | null = null;
    connectEvents(vi.fn(), () => token);

    // No token yet: no EventSource is opened, and each poll is a flat 1s apart.
    expect(MockEventSource.instances).toHaveLength(0);
    for (let i = 0; i < 5; i++) {
      vi.advanceTimersByTime(999);
      expect(MockEventSource.instances).toHaveLength(0);
      vi.advanceTimersByTime(1);
    }

    // Token pasted: the very next poll connects on the still-1s cadence rather
    // than a backed-off delay, and the ladder is fresh for the first real error.
    token = 'tok-1';
    vi.advanceTimersByTime(1_000);
    expect(MockEventSource.instances).toHaveLength(1);

    MockEventSource.last.emitError();
    const count = MockEventSource.instances.length;
    vi.advanceTimersByTime(1_000); // first real reconnect is the 1s rung
    expect(MockEventSource.instances).toHaveLength(count + 1);
  });

  it('probes auth after a run of failures and stops + reloads on confirmed loss', async () => {
    const onAuthLost = vi.fn();
    // checkStillAuthed resolves false => confirmed auth loss.
    const checkStillAuthed = vi.fn().mockResolvedValue(false);
    connectEvents(vi.fn(), () => 'tok-1', undefined, undefined, {
      checkStillAuthed,
      onAuthLost,
      probeThreshold: 3,
    });

    // Two failures: below threshold, no probe, keeps retrying.
    MockEventSource.last.emitError();
    vi.advanceTimersByTime(1_000);
    MockEventSource.last.emitError();
    vi.advanceTimersByTime(2_000);
    expect(checkStillAuthed).not.toHaveBeenCalled();

    // Third failure hits the threshold → probe fires.
    MockEventSource.last.emitError();
    expect(checkStillAuthed).toHaveBeenCalledTimes(1);

    // Let the probe promise resolve (flush microtasks) → confirmed loss.
    await vi.advanceTimersByTimeAsync(0);
    expect(onAuthLost).toHaveBeenCalledTimes(1);

    // Retrying has stopped: no new EventSource opens afterwards.
    const count = MockEventSource.instances.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(MockEventSource.instances).toHaveLength(count);
  });

  it('keeps retrying when the auth probe is inconclusive (network blip)', async () => {
    const onAuthLost = vi.fn();
    const checkStillAuthed = vi.fn().mockResolvedValue(true); // still authed
    connectEvents(vi.fn(), () => 'tok-1', undefined, undefined, {
      checkStillAuthed,
      onAuthLost,
      probeThreshold: 1,
    });

    MockEventSource.last.emitError(); // first failure meets threshold=1
    expect(checkStillAuthed).toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(0); // flush the probe promise
    expect(onAuthLost).not.toHaveBeenCalled();

    // Backoff resumes: a reconnect is scheduled after the probe says "still authed".
    const count = MockEventSource.instances.length;
    await vi.advanceTimersByTimeAsync(1_000);
    expect(MockEventSource.instances.length).toBeGreaterThan(count);
  });

  it('disconnect() closes the stream and cancels pending retries', () => {
    const disconnect = connectEvents(vi.fn(), () => 'tok-1');
    const es = MockEventSource.last;

    es.emitError(); // schedules a retry
    disconnect();
    expect(es.closed).toBe(true);

    const count = MockEventSource.instances.length;
    vi.advanceTimersByTime(60_000);
    expect(MockEventSource.instances).toHaveLength(count); // no reconnect
  });
});

describe('shouldProbeAuth', () => {
  it('is false below the threshold and true at/above it', () => {
    expect(shouldProbeAuth(0, 3)).toBe(false);
    expect(shouldProbeAuth(2, 3)).toBe(false);
    expect(shouldProbeAuth(3, 3)).toBe(true);
    expect(shouldProbeAuth(9, 3)).toBe(true);
  });

  it('defaults to a threshold of 3', () => {
    expect(shouldProbeAuth(2)).toBe(false);
    expect(shouldProbeAuth(3)).toBe(true);
  });
});
