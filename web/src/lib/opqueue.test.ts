import 'fake-indexeddb/auto';
import { describe, it, expect, vi } from 'vitest';
import { OpQueue } from './opqueue';
import { ApiError } from './api';

const op = (k: string, id: string) =>
  ({ opId: id, kind: k, itemId: id, ts: '2026-08-05T00:00:00Z' } as any);

it('replays FIFO and clears on success', async () => {
  const q = new OpQueue('t1');
  await q.enqueue(op('check', 'a')); await q.enqueue(op('check', 'b'));
  const seen: string[] = [];
  await q.flush(async (o) => { seen.push(o.itemId!); });
  expect(seen).toEqual(['a', 'b']);
  expect(await q.pending()).toEqual([]);
});

it('drops ops that 404 and continues', async () => {
  const q = new OpQueue('t2');
  await q.enqueue(op('check', 'gone')); await q.enqueue(op('check', 'ok'));
  const seen: string[] = [];
  await q.flush(async (o) => {
    if (o.itemId === 'gone') throw new ApiError(404, 'not found');
    seen.push(o.itemId!);
  });
  expect(seen).toEqual(['ok']);
  expect(await q.pending()).toEqual([]);
});

it('rewriteItemId repoints queued ops durably and reports what it changed', async () => {
  const q = new OpQueue('t4');
  await q.enqueue(op('check', 'opt'));
  await q.enqueue(op('check', 'other'));
  await q.enqueue({ ...op('delete', 'opt'), opId: 'd1' });

  const rewritten = await q.rewriteItemId('opt', 'real');

  expect(rewritten.map((o) => [o.kind, o.itemId])).toEqual([
    ['check', 'real'],
    ['delete', 'real'],
  ]);
  expect((await q.pending()).map((o) => o.itemId)).toEqual(['real', 'other', 'real']);
});

it('rewriteItemId reaches ops the in-flight replay has already read', async () => {
  // run() snapshots the queue before executing, so a rewrite triggered from
  // inside exec (an add reconciling onto a deduped id) must patch that
  // snapshot too — otherwise the very next op still carries the dead id.
  const q = new OpQueue('t5');
  await q.enqueue({ ...op('add', 'opt'), itemId: undefined, item: { id: 'opt', name: 'Bananas' } });
  await q.enqueue(op('check', 'opt'));

  const seen: (string | undefined)[] = [];
  await q.flush(async (o) => {
    if (o.kind === 'add') await q.rewriteItemId('opt', 'real');
    else seen.push(o.itemId);
  });

  expect(seen).toEqual(['real']);
  expect(await q.pending()).toEqual([]);
});

/**
 * Emulate the spec's transaction-activity rules, which fake-indexeddb does not
 * enforce: a transaction is active during the task that created it and during
 * the dispatch of each request's success event, and is deactivated at the
 * microtask checkpoint that follows. Writes issued from a promise continuation
 * (i.e. after an `await`) therefore throw `TransactionInactiveError`, exactly as
 * they do in a real browser under load.
 */
function enforceTransactionActivity(): () => void {
  const original = IDBDatabase.prototype.transaction;
  IDBDatabase.prototype.transaction = function (this: IDBDatabase, ...args: any[]) {
    const tx = original.apply(this, args as any);
    let active = true;
    const deactivateAtCheckpoint = () => queueMicrotask(() => (active = false));
    deactivateAtCheckpoint();
    const realObjectStore = tx.objectStore.bind(tx);
    tx.objectStore = (name: string) => {
      const store = realObjectStore(name);
      const wrap =
        <T extends (...a: any[]) => IDBRequest>(fn: T) =>
        (...a: Parameters<T>): IDBRequest => {
          if (!active) {
            throw new DOMException('transaction is not active', 'TransactionInactiveError');
          }
          const req = fn.apply(store, a);
          // Our listener is registered first, so it re-activates before the
          // caller's own onsuccess runs, then deactivates at the checkpoint.
          req.addEventListener('success', () => {
            active = true;
            deactivateAtCheckpoint();
          });
          return req;
        };
      for (const m of ['get', 'getAll', 'getAllKeys', 'put', 'add', 'delete'] as const) {
        (store as any)[m] = wrap((store as any)[m]);
      }
      return store;
    };
    return tx;
  } as any;
  return () => {
    IDBDatabase.prototype.transaction = original;
  };
}

