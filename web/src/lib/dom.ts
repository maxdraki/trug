/**
 * Swallow the single click that a pointer gesture (drag or swipe) leaves in its
 * wake. A pointerup at the end of a drag/swipe is followed by a synthetic click
 * on whatever element the pointer released over; without this the click would
 * toggle the item or trigger its button. We capture the very next click and
 * stop it, then clean the listener up shortly after in case none arrives.
 */
export function swallowNextClick(): void {
  const handler = (e: MouseEvent): void => {
    e.stopPropagation();
    e.preventDefault();
  };
  window.addEventListener('click', handler, { capture: true, once: true });
  setTimeout(() => window.removeEventListener('click', handler, true), 350);
}
