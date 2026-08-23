# Trug — Animation Reference

Audited against `web/src/lib/motion.ts` @ `main`, August 2026. That file is the source of truth for durations and curves — everything below is read from it and the components that consume it, not from the original design docs, which describe several animations that were later removed on purpose (see **Divergence from design docs** at the bottom, and the dated notes now sitting in the specs themselves).

## System overview

Two duration gates, and they mean different things:

- **`d(ms)`** — gates *travel*: transforms, heights, positions. Returns `0` under `prefers-reduced-motion: reduce`.
- **`fadeDur(ms)`** — gates *fades*: opacity, colour. Shortened (capped at `REDUCED_FADE` = 100ms) rather than zeroed under reduced motion, per WCAG 2.3.3 which explicitly excludes colour/opacity from "motion animation."

All curves are named cubic-béziers defined once in `CURVE`, exposed two ways from the same source so JS and CSS can't drift apart:
- `EASE.<name>` — a JS easing function (Newton-Raphson + bisection solve) for Svelte transitions.
- `EASE_CSS.<name>` — the literal `cubic-bezier(...)` string for inline styles/stylesheets.

Nothing in the system exceeds 400ms, and only one thing (clearing the basket) reaches it.

## Swipe grammar

One rule governs both horizontal swipes: **a committed swipe follows through in the direction it was thrown; a rejected one springs back.** Delete (left) and check-off (right) move identically — the same `swipeCommit` slide off the same edge the thumb chose — because the kinematics are not where the meaning lives. Direction, the colour of the field revealed under the row, and its glyph carry the semantics. This is the iOS Mail grammar: swipe-to-archive follows through exactly as swipe-to-delete does, and nobody mistakes one for the other.

The one asymmetry is *when the mutation fires*. Delete waits until 60% of the slide (it has a five-second undo behind it); check-off fires on release, before the slide, because `lib/hold.svelte.ts` is built on presentation never delaying a mutation — a dropped animation costs an animation, never a check.

## Named durations (`DUR`, ms)

| Name | ms | Curve | Used for |
|---|---|---|---|
| `enter` | 250 | decelerate hard `[0,0,0,1]` | row arriving in a list |
| `exit` | 200 | accelerate `[0.3,0,1,1]` | row leaving in place |
| `expand` | 250 | emphasised decelerate `[0.05,0.7,0.1,1]` | aisle/drawer opening |
| `collapse` | 200 | emphasised accelerate `[0.3,0,0.8,0.15]` | aisle/drawer closing |
| `check` | 150 | standard `[0.2,0,0,1]` | strike-through draw + colour |
| `flip` | 250 | — | FLIP drift as a row changes slot |
| `gap` | 140 | same as `check` | drag-gap parting rows |
| `lift` | 160 | ease | box-shadow easing in on pickup (NOT `d()`-gated — see below) |
| `swipeBack` | 200 | ease-out-quint `[0.22,1,0.36,1]` | uncommitted swipe rubber-banding home |
| `swipeCommit` | 250 | `[0.33,1,0.68,1]` | a committed swipe carrying a row off-edge, in either direction |
| `toastIn` | 300 | `enter` | toast/banner arriving |
| `toastOut` | 200 | `exit` | toast/banner leaving |
| `glow` | 350 | — | one-shot accent glow on ring arrival |
| `clear` | 400 | `[0.4,0.14,0.3,1]` | clearing the basket — the one expressive moment |

Stagger: `STAGGER` = 30ms/index, capped at index 4 (`STAGGER_CAP`) — a ring batch reads as "arrived together" without the 12th item waiting half a second to exist.

## Per-action audit

