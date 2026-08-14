import { mount, unmount } from 'svelte';

/**
 * Mount a component with props that behave the way a real parent's do: one
 * signal each, so an effect re-runs only when a prop it actually READS changes.
 *
 * `@testing-library/svelte`'s `rerender` cannot express that. It re-seats the
 * whole props record, so every effect in the component re-runs whatever
 * changed — which quietly turns "refetches when `revision` changes" into
 * "refetches when anything changes", and lets the line that reads `revision`
 * be deleted with the test still green. Reach for this whenever the assertion
 * is about WHICH prop drove a re-run; `render` is fine for everything else.
 *
 * The props object is returned: assign to a field to drive an update, exactly
 * as a parent component would.
 */
export function mountWithProps<P extends Record<string, unknown>>(
  // Svelte's generated component types differ between `mount` and the test
  // helpers; the shape that matters here is only "something `mount` accepts".
  component: unknown,
  initial: P,
): { container: HTMLElement; props: P; unmount: () => void } {
  const props = $state({ ...initial });
  const container = document.createElement('div');
  document.body.appendChild(container);
  const instance = mount(component as Parameters<typeof mount>[0], {
    target: container,
    props: props as Record<string, unknown>,
  });
  return {
    container,
    props: props as P,
    unmount: () => {
      void unmount(instance);
      container.remove();
    },
  };
}
