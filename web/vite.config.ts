/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import { VitePWA } from 'vite-plugin-pwa'
import pkg from './package.json' with { type: 'json' }

const { version } = pkg

// Vite's built-in `vite:asset-import-meta-url` plugin rewrites the AST pattern
// `new URL(<literal>, import.meta.url)` into a served-asset URL. In tests that
// use that idiom to read a real file from disk (e.g. theme.css), the rewrite
// turns the path into a dev-server URL and the read fails. This `pre` transform
// runs first and wraps `import.meta.url` in test files as `(import.meta.url)`
// via string concat, so the second argument is no longer the bare MemberExpression
// the asset plugin matches — runtime semantics are identical (still the file URL).
const keepImportMetaUrlInTests: Plugin = {
  name: 'test:keep-import-meta-url',
  enforce: 'pre',
  transform(code, id) {
    if (!/\.test\.[jt]sx?$/.test(id) || !code.includes('import.meta.url')) return
    return { code: code.replace(/import\.meta\.url/g, "('' + import.meta.url)"), map: null }
  },
}

// https://vite.dev/config/
export default defineConfig({
  define: { __APP_VERSION__: JSON.stringify(version) },
  plugins: [
    svelte(),
    keepImportMetaUrlInTests,
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'icons/*.png'],
      // Cache-first app shell so the list renders offline. The API is never
      // handled by the service worker: SPA navigations fall back to the cached
      // index.html EXCEPT under /api (denylist), and there is no runtime
      // caching rule for /api, so live data always hits the network.
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,ico,woff2}'],
        navigateFallback: 'index.html',
        navigateFallbackDenylist: [/^\/api/, /^\/oauth/, /^\/mcp/, /^\/\.well-known/, /^\/healthz/],
      },
      manifest: {
        name: 'Trug',
        short_name: 'Trug',
        description: 'Your shared shopping list.',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        // Catppuccin Mocha base.
        theme_color: '#1e1e2e',
        background_color: '#1e1e2e',
        icons: [
          { src: 'icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icons/icon-512.png', sizes: '512x512', type: 'image/png' },
          {
            src: 'icons/icon-192-maskable.png',
            sizes: '192x192',
            type: 'image/png',
            purpose: 'maskable',
          },
          {
            src: 'icons/icon-512-maskable.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
    }),
  ],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
    },
  },
  // Under Vitest, resolve Svelte's browser (client) build so components can be
  // mounted in jsdom; the server build throws on lifecycle functions.
  resolve: process.env.VITEST ? { conditions: ['browser'] } : {},
  test: {
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
  },
})