it('rewriteItemId issues its writes while the transaction is still active', async () => {
  // Reads before the first await but writes after it only works because
  // microtasks drain inside the request's activation task. Under a stricter
  // environment the puts throw mid-loop, committing a PARTIALLY applied
  // rewrite: some ops repointed, others left naming a dead id.
  const q = new OpQueue('t6');
  await q.enqueue(op('check', 'opt'));
  await q.enqueue(op('check', 'other'));
  await q.enqueue({ ...op('delete', 'opt'), opId: 'd1' });

  const restore = enforceTransactionActivity();
  try {
    const rewritten = await q.rewriteItemId('opt', 'real');
    expect(rewritten.map((o) => o.itemId)).toEqual(['real', 'real']);
    expect((await q.pending()).map((o) => o.itemId)).toEqual(['real', 'other', 'real']);
  } finally {
    restore();
  }
});

it('stops on network error and keeps remaining ops', async () => {
  const q = new OpQueue('t3');
  await q.enqueue(op('check', 'a')); await q.enqueue(op('check', 'b'));
  const errors = vi.spyOn(console, 'warn').mockImplementation(() => {});
  try {
    await q.flush(async () => { throw new TypeError('fetch failed'); });
    expect((await q.pending()).length).toBe(2);
    // A stall that is NOT an ApiError used to `return` in total silence, while
    // its doc comment called the case "network/offline" — it is equally a
    // TypeError from a bug, or a request we aborted for hanging.
    const line = errors.mock.calls.map((c) => String(c[0])).find((m) => m.startsWith('[trug]'));
    expect(line).toBeDefined();
    expect(line).toContain('check');
    expect(line).toContain('2 op(s) still queued');
  } finally {
    errors.mockRestore();
  }
});

it('re-attempts a failed database open instead of poisoning the whole session', async () => {
  // One rejected `indexedDB.open` used to be cached for the life of the page,
  // so every later enqueue/pending/flush rejected — silently — until reload.
  const q = new OpQueue('t7');
  const failOnce = vi.spyOn(indexedDB, 'open').mockImplementationOnce(() => {
    const req: any = {
      result: undefined,
      error: new DOMException('open denied', 'InvalidStateError'),
      onsuccess: null,
      onerror: null,
      onupgradeneeded: null,
    };
    setTimeout(() => req.onerror?.(), 0);
    return req;
  });
  const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
  try {
    await expect(q.enqueue(op('check', 'a'))).rejects.toBeInstanceOf(DOMException);
    expect(errors.mock.calls.some((c) => String(c[0]).startsWith('[trug]'))).toBe(true);

    // The next call must re-open rather than replay the cached rejection.
    await q.enqueue(op('check', 'b'));
    expect((await q.pending()).map((o) => o.itemId)).toEqual(['b']);
  } finally {
    failOnce.mockRestore();
    errors.mockRestore();
  }
});

it('reports (and stops on) a dequeue that fails after the op succeeded', async () => {
  // `await this.remove(key)` rejecting used to escape run() entirely and become
  // an unhandled rejection at the caller.
  const q = new OpQueue('t8');
  await q.enqueue(op('check', 'a'));
  await q.enqueue(op('check', 'b'));
  const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
  const realTx = IDBDatabase.prototype.transaction;
  const broken = vi
    .spyOn(IDBDatabase.prototype, 'transaction')
    .mockImplementation(function (this: IDBDatabase, stores: any, mode?: any) {
      // Reads still work; only the dequeue write fails.
      if (mode === 'readwrite') throw new DOMException('storage gone', 'InvalidStateError');
      return realTx.call(this, stores, mode);
    } as any);
  try {
    const seen: string[] = [];
    await expect(q.flush(async (o) => { seen.push(o.itemId!); })).resolves.toBeUndefined();
    expect(seen).toEqual(['a']); // stopped rather than looping on the same op
    expect(errors.mock.calls.some((c) => String(c[0]).startsWith('[trug]'))).toBe(true);
  } finally {
    broken.mockRestore();
    errors.mockRestore();
  }
});
