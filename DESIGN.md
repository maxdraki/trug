# Trug — Design System

A shared household shopping list that feels like a considered modernist object, not a CRUD demo. One screen, one job: make Saturday's shop faster.

## Identity

- **Name:** trug (lowercase in the wordmark). A trug is a Sussex garden basket.
- **Aesthetic:** minimalist modernist. Cool, precise, quiet. Personality comes from typography, spacing discipline, and one signature motif — the basket — not from decoration.
- **Story:** the list is the shelf; checking off puts things in the basket. Aisle groups are shelves you walk past in store order.

## Color

Palette is **Catppuccin**, used as a strict surface ladder. Dark default is Mocha; light is Latte (follows system preference; Frappé and Macchiato selectable). Never invent greys — every color is a Catppuccin token.

Mocha values (Latte equivalents mirror the same roles):

- `crust #11111b` — page background (the gutter around the app column)
- `mantle #181825` — app column background
- `base #1e1e2e` — cards / "shelves", sheets, the add pill
- `surface0 #313244` — hairline rules, input borders, icon chip squares
- `text #cdd6f4` — primary text and line icons
- `subtext0 #a6adc8` — notes, labels, metadata, checked items
- `peach #fab387` — the **default** accent (`--accent`), user-swappable in settings. Every accent-carrying element resolves `var(--accent)`, never the literal peach token, so changing the accent repaints them all in one step.
- `lavender #b4befe` — monogram fallback chips only (never the accent — a monogram is a "no icon" state, not an "outstanding" one).

**Accent scope (variant B).** The accent means *outstanding — still to get*; monochrome ink means *done*. It is spent on:

- the primary add action and the ring/basket moment (arrival glow, ring toast) — the original two;
- **active** item line-icon chip glyphs (the chip square stays ink; only the stroke takes the accent);
- aisle shelf-label icons (`.cat-ico`) and the "in the basket" header basket;
- recents-grid line-icon chips ("things you'll likely need" — same family).

Checked ("in the basket") item chips drop back to monochrome ink — accent = still to get, ink = got it. Ring-badge wands, edit pencils, the settings gear and remove ✕ stay ink (they are controls, not shopping state). Never hardcode a hex or `--ctp-peach` in a component; the accent is always `var(--accent)`.

Separation comes from surface steps and 1px hairlines, not shadows. Shadows, where used at all, are near-imperceptible.

## Logo

The signature mark is a stroke-drawn garden basket with a check cutting across the rim. **Source of truth:** `/icon/trug-logo.svg` (viewBox 512, brand green `#326850`, stroke-width 40).

- In-app it lives as an inline Svelte component, `src/lib/Logo.svelte`, drawn with `stroke="currentColor"` (no baked colour) and per-instance-namespaced mask/clip ids so two logos in the DOM never collide. It appears at **accent** colour in the header (before the wordmark, ~24px) and on the token gate (~56px), and as a **quiet ink** mark (`overlay0`) on the empty shelf.
- **Favicon** (`public/favicon.svg`) and the **PWA app icons** (`public/icons/*.png`) use the brand green `#326850` directly (hardcoded hex is correct in static brand assets — they are not theme-aware). App icons are the "Orchard" colourway: a cream mark (Catppuccin Latte base `#eff1f5`) on the brand-green field `#326850`; maskable variants pad the mark into the ~80% safe zone.
- App icons are regenerated from the source SVG by `web/scripts/gen-app-icons.mjs` (sharp, `npm run gen-app-icons`) — a committed, repeatable rasterise step, so the raster icons can never drift from the mark.

## Typography

- **Display: Space Grotesk** — wordmark, aisle shelf labels, counts, empty state. Tight tracking; lowercase wordmark "trug". Used with restraint — it is the voice, not the wallpaper.
- **Body: Inter** — item names (15–16px medium), notes (13px regular, subtext0), controls. Tabular numerals for anything counted.
- Aisle headers are shelf labels: Space Grotesk, 11–12px, uppercase, +6% letterspacing, subtext0, sitting on a hairline "shelf edge" rule.
- Both faces bundled locally (offline PWA — no font CDNs).

## Iconography

