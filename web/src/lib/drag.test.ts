import { describe, it, expect } from 'vitest';
import { targetIndexHysteretic, rowGapShift, aisleReserve } from './drag.svelte';

/**
 * These cover the pure functions the drag controller shares between the live gap
 * preview (every pointer move) and the release commit — so what the list shows
 * as the open slot is provably where the drop lands.
 */

describe('targetIndexHysteretic', () => {
  // Three rows with midlines at 100, 200, 300; dead zone 8px.
  const mids = [100, 200, 300];
  const DZ = 8;

  it('with no committed slot (-1) behaves as a plain midline test', () => {
    expect(targetIndexHysteretic(mids, 90, -1, DZ)).toBe(0); // above row 0
    expect(targetIndexHysteretic(mids, 150, -1, DZ)).toBe(1); // between 0 and 1
    expect(targetIndexHysteretic(mids, 250, -1, DZ)).toBe(2);
    expect(targetIndexHysteretic(mids, 400, -1, DZ)).toBe(3); // append
  });

  it('does NOT flip while the pointer stays within the dead zone of a boundary', () => {
    // Committed at slot 1 (between rows 0 and 1). Jitter around row-1 midline
    // (200) must not advance to slot 2 until the pointer clears 200 + DZ.
    expect(targetIndexHysteretic(mids, 200, 1, DZ)).toBe(1);
    expect(targetIndexHysteretic(mids, 205, 1, DZ)).toBe(1); // inside dead band
    expect(targetIndexHysteretic(mids, 207, 1, DZ)).toBe(1); // still inside (< 208)
  });

  it('flips forward once the pointer clears the midline + dead zone', () => {
    expect(targetIndexHysteretic(mids, 209, 1, DZ)).toBe(2); // past 200 + 8
  });

  it('is sticky on the way back: holds the higher slot within the dead band', () => {
    // Committed at slot 2; retreating toward 200 keeps slot 2 until 200 − DZ.
    expect(targetIndexHysteretic(mids, 200, 2, DZ)).toBe(2);
    expect(targetIndexHysteretic(mids, 193, 2, DZ)).toBe(2); // inside (> 192)
    expect(targetIndexHysteretic(mids, 191, 2, DZ)).toBe(1); // past 200 − 8, flips back
  });

  it('handles an empty aisle (always slot 0)', () => {
    expect(targetIndexHysteretic([], 123, -1, DZ)).toBe(0);
    expect(targetIndexHysteretic([], 123, 0, DZ)).toBe(0);
  });
});

describe('rowGapShift — same aisle', () => {
  const GAP = 50;
  // Aisle rows [r0, r1(dragged), r2, r3]; filtered list (dragged removed) = [r0, r2, r3].
  const drag = { originCategory: 'A', originIndex: 1, targetCategory: 'A' };
  const shift = (rowIndex: number, ti: number) =>
    rowGapShift('A', rowIndex, { ...drag, targetIndex: ti }, GAP);

  it('opens a gap at the top: only rows above the source descend', () => {
    // ti=0 → dragged goes to the front; r0 moves down one slot, r2/r3 unchanged.
    expect(shift(0, 0)).toBe(GAP); // r0
    expect(shift(2, 0)).toBe(0); // r2
    expect(shift(3, 0)).toBe(0); // r3
  });

  it('relocates the hole downward: rows between source and target rise', () => {
    // ti=2 → dragged lands between r2 and r3; r2 rises to fill the source hole.
    expect(shift(0, 2)).toBe(0); // r0
    expect(shift(2, 2)).toBe(-GAP); // r2
    expect(shift(3, 2)).toBe(0); // r3
  });

  it('moves the hole all the way to the end', () => {
    // ti=3 (append) → both r2 and r3 rise to close the source hole.
    expect(shift(0, 3)).toBe(0); // r0
    expect(shift(2, 3)).toBe(-GAP); // r2
    expect(shift(3, 3)).toBe(-GAP); // r3
  });

  it('is a no-op when the drop slot equals the source slot', () => {
    // Filtered target index 1 == the dragged row's original position.
    expect(shift(0, 1)).toBe(0);
    expect(shift(2, 1)).toBe(0);
    expect(shift(3, 1)).toBe(0);
  });
});

