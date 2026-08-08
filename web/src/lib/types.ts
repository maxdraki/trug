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
  ts: string;
}
