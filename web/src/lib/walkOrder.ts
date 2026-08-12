/**
 * The client's copy of the default supermarket walk order. The server is the
 * source of truth for grouping semantics; this list drives group ordering in
 * the UI and the manual category picker in no-key mode. Mirrors the server's
 * `DEFAULT_WALK_ORDER` and always ends in the catch-all "Other".
 */
/** Aisle -> line-icon slug (src/lib/icons.ts) for the shelf-label headers. */
export const CATEGORY_ICON: Record<string, string> = {
  'Fruit & Veg': 'carrot',
  Bakery: 'baguette',
  'Meat & Fish': 'meat',
  'Dairy & Eggs': 'egg',
  Cupboard: 'archive',
  // A herb sprig, not the `leaf` used by the produce rows, so the shelf label
  // stays distinct from the items sitting under it.
  'Herbs & Spices': 'leaf-2',
  Frozen: 'snowflake',
  Drinks: 'glass-full',
  Household: 'spray',
  // A single tablet, not the `pills` pair the rows in this aisle carry, so the
  // shelf label stays distinct from the items under it (as `leaf-2` does).
  Medicines: 'pill',
  Pet: 'paw',
  Other: 'shopping-bag',
};

export const WALK_ORDER: string[] = [
  'Fruit & Veg',
  'Bakery',
  'Meat & Fish',
  'Dairy & Eggs',
  'Cupboard',
  'Herbs & Spices',
  'Frozen',
  'Drinks',
  'Household',
  'Medicines',
  'Pet',
  'Other',
];
