<script lang="ts">
  import { ICONS } from '../lib/icons';

  let {
    name,
    size = 18,
    stroke = 2,
  }: {
    /** Slug into the generated Tabler outline map (src/lib/icons.ts). */
    name: string;
    size?: number;
    /** Stroke width in the 24px grid; 2 is the system default. */
    stroke?: number;
  } = $props();
</script>

{#if ICONS[name]}
  <svg
    viewBox="0 0 24 24"
    width={size}
    height={size}
    fill="none"
    stroke="currentColor"
    stroke-width={stroke}
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <!-- eslint-disable-next-line svelte/no-at-html-tags — build-time generated markup -->
    {@html ICONS[name]}
  </svg>
{/if}

<style>
  svg {
    display: block;
    flex: 0 0 auto;
  }
</style>
