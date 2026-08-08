<script lang="ts">
  // The trug mark: a stroke-drawn garden basket with the check cutting across
  // the rim. Source of truth is /icon/trug-logo.svg; kept in sync by hand.
  // Renders with `stroke="currentColor"` so callers colour it via `color`
  // (accent in the header/token gate, quiet ink on the empty shelf).
  //
  // The mask/clipPath carry ids, so each instance namespaces them with a
  // per-instance uid — two logos in the DOM at once (header + empty state)
  // must not collide on `url(#…)` references.
  let { size = 24, title }: { size?: number; title?: string } = $props();
  const uid = $props.id();
  const gapId = `trug-check-gap-${uid}`;
  const cutId = `trug-rim-cut-${uid}`;
</script>

<svg
  viewBox="0 0 512 512"
  width={size}
  height={size}
  fill="none"
  stroke="currentColor"
  stroke-width="40"
  stroke-linecap="round"
  stroke-linejoin="round"
  role={title ? 'img' : undefined}
  aria-label={title}
  aria-hidden={title ? undefined : 'true'}
>
  <!-- knockout so the rim breaks cleanly where the check crosses it -->
  <mask id={gapId}>
    <rect width="512" height="512" fill="#fff" />
    <path
      d="M170 300 L242 370 L452 124"
      stroke="#000"
      stroke-width="64"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </mask>

  <!-- straight cut parallel to the check, for the rim's right stub -->
  <clipPath id={cutId}>
    <path d="M459 178 L391 258 L520 258 L520 178 Z" />
  </clipPath>

  <!-- handle: arch whose right leg sweeps back in along the rim -->
  <path
    d="m 181,215 v -65 c 0,-41.42138 33.57864,-74.999967 75,-74.999967 41.42136,0 75,33.578587 75,74.999967 0,28 -15,50 -47,60 -14,4 -50.25263,5.10175 -60.25263,5.10175"
  />

  <!-- rim, broken where the check crosses -->
  <path d="M52 215 H224" />
  <path d="M390 215 H460" clip-path="url(#{cutId})" />

  <!-- bowl -->
  <path
    d="M52 215 L96 383 Q107 436 158 436 H354 Q405 436 416 383 L460 215"
    mask="url(#{gapId})"
  />

  <!-- check -->
  <path d="M170 300 L242 370 L452 124" />
</svg>

<style>
  svg {
    display: block;
    flex: 0 0 auto;
  }
</style>
