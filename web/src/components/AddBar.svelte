<script lang="ts" module>
  /* Instance counter for listbox/option ids — see `uid` below. */
  let comboSeq = 0;
</script>

<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import type { CatalogEntry } from '../lib/types';
  import { ICONS } from '../lib/icons';
  import Icon from './Icon.svelte';
  import {
    createSpeech as defaultCreateSpeech,
    type Speech,
    type SpeechErrorKind,
    type SpeechOptions,
  } from '../lib/speech.svelte';

  let {
    search,
    onAdd,
    onQueryChange,
    // Injected so the mic can be driven by a fake in tests. In the app this is
    // the real wrapper, which reports `supported: false` wherever the browser
    // cannot hear — and the button simply isn't rendered.
    createSpeech = defaultCreateSpeech,
  }: {
    search: (q: string) => Promise<CatalogEntry[]>;
    onAdd: (name: string) => void;
    onQueryChange?: (q: string) => void;
    createSpeech?: (opts: SpeechOptions) => Speech;
  } = $props();

  let query = $state('');
  let suggestions = $state<CatalogEntry[]>([]);
  let timer: ReturnType<typeof setTimeout> | undefined;

  /* Which suggestion the arrow keys have landed on, or -1 for "none — Enter
     takes what was typed".

     -1 is the whole fix for the reported bug. Enter used to commit
     `suggestions[0]` whether or not the shopper had chosen it, and catalogue
     search matches substrings (`LIKE %gin%`), so typing "gin" where the
     household buys ginger offered Ginger first and Enter took it. When the
     offered row was already on the list the server deduped it and the screen
     did not change at all: the gin was simply never added, silently. Nothing is
     chosen now until someone chooses it. */
  let active = $state(-1);

  /* The query whose suggestions were dismissed with Escape. A response already
     in flight when Escape lands still matches `query`, so it needs something
     other than the query itself to know it is unwanted. Cleared by the next
     keystroke, because typing is a fresh request for suggestions. */
  let dismissed = '';

  /* Ids for `aria-activedescendant`. Focus never leaves the input — that is
     what makes this a combobox rather than a menu — so the highlight cannot be
     carried by focus, and a reader has no other way to be told what the arrow
     keys just landed on. Counter-based because two add bars on one page would
     otherwise share ids and point a reader at the wrong list. */
  const uid = `trug-ac-${++comboSeq}`;
  let listEl = $state<HTMLElement>();

  /* Keep the highlighted row on screen: the list is capped at 45dvh and
     scrolls, so arrowing past the fold otherwise moves an invisible selection.
     `nearest` scrolls the minimum needed and does nothing when it already
     fits. */
  $effect(() => {
    if (active < 0 || !listEl) return;
    listEl.querySelector(`#${uid}-${active}`)?.scrollIntoView({ block: 'nearest' });
  });

  /**
   * What to say when the mic fails. Every kind speaks, and that is a deliberate
   * reversal of the first draft, which kept two of them quiet.
   *
   * `no-speech` looked like the one case a message would only nag — you didn't
   * say anything, you know you didn't. But the engine also raises it when you
   * DID speak and it heard nothing usable, which is common on a low-gain
   * Bluetooth mic. From the user's seat that is indistinguishable from the
   * transcript being heard and thrown away.
   *
   * `service-not-allowed` retires the button on this device, so silence there
   * would read as the mic vanishing for no reason — this is the installed-web-
   * app-on-iOS case, and the disappearance is exactly the thing needing a word.
   */
  const HINTS: Record<SpeechErrorKind, string> = {
    'not-allowed': 'Microphone blocked — allow it in your browser settings.',
    'service-not-allowed': "This browser can't do voice input.",
    'no-speech': "Didn't catch that.",
    network: 'No connection for voice right now.',
    other: "Voice input didn't work.",
  };

  let hint = $state('');

  /** A recogniser that isn't there: no mic button, nothing to tear down. */
  const NO_SPEECH: Speech = {
    supported: false,
    listening: false,
    interim: '',
    start() {},
    stop() {},
    dispose() {},
  };

  // One recogniser for the life of the bar. `untrack` because it is deliberately
  // built from the prop's INITIAL value — swapping the factory mid-life would
  // strand an open mic — and without it the compiler warns about capturing that
  // value. The catch is the project rule made concrete: this is the only way
  // into the list, so an optional extra must never be what stops it rendering.
  const speech = untrack(() => {
    try {
      return createSpeech({
        onResult: (text: string) => commit(text),
        onError: (kind: SpeechErrorKind) => {
          hint = HINTS[kind];
        },
      });
    } catch (err) {
      console.error('[trug] voice input unavailable; the add bar carries on without it', err);
      return NO_SPEECH;
    }
  });

  onDestroy(() => speech.dispose());

  function toggleVoice() {
    hint = '';
    if (speech.listening) speech.stop();
    else speech.start();
  }

  function onInput(e: Event) {
    // Typing is the user moving on: a stale "microphone blocked" would otherwise
    // sit above the add bar for the rest of the session.
    hint = '';
    // And typing takes the input back from the mic. Without this the box was
    // `readonly` while listening, so an engine that went quiet without ever
    // firing `end` or `error` — a backgrounded tab, the WebKit misbehaviour this
    // module documents — left the ONLY way into the list frozen, with no way out
    // but knowing to tap the mic a second time.
    if (speech.listening) speech.stop();
    query = (e.target as HTMLInputElement).value;
    onQueryChange?.(query);
    clearTimeout(timer);
    const q = query.trim();
    // A new keystroke means a new list; a selection made against the old one
    // would commit whatever happened to land in that slot. It also un-dismisses
    // the dropdown: typing is asking for suggestions again.
    active = -1;
    dismissed = '';
    if (!q) {
      suggestions = [];
      return;
    }
    timer = setTimeout(async () => {
      try {
        const results = await search(q);
        // Ignore stale responses if the query moved on.
        if (query.trim() === q && dismissed !== q) {
          suggestions = results;
          active = -1;
        }
      } catch (err) {
        // Same staleness guard as the success path, and for a sharper reason: a
        // rejection from an ABANDONED query used to tear down the list that a
        // later query had already filled, so the suggestions vanished under a
        // shopper who was mid-choice. Only the current query may clear them.
        if (query.trim() !== q) return;
        suggestions = [];
        // ...and the selection goes with them. Left behind it pointed
        // `aria-activedescendant` at a row no longer in the document, so a
        // reader was told the current option was something that had gone.
        active = -1;
        // Say nothing to the shopper — a dead typeahead degrades to "type the
        // name and press Enter", which is the whole list still working, and a
        // toast per keystroke would be worse than the fault. But leave a trace:
        // without one, a 500, an expired session and a genuinely empty
        // catalogue are the same blank dropdown to anyone trying to diagnose
        // "the suggestions stopped working on my phone".
        console.warn(`[trug] no suggestions for "${q}"; the list itself is unaffected`, err);
      }
    }, 120);
  }

  function commit(name: string) {
    const value = name.trim();
    if (!value) return;
    clearTimeout(timer);
    hint = '';
    onAdd(value);
    query = '';
    suggestions = [];
    active = -1;
    onQueryChange?.('');
  }

  /* Step the highlight through a ring whose FIRST rung is the typed text.
     Arrowing into the list by mistake is then reversible without losing the
     word — off the top or past the bottom you land back on what you typed,
     which is how a browser's own address bar behaves. */
  function move(delta: number) {
    if (!suggestions.length) return;
    const rungs = suggestions.length + 1;
    // Rung 0 is the typed text; rung i is suggestions[i - 1]. Hence the shift
    // in and back out, and the `+ rungs` so a step below zero still wraps.
    const rung = (active + 1 + delta + rungs) % rungs;
    active = rung - 1;
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      move(1);
    } else if (e.key === 'ArrowUp') {
      // Also stops the caret jumping to the start of the input, which would
      // otherwise happen underneath the list.
      e.preventDefault();
      move(-1);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      // The load-bearing line of this whole change, and it is deliberately
      // written to fail SAFE: every torn state — no selection, an index into a
      // list that has since shrunk or been cleared — lands on `undefined` and
      // falls back to what the shopper typed. Do not "simplify" this to
      // `suggestions[active] ?? suggestions[0]`; that is precisely the bug this
      // exists to fix, where Enter took a suggestion nobody had chosen.
      const chosen = active >= 0 ? suggestions[active] : undefined;
      commit(chosen ? chosen.display_name : query);
    } else if (e.key === 'Escape') {
      // Also drop the pending search. Escape used to clear the list while a
      // request was still in flight, and `query` is unchanged by it — so the
      // response passed the staleness guard and reopened the dropdown over the
      // keyboard about 200ms after the shopper had dismissed it.
      clearTimeout(timer);
      dismissed = query.trim();
      suggestions = [];
      active = -1;
    }
  }
