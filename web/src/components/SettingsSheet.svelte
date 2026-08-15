<script lang="ts">
  import { fade } from 'svelte/transition';
  import { d, fadeDur, DUR, EASE, EASE_CSS, rise, unfold, fold } from '../lib/motion';
  import { applyTheme, applyDensity } from '../lib/theme';
  import { hapticsEnabled, setHapticsEnabled } from '../lib/haptics';
  import { readStored } from '../lib/safeStorage';
  import {
    api,
    type SessionInfo,
    type Connections,
    type Member,
    type LlmConfig,
    type LlmProvider,
    type LlmConfigInput,
    type LlmTestResult,
  } from '../lib/api';
  import { clearSnapshot, formatAge } from '../lib/snapshot';
  import { resolveIcon } from '../lib/resolveIcon';
  import Icon from './Icon.svelte';

  let {
    open,
    onClose,
    cookieAuth = false,
  }: { open: boolean; onClose: () => void; cookieAuth?: boolean } = $props();

  // 'auto' maps to a null flavour (follow the OS light/dark preference); the
  // named flavours pin a specific palette.
  const FLAVOURS = [
    { id: null, label: 'Auto' },
    { id: 'mocha', label: 'Mocha' },
    { id: 'latte', label: 'Latte' },
    { id: 'frappe', label: 'Frappé' },
    { id: 'macchiato', label: 'Macchiato' },
  ] as const;

  // A curated handful of accents; peach is the app default.
  const ACCENTS = [
    { id: 'peach', label: 'Peach' },
    { id: 'mauve', label: 'Mauve' },
    { id: 'green', label: 'Green' },
    { id: 'blue', label: 'Blue' },
    { id: 'pink', label: 'Pink' },
    { id: 'yellow', label: 'Yellow' },
  ] as const;

  // Row spacing. 'Comfortable' is the default and maps to a null density (no
  // data-density attribute); 'dense' packs more of the list onto one screen.
  const DENSITIES = [
    { id: null, label: 'Comfortable' },
    { id: 'dense', label: 'Dense' },
  ] as const;

  // Seed from persisted choices so the sheet reflects the live theme.
  // Read through the guard: these run at component init, so an unguarded lookup
  // in a browser with site data blocked took the whole sheet down rather than
  // costing a remembered preference.
  let flavour = $state<string | null>(readStored('trug_flavour'));
  let accent = $state<string>(readStored('trug_accent') ?? 'peach');
  let density = $state<string | null>(readStored('trug_density'));
  // Per-device: buzz when the ring or an assistant adds something. On unless
  // this device has turned it off.
  let haptics = $state<boolean>(hapticsEnabled());
  const canVibrate = typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function';

  // --- collapsible sections -------------------------------------------------
  // Every section is a disclosure. Theme + Accent + Density default open (the
  // everyday adjustments); Devices, Invite, Connections default collapsed
  // (occasional). Open/closed state persists per section so the sheet reopens
  // as you left it.
  type SectionId =
    | 'theme'
    | 'accent'
    | 'density'
    | 'alerts'
    | 'devices'
    | 'members'
    | 'connections'
    | 'ai'
    | 'about';
  const DEFAULT_OPEN: Record<SectionId, boolean> = {
    theme: true,
    accent: true,
    density: true,
    alerts: true,
    devices: false,
    members: false,
    connections: false,
    ai: false,
    about: false,
  };

  // Build version, injected by Vite's define. Guarded so tests (and any build
  // without the define) fall back to 'dev' rather than throwing on the ident.
  const version = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev';

  function loadOpen(): Record<SectionId, boolean> {
    try {
      const raw = localStorage.getItem('trug_settings_open');
      if (raw) return { ...DEFAULT_OPEN, ...JSON.parse(raw) };
    } catch {
      /* corrupt/blocked storage — fall back to the defaults */
    }
    return { ...DEFAULT_OPEN };
  }

  let openSections = $state<Record<SectionId, boolean>>(loadOpen());

  function toggleSection(id: SectionId) {
    openSections[id] = !openSections[id];
    try {
      localStorage.setItem('trug_settings_open', JSON.stringify(openSections));
    } catch {
      /* storage blocked — the toggle still works for this session */
    }
  }

  function pickFlavour(id: string | null) {
    flavour = id;
    applyTheme(id, accent);
  }

  function pickAccent(id: string) {
    accent = id;
    applyTheme(flavour, id);
  }

  function pickDensity(id: string | null) {
    density = id;
    applyDensity(id);
  }

  function toggleHaptics() {
    haptics = !haptics;
    setHapticsEnabled(haptics);
  }

  // --- devices (cookie-authed humans) ---------------------------------------
  let sessions = $state<SessionInfo[]>([]);
  let devicesError = $state<string | null>(null);

  // Load the session list whenever the sheet opens for a cookie-authed human.
  $effect(() => {
    if (open && cookieAuth) loadSessions();
  });

  async function loadSessions() {
    devicesError = null;
    try {
      sessions = await api.auth.sessions();
    } catch {
      devicesError = "couldn't load your devices.";
    }
  }

  async function revoke(id: string) {
    try {
      await api.auth.revokeSession(id);
      sessions = sessions.filter((s) => s.id !== id);
    } catch {
      devicesError = "couldn't revoke that device.";
    }
  }

  let signOutError = $state<string | null>(null);
  async function signOut() {
    signOutError = null;
    try {
      await api.auth.logout();
      // Confirmed sign-out: drop the cached list so the next boot can't flash a
      // stale household shelf at a signed-out device.
      clearSnapshot();
      // Only on a confirmed logout: reload so the gate reasserts and the stream
      // tears down. On failure we stay on the sheet and surface the error.
      location.reload();
    } catch {
      signOutError = "couldn't sign out — check your connection.";
    }
  }

  /** Short, human label for a session's user-agent. */
  function deviceLabel(ua: string | null): string {
    if (!ua) return 'unknown device';
    if (/iphone/i.test(ua)) return 'iPhone';
    if (/ipad/i.test(ua)) return 'iPad';
    if (/android/i.test(ua)) return 'Android';
    if (/mac os x|macintosh/i.test(ua)) return 'Mac';
    if (/windows/i.test(ua)) return 'Windows';
    if (/linux/i.test(ua)) return 'Linux';
    return 'browser';
  }

  // --- connections (cookie-authed humans: self-serve ring + MCP setup) ------
  let connections = $state<Connections | null>(null);
  let connectionsError = $state<string | null>(null);
  // Which secret fields are revealed, and which field was just copied.
  let revealed = $state<Record<string, boolean>>({});
  let copiedField = $state<string | null>(null);

  $effect(() => {
    if (open && cookieAuth) loadConnections();
  });

  async function loadConnections() {
    connectionsError = null;
    try {
      connections = await api.auth.connections();
    } catch {
      connectionsError = "couldn't load your connection details.";
    }
  }

  async function copyField(value: string, key: string) {
    try {
      await navigator.clipboard.writeText(value);
      copiedField = key;
      setTimeout(() => copiedField === key && (copiedField = null), 1500);
    } catch {
      /* clipboard blocked — the field is selectable as a fallback */
    }
  }

  // --- members (dynamic household roster; session-gated) --------------------
  // The roster is the server's source of truth (GET /auth/users): who's in, who
  // is still a pending invite, and which row is "you". Any enrolled member can
  // invite anyone new and remove members (flat household, no admin tier).
  let members = $state<Member[]>([]);
  let me = $state<string | null>(null);
  let membersError = $state<string | null>(null);

  $effect(() => {
    if (open && cookieAuth) loadMembers();
  });

  async function loadMembers() {
    membersError = null;
    try {
      const roster = await api.auth.listMembers();
      members = roster.users;
      me = roster.me;
    } catch {
      membersError = "couldn't load the household.";
    }
  }

  const enrolledCount = $derived(members.filter((m) => m.enrolled).length);

  // Invite-someone: a free-text name (any new name), a minted single-use link,
  // and a copy affordance mirroring the connections copy pattern.
  let inviteName = $state('');
  let inviteLink = $state<string | null>(null);
  let inviteError = $state<string | null>(null);
  let minting = $state(false);
  let copied = $state(false);

  async function mintInvite() {
    const name = inviteName.trim();
    if (!name || minting) return;
    minting = true;
    inviteError = null;
    inviteLink = null;
    copied = false;
    try {
      const { invite } = await api.auth.inviteUser(name);
      inviteLink = `${location.origin}/#invite=${invite}`;
      inviteName = '';
      loadMembers(); // the new pending member now shows in the list
    } catch (err) {
      inviteError = err instanceof Error ? err.message : "couldn't create an invite.";
    } finally {
      minting = false;
    }
  }

  async function copyLink() {
    if (!inviteLink) return;
    try {
      await navigator.clipboard.writeText(inviteLink);
      copied = true;
      setTimeout(() => (copied = false), 1500);
    } catch {
      /* clipboard blocked — the field is selectable as a fallback */
    }
  }

  // Two-tap remove: the first tap arms the row (no native dialog), the second
  // confirms. Removing yourself ends your session, so reload back to the gate.
  let confirming = $state<string | null>(null);
  async function removeMember(name: string) {
    if (confirming !== name) {
      confirming = name;
      return;
    }
    confirming = null;
    membersError = null;
    try {
      await api.auth.removeMember(name);
      if (name === me) {
        clearSnapshot();
        location.reload();
        return;
      }
      loadMembers();
    } catch (err) {
      membersError = err instanceof Error ? err.message : "couldn't remove that member.";
    }
  }

  // --- AI enrichment (BYOK; cookie-authed humans) ---------------------------
  // Pick a provider, add a key, choose a model, test a live hello-world, save.
  // The full key never leaves the server; we only ever show a masked hint.
  const PROVIDERS = [
    { id: 'anthropic', label: 'Anthropic' },
    { id: 'gemini', label: 'Google Gemini' },
    { id: 'openai', label: 'OpenAI' },
    { id: 'ollama', label: 'Ollama (local)' },
    { id: 'custom', label: 'Custom (OpenAI-compatible)' },
  ] as const;

  // Per-provider default model + a datalist of a few common ids; ollama also
  // carries a default local base URL.
  const PROVIDER_META: Record<
    LlmProvider,
    { model: string; base_url?: string; models: string[] }
  > = {
    anthropic: { model: 'claude-haiku-4-5', models: ['claude-haiku-4-5', 'claude-sonnet-4-5'] },
    gemini: {
      // Placeholder ids only — "Fetch models" replaces these with the provider's
      // live list. Kept current (2.0-flash is removed/404) so a no-fetch fallback
      // isn't a known-dead id.
      model: 'gemini-2.5-flash',
      models: ['gemini-2.5-flash', 'gemini-2.5-pro'],
    },
    openai: { model: 'gpt-4o-mini', models: ['gpt-4o-mini', 'gpt-4o'] },
    ollama: {
      model: 'llama3.2',
      base_url: 'http://localhost:11434/v1',
      models: ['llama3.2', 'qwen2.5', 'mistral'],
    },
    custom: { model: '', models: [] },
  };

  let aiProvider = $state<LlmProvider>('anthropic');
  let aiKey = $state('');
  let aiModel = $state('');
  let aiBaseUrl = $state('');
  let aiConfig = $state<LlmConfig | null>(null);
  let aiReplacing = $state(false); // reveal an empty key field over a configured one
  let aiError = $state<string | null>(null);
  let aiTesting = $state(false);
  let aiSaving = $state(false);
  let aiTestResult = $state<LlmTestResult | null>(null);
  // Live model listing: ids fetched from the provider once a key is present, an
  // inline detail if the fetch failed, and a manual-entry escape hatch.
  let aiModels = $state<string[]>([]);
  let aiModelLabels = $state<Record<string, string>>({}); // id → display name, where given
  let aiFetchingModels = $state(false);
  let aiModelsDetail = $state<string | null>(null);
  let aiManualModel = $state(false); // type a model by hand instead of the dropdown

  // Offline guard for the network-bound Test/Save/Clear buttons.
  let online = $state(typeof navigator === 'undefined' || navigator.onLine);
  $effect(() => {
    const set = () => (online = navigator.onLine);
    window.addEventListener('online', set);
    window.addEventListener('offline', set);
    return () => {
      window.removeEventListener('online', set);
      window.removeEventListener('offline', set);
    };
  });

  $effect(() => {
    if (open && cookieAuth) loadAiConfig();
  });

  const needsBaseUrl = (p: LlmProvider) => p === 'ollama' || p === 'custom';
  const needsKey = (p: LlmProvider) => p !== 'ollama';

  async function loadAiConfig() {
    aiError = null;
    try {
      const cfg = await api.auth.getLlmConfig();
      aiConfig = cfg;
      if (cfg.provider) aiProvider = cfg.provider;
      aiModel = cfg.model ?? PROVIDER_META[aiProvider].model;
      aiBaseUrl = cfg.base_url ?? '';
      aiReplacing = false;
      aiKey = '';
      aiTestResult = null;
      clearFetchedModels();
    } catch {
      aiError = "couldn't load your AI settings.";
    }
  }

  // Provider change resets the model to that provider's default and seeds the
  // base URL (ollama → localhost); a stale test result and any fetched model
  // list (belonging to the old provider) are cleared.
  function pickProvider(p: LlmProvider) {
    aiProvider = p;
    aiModel = PROVIDER_META[p].model;
    aiBaseUrl = PROVIDER_META[p].base_url ?? '';
    aiTestResult = null;
    aiError = null;
    clearFetchedModels();
  }

  // Any edit to the key/model/base-url invalidates a prior green test, forcing a
  // re-test before Save re-enables — the test gate must reflect what will be sent.
  function onConfigEdit() {
    aiTestResult = null;
  }

  // Drop a fetched list so a stale set of ids can't outlive the key/provider it
  // came from; falls back to the manual/text control.
  function clearFetchedModels() {
    aiModels = [];
    aiModelLabels = {};
    aiModelsDetail = null;
    aiManualModel = false;
  }

  // Editing the key (or the base URL) both re-gates Save and invalidates the
  // fetched list — the ids were listed under the old credentials/endpoint.
  function onKeyEdit() {
    onConfigEdit();
    clearFetchedModels();
  }

  // Fetch the provider's real text-generation models and switch the model control
  // to a dropdown. A provider error comes back as {models: [], detail} — shown
  // inline while manual entry stays available.
  async function fetchModels() {
    aiModelsDetail = null;
    aiFetchingModels = true;
    try {
      const res = await api.auth.listLlmModels(buildInput());
      aiModels = res.models;
      aiModelLabels = res.labels ?? {};
      aiModelsDetail = res.detail ?? null;
      if (res.models.length) {
        aiManualModel = false;
        // The provider listing is often INCOMPLETE — a working model can be
        // missing (e.g. Gemini). So NEVER overwrite a model the human already
        // has; only fill a blank one with the newest listed id. Re-gate Save.
        if (!aiModel.trim()) {
          aiModel = res.models[0];
          onConfigEdit();
        }
      }
    } catch (err) {
      aiModelsDetail = err instanceof Error ? err.message : "couldn't fetch models.";
    } finally {
      aiFetchingModels = false;
    }
  }

  // The active config's source. env-sourced configs come from the server
  // environment (read-only); settings-sourced are saved in this household.
  const envSourced = $derived(aiConfig?.source === 'env');
  // A stored key that no longer decrypts — prompt a re-entry for this provider.
  const showUnreadable = $derived(
    aiConfig?.unreadable_key === true && aiConfig?.provider === aiProvider,
  );

  // Whether a key is already stored (settings source) for the current provider,
  // so it can be kept without re-entry. Never for env (that key lives server-side
  // and isn't "stored" here) nor an unreadable row (which must be re-entered).
  const keyConfigured = $derived(
    aiConfig?.source === 'settings' &&
      !showUnreadable &&
      !!aiConfig?.key_hint &&
      aiConfig?.provider === aiProvider &&
      !aiReplacing,
  );

  // Save persists a settings row. For an env-sourced config there's nothing to
  // persist unless the human is overriding with their own key.
  const canSave = $derived(
    !!aiTestResult?.ok && !(envSourced && !aiReplacing && !aiKey),
  );

  // The dropdown's options: the fetched ids, plus the current model when it's a
  // non-empty id the (incomplete) listing didn't include — so a working-but-
  // unlisted model stays selectable rather than being silently swapped out.
  const modelOptions = $derived(
    aiModels.length && aiModel.trim() && !aiModels.includes(aiModel)
      ? [aiModel, ...aiModels]
      : aiModels,
  );

  // Whether we have enough to ask the provider for its models: online, a base URL
  // where one is required, and a key (typed, stored, or env) where one is needed.
  const canFetchModels = $derived(
    online &&
      (!needsBaseUrl(aiProvider) || aiBaseUrl.trim().length > 0) &&
      (!needsKey(aiProvider) ||
        aiKey.length > 0 ||
        keyConfigured ||
        (envSourced && aiConfig?.provider === aiProvider)),
  );

  function buildInput(): LlmConfigInput {
    const cfg: LlmConfigInput = { provider: aiProvider };
    if (aiModel.trim()) cfg.model = aiModel.trim();
    if (needsBaseUrl(aiProvider) && aiBaseUrl.trim()) cfg.base_url = aiBaseUrl.trim();
    // Send the key only when the user typed one; omitted keeps the stored key.
    if (aiKey) cfg.api_key = aiKey;
    return cfg;
  }

  async function testConnection() {
    aiError = null;
    aiTestResult = null;
    aiTesting = true;
    try {
      aiTestResult = await api.auth.testLlmConfig(buildInput());
    } catch (err) {
      aiError = err instanceof Error ? err.message : 'test failed — check your connection.';
    } finally {
      aiTesting = false;
    }
  }

  async function saveConfig() {
    aiError = null;
    aiSaving = true;
    try {
      aiConfig = await api.auth.putLlmConfig(buildInput());
      aiKey = '';
      aiReplacing = false;
    } catch (err) {
      aiError = err instanceof Error ? err.message : "couldn't save — check your connection.";
    } finally {
      aiSaving = false;
    }
  }

  async function clearConfig() {
    aiError = null;
    try {
      aiConfig = await api.auth.clearLlmConfig();
      if (aiConfig.provider) aiProvider = aiConfig.provider;
      aiModel = aiConfig.model ?? PROVIDER_META[aiProvider].model;
      aiBaseUrl = aiConfig.base_url ?? '';
      aiKey = '';
      aiReplacing = false;
      aiTestResult = null;
    } catch {
      aiError = "couldn't clear — check your connection.";
    }
  }
