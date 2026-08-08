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

it('stops on network error and keeps remaining ops', async () => {
  const q = new OpQueue('t3');
  await q.enqueue(op('check', 'a')); await q.enqueue(op('check', 'b'));
  await q.flush(async () => { throw new TypeError('fetch failed'); });
  expect((await q.pending()).length).toBe(2);
});
