import type { Op } from './types';
import { ApiError } from './api';

const STORE = 'ops';

function promisify<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
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

  constructor(dbName: string) {
    this.dbName = dbName;
  }

  private db(): Promise<IDBDatabase> {
    if (!this.dbPromise) {
      this.dbPromise = new Promise((resolve, reject) => {
        const req = indexedDB.open(this.dbName, 1);
        req.onupgradeneeded = () => {
          req.result.createObjectStore(STORE, { autoIncrement: true });
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
    }
    return this.dbPromise;
  }

  async enqueue(op: Op): Promise<void> {
    const db = await this.db();
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).add(op);
    await new Promise<void>((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
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
    await new Promise<void>((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  }

  /**
   * Replay queued ops in FIFO order via `exec`.
   * - success: op removed.
   * - ApiError 404/409: op dropped silently, replay continues.
   * - anything else (network/offline): stop, leaving that op and the rest queued.
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
    for (const { key, op } of await this.entries()) {
      try {
        await exec(op);
      } catch (err) {
        if (err instanceof ApiError && (err.status === 404 || err.status === 409)) {
          await this.remove(key);
          continue;
        }
        return;
      }
      await this.remove(key);
    }
  }
}
