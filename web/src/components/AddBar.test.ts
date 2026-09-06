import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import { describe, it, expect, vi } from 'vitest';
import { tick } from 'svelte';
import AddBar from './AddBar.svelte';
import type { CatalogEntry } from '../lib/types';
import { fakeSpeech } from '../lib/testing/fakeSpeech.svelte';
import {
  contrast,
  declaration,
  FLAVOURS,
  OFFERED_ACCENTS,
  resolveColour,
  themeVars,
} from '../lib/testing/contrast';

function entry(display_name: string): CatalogEntry {
  return { name_norm: display_name.toLowerCase(), display_name, icon: null, category: null, times_added: 1 };
}

const noSearch = () => Promise.resolve<CatalogEntry[]>([]);

describe('AddBar', () => {
  it('adds what was TYPED on Enter, not whatever the typeahead offered', async () => {
    // The reported bug, and the whole reason the selection state below exists.
    // Enter used to commit `suggestions[0]` whether or not the shopper had
    // chosen it. Catalogue search is a substring match (`LIKE %gin%`), so
    // typing "gin" in a household that buys ginger offered Ginger first and
    // Enter took it — and when the offered row was already on the list the
    // server deduped it, so the screen did not change at all and the gin was
    // simply never added.
    const onAdd = vi.fn();
    const search = vi.fn(async (_q: string): Promise<CatalogEntry[]> => [entry('Ginger')]);
    render(AddBar, { search, onAdd });

    const input = screen.getByRole('combobox');
    await fireEvent.input(input, { target: { value: 'gin' } });

    await waitFor(() => expect(search).toHaveBeenCalled());
    await screen.findByText('Ginger');

    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(onAdd).toHaveBeenCalledWith('gin');
  });

  it('adds the raw text on Enter when there are no suggestions', async () => {
    const onAdd = vi.fn();
    const search = vi.fn(async (): Promise<CatalogEntry[]> => []);
    render(AddBar, { search, onAdd });

    const input = screen.getByRole('combobox');
    await fireEvent.input(input, { target: { value: 'Dragonfruit' } });
    await waitFor(() => expect(search).toHaveBeenCalled());

    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(onAdd).toHaveBeenCalledWith('Dragonfruit');
  });

  it('clears the pending debounce timer on commit so no stray search fires after an add', async () => {
    const onAdd = vi.fn();
    const search = vi.fn(async (): Promise<CatalogEntry[]> => []);
    render(AddBar, { search, onAdd });

    const input = screen.getByRole('combobox');
    await fireEvent.input(input, { target: { value: 'Egg' } });
    // Commit immediately, before the 120ms debounce timer fires.
    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(onAdd).toHaveBeenCalledWith('Egg');

    // Wait past the debounce window; the pending search must not fire.
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(search).not.toHaveBeenCalled();
  });
});