</script>

<svelte:window onkeydown={(e) => open && e.key === 'Escape' && onClose()} />

{#if open}
  <div
    class="backdrop"
    role="button"
    tabindex="-1"
    aria-label="Close"
    onclick={onClose}
    onkeydown={(e) => e.key === 'Escape' && onClose()}
    transition:fade={{ duration: fadeDur(DUR.expand) }}
  ></div>
  <div
    class="sheet"
    role="dialog"
    aria-modal="true"
    aria-label="Settings"
    in:rise={{ y: 24, duration: DUR.expand, easing: EASE.expand }}
    out:rise={{ y: 24, duration: DUR.collapse, easing: EASE.collapse }}
  >
    <div class="grip" aria-hidden="true"></div>
    <div class="head">
      <h2>Settings</h2>
    </div>

    {#snippet disclosure(id: SectionId, label: string)}
      <button
        type="button"
        class="disclosure"
        aria-expanded={openSections[id]}
        aria-controls="settings-region-{id}"
        onclick={() => toggleSection(id)}
      >
        <span class="section-label">{label}</span>
        <span
          class="chev"
          class:open={openSections[id]}
          style="transition: transform {d(DUR.expand)}ms {EASE_CSS.expand}"
          aria-hidden="true"
        >
          <Icon name="chevron-right" size={18} stroke={2} />
        </span>
      </button>
    {/snippet}

    <div class="body">
      <section>
        {@render disclosure('theme', 'Theme')}
        {#if openSections.theme}
          <div
            class="region"
            id="settings-region-theme"
            role="region"
            aria-label="Theme"
            in:unfold
            out:fold
          >
            <div class="chips">
              {#each FLAVOURS as f (f.label)}
                <button
                  type="button"
                  class="chip"
                  class:selected={flavour === f.id}
                  aria-pressed={flavour === f.id}
                  onclick={() => pickFlavour(f.id)}
                >
                  {f.label}
                </button>
              {/each}
            </div>
          </div>
        {/if}
      </section>

      <section>
        {@render disclosure('accent', 'Accent')}
        {#if openSections.accent}
          <div
            class="region"
            id="settings-region-accent"
            role="region"
            aria-label="Accent"
            in:unfold
            out:fold
          >
            <div class="chips">
              {#each ACCENTS as a (a.id)}
                <button
                  type="button"
                  class="chip swatch"
                  class:selected={accent === a.id}
                  aria-pressed={accent === a.id}
                  style="--dot: var(--ctp-{a.id})"
                  onclick={() => pickAccent(a.id)}
                >
                  <span class="dot" aria-hidden="true"></span>{a.label}
                </button>
              {/each}
            </div>
          </div>
        {/if}
      </section>

      <section>
        {@render disclosure('density', 'Density')}
        {#if openSections.density}
          <div
            class="region"
            id="settings-region-density"
            role="region"
            aria-label="Density"
            in:unfold
            out:fold
          >
            <div class="chips">
              {#each DENSITIES as x (x.label)}
                <button
                  type="button"
                  class="chip"
                  class:selected={density === x.id}
                  aria-pressed={density === x.id}
                  onclick={() => pickDensity(x.id)}
                >
                  {x.label}
                </button>
              {/each}
            </div>
          </div>
        {/if}
      </section>

      <!-- Only where the browser can actually buzz. Safari has never shipped
           `navigator.vibrate` and Firefox dropped it at 129, so on an iPhone —
           the device most likely to be in a pocket at the shop — this section
           would otherwise promise a buzz that can never happen, with an
           on-by-default switch to make the promise look deliberate. -->
      {#if canVibrate}
      <section>
        {@render disclosure('alerts', 'Alerts')}
        {#if openSections.alerts}
          <div
            class="region"
            id="settings-region-alerts"
            role="region"
            aria-label="Alerts"
            in:unfold
            out:fold
          >
            <button
              type="button"
              class="toggle"
              aria-pressed={haptics}
              onclick={toggleHaptics}
            >
              <span class="toggle-label">Buzz when the ring or an assistant adds an item</span>
              <span class="track" class:on={haptics} aria-hidden="true">
                <span
                  class="knob"
                  style="transition: transform {d(DUR.expand)}ms {EASE_CSS.expand}"
                ></span>
              </span>
            </button>
            <p class="hint">
              This phone only, and not until the page has been touched once —
              browsers won't buzz at a page you haven't interacted with.
            </p>
          </div>
        {/if}
      </section>
      {/if}

      {#if cookieAuth}
        <section>
          {@render disclosure('devices', 'Devices')}
          {#if openSections.devices}
            <div
              class="region"
              id="settings-region-devices"
              role="region"
              aria-label="Devices"
              in:unfold
              out:fold
            >
              {#if devicesError}
                <p class="hint error">{devicesError}</p>
              {/if}
              <ul class="devices">
                {#each sessions as s (s.id)}
                  <li class="device">
                    <span class="device-detail">
                      <span class="device-name">
                        {deviceLabel(s.user_agent)}{#if s.current}<span class="badge">this device</span>{/if}
                      </span>
                      <span class="device-last-active">
                        {#if s.current}active now{:else}last active {formatAge(Date.now() - new Date(s.last_seen).getTime())}{/if}
                      </span>
                    </span>
                    {#if !s.current}
                      <button
                        type="button"
                        class="revoke"
                        aria-label="Revoke {deviceLabel(s.user_agent)}"
                        onclick={() => revoke(s.id)}
                      >
                        <Icon name="x" size={16} stroke={2} />
                      </button>
                    {/if}
                  </li>
                {/each}
              </ul>
              <button type="button" class="ghost signout" onclick={signOut}>Sign out</button>
              {#if signOutError}<p class="hint error">{signOutError}</p>{/if}
            </div>
          {/if}
        </section>
      {/if}

    {#snippet urlRow(label: string, value: string, key: string)}
      <div class="conn-row">
        <span class="conn-key">{label}</span>
        <div class="row">
          <code class="mono" aria-label={label}>{value}</code>
          <button type="button" class="ghost copy" onclick={() => copyField(value, key)}>
            {copiedField === key ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>
    {/snippet}

    {#snippet secretRow(label: string, value: string, key: string)}
      <div class="conn-row">
        <span class="conn-key">{label}</span>
        <div class="row">
          <code class="mono" aria-label={label}
            >{revealed[key] ? value : '•'.repeat(16)}</code
          >
          <button
            type="button"
            class="ghost reveal"
            aria-label={revealed[key] ? `Hide ${label}` : `Reveal ${label}`}
            onclick={() => (revealed[key] = !revealed[key])}
          >
            <Icon name={revealed[key] ? 'eye-off' : 'eye'} size={16} stroke={2} />
          </button>
          <button type="button" class="ghost copy" onclick={() => copyField(value, key)}>
            {copiedField === key ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>
    {/snippet}

      {#if cookieAuth}
        <section>
          {@render disclosure('connections', 'Connections')}
          {#if openSections.connections}
            <div
              class="region"
              id="settings-region-connections"
              role="region"
              aria-label="Connections"
              in:unfold
              out:fold
            >
              {#if connectionsError}
                <p class="hint error">{connectionsError}</p>
              {/if}
              {#if connections}
                <div class="card">
                  <span class="card-title">AI assistants (MCP)</span>
                  {@render urlRow('URL', connections.mcp_url, 'mcp_url')}
                  {@render secretRow('Token', connections.mcp_token, 'mcp_token')}
                </div>
                <div class="card">
                  <span class="card-title">Pebble ring (webhook)</span>
                  {@render urlRow('URL', connections.webhook_url, 'webhook_url')}
                  {@render secretRow('Token', connections.ring_token, 'ring_token')}
                  <p class="hint">sends transcription as multipart — works out of the box.</p>
                </div>
              {/if}
            </div>
          {/if}
        </section>
      {/if}

      {#if cookieAuth}
        <section>
          {@render disclosure('ai', 'AI enrichment')}
          {#if openSections.ai}
            <div
              class="region"
              id="settings-region-ai"
              role="region"
              aria-label="AI enrichment"
              in:unfold
              out:fold
            >
              <p class="hint">
                Optional. Common groceries already get icons and aisles with no key — a key
                adds prose-parsing and covers rarer items.
              </p>

              <label class="field">
                <span class="field-label">Provider</span>
                <select
                  class="select"
                  aria-label="Provider"
                  value={aiProvider}
                  onchange={(e) => pickProvider((e.currentTarget as HTMLSelectElement).value as LlmProvider)}
                >
                  {#each PROVIDERS as p (p.id)}
                    <option value={p.id}>{p.label}</option>
                  {/each}
                </select>
              </label>

              {#if needsKey(aiProvider)}
                <div class="field">
                  <span class="field-label">API key</span>
                  {#if showUnreadable}
                    <p class="hint error">
                      Your saved key can no longer be decrypted — re-enter it.
                    </p>
                    <input
                      type="password"
                      aria-label="API key"
                      bind:value={aiKey}
                      oninput={onKeyEdit}
                      placeholder="paste your key"
                      autocapitalize="off"
                      autocomplete="off"
                      spellcheck="false"
                    />
                  {:else if envSourced && aiConfig?.provider === aiProvider && !aiReplacing}
                    <p class="hint">Using the key from the server environment.</p>
                    <button type="button" class="ghost" onclick={() => (aiReplacing = true)}>
                      Override with your own key
                    </button>
                  {:else if keyConfigured}
                    <div class="row">
                      <code class="mono" aria-label="Configured key">configured {aiConfig?.key_hint}</code>
                      <button type="button" class="ghost" onclick={() => (aiReplacing = true)}>
                        Replace
                      </button>
                    </div>
                  {:else}
                    <input
                      type="password"
                      aria-label="API key"
                      bind:value={aiKey}
                      oninput={onKeyEdit}
                      placeholder="paste your key"
                      autocapitalize="off"
                      autocomplete="off"
                      spellcheck="false"
                    />
                  {/if}
                </div>
              {/if}

              {#if needsBaseUrl(aiProvider)}
                <label class="field">
                  <span class="field-label">Base URL</span>
                  <input
                    type="text"
                    aria-label="Base URL"
                    bind:value={aiBaseUrl}
                    oninput={onKeyEdit}
                    placeholder="http://localhost:11434/v1"
                    autocapitalize="off"
                    autocomplete="off"
                    spellcheck="false"
                  />
                </label>
              {/if}

              <div class="field">
                <span class="field-label">Model</span>
                {#if aiModels.length > 0 && !aiManualModel}
                  <!-- Live list fetched from the provider — pick, don't guess.
                       A current-but-unlisted model is kept as an option so the
                       listing's gaps never silently drop it. -->
                  <select
                    class="select"
                    aria-label="Model"
                    bind:value={aiModel}
                    onchange={onConfigEdit}
                  >
                    {#each modelOptions as m (m)}
                      <option value={m}>{aiModelLabels[m] ?? m}</option>
                    {/each}
                  </select>
                {:else}
                  <input
                    type="text"
                    aria-label="Model"
                    bind:value={aiModel}
                    oninput={onConfigEdit}
                    list="ai-models-{aiProvider}"
                    placeholder={PROVIDER_META[aiProvider].model || 'model id'}
                    autocapitalize="off"
                    autocomplete="off"
                    spellcheck="false"
                  />
                  <datalist id="ai-models-{aiProvider}">
                    {#each PROVIDER_META[aiProvider].models as m (m)}
                      <option value={m}></option>
                    {/each}
                  </datalist>
                {/if}
                <div class="row model-fetch">
                  <button
                    type="button"
                    class="ghost"
                    onclick={fetchModels}
                    disabled={aiFetchingModels || !canFetchModels}
                  >
                    {aiFetchingModels
                      ? 'fetching…'
                      : aiModels.length
                        ? 'Refresh models'
                        : 'Fetch models'}
                  </button>
                  {#if aiFetchingModels}
                    <span
                      class="spinner"
                      data-motion="essential"
                      aria-label="Fetching models"
                      role="status"
                    ></span>
                  {:else if aiModels.length > 0 && !aiManualModel}
                    <span class="hint model-count">{aiModels.length} available</span>
                    <button
                      type="button"
                      class="ghost model-manual"
                      onclick={() => (aiManualModel = true)}
                    >
                      Type manually
                    </button>
                  {:else if aiManualModel && aiModels.length > 0}
                    <button
                      type="button"
                      class="ghost model-manual"
                      onclick={() => (aiManualModel = false)}
                    >
                      Pick from list
                    </button>
                  {/if}
                </div>
                {#if aiModelsDetail}
                  <p class="hint error" role="status">{aiModelsDetail}</p>
                {:else if aiModels.length === 0}
                  <p class="hint">Enter your key, then fetch the provider's models.</p>
                {:else}
                  <p class="hint">not every working model is listed — you can type one in.</p>
                {/if}
              </div>

              <div class="row ai-actions">
                <button
                  type="button"
                  class="ghost"
                  onclick={testConnection}
                  disabled={aiTesting || !online}
                >
                  {aiTesting ? 'testing…' : 'Test connection'}
                </button>
                <button
                  type="button"
                  class="primary"
                  onclick={saveConfig}
                  disabled={aiSaving || !online || !canSave}
                >
                  {aiSaving ? 'saving…' : 'Save'}
                </button>
                {#if aiConfig?.source === 'settings'}
                  <button type="button" class="ghost" onclick={clearConfig} disabled={!online}>
                    Clear
                  </button>
                {/if}
              </div>

              {#if aiTestResult}
                {#if aiTestResult.ok}
                  <p class="hint ai-ok" role="status">
                    <span aria-hidden="true">✓</span>
                    Working — milk →
                    {#if aiTestResult.result}
                      {@const res = resolveIcon(aiTestResult.result.icon, 'milk')}
                      {#if res.kind === 'line'}<Icon name={res.slug} size={16} stroke={2} />{/if}
                      <span class="ai-cat">{aiTestResult.result.category}</span>
                    {/if}
                  </p>
                {:else}
                  <p class="hint error" role="status">✗ {aiTestResult.detail}</p>
                {/if}
              {/if}
              {#if !aiTestResult?.ok}
                <p class="hint">Test a connection before saving.</p>
              {:else if envSourced && !aiReplacing && !aiKey}
                <p class="hint">
                  Enrichment is using the server-environment key. Override above to save your own.
                </p>
              {/if}
              {#if aiError}<p class="hint error">{aiError}</p>{/if}
            </div>
          {/if}
        </section>
      {/if}

      {#if cookieAuth}
        <section>
          {@render disclosure('members', 'Members')}
          {#if openSections.members}
            <div
              class="region"
              id="settings-region-members"
              role="region"
              aria-label="Members"
              in:unfold
              out:fold
            >
              {#if membersError}<p class="hint error">{membersError}</p>{/if}

              <ul class="members">
                {#each members as m (m.name)}
                  <li class="member">
                    <span class="member-name">
                      {m.name}{#if m.name === me}<span class="badge">you</span>{/if}
                    </span>
                    <span class="member-right">
                      {#if m.enrolled}
                        <span class="status enrolled" aria-label="enrolled">enrolled ✓</span>
                      {:else}
                        <span class="status pending">invited — pending</span>
                      {/if}
                      <!-- The last enrolled member can't be removed (household lockout). -->
                      <button
                        type="button"
                        class="revoke"
                        aria-label={m.enrolled ? `Remove ${m.name}` : `Revoke ${m.name}`}
                        disabled={m.enrolled && enrolledCount <= 1}
                        class:armed={confirming === m.name}
                        onclick={() => removeMember(m.name)}
                      >
                        {#if confirming === m.name}
                          remove?
                        {:else}
                          <Icon name="x" size={16} stroke={2} />
                        {/if}
                      </button>
                    </span>
                  </li>
                {/each}
              </ul>

              <div class="invite-row">
                <input
                  class="invite-name"
                  type="text"
                  bind:value={inviteName}
                  placeholder="+ invite someone — their name"
                  aria-label="New member name"
                  autocapitalize="words"
                  onkeydown={(e) => e.key === 'Enter' && mintInvite()}
                />
                <button
                  type="button"
                  class="primary mint"
                  onclick={mintInvite}
                  disabled={minting || !inviteName.trim()}
                >
                  {minting ? 'minting…' : 'Invite'}
                </button>
              </div>
              {#if inviteError}<p class="hint error">{inviteError}</p>{/if}
              {#if inviteLink}
                <div class="row">
                  <input
                    class="invite-link"
                    type="text"
                    readonly
                    value={inviteLink}
                    aria-label="Invite link"
                    onclick={(e) => (e.currentTarget as HTMLInputElement).select()}
                  />
                  <button type="button" class="ghost" onclick={copyLink}>{copied ? 'Copied' : 'Copy'}</button>
                </div>
                <p class="hint">Single-use, expires in 24 hours. Share it privately.</p>
              {/if}
            </div>
          {/if}
        </section>
      {/if}

      <section>
        {@render disclosure('about', 'About')}
        {#if openSections.about}
          <div
            class="region"
            id="settings-region-about"
            role="region"
            aria-label="About"
            in:unfold
            out:fold
          >
            <p class="hint">Trug v{version}</p>
            <p class="hint">
              <a
                class="repo-link"
                href="https://github.com/maxdraki/trug"
                target="_blank"
                rel="noopener noreferrer">github.com/maxdraki/trug</a
              >
            </p>
          </div>
        {/if}
      </section>

      <div class="actions">
        <button type="button" class="ghost" onclick={onClose}>Done</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: color-mix(in srgb, var(--shadow-tint) 45%, transparent);
    border: none;
    z-index: 200; /* above the footer (40) like ItemSheet */
  }
  .sheet {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 201;
    max-width: 640px;
    /* Cap the sheet so it never fills the whole viewport; the body scrolls
       within while the grip + title header stay pinned at the top. */
    max-height: 85dvh;
    margin: 0 auto;
    padding: 8px 0 0;
    background: var(--ctp-base);
    border-radius: var(--radius) var(--radius) 0 0;
    border-top: var(--hairline);
    display: flex;
    flex-direction: column;
  }
  .grip {
    width: 32px;
    height: 3px;
    border-radius: 2px;
    background: var(--ctp-surface1);
    margin: 0 auto;
    flex: 0 0 auto;
  }
  .head {
    flex: 0 0 auto;
    padding: 12px 20px;
    border-bottom: var(--hairline);
  }
  .body {
    flex: 1 1 auto;
    min-height: 0;
    overflow-y: auto;
    /* Inherit the global hairline scrollbar (app.css: thin, surface0 thumb). */
    padding: 0 20px calc(20px + env(safe-area-inset-bottom));
    display: flex;
    flex-direction: column;
  }
  h2 {
    margin: 0;
    font-family: var(--font-display);
    font-size: 19px;
    font-weight: 500;
    letter-spacing: -0.01em;
    color: var(--ctp-text);
  }
  section {
    display: flex;
    flex-direction: column;
    border-bottom: var(--hairline);
  }
  .disclosure {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    padding: 16px 0;
    background: none;
    border: none;
    cursor: pointer;
    font-family: var(--font-display);
    font-size: 12px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--ctp-subtext0);
  }
  .disclosure:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    border-radius: var(--radius);
  }
  .chev {
    display: inline-flex;
    color: var(--ctp-overlay1);
  }
  .chev.open {
    transform: rotate(90deg);
  }
  .region {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding-bottom: 16px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 8px 14px;
    font-size: 14px;
    border-radius: var(--radius);
    border: var(--hairline);
    background: var(--ctp-mantle);
    color: var(--ctp-text);
    cursor: pointer;
    transition: border-color 120ms ease;
  }
  .chip.selected {
    border-color: var(--ctp-overlay1);
    background: var(--ctp-surface0);
  }
  .toggle {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    width: 100%;
    padding: 10px 14px;
    font-size: 14px;
    text-align: left;
    border-radius: var(--radius);
    border: var(--hairline);
    background: var(--ctp-mantle);
    color: var(--ctp-text);
    cursor: pointer;
  }
  .toggle-label {
    flex: 1 1 auto;
    min-width: 0;
  }
  .track {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    width: 40px;
    height: 24px;
    padding: 3px;
    border-radius: 999px;
    background: var(--ctp-surface1);
    transition: background-color 120ms ease;
  }
  .track.on {
    background: var(--accent);
  }
  .knob {
    width: 18px;
    height: 18px;
    border-radius: 999px;
    background: var(--ctp-base);
  }
  .track.on .knob {
    transform: translateX(16px);
  }
  @media (prefers-reduced-motion: reduce) {
    .knob {
      transition: none !important;
    }
  }
  .swatch .dot {
    width: 12px;
    height: 12px;
    border-radius: 999px;
    background: var(--dot);
  }
  .row {
    display: flex;
    gap: 10px;
  }
  input {
    flex: 1 1 auto;
    min-width: 0;
    padding: 12px 14px;
    font-size: 16px;
    border-radius: var(--radius);
    border: var(--hairline);
    background: var(--ctp-mantle);
    color: var(--ctp-text);
  }
  input:focus {
    outline: none;
    border-color: var(--accent);
  }
  .hint {
    margin: 0;
    font-size: 12px;
    color: var(--ctp-subtext0);
  }
  .hint.error {
    color: var(--ctp-red);
  }
  .repo-link {
    color: var(--accent);
    text-decoration: none;
  }
  .repo-link:hover {
    text-decoration: underline;
  }
  .devices {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
  }
  .device {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 0;
    border-bottom: var(--hairline);
    font-size: 14px;
    color: var(--ctp-text);
  }
  .device:last-child {
    border-bottom: none;
  }
  .device-detail {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  .device-name {
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }
  .device-last-active {
    font-size: 12px;
    color: var(--ctp-subtext0);
  }
  .badge {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--ctp-subtext0);
    border: var(--hairline);
    padding: 2px 7px;
    border-radius: var(--radius);
  }
  .revoke {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    padding: 0;
    border: none;
    border-radius: var(--radius);
    background: none;
    color: var(--ctp-subtext0);
    cursor: pointer;
  }
  .revoke:hover {
    background: var(--ctp-surface0);
    color: var(--ctp-text);
  }
  .signout {
    align-self: flex-start;
    margin-top: 4px;
  }
  .card {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 14px;
    border-radius: var(--radius);
    border: var(--hairline);
    background: var(--ctp-mantle);
  }
  .card-title {
    font-family: var(--font-display);
    font-size: 14px;
    font-weight: 500;
    color: var(--ctp-text);
  }
  .conn-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .conn-key {
    font-size: 12px;
    color: var(--ctp-subtext0);
  }
  .conn-row .row {
    align-items: center;
  }
  .mono {
    flex: 1 1 auto;
    min-width: 0;
    padding: 10px 12px;
    font-family: ui-monospace, 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    font-size: 13px;
    border-radius: var(--radius);
    border: var(--hairline);
    background: var(--ctp-base);
    color: var(--ctp-text);
    overflow-x: auto;
    white-space: nowrap;
  }
  .copy,
  .reveal {
    flex: 0 0 auto;
  }
  .reveal {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 10px;
  }
  .invite-link {
    margin-top: 4px;
    font-size: 13px;
    color: var(--ctp-subtext0);
  }
  .mint {
    flex: 0 0 auto;
    width: auto;
    white-space: nowrap;
  }
  /* --- members ------------------------------------------------------------ */
  .members {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
  }
  .member {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 10px 0;
    border-bottom: var(--hairline);
    font-size: 14px;
    color: var(--ctp-text);
  }
  .member:last-child {
    border-bottom: none;
  }
  .member-name {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .member-right {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    flex: 0 0 auto;
  }
  .status {
    font-size: 12px;
  }
  .status.enrolled {
    color: var(--ctp-green);
  }
  .status.pending {
    color: var(--ctp-subtext0);
  }
  .revoke.armed {
    width: auto;
    padding: 0 8px;
    font-size: 12px;
    font-weight: 600;
    color: var(--ctp-red);
  }
  .revoke:disabled {
    opacity: 0.35;
    cursor: default;
  }
  .invite-row {
    display: flex;
    gap: 10px;
    align-items: center;
    margin-top: 8px;
  }
  .invite-name {
    flex: 1 1 auto;
    min-width: 0;
  }
  .actions {
    display: flex;
    justify-content: flex-end;
    padding-top: 16px;
  }
  button {
    padding: 11px 18px;
    font-size: 15px;
    border-radius: var(--radius);
    border: none;
    cursor: pointer;
  }
  .primary {
    background: var(--accent);
    color: var(--ctp-crust);
    font-weight: 600;
  }
  .primary:disabled {
    opacity: 0.6;
  }
  .ghost {
    background: var(--ctp-surface0);
    color: var(--ctp-text);
  }
  .ghost:disabled,
  .primary:disabled {
    opacity: 0.6;
    cursor: default;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .field-label {
    font-size: 12px;
    color: var(--ctp-subtext0);
  }
  .select {
    padding: 12px 14px;
    font-size: 16px;
    border-radius: var(--radius);
    border: var(--hairline);
    background: var(--ctp-mantle);
    color: var(--ctp-text);
  }
  .select:focus {
    outline: none;
    border-color: var(--accent);
  }
  .field .row {
    align-items: center;
  }
  .ai-actions {
    flex-wrap: wrap;
    margin-top: 4px;
  }
  .ai-ok {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--ctp-green);
  }
  .model-fetch {
    align-items: center;
    flex-wrap: wrap;
    margin-top: 4px;
  }
  .model-count {
    align-self: center;
  }
  .model-manual {
    padding: 8px 12px;
    font-size: 13px;
  }
  .spinner {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 2px solid var(--ctp-surface1);
    border-top-color: var(--accent);
    animation: spin 0.7s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  /* Slowed, never stopped — and it takes `data-motion="essential"` on the
     element to get that far: app.css kills every other animation outright under
     reduced motion, and a spinner that stops spinning is not a calm spinner,
     it is a missing progress indicator. */
  @media (prefers-reduced-motion: reduce) {
    .spinner {
      animation-duration: 1.6s;
    }
  }
</style>
