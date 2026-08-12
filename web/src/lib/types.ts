export type ItemStatus = 'active' | 'checked';

export interface Item {
  id: string;
  name: string;
  note: string | null;
  icon: string | null;
  category: string | null;
  status: ItemStatus;
  source: string | null;
  added_by: string | null;
  created_at: string;
  checked_at: string | null;
  /** Drag-to-reorder position within an aisle. Ascending. */
  sort_key: number;
}

export interface CatalogEntry {
  name_norm: string;
  display_name: string;
  icon: string | null;
  category: string | null;
  times_added: number;
}

export interface ListResponse {
  active: Record<string, Item[]>;
  checked: Item[];
}

export type OpKind = 'add' | 'check' | 'uncheck' | 'delete' | 'clear';

export interface Op {
  opId: string;
  kind: OpKind;
  item?: { id: string; name: string; note?: string };
  itemId?: string;
  /**
   * `add` only: the id of the row this op actually put on screen, which is not
   * always `item.id`. When `add()` recognises the name locally it renders THAT
   * existing row and sends a fresh uuid anyway (so the server's reactivate
   * branch runs), so the rendered id — not the sent one — is what queued
   * check-offs name and what reconciliation must clean up. Persisted with the
   * op so a reload still knows which row belongs to it.
   */
  pendingId?: string;
  ts: string;
}
