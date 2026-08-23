/**
 * Pointer events for tests, because jsdom has no `PointerEvent`.
 *
 * A `MouseEvent` already carries the coordinates and the button, so the two
 * fields the app's handlers actually read — `pointerId` and `pointerType` — are
 * pinned on afterwards with `defineProperty` (they are getters on the real
 * interface, so plain assignment does not take).
 *
 * This is only the EVENT BUILDER. The gesture helpers that use it — swipe a row
 * this far and let go, right-click a pill, lift and drag — deliberately stay in
 * the test files that own them: each encodes what its own component reads off
 * the event, and folding them together would hide those differences rather than
 * remove any real duplication.
 *
 * Note what this cannot fake: jsdom does not implement pointer capture either,
 * so code under test must tolerate `setPointerCapture` throwing. Trug's does,
 * and falls back to window listeners — which is why dispatching `pointermove`
 * and `pointerup` at `window` rather than at the element is what a real gesture
 * looks like here.
 */

export interface PointerOptions {
  /** clientX. */
  x?: number;
  /** clientY. */
  y?: number;
  /** `pointerId`, for handlers that track one pointer through a gesture. */
  id?: number;
  /** `pointerType` — the two branches components take are 'touch' and 'mouse'. */
  pointerType?: 'touch' | 'mouse' | 'pen';
  /** Mouse button; 2 is the right-click that opens a context menu. */
  button?: number;
  cancelable?: boolean;
}

/** A pointer event of `type`, shaped the way a real one arrives. */
export function pointerEvent(type: string, opts: PointerOptions = {}): MouseEvent {
  const ev = new MouseEvent(type, {
    bubbles: true,
    cancelable: opts.cancelable ?? true,
    clientX: opts.x ?? 0,
    clientY: opts.y ?? 0,
    button: opts.button ?? 0,
  });
  Object.defineProperty(ev, 'pointerType', { value: opts.pointerType ?? 'touch' });
  Object.defineProperty(ev, 'pointerId', { value: opts.id ?? 1 });
  return ev;
}