| Action | Component | Mechanism | Timing |
|---|---|---|---|
| **Add item** (type, voice, tap suggestion/recent) | `ItemRow`, `AisleGroup` | `rise()`: fade + 8px upward rise; aisle unfolds via `slide` if it's new | `enter` 250ms |
| **Fresh-add glint** | `ItemRow` | CSS `::before` — 2px peach left-edge tick, fades out (`forwards`) | 2000ms ease-out, one-shot; JS gate deliberately *not* used (media query only, since `reducedMotion()` isn't reactive) |
| **Check off (tap)** | `ItemRow` | Strike-through: `background-size` 0%→100% on a gradient underline. Chip: `Spring` squash to 0.94 then back. Row then FLIP-drifts into the checked pile. | strike `check` 150ms; squash spring `{stiffness:0.4, damping:0.75}` |
| **Check off (swipe right)** | `ItemRow`, `ListView` | Content follows through to `translateX(100%)` — the mirror of the delete slide — over the basket field it revealed, which stays painted until the row's box goes. The check-off hold is widened from `check` to `swipeCommit` so the row survives its own slide; the mutation still fires on release, not partway through. | `swipeCommit` 250ms |
| **Delete (swipe left / ✕)** | `ItemRow`, `leaveRow()` | Row slides off-edge, then collapses height/padding/margin/border in place. Opacity fully fades by 60% of the way through so the row is gone before its box is. Mutation fires at 60% of the slide. | `swipeCommit` 250ms |
| **Swipe, uncommitted** | `ItemRow` | Rubber-bands back to rest | `swipeBack` 200ms |
| **Ring arrival (SSE)** | `ItemRow`, `AisleGroup` | Staggered `rise()` by index (capped at 5 rows) + one-shot accent glow (`box-shadow`/background keyframe) | `enter` 250ms + `glow` 350ms |
| **Clear checked (batch)** | `ListView` | Staggered cascade exit — the one place 400ms/`clear` curve is used | 400ms |
| **Drag-to-reorder / recategorise** | `drag.svelte.ts`, `AisleGroup` | Hand-rolled controller: one direct-DOM transform write/frame on the captured node (no reactive state touched per pointer sample); other rows part via cached-geometry `translateY` gap-shift; release settles through the list's own FLIP path | `gap` 140ms; lift shadow `lift` 160ms |
| **Row "lifted" elevation** | `ItemRow` | Box-shadow eases in as a row is picked up | 160ms — explicitly *not* routed through `d()`: elevation is treated as tone, not travel, so it still eases (shortened, not cut) under reduced motion |
| **Aisle open/close** | `AisleGroup`, `unfold()`/`fold()` | Height `slide` | `expand` 250ms / `collapse` 200ms, asymmetric curves |
| **Basket drawer open** | `ListView` (`revealDrawer`) | Drawer unfolds; viewport scroll re-aims every frame *while* the drawer grows (not once after) so opening + scrolling read as one gesture; capped to a single jump past ~1.5 viewports of travel | `expand` 250ms + frame-chased scroll |
| **Toast in/out** | `Toast` | `rise()`, 12px | in `toastIn` 300ms (slower — "it has something to say"), out `toastOut` 200ms |
| **Recents grid — press** | `RecentsGrid` | `Spring` scale-dip to 0.96 | `{stiffness:0.45, damping:0.85}` |
| **Recents grid — reorder** | `RecentsGrid`, `settleFlip()` | FLIP, guarded: refuses to animate a box it couldn't measure (hidden `display:none` overflow pills were producing `NaN`/`Infinity` transforms), and caps max travel distance so a pill re-wrapping rows doesn't fly across the whole tray | `flip` 250ms |
| **Empty state** | `EmptyState` | None — static illustration, deliberately still | — |

## Reduced motion — how each type degrades

- **Travel** (`d()`): zeroed. Row heights/positions/transforms snap.
- **Fades** (`fadeDur()`): shortened to ≤100ms, never removed — a row that blinks between two frames gives no sign anything happened.
- **Fresh-add glint**: removed outright (its own `display:none` in the reduced-motion media block), because the global `animation:none` would otherwise strand it at full opacity mid-fade with no `forwards` to complete.
- **Swipe**: no slide to drag a threshold out of — a modest travel commits outright once past `REDUCED_MOTION_TRIGGER_PX`, and the commit happens without the follow-through slide.
- **Lift shadow**: kept (100ms, from `app.css`'s reduced-motion block) — box-shadow is tone, not travel, and a shadow popping in over one frame is a flicker, which reduced-motion users like less than a fast fade.
- Haptics (`navigator.vibrate`) and all state changes are never gated — only the *animation* of the change is.

## Divergence from design docs

The original design docs are not in this repository — they carry the household's own context, so they stay private. Several animations they specify were deliberately walked back, and the reasoning for each is recorded in the header comments of `web/src/lib/motion.ts`. The short version:

- **"Item chip lifts off the add bar/recents grid and flies to its aisle group."** — Not in the current code. `motion.ts` carries a comment explaining the reversal: the flight was measured at ~2373px over 300ms (~220°/s of visual angle, ~7× faster than smooth pursuit can track), so on the single most-repeated gesture in the app, nobody ever saw the item arrive — only a smear. Current model: a departure and an arrival are two separate rows in two separate lists, not one row flying between them.
- **Check-off spec: "strike → squash → *arc* into the basket."** — The arc is gone for the same reason; it's strike → squash → FLIP settle now, no arc.
- **`SQUASH_SPRING` values**: the polish spec called for raising spring energy (higher stiffness, lower damping) for more visible overshoot. Shipped code went the other way — `{stiffness:0.4, damping:0.75}`, firmer and better-damped than an earlier `{0.32, 0.5}`, tuned to "one quick dip and done, with no visible bounce to sit through."
- **Add: "crossfade send/receive between chip and aisle row."** — Superseded by the `rise()` fade-and-lift-in-place model described above.
- **Reduced motion: "disables the animation set entirely."** — Shipped behaviour is finer-grained than the brief's acceptance line: travel is cut, fades are shortened, and the lift shadow keeps a 100ms ease (see the section above).
- **"Everything ≤350ms."** — One deliberate exception: clearing the basket runs 400ms, the single expressive moment of the shop.

Net effect: the shipped motion system is *more conservative and more accessibility-considered* than what was speced, and the reasoning for each rollback lives inline in `motion.ts` alongside the dated notes in the specs.
