import type { Op } from './types';
import { ApiError } from './api';

const STORE = 'ops';

function promisify<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/** Resolves when the transaction commits; rejects if it errors or aborts. */
function done(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

/**
 * Durable FIFO queue of pending offline ops, backed by IndexedDB.
 * One object store `ops` keyed by an autoincrementing sequence.
 */
export class OpQueue {
  private dbName: string;
  private dbPromise?: Promise<IDBDatabase>;
  private flushing?: Promise<void>;
  /** The replay currently in progress: its snapshot, and how far it has got. */
  private inflight?: { entries: { key: IDBValidKey; op: Op }[]; index: number };

  constructor(dbName: string) {
    this.dbName = dbName;
  }

  /**
   * The open database, opened once and shared.
   *
   * A FAILED open is deliberately not cached. Caching the rejected promise made
   * one transient fault — a blocked upgrade, storage evicted mid-session, a
   * momentarily unavailable database — poison every later `enqueue`, `pending`,
   * `entries`, `remove` and `rewriteItemId` for the life of the page, silently.
   * Clearing the cache costs one extra `indexedDB.open` per failed call and buys
   * self-healing on the next tap.
   */
  private db(): Promise<IDBDatabase> {
    if (!this.dbPromise) {
      const attempt: Promise<IDBDatabase> = new Promise<IDBDatabase>((resolve, reject) => {
        const req = indexedDB.open(this.dbName, 1);
        req.onupgradeneeded = () => {
          req.result.createObjectStore(STORE, { autoIncrement: true });
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      }).catch((err) => {
        // Only drop the cache if it is still ours — a later successful open
        // must never be discarded by a straggling failure.
        if (this.dbPromise === attempt) this.dbPromise = undefined;
        console.error(
          `[trug] could not open the offline queue database "${this.dbName}";` +
            ' changes made now cannot be saved for later. Retrying on the next change.',
          err,
        );
        throw err;
      });
      this.dbPromise = attempt;
    }
    return this.dbPromise;
  }

  async enqueue(op: Op): Promise<void> {
    const db = await this.db();
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).add(op);
    await done(tx);
  }

  /**
   * Repoint every queued op that targets `from` at `to`. Returns the rewritten
   * ops in FIFO order (empty when nothing matched).
   *
   * Called when the server dedups an add onto a row the client had never seen:
   * without this, a `check` or `delete` queued against the optimistic id names
   * an item the server has never heard of, 404s, and is dropped — silently
   * losing a check-off made in the aisle. The 404 drop stays correct for a
   * genuinely deleted item; this only stops the ids going stale underneath it.
   *
   * EVERY op kind is repointed, `delete` included, and that is a deliberate
   * choice with teeth: "add Lemon, change my mind, delete it" made offline,
   * where the add then dedups onto a pre-existing row Y, deletes Y — a row that
   * was on the household list before the shopper touched anything. Previously
   * the delete 404'd and Y survived. Repointing is still the right call because
   * it is exactly what the same two taps do online: the add returns Y (the
   * server reactivates it in place), the shopper's delete lands on Y, Y goes.
   * Declining to repoint would not restore innocence either — the add already
   * executed, so Y is on the list, reactivated, and the shopper is left
   * believing they cancelled it. Consistency with the online path wins; the
   * loss is bounded by the fact that the two ops name the same item.
   *
   * The rewrite is durable (it commits to IndexedDB) *and* patches the snapshot
   * an in-flight replay is iterating, but only from the next op onwards. The op
   * currently executing has already gone to the wire, so rewriting its stored
   * copy could not change what the server sees — and in practice it is always
   * the `add` that triggered this, which carries no itemId anyway.
   */
  async rewriteItemId(from: string, to: string): Promise<Op[]> {
    if (from === to) return [];
    const db = await this.db();
    const tx = db.transaction(STORE, 'readwrite');
    const store = tx.objectStore(STORE);

    // Reads and writes share one transaction so a concurrent dequeue cannot
    // slip between them and have a `put` resurrect an op that just completed.
    // The puts are issued from inside the read's success callback rather than
    // from a promise continuation: a transaction is only guaranteed active
    // during the task that created it and during each request's event dispatch,
    // so writing after an `await` throws `TransactionInactiveError` mid-loop in
    // stricter environments and commits a half-applied rewrite.
    const rewritten: Op[] = [];
    const keysReq = store.getAllKeys();
    const opsReq = store.getAll() as IDBRequest<Op[]>;
    // Requests against one store complete in issue order, so `keysReq` has
    // already resolved by the time this fires.
    opsReq.onsuccess = () => {
      keysReq.result.forEach((key, i) => {
        const op = opsReq.result[i];
        if (op.itemId !== from) return;
        const next = { ...op, itemId: to };
        store.put(next, key);
        rewritten.push(next);
      });
    };
    await done(tx);
    if (rewritten.length === 0) return rewritten;

    const flight = this.inflight;
    if (flight) {
      for (let i = flight.index + 1; i < flight.entries.length; i++) {
        const entry = flight.entries[i];
        if (entry.op.itemId === from) entry.op = { ...entry.op, itemId: to };
      }
    }
    return rewritten;
  }

  /** All queued ops in FIFO (insertion) order. */
  async pending(): Promise<Op[]> {
    const db = await this.db();
    const tx = db.transaction(STORE, 'readonly');
    return promisify(tx.objectStore(STORE).getAll() as IDBRequest<Op[]>);
  }

  private async entries(): Promise<{ key: IDBValidKey; op: Op }[]> {
    const db = await this.db();
    const tx = db.transaction(STORE, 'readonly');
    const store = tx.objectStore(STORE);
    const keys = await promisify(store.getAllKeys());
    const ops = await promisify(store.getAll() as IDBRequest<Op[]>);
    return keys.map((key, i) => ({ key, op: ops[i] }));
  }

  private async remove(key: IDBValidKey): Promise<void> {
    const db = await this.db();
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).delete(key);
    await done(tx);
  }

  /** A label for logs: whichever id the op names, if any. */
  private static target(op: Op): string {
    return op.itemId ?? op.item?.id ?? '(none)';
  }

  /**
   * Dequeue one op, reporting rather than throwing if storage refuses. Returns
   * false when the op could NOT be removed, which stops the replay: continuing
   * past an op we cannot dequeue would re-send it on every future drain.
   */
  private async drop(key: IDBValidKey, op: Op, why: string): Promise<boolean> {
    try {
      await this.remove(key);
      return true;
    } catch (err) {
      console.error(
        `[trug] ${why} the queued ${op.kind} for item ${OpQueue.target(op)}, but could not` +
          ' remove it from the offline queue; the replay is paused so it is not sent twice.',
        err,
      );
      return false;
    }
  }

  /**
   * Replay queued ops in FIFO order via `exec`.
   * - success: op removed.
   * - ApiError 404/409: op dropped (and logged — see below), replay continues.
   * - anything else: stop, leaving that op and the rest queued. That covers a
   *   genuine offline, but equally a request we aborted for hanging or a
   *   TypeError from a bug, so it is logged rather than assumed benign.
   * Concurrent calls coalesce into the in-flight replay.
   */
  flush(exec: (op: Op) => Promise<void>): Promise<void> {
    if (this.flushing) return this.flushing;
    this.flushing = this.run(exec).finally(() => {
      this.flushing = undefined;
    });
    return this.flushing;
  }

  private async run(exec: (op: Op) => Promise<void>): Promise<void> {
    const entries = await this.entries();
    this.inflight = { entries, index: -1 };
    try {
      for (let i = 0; i < entries.length; i++) {
        this.inflight.index = i;
        // Re-read through the array: `rewriteItemId` may have repointed this op
        // while an earlier one was executing.
        const { key, op } = entries[i];
        try {
          await exec(op);
        } catch (err) {
          const remaining = entries.length - i;
          if (err instanceof ApiError && (err.status === 404 || err.status === 409)) {
            // Now that stale ids are repointed rather than left to 404, a drop
            // here is the genuinely lossy case — somebody else deleted the item
            // while you were checking it off in the aisle — so say so.
            console.warn(
              `[trug] dropping queued ${op.kind} for item ${OpQueue.target(op)}:` +
                ` server answered ${err.status}`,
            );
            if (!(await this.drop(key, op, 'dropped'))) return;
            continue;
          }
          if (err instanceof ApiError) {
            // The server was reached and refused: not offline, so no banner
            // fires, and a `clear` carries no item id to light the "queued"
            // chip. Without this line the replay stalls in total silence.
            console.error(
              `[trug] queue stalled on ${op.kind} for item ${OpQueue.target(op)}:` +
                ` server answered ${err.status}. ${remaining} op(s) still queued.`,
              err,
            );
          } else {
            // Usually offline (covered by the banner), but the same branch
            // catches a request aborted for hanging and a plain TypeError from
            // a bug — neither of which anything else would ever mention.
            console.warn(
              `[trug] queue paused on ${op.kind} for item ${OpQueue.target(op)}:` +
                ` the request did not reach the server. ${remaining} op(s) still queued.`,
              err,
            );
          }
          return;
        }
        if (!(await this.drop(key, op, 'sent'))) return;
      }
    } finally {
      this.inflight = undefined;
    }
  }
}