</script>

<div class="addbar">
  {#if suggestions.length}
    <!-- Options carry the role directly rather than wrapping a button: a
         listbox's children are its options, and an interactive element inside
         one is not something a reader can describe. Nothing here needs to take
         focus — the input keeps it throughout, and the pointer is handled by
         the click below. -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <ul class="dropdown" role="listbox" id={uid} bind:this={listEl}>
      {#each suggestions as s, i (s.name_norm)}
        <li
          class="opt"
          class:active={i === active}
          role="option"
          id="{uid}-{i}"
          aria-selected={i === active}
          onclick={() => commit(s.display_name)}
        >
          {#if s.icon && s.icon in ICONS}
            <span class="ico" aria-hidden="true"><Icon name={s.icon} size={17} stroke={1.75} /></span>
          {:else if s.icon && !/^[\x20-\x7e]+$/.test(s.icon)}
            <span class="ico" aria-hidden="true">{s.icon}</span>
          {/if}
          <span class="label">{s.display_name}</span>
        </li>
      {/each}
    </ul>
  {/if}
  <!-- The live region is rendered ALWAYS, empty when there is nothing to say.
       A region inserted already carrying its text is usually not announced —
       screen readers report changes within a region that was in the tree before
       the change — so creating it and filling it in one mutation would leave
       the mic's failures visible only to sighted users. -->
  <p class="hint" class:empty={!hint} role="status" aria-live="polite">{hint}</p>
  <div class="field" class:hearing={speech.listening}>
    <input
      type="text"
      value={speech.listening ? speech.interim : query}
      oninput={onInput}
      onkeydown={onKeydown}
      placeholder={speech.listening ? 'Listening…' : 'Add an item…'}
      aria-label="Add an item"
      role="combobox"
      aria-expanded={suggestions.length > 0}
      aria-controls={uid}
      aria-autocomplete="list"
      aria-activedescendant={active >= 0 ? `${uid}-${active}` : undefined}
      autocapitalize="off"
      autocomplete="off"
      class:voice={speech.supported}
    />
    {#if speech.supported}
      <button
        type="button"
        class="mic"
        aria-label="Add by voice"
        aria-pressed={speech.listening}
        onclick={toggleVoice}
      >
        <Icon name="microphone" size={20} stroke={1.75} />
      </button>
    {/if}
  </div>
</div>

<style>
  /* Floating bar: a base-surface object lifted off the mantle column by a
     hairline and a whisper of shadow. Accent is spent here — the add action. */
  .addbar {
    position: relative;
    padding: 8px 12px calc(12px + env(safe-area-inset-bottom));
  }
  /* Accent CTA at rest: a whisper of the accent washed through the pill (bg +
     border tint + placeholder), so the add action reads as the one primary
     surface without shouting. Focus keeps the stronger full-accent border. */
  .field {
    position: relative;
    display: flex;
    align-items: center;
  }
  input {
    width: 100%;
    box-sizing: border-box;
    padding: 13px 16px;
    font-size: 16px;
    border-radius: var(--radius);
    border: var(--hairline);
    border-color: color-mix(in srgb, var(--accent) 30%, var(--ctp-surface0));
    background: color-mix(in srgb, var(--accent) 10%, var(--ctp-base));
    color: var(--ctp-text);
    box-shadow: var(--shadow-2);
    transition: border-color 120ms ease;
  }
  input::placeholder {
    color: color-mix(in srgb, var(--accent) 40%, var(--ctp-subtext0));
  }
  input:focus {
    outline: none;
    border-color: var(--accent);
  }
  .dropdown {
    position: absolute;
    left: 12px;
    right: 12px;
    bottom: calc(100% - 2px);
    margin: 0 0 8px;
    padding: 4px;
    list-style: none;
    /* dvh (not vh) so this shrinks with the keyboard-reduced viewport instead
       of a fixed fraction of the full device height — otherwise the list can
       ask for more room above the input than the keyboard has left it. */
    max-height: 45dvh;
    overflow-y: auto;
    background: var(--ctp-base);
    border: var(--hairline);
    border-radius: var(--radius);
    box-shadow: var(--shadow-2);
  }
  .dropdown li + li {
    border-top: var(--hairline);
  }
  .dropdown .opt {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 11px 12px;
    border-radius: calc(var(--radius) - 2px);
    color: var(--ctp-text);
    font-size: 15px;
    text-align: left;
    cursor: pointer;
  }
  .dropdown .opt:hover {
    background: var(--ctp-mantle);
  }
  /* The arrowed-to row. A surface step alone reads as hover, so the edge says
     "this is the one Enter takes" — the same 2px tick a freshly-added row
     wears.

     The edge is not the raw accent, which measured 1.70:1 against this surface
     in Latte on yellow: the light accents on a light surface vanish into it,
     exactly as the mic's hover state did. Mixing toward `--ctp-text` is
     adaptive — it darkens in Latte and lightens in Mocha — and at 40% it holds
     3.29:1 at its worst (Latte on pink) while still reading as the accent
     rather than as a plain rule. Tests below pin both this and the label. */
  /* `--edge` is named so the contrast test can read the colour itself rather
     than carve it back out of the shadow — moving or blurring the shadow then
     cannot quietly stop the measurement measuring what it claims to. The
     comment sits ABOVE the rule, not inside it: the test helper finds a
     declaration by looking for `^` or `;` before the property, and a comment
     between the two hides it (which is how this was caught). */
  .dropdown .opt.active {
    --edge: color-mix(in srgb, var(--accent) 40%, var(--ctp-text));
    background: var(--ctp-surface0);
    box-shadow: inset 2px 0 0 var(--edge);
  }
  .ico {
    display: inline-flex;
    color: var(--ctp-subtext1);
    font-size: 18px;
    line-height: 1;
  }

  /* Voice input. The mic sits inside the pill rather than beside it so the add
     bar stays one object; the input reserves room for it only when it's there,
     which is why the padding rides on a class and not on `input` itself. */
  input.voice {
    padding-right: 52px;
  }
  /* The mic's rest colour is a neutral, NOT an accent tint. It sits on the one
     accent-washed surface in the app, and an accent over a wash of itself is
     the pairing that has already shipped a contrast regression once: in Latte,
     peach, pink and yellow land under 3:1 against this very background. The
     accent is spent on hover and on the listening state instead, where the fill
     flips and carries the meaning. */
  .mic {
    position: absolute;
    right: 4px;
    /* 44px square: the tap-target floor, and the reason the button is inset
       into the pill's padding rather than sized to the icon. */
    width: 44px;
    height: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: none;
    border-radius: 50%;
    background: none;
    color: var(--ctp-subtext0);
    cursor: pointer;
  }
  /* Hover leans toward the accent WITHOUT reaching it. The bare accent over its
     own 10% wash is the same trap the rest state avoids: in Latte it measures
     2.1–2.7:1, worse than the neutral it replaces, so hovering would make the
     glyph harder to see. Mixed toward `--ctp-text` it still reads as a hue
     shift and stays legible. Measured in AddBar.test.ts. */
  .mic:hover {
    color: color-mix(in srgb, var(--accent) 65%, var(--ctp-text));
  }
  /* Listening: a wash of the accent, not a solid fill of it. A solid accent
     with a base-coloured glyph measures 2.3:1 in Latte on yellow and pink —
     Latte's accents are light pastels and its base is nearly white, so the
     inverse pairing collapses. A 25% wash under `--ctp-text` clears 4.5:1 in
     every flavour on every accent, and reads calmer besides. */
  .mic[aria-pressed='true'] {
    color: var(--ctp-text);
    background: color-mix(in srgb, var(--accent) 25%, var(--ctp-base));
    animation: hearing 1.4s ease-in-out infinite;
  }
  /* A slow breath, not a flash: the only job is to say the mic is still open.
     Scale stays under 1.06 so it never nudges the pill's layout. */
  @keyframes hearing {
    0%,
    100% {
      transform: scale(1);
      box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent) 45%, transparent);
    }
    50% {
      transform: scale(1.06);
      box-shadow: 0 0 0 6px color-mix(in srgb, var(--accent) 0%, transparent);
    }
  }
  /* Substitute rather than strip: the accent fill alone still says "listening",
     which is the whole message the pulse was carrying. */
  @media (prefers-reduced-motion: reduce) {
    .mic[aria-pressed='true'] {
      animation: none;
    }
  }
  .field.hearing input {
    border-color: var(--accent);
  }
  .hint {
    margin: 0 4px 8px;
    font-size: 13px;
    line-height: 1.3;
    color: var(--ctp-subtext0);
  }
  /* Collapsed, NOT hidden. `display: none` and `visibility: hidden` both take
     the element out of the accessibility tree, which is the one thing a live
     region must never leave. An empty block generates no line box, so dropping
     the margin is enough to make it occupy nothing. */
  .hint.empty {
    margin: 0;
  }
</style>