describe('rowGapShift — cross aisle', () => {
  const GAP = 40;
  // Source A = [s0, s1(dragged), s2]; target B = [t0, t1].
  const drag = { originCategory: 'A', originIndex: 1, targetCategory: 'B', targetIndex: 1 };

  it('closes the source aisle: rows below the lift rise, rows above stay', () => {
    expect(rowGapShift('A', 0, drag, GAP)).toBe(0); // s0
    expect(rowGapShift('A', 2, drag, GAP)).toBe(-GAP); // s2
  });

  it('opens a gap in the target aisle at the drop index', () => {
    expect(rowGapShift('B', 0, drag, GAP)).toBe(0); // t0 above the slot
    expect(rowGapShift('B', 1, drag, GAP)).toBe(GAP); // t1 at/after the slot descends
  });

  it('opens the gap at the END when appending to the target aisle', () => {
    const append = { ...drag, targetIndex: 2 };
    expect(rowGapShift('B', 0, append, GAP)).toBe(0);
    expect(rowGapShift('B', 1, append, GAP)).toBe(0); // both rows stay; gap is past the last
  });

  it('leaves unrelated aisles untouched', () => {
    expect(rowGapShift('C', 0, drag, GAP)).toBe(0);
    expect(rowGapShift('C', 3, drag, GAP)).toBe(0);
  });
});

describe('cross-aisle displacement respects group boundaries', () => {
  // The reported bug: dragging coffee (Cupboard) into Drinks must displace ONLY
  // Drinks rows to open the gap — never rows in Household or any other aisle.
  // Model a five-aisle list; the drag lifts a row from B and hovers into D.
  const GAP = 60;
  const drag = { originCategory: 'B', originIndex: 1, targetCategory: 'D', targetIndex: 0 };
  const layout: Record<string, number> = { A: 2, B: 3, C: 2, D: 1, E: 2 };

  it('gives a non-zero shift ONLY to rows in the origin (B) and target (D) aisles', () => {
    for (const [cat, n] of Object.entries(layout)) {
      for (let i = 0; i < n; i++) {
        const shift = rowGapShift(cat, i, drag, GAP);
        if (cat === 'B' || cat === 'D') continue; // origin/target may move
        expect(shift, `bystander ${cat}[${i}] must not move`).toBe(0);
      }
    }
  });

  it('opens the gap at the correct index inside the target aisle only', () => {
    // Drop at D index 0 → D's single row descends to reveal the slot above it.
    expect(rowGapShift('D', 0, drag, GAP)).toBe(GAP);
    // Every non-target/origin aisle stays put regardless of index.
    expect(rowGapShift('C', 0, drag, GAP)).toBe(0);
    expect(rowGapShift('C', 1, drag, GAP)).toBe(0);
    expect(rowGapShift('E', 0, drag, GAP)).toBe(0);
    expect(rowGapShift('A', 0, drag, GAP)).toBe(0);
  });

  it('appends into the target end without moving any target rows', () => {
    const append = { ...drag, targetIndex: 1 }; // past D's only row
    expect(rowGapShift('D', 0, append, GAP)).toBe(0);
    expect(rowGapShift('E', 0, append, GAP)).toBe(0);
  });
});

describe('aisleReserve — cross-aisle space reservation', () => {
  const GAP = 60;
  const drag = { originCategory: 'Cupboard', targetCategory: 'Drinks' };

  it('grows the TARGET aisle by one row-height to hold the opening gap', () => {
    expect(aisleReserve('Drinks', drag, GAP)).toBe(GAP);
  });

  it('shrinks the ORIGIN aisle by one row-height to close the vacated slot', () => {
    expect(aisleReserve('Cupboard', drag, GAP)).toBe(-GAP);
  });

  it('leaves every bystander aisle unchanged (net-zero, so nothing else moves)', () => {
    // Target grow (+gap) and origin shrink (−gap) cancel for aisles between/after
    // them, so no aisle other than origin/target reserves any space.
    expect(aisleReserve('Household', drag, GAP)).toBe(0);
    expect(aisleReserve('Bakery', drag, GAP)).toBe(0);
    expect(aisleReserve('Pet', drag, GAP)).toBe(0);
  });

  it('reserves nothing for a same-aisle drag (the hole is relocated in place)', () => {
    const same = { originCategory: 'Drinks', targetCategory: 'Drinks' };
    expect(aisleReserve('Drinks', same, GAP)).toBe(0);
    expect(aisleReserve('Household', same, GAP)).toBe(0);
  });
});
