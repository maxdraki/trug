import { mount } from 'svelte'
import { registerSW } from 'virtual:pwa-register'
// Fonts are bundled locally (@fontsource) so the PWA renders offline — no CDN.
import '@fontsource-variable/inter/wght.css'
import '@fontsource-variable/space-grotesk'
import './theme.css'
import './app.css'
import { loadTheme } from './lib/theme'
import { setToken, validateToken, markUrlTokenInvalid } from './lib/api'
import App from './App.svelte'

loadTheme()

// Register the service worker with `immediate: true` so a redeploy's new
// service worker takes over as soon as it activates — the freshly precached
// assets are served on the next load, with no second manual reload. Without an
// explicit registration the autoUpdate SW installs but the open client keeps
// serving the old precache until it's loaded twice.
registerSW({ immediate: true })

// Self-erase auth token from the URL: if the app is opened with ?token=...,
// strip it from the address bar immediately (so it never lingers in history)
// and validate it before trusting it. An invalid token is never stored —
// storing it would just hand AuthGate a "logged in" state that 401s on the
// first real request, which is the confusing bit this is fixing.
const params = new URLSearchParams(location.search)
const urlToken = params.get('token')
if (urlToken) {
  params.delete('token')
  const qs = params.toString()
  history.replaceState(null, '', location.pathname + (qs ? `?${qs}` : '') + location.hash)
}

async function init() {
  if (urlToken) {
    if (await validateToken(urlToken)) {
      setToken(urlToken)
    } else {
      markUrlTokenInvalid()
    }
  }

  mount(App, {
    target: document.getElementById('app')!,
  })
}

const app = init()

export default app