// Arrow keys drive the typeahead, and nothing is chosen until the shopper
// chooses it. Focus never leaves the input — that is what a combobox is — so
// the highlight is carried by `aria-activedescendant` rather than by moving
// focus, which is also the only way a screen reader can say what you landed on.
describe('AddBar typeahead selection', () => {
  /** Type `q`, let the debounce settle, and hand back the input. */
  async function typeahead(search: (q: string) => Promise<CatalogEntry[]>, q: string) {
    const onAdd = vi.fn();
    render(AddBar, { search, onAdd });
    const input = screen.getByRole('combobox');
    await fireEvent.input(input, { target: { value: q } });
    await waitFor(() => expect(screen.queryAllByRole('option').length).toBeGreaterThan(0));
    return { input, onAdd };
  }

  const butter = async (): Promise<CatalogEntry[]> => [
    entry('Butter'),
    entry('Butternut Squash'),
    entry('Buttermilk'),
  ];

  it('takes the first suggestion on one press of the down arrow', async () => {
    const { input, onAdd } = await typeahead(butter, 'but');

    await fireEvent.keyDown(input, { key: 'ArrowDown' });
    await fireEvent.keyDown(input, { key: 'Enter' });

    expect(onAdd).toHaveBeenCalledWith('Butter');
  });

  it('walks down the list', async () => {
    const { input, onAdd } = await typeahead(butter, 'but');

    await fireEvent.keyDown(input, { key: 'ArrowDown' });
    await fireEvent.keyDown(input, { key: 'ArrowDown' });
    await fireEvent.keyDown(input, { key: 'Enter' });

    expect(onAdd).toHaveBeenCalledWith('Butternut Squash');
  });

  it('walks back up, and off the top returns what was typed', async () => {
    // The typed text is a rung on the ladder, not a thing you have to Escape
    // back to: arrowing into the list by mistake has to be reversible without
    // losing the word. This is what a browser's own address bar does.
    const { input, onAdd } = await typeahead(butter, 'but');

    await fireEvent.keyDown(input, { key: 'ArrowDown' });
    await fireEvent.keyDown(input, { key: 'ArrowUp' });
    await fireEvent.keyDown(input, { key: 'Enter' });

    expect(onAdd).toHaveBeenCalledWith('but');
  });

  it('wraps from the last suggestion back to what was typed', async () => {
    const { input, onAdd } = await typeahead(butter, 'but');

    for (let i = 0; i < 3; i++) await fireEvent.keyDown(input, { key: 'ArrowDown' });
    await fireEvent.keyDown(input, { key: 'ArrowDown' }); // past the end
    await fireEvent.keyDown(input, { key: 'Enter' });

    expect(onAdd).toHaveBeenCalledWith('but');
  });

  it('wraps up from the typed text to the last suggestion', async () => {
    const { input, onAdd } = await typeahead(butter, 'but');

    await fireEvent.keyDown(input, { key: 'ArrowUp' });
    await fireEvent.keyDown(input, { key: 'Enter' });

    expect(onAdd).toHaveBeenCalledWith('Buttermilk');
  });

  it('says which option is current, for a reader that cannot see the highlight', async () => {
    const { input } = await typeahead(butter, 'but');

    expect(input.getAttribute('aria-expanded')).toBe('true');
    expect(input.getAttribute('aria-activedescendant')).toBeNull();

    await fireEvent.keyDown(input, { key: 'ArrowDown' });

    const options = screen.getAllByRole('option');
    expect(input.getAttribute('aria-activedescendant')).toBe(options[0].id);
    expect(options[0].getAttribute('aria-selected')).toBe('true');
    expect(options[1].getAttribute('aria-selected')).toBe('false');
  });

  it('lets go of the selection when the shopper carries on typing', async () => {
    // The highlighted row belongs to the list that was on screen when it was
    // chosen. Type another letter and the list is a different list — holding
    // index 1 across it would commit whatever happened to land in that slot.
    const search = vi.fn(async (q: string): Promise<CatalogEntry[]> =>
      q === 'but' ? [entry('Butter'), entry('Butternut Squash')] : [entry('Butane')],
    );
    const { input, onAdd } = await typeahead(search, 'but');

    await fireEvent.keyDown(input, { key: 'ArrowDown' });
    await fireEvent.keyDown(input, { key: 'ArrowDown' });
    await fireEvent.input(input, { target: { value: 'buta' } });
    await waitFor(() => expect(screen.getByText('Butane')).toBeTruthy());
    await fireEvent.keyDown(input, { key: 'Enter' });

    expect(onAdd).toHaveBeenCalledWith('buta');
  });

  it('shuts the list on Escape and hands the keys back to the typed text', async () => {
    const { input, onAdd } = await typeahead(butter, 'but');

    await fireEvent.keyDown(input, { key: 'ArrowDown' });
    await fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryAllByRole('option')).toHaveLength(0);
    expect(input.getAttribute('aria-expanded')).toBe('false');

    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(onAdd).toHaveBeenCalledWith('but');
  });

  it('stays shut after Escape, even when a search was still in flight', async () => {
    // Escape clears the list, but `query` is untouched by it — so a response
    // already on its way still matched the freshness check and reopened the
    // dropdown over the keyboard a moment after the shopper dismissed it.
    let release!: (v: CatalogEntry[]) => void;
    const search = vi
      .fn<(q: string) => Promise<CatalogEntry[]>>()
      .mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }))
      .mockImplementation(async () => [entry('Buttermilk')]);
    render(AddBar, { search, onAdd: vi.fn() });
    const input = screen.getByRole('combobox');

    await fireEvent.input(input, { target: { value: 'but' } });
    await waitFor(() => expect(search).toHaveBeenCalledTimes(1));
    await fireEvent.keyDown(input, { key: 'Escape' });

    release([entry('Butter'), entry('Butternut Squash')]);
    // Let the awaiting continuation run AND Svelte flush the render it would
    // cause. Asserting before both is the trap: with the fix reverted the
    // dropdown does come back, just not by the next microtask, so a hasty
    // assertion passes against a DOM that has simply not caught up yet.
    await new Promise((r) => setTimeout(r, 0));
    await tick();
    expect(screen.queryAllByRole('option')).toHaveLength(0);

    // ...and typing asks for them again, so Escape is a dismissal, not a mute.
    await fireEvent.input(input, { target: { value: 'butt' } });
    await waitFor(() => expect(screen.queryAllByRole('option').length).toBeGreaterThan(0));
  });

  it('drops a selection when the search fails under it', async () => {
    // The shopper has arrowed onto a row that is on screen. The request for the
    // NEXT keystroke then fails. Left alone, `active` still pointed at index 1
    // of a list that had just been emptied — so the input advertised an
    // `aria-activedescendant` naming a row no longer in the document, and Enter
    // read from a list that had gone.
    let fail!: (e: Error) => void;
    const search = vi
      .fn<(q: string) => Promise<CatalogEntry[]>>()
      .mockImplementationOnce(async () => [entry('Butter'), entry('Butternut Squash')])
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject; }));
    const onAdd = vi.fn();
    render(AddBar, { search, onAdd });
    const input = screen.getByRole('combobox');

    await fireEvent.input(input, { target: { value: 'but' } });
    await waitFor(() => expect(screen.queryAllByRole('option')).toHaveLength(2));

    // The next keystroke's request is in flight; the OLD list is still up and
    // still arrowable, which is exactly how a live selection outlives its list.
    await fireEvent.input(input, { target: { value: 'butt' } });
    await waitFor(() => expect(search).toHaveBeenCalledTimes(2));
    await fireEvent.keyDown(input, { key: 'ArrowDown' });
    await fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(input.getAttribute('aria-activedescendant')).not.toBeNull();

    fail(new Error('offline'));
    await waitFor(() => expect(screen.queryAllByRole('option')).toHaveLength(0));
    expect(input.getAttribute('aria-activedescendant')).toBeNull();

    // And Enter adds what was typed, not a fragment of a list that has gone.
    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(onAdd).toHaveBeenCalledWith('butt');
  });

  it('still adds a suggestion that is tapped rather than arrowed to', async () => {
    const { onAdd } = await typeahead(butter, 'but');

    await fireEvent.click(screen.getByRole('option', { name: /Buttermilk/ }));
    expect(onAdd).toHaveBeenCalledWith('Buttermilk');
  });
});

