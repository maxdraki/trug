import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import { describe, it, expect, vi } from 'vitest';
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
  it('adds the top suggestion on Enter', async () => {
    const onAdd = vi.fn();
    const search = vi.fn(async (_q: string): Promise<CatalogEntry[]> => [entry('Milk'), entry('Millet')]);
    render(AddBar, { search, onAdd });

    const input = screen.getByRole('textbox');
    await fireEvent.input(input, { target: { value: 'mi' } });

    // Wait for the debounced typeahead to populate.
    await waitFor(() => expect(search).toHaveBeenCalled());
    await screen.findByText('Milk');

    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(onAdd).toHaveBeenCalledWith('Milk');
  });

  it('adds the raw text on Enter when there are no suggestions', async () => {
    const onAdd = vi.fn();
    const search = vi.fn(async (): Promise<CatalogEntry[]> => []);
    render(AddBar, { search, onAdd });

    const input = screen.getByRole('textbox');
    await fireEvent.input(input, { target: { value: 'Dragonfruit' } });
    await waitFor(() => expect(search).toHaveBeenCalled());

    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(onAdd).toHaveBeenCalledWith('Dragonfruit');
  });

  it('clears the pending debounce timer on commit so no stray search fires after an add', async () => {
    const onAdd = vi.fn();
    const search = vi.fn(async (): Promise<CatalogEntry[]> => []);
    render(AddBar, { search, onAdd });

    const input = screen.getByRole('textbox');
    await fireEvent.input(input, { target: { value: 'Egg' } });
    // Commit immediately, before the 120ms debounce timer fires.
    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(onAdd).toHaveBeenCalledWith('Egg');

    // Wait past the debounce window; the pending search must not fire.
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(search).not.toHaveBeenCalled();
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
    await fireEvent.input(screen.getByRole('textbox'), { target: { value: 'bana' } });
    await fireEvent.click(screen.getByRole('button', { name: /voice/i }));
    emitResult('coffee');

    await waitFor(() => expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe(''));
  });

  it('shows the words as they are heard', async () => {
    const { create, emitInterim } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    await fireEvent.click(screen.getByRole('button', { name: /voice/i }));
    emitInterim('basmati ri');

    await waitFor(() =>
      expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('basmati ri'),
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
      expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('basmati ri'),
    );
    expect(onQueryChange).not.toHaveBeenCalledWith('basmati ri');
  });

  it('hands the input back to the keyboard when the user starts typing', async () => {
    const { create, speech, emitInterim } = fakeSpeech();
    render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    await fireEvent.click(screen.getByRole('button', { name: /voice/i }));
    emitInterim('basma');
    await fireEvent.input(screen.getByRole('textbox'), { target: { value: 'r' } });

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

    await fireEvent.input(screen.getByRole('textbox'), { target: { value: 'milk' } });

    await waitFor(() => expect(screen.getByRole('status').textContent).toBe(''));
  });

  // The mic is a line glyph, so it answers to WCAG's 3:1 for graphics rather
  // than the 4.5:1 text gets. It needs asserting because it sits on the ONE
  // accent-tinted surface in the app: an accent-derived colour on a wash of the
  // same accent is exactly the pairing that measured 3.01:1 on the pills.
  // Read from the shipped declarations, so retuning the colour re-measures it.
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

    expect(screen.getByRole('textbox')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /voice/i })).toBeNull();
  });

  it('releases the microphone when the bar goes away', async () => {
    const { create, speech } = fakeSpeech();
    const { unmount } = render(AddBar, { search: noSearch, onAdd: vi.fn(), createSpeech: create });

    unmount();
    expect(speech.dispose).toHaveBeenCalled();
  });
});
