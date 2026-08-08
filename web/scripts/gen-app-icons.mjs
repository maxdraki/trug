// Rasterises the trug mark into the PWA app icons under public/icons/.
// Source of truth is /icon/trug-logo.svg (stroke-based basket + check). The
// "Orchard" colourway draws the mark as a cream stroke (Catppuccin Latte base
// #eff1f5) on the trug brand-green field #326850 — see BASE/MARK below.
//
//   node scripts/gen-app-icons.mjs   (or: npm run gen-app-icons)
//
// The mark does not fill its own 512 viewBox (it has generous internal
// whitespace), so scaling the raw viewBox would leave a tiny glyph swimming in
// padding. Instead we render the mark alone on a transparent field, TRIM to its
// true bounding box, then composite it centred onto the base at a target
// coverage — so the mark reads large and centred at every size.
//
// Outputs (overwritten in place):
//   public/icons/icon-192.png           any-purpose,  mark ~88% width
//   public/icons/icon-512.png           any-purpose,  mark ~88% width
//   public/icons/icon-192-maskable.png  safe-zone,    mark ~70% width
//   public/icons/icon-512-maskable.png  safe-zone,    mark ~70% width
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import sharp from 'sharp';

const here = dirname(fileURLToPath(import.meta.url));
const web = join(here, '..');
const outDir = join(web, 'public/icons');
mkdirSync(outDir, { recursive: true });

// "Orchard" colourway: cream mark (Catppuccin Latte base #eff1f5) on the
// trug brand-green field #326850.
const BASE = { r: 0x32, g: 0x68, b: 0x50, alpha: 1 };
const MARK = '#eff1f5';

// Any-purpose icons fill most of the frame (the OS applies its own corner mask
// and badging). Maskable icons keep the mark inside the central 80% safe-zone
// circle, so ~70% of the width leaves a comfortable margin.
const COVERAGE_ANY = 0.88;
const COVERAGE_MASKABLE = 0.7;

// Inner geometry of the mark, with the outer <svg> wrapper stripped so its
// stroke attributes can be re-declared on our own <g>.
const logo = readFileSync(join(web, '../icon/trug-logo.svg'), 'utf8');
const inner = logo.replace(/^[\s\S]*?<svg[^>]*>/, '').replace(/<\/svg>\s*$/, '');

// The mark alone on a transparent field, rendered large for a clean downscale.
const markSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 512 512">
  <g fill="none" stroke="${MARK}" stroke-width="40" stroke-linecap="round" stroke-linejoin="round">
${inner}
  </g>
</svg>`;

// Trim the transparent border down to the mark's true bounding box.
const mark = await sharp(Buffer.from(markSvg)).trim().png().toBuffer();

async function emit(name, size, coverage) {
  const box = Math.round(size * coverage);
  // Fit the trimmed mark inside a coverage×coverage box (longer side wins).
  const fitted = await sharp(mark)
    .resize({ width: box, height: box, fit: 'inside' })
    .toBuffer();
  const { width, height } = await sharp(fitted).metadata();
  await sharp({ create: { width: size, height: size, channels: 4, background: BASE } })
    .composite([
      { input: fitted, left: Math.round((size - width) / 2), top: Math.round((size - height) / 2) },
    ])
    .png()
    .toFile(join(outDir, name));
  console.log(`wrote icons/${name} (${size}px, mark ~${Math.round(coverage * 100)}%)`);
}

await emit('icon-512.png', 512, COVERAGE_ANY);
await emit('icon-192.png', 192, COVERAGE_ANY);
await emit('icon-512-maskable.png', 512, COVERAGE_MASKABLE);
await emit('icon-192-maskable.png', 192, COVERAGE_MASKABLE);