describe('AddBar voice input', () => {
  it('offers no mic when the device cannot do speech recognition', () => {
    const { create } = fakeSpeech({ supported: false });
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    expect(screen.queryByRole('button', { name: /voice/i })).toBeNull();
  });

  it('starts listening when the mic is tapped', async () => {
    const { create, speech } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    await fireEvent.click(screen.getByRole('button', { name: /voice/i }));
    expect(speech.start).toHaveBeenCalled();
  });

  it('stops listening when the mic is tapped again', async () => {
    const { create, speech } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    const mic = screen.getByRole('button', { name: /voice/i });
    await fireEvent.click(mic);
    await fireEvent.click(mic);
    expect(speech.stop).toHaveBeenCalled();
  });

  it('adds what was heard', async () => {
    const onAdd = vi.fn();
    const { create, emitResult } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd, createSpeech: create });

    await fireEvent.click(screen.getByRole('button', { name: /voice/i }));
    emitResult('coffee');

    expect(onAdd).toHaveBeenCalledWith('coffee');
  });

  it('leaves the box empty after a voice add, ready for the next one', async () => {
    const { create, emitResult } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    // Type something first — otherwise the box starts empty and the assertion
    // holds however badly the clearing is broken.
    await fireEvent.input(screen.getByRole('combobox'), { target: { value: 'bana' } });
    await fireEvent.click(screen.getByRole('button', { name: /voice/i }));
    emitResult('coffee');

    await waitFor(() => expect((screen.getByRole('combobox') as HTMLInputElement).value).toBe(''));
  });

  it('shows the words as they are heard', async () => {
    const { create, emitInterim } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    await fireEvent.click(screen.getByRole('button', { name: /voice/i }));
    emitInterim('basmati ri');

    await waitFor(() =>
      expect((screen.getByRole('combobox') as HTMLInputElement).value).toBe('basmati ri'),
    );
  });

  it('does not treat the half-heard words as something the user typed', async () => {
    const onQueryChange = vi.fn();
    const { create, emitInterim } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create, onQueryChange });

    await fireEvent.click(screen.getByRole('button', { name: /voice/i }));
    emitInterim('basmati ri');

    // The interim transcript is a display of what the mic heard, not a query —
    // it must not drive the typeahead or the parent's view of the input.
    await waitFor(() =>
      expect((screen.getByRole('combobox') as HTMLInputElement).value).toBe('basmati ri'),
    );
    expect(onQueryChange).not.toHaveBeenCalledWith('basmati ri');
  });

  it('hands the input back to the keyboard when the user starts typing', async () => {
    const { create, speech, emitInterim } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    await fireEvent.click(screen.getByRole('button', { name: /voice/i }));
    emitInterim('basma');
    await fireEvent.input(screen.getByRole('combobox'), { target: { value: 'r' } });

    expect(speech.stop).toHaveBeenCalled();
  });

  it('takes the mic away for good once the browser has refused it', async () => {
    const { create, emitFatal } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    await fireEvent.click(screen.getByRole('button', { name: /voice/i }));
    emitFatal();

    await waitFor(() => expect(screen.queryByRole('button', { name: /voice/i })).toBeNull());
  });

  it('explains itself when the microphone permission is refused', async () => {
    const { create, emitError } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    await fireEvent.click(screen.getByRole('button', { name: /voice/i }));
    emitError('not-allowed');

    expect(await screen.findByText(/microphone/i)).toBeTruthy();
  });

  it('explains the mic disappearing when the browser cannot do it at all', async () => {
    const { create, emitFatal } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    await fireEvent.click(screen.getByRole('button', { name: /voice/i }));
    // The device is ruled out and the button is about to vanish, so saying
    // nothing here reads as a tap that did nothing at all.
    emitFatal();

    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/voice input/i));
  });

  it('owns up when it heard nothing usable', async () => {
    const { create, emitError } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    await fireEvent.click(screen.getByRole('button', { name: /voice/i }));
    // Not always the user's silence: a low-gain Bluetooth mic raises this after
    // they have spoken, and silence would be indistinguishable from a lost add.
    emitError('no-speech');

    await waitFor(() => expect(screen.getByRole('status').textContent).not.toBe(''));
  });

  // The region has to be in the accessibility tree BEFORE the text lands, or a
  // screen reader will not announce it — which is why it is rendered always,
  // empty, rather than conjured up alongside its own message.
  it('keeps the live region in the page while there is nothing to say', () => {
    const { create } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    expect(screen.getByRole('status').textContent).toBe('');
  });

  it('clears a stale hint once the user gets on with typing', async () => {
    const { create, emitError } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    await fireEvent.click(screen.getByRole('button', { name: /voice/i }));
    emitError('not-allowed');
    await waitFor(() => expect(screen.getByRole('status').textContent).not.toBe(''));

    await fireEvent.input(screen.getByRole('combobox'), { target: { value: 'milk' } });

    await waitFor(() => expect(screen.getByRole('status').textContent).toBe(''));
  });

  // The mic is a line glyph, so it answers to WCAG's 3:1 for graphics rather
  // than the 4.5:1 text gets. It needs asserting because it sits on the ONE
  // accent-tinted surface in the app: an accent-derived colour on a wash of the
  // same accent is exactly the pairing that measured 3.01:1 on the pills.
  // Read from the shipped declarations, so retuning the colour re-measures it.
  // The arrowed-to row is a colour decision like any other, and it is the one
  // the eye is tracking while the keyboard drives the list. Measured rather
  // than eyeballed, across every flavour and accent the household can pick.
  describe('highlighted suggestion contrast', () => {
    const file = new URL('./AddBar.svelte', '' + import.meta.url);
    const optFg = declaration(file, '.dropdown .opt', 'color')!;
    const activeBg = declaration(file, '.dropdown .opt.active', 'background')!;
    // The edge colour itself, not the shadow it is used in: reading the custom
    // property means moving or blurring the shadow cannot quietly stop this
    // measuring what it says it measures.
    const activeEdge = declaration(file, '.dropdown .opt.active', '--edge')!;

    for (const flavour of FLAVOURS)
      for (const accent of OFFERED_ACCENTS) {
        it(`keeps the label readable on the highlight in ${flavour} on ${accent}`, () => {
          const vars = themeVars(flavour, accent);
          // Body text, so the full 4.5:1 rather than the 3:1 a glyph gets.
          expect(
            contrast(resolveColour(optFg, vars), resolveColour(activeBg, vars)),
          ).toBeGreaterThanOrEqual(4.5);
        });

        it(`keeps the accent edge visible against the highlight in ${flavour} on ${accent}`, () => {
          // The edge is what separates "arrowed to" from "moused over"; if it
          // sinks into its own background the two states become one.
          const vars = themeVars(flavour, accent);
          expect(
            contrast(resolveColour(activeEdge, vars), resolveColour(activeBg, vars)),
          ).toBeGreaterThanOrEqual(3);
        });
      }
  });

  describe('mic contrast', () => {
    const file = new URL('./AddBar.svelte', '' + import.meta.url);
    const fg = declaration(file, '.mic', 'color')!;
    const bg = declaration(file, 'input', 'background')!;

    // Listening flips the button to a filled accent pill with a base-coloured
    // glyph — the inverse pairing, and the one the eye actually lands on while
    // the mic is open, so it gets measured too rather than assumed.
    const litFg = declaration(file, ".mic[aria-pressed='true']", 'color')!;
    const litBg = declaration(file, ".mic[aria-pressed='true']", 'background')!;
    // Hover was the state that got away: the rest colour was chosen to avoid an
    // accent over its own wash, and hover then went and used exactly that.
    const hoverFg = declaration(file, '.mic:hover', 'color')!;

    for (const flavour of FLAVOURS)
      for (const accent of OFFERED_ACCENTS) {
        it(`clears 3:1 at rest in ${flavour} on ${accent}`, () => {
          const vars = themeVars(flavour, accent);
          expect(contrast(resolveColour(fg, vars), resolveColour(bg, vars))).toBeGreaterThanOrEqual(
            3,
          );
        });

        it(`clears 3:1 on hover in ${flavour} on ${accent}`, () => {
          const vars = themeVars(flavour, accent);
          expect(
            contrast(resolveColour(hoverFg, vars), resolveColour(bg, vars)),
          ).toBeGreaterThanOrEqual(3);
        });

        it(`clears 3:1 while listening in ${flavour} on ${accent}`, () => {
          const vars = themeVars(flavour, accent);
          expect(
            contrast(resolveColour(litFg, vars), resolveColour(litBg, vars)),
          ).toBeGreaterThanOrEqual(3);
        });
      }
  });

  // The add bar is the only way into the list. Voice is an extra, and an extra
  // that fails to construct must not be what stops the list being usable.
  it('still renders the add bar when the recogniser cannot be built at all', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const create = () => {
      throw new TypeError('SpeechRecognition is not a constructor');
    };
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    expect(screen.getByRole('combobox')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /voice/i })).toBeNull();
  });

  it('releases the microphone when the bar goes away', async () => {
    const { create, speech } = fakeSpeech();
    const { unmount } = render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    unmount();
    expect(speech.dispose).toHaveBeenCalled();
  });
});