- **Tabler Icons** line icons, 2px stroke, `currentColor`, on a 24px grid. Item chip glyphs follow the accent scope above — accent on active rows, monochrome ink once checked. Non-shopping chrome icons (gear, pencil, ✕, ring wand) stay ink.
- Items resolve icons from a small shared vocabulary (~60–100 slugs, e.g. `milk`, `bread`, `egg`, `carrot`, `fish`, `paw`): many-to-one mapping is intentional — coherence over uniqueness.
- Unknown items get a lavender monogram chip (first letter) — an intentional fallback, not a missing image.
- Aisle labels carry a small matching line icon (accent). The trug logo (see **Logo**) is the signature mark: header, token gate, and empty shelf. The checked pile ("In the basket") keeps the Tabler basket line icon at accent.
- Emoji never appear in UI chrome; at most one 🪄 inside toast prose.

## Spacing, layout, shape

- 4px base scale (4/8/12/16/24/32). Content column max-width 560px, centered; crust gutter beyond it.
- Radius: 8px on cards, pills, and chips — one radius everywhere.
- Rows: 52–56px tall, icon chip (34px square, surface0, line icon inside) → name/note stack → quiet actions. Rows separated by hairlines, not gaps.
- The add bar is a floating pill, bottom-pinned at thumb reach, elevated one surface step above the column.
- Recents grid: icon-first chips, label beneath, same chip language as rows.

## Motion

Named cubic-bézier curves plus two small springs, nothing linear; precision over bounce (the check-off squash is one quick dip, no visible overshoot). Nothing exceeds 400ms and only clearing the basket reaches it. Every duration is gated on `prefers-reduced-motion`, and the gate is finer than "instant": travel is cut to zero, but fades are shortened to ≤100ms rather than removed — WCAG 2.3.3 excludes opacity and colour from motion, and a row that blinks between two frames gives no sign anything happened — and haptics are never gated. `web/src/lib/motion.ts` is the source of truth; the full audit is [`docs/animations.md`](docs/animations.md).

- Add: the row fades up in place over an 8px rise; the shelf eases open to receive it. (It flew from the pill to its shelf once — measured at ~7× faster than smooth-pursuit eye tracking, so nobody ever saw it arrive.)
- Check-off: strike draws left→right, subtle squash, the row settles into "In the basket".
- Swipe: a committed swipe follows through off the edge it was thrown at — check-off (right) and delete (left) move identically, because direction, the revealed field's colour and its glyph carry the meaning, not the kinematics. A rejected swipe springs back.
- Ring arrivals: staggered entrance with a brief peach glow + toast — the one allowed flourish.
- Clear: staggered sweep with a 5s undo — the one expressive moment, and the only 400ms.

## Voice

Plain, lowercase-leaning, zero filler. Buttons say what they do ("Clear checked", "Undo"). Empty state: quiet type + one line-icon basket — "Shelf's empty. Add the first thing." Errors say what happened and what to do; no apologies, no whimsy.

## Components

- **Token gate:** centered column — basket icon, wordmark, one field, one peach action.
- **Header:** wordmark left, presence dot slot, settings gear right. Hairline below.
- **Shelf (aisle group):** shelf label + hairline, rows beneath, on base.
- **Item row:** icon chip, name, note under, ring badge (tiny wand chip) when captured by ring, ⓘ and ✕ quiet at right; whole row toggles.
- **In the basket:** collapsed pile at bottom under a hairline; struck, subtext0, greyscale chips; "Clear checked" affordance.
- **Add pill:** floating input; typeahead list rises above it; ↵ adds top match.
- **Recents grid:** 4-wide icon chips of the frecency top; tap to add.
- **Item sheet:** modal over everything (including the pill); note field, category select, Remove/Cancel/Save.
- **Toasts:** one docked treatment above the pill; ring variant may glow peach.
- **Settings sheet:** flavour (auto/mocha/latte/frappé/macchiato), accent, token re-entry.

## Quality floor

Responsive to 390px; visible keyboard focus everywhere; WCAG AA contrast within the Catppuccin ladder; `prefers-reduced-motion` fully honored; works offline (queued writes surfaced honestly: banner + "n queued" chip).
