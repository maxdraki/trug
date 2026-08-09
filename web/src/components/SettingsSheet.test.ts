import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import SettingsSheet from './SettingsSheet.svelte';

// The sheet drives theming through applyTheme and token re-entry through setToken;
// mock both so the test asserts the calls without touching real storage/DOM state.
const applyTheme = vi.fn();
const inviteUser = vi.fn();
const listMembers = vi.fn();
const removeMember = vi.fn();
const sessions = vi.fn();
const revokeSession = vi.fn();
const logout = vi.fn();
const connections = vi.fn();
const getLlmConfig = vi.fn();
const putLlmConfig = vi.fn();
const testLlmConfig = vi.fn();
const clearLlmConfig = vi.fn();
const listLlmModels = vi.fn();
vi.mock('../lib/theme', () => ({ applyTheme: (...a: unknown[]) => applyTheme(...a) }));
vi.mock('../lib/api', () => ({
  api: {
    auth: {
      inviteUser: (...a: unknown[]) => inviteUser(...a),
      listMembers: (...a: unknown[]) => listMembers(...a),
      removeMember: (...a: unknown[]) => removeMember(...a),
      sessions: (...a: unknown[]) => sessions(...a),
      revokeSession: (...a: unknown[]) => revokeSession(...a),
      logout: (...a: unknown[]) => logout(...a),
      connections: (...a: unknown[]) => connections(...a),
      getLlmConfig: (...a: unknown[]) => getLlmConfig(...a),
      putLlmConfig: (...a: unknown[]) => putLlmConfig(...a),
      testLlmConfig: (...a: unknown[]) => testLlmConfig(...a),
      clearLlmConfig: (...a: unknown[]) => clearLlmConfig(...a),
      listLlmModels: (...a: unknown[]) => listLlmModels(...a),
    },
  },
}));

const LLM_NONE = {
  provider: null,
  model: null,
  base_url: null,
  configured: false,
  key_hint: null,
  source: 'none',
};

const CONNECTIONS = {
  mcp_url: 'http://localhost:8000/mcp',
  mcp_token: 'tok-mcp-secret',
  webhook_url: 'http://localhost:8000/api/capture',
  ring_token: 'tok-ring-secret',
};

describe('SettingsSheet', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    // The Members section loads the household roster from the server on open.
    listMembers.mockResolvedValue({
      users: [
        { name: 'alice', enrolled: true, created_at: '', credential_count: 1 },
        { name: 'bob', enrolled: false, created_at: '', credential_count: 0 },
      ],
      me: 'alice',
    });
    connections.mockResolvedValue(CONNECTIONS);
    getLlmConfig.mockResolvedValue(LLM_NONE);
    listLlmModels.mockResolvedValue({ models: [] });
  });

  it('applies the chosen flavour (with the current accent) via applyTheme', async () => {
    render(SettingsSheet, { open: true, onClose: vi.fn() });

    await fireEvent.click(screen.getByRole('button', { name: 'Latte' }));

    expect(applyTheme).toHaveBeenCalledWith('latte', 'peach');
  });

  it('maps the Auto flavour to a null flavour', async () => {
    render(SettingsSheet, { open: true, onClose: vi.fn() });

    // Move off Auto first, then back, so the click is a real state change.
    await fireEvent.click(screen.getByRole('button', { name: 'Mocha' }));
    applyTheme.mockClear();
    await fireEvent.click(screen.getByRole('button', { name: 'Auto' }));

    expect(applyTheme).toHaveBeenCalledWith(null, 'peach');
  });

  it('applies the chosen accent via applyTheme', async () => {
    render(SettingsSheet, { open: true, onClose: vi.fn() });

    await fireEvent.click(screen.getByRole('button', { name: 'Mauve' }));

    expect(applyTheme).toHaveBeenCalledWith(null, 'mauve');
  });

  it('defaults Theme + Accent open and the occasional sections collapsed', () => {
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });

    const expanded = (name: string) =>
      screen.getByRole('button', { name }).getAttribute('aria-expanded');
    expect(expanded('Theme')).toBe('true');
    expect(expanded('Accent')).toBe('true');
    expect(expanded('Devices')).toBe('false');
    expect(expanded('Members')).toBe('false');
    expect(expanded('Connections')).toBe('false');
  });

  it('persists a section toggle to localStorage', async () => {
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });

    await fireEvent.click(screen.getByRole('button', { name: 'Members' }));
    expect(screen.getByRole('button', { name: 'Members' }).getAttribute('aria-expanded')).toBe(
      'true',
    );
    const saved = JSON.parse(localStorage.getItem('trug_settings_open') ?? '{}');
    expect(saved.members).toBe(true);
  });

  it('lists devices for a cookie-authed human and flags the current one', async () => {
    sessions.mockResolvedValue([
      { id: 's1', created_at: '', last_seen: '', user_agent: 'iPhone; Mobile', current: true },
      { id: 's2', created_at: '', last_seen: '', user_agent: 'Macintosh', current: false },
    ]);
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });

    // Devices defaults collapsed — expand it to reach the device list.
    await fireEvent.click(screen.getByRole('button', { name: 'Devices' }));
    await waitFor(() => expect(sessions).toHaveBeenCalled());
    expect(await screen.findByText('this device')).toBeTruthy();
    // Only the non-current device is revocable.
    const revoke = await screen.findByRole('button', { name: /revoke mac/i });
    await fireEvent.click(revoke);
    await waitFor(() => expect(revokeSession).toHaveBeenCalledWith('s2'));
  });

  it('shows a last-active age for a device so multiple devices are distinguishable', async () => {
    const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
    sessions.mockResolvedValue([
      { id: 's1', created_at: '', last_seen: '', user_agent: 'iPhone; Mobile', current: true },
      { id: 's2', created_at: '', last_seen: twoHoursAgo, user_agent: 'Macintosh', current: false },
    ]);
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });

    await fireEvent.click(screen.getByRole('button', { name: 'Devices' }));
    await waitFor(() => expect(sessions).toHaveBeenCalled());
    expect(await screen.findByText(/last active .*ago/i)).toBeTruthy();
    expect(screen.getByText(/hours ago/i)).toBeTruthy();
  });

  it('does not query sessions when not cookie-authed', () => {
    render(SettingsSheet, { open: true, onClose: vi.fn() });
    expect(sessions).not.toHaveBeenCalled();
  });

  it('invites a new name (free text) and builds a share link at #invite=', async () => {
    inviteUser.mockResolvedValue({ invite: 'tok-xyz', name: 'guest' });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });

    // Members defaults collapsed — expand it to reach the invite row.
    await fireEvent.click(screen.getByRole('button', { name: 'Members' }));
    await waitFor(() => expect(listMembers).toHaveBeenCalled());
    await fireEvent.input(screen.getByLabelText('New member name'), {
      target: { value: 'guest' },
    });
    await fireEvent.click(screen.getByRole('button', { name: /^invite$/i }));

    await waitFor(() => expect(inviteUser).toHaveBeenCalledWith('guest'));
    const link = (await screen.findByLabelText('Invite link')) as HTMLInputElement;
    expect(link.value).toBe(`${location.origin}/#invite=tok-xyz`);
  });

  it('lists the roster with enrolled/pending status and marks "you"', async () => {
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });

    await fireEvent.click(screen.getByRole('button', { name: 'Members' }));
    await waitFor(() => expect(listMembers).toHaveBeenCalled());
    // alice is enrolled and is the caller ("you"); bob is a pending invite.
    expect(await screen.findByText('enrolled ✓')).toBeTruthy();
    expect(screen.getByText('you')).toBeTruthy();
    expect(screen.getByText(/invited — pending/i)).toBeTruthy();
  });

  it('two-tap remove of a pending member calls removeMember', async () => {
    removeMember.mockResolvedValue(undefined);
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });

    await fireEvent.click(screen.getByRole('button', { name: 'Members' }));
    await waitFor(() => expect(listMembers).toHaveBeenCalled());
    const revoke = await screen.findByRole('button', { name: /revoke bob/i });
    await fireEvent.click(revoke); // arms
    await fireEvent.click(screen.getByRole('button', { name: /revoke bob/i })); // confirms
    await waitFor(() => expect(removeMember).toHaveBeenCalledWith('bob'));
  });

  it('disables removing the last enrolled member', async () => {
    listMembers.mockResolvedValue({
      users: [{ name: 'alice', enrolled: true, created_at: '', credential_count: 1 }],
      me: 'alice',
    });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });

    await fireEvent.click(screen.getByRole('button', { name: 'Members' }));
    await waitFor(() => expect(listMembers).toHaveBeenCalled());
    const remove = (await screen.findByRole('button', { name: /remove alice/i })) as HTMLButtonElement;
    expect(remove.disabled).toBe(true);
  });

  it('hides the Members section when not cookie-authed', () => {
    render(SettingsSheet, { open: true, onClose: vi.fn() });
    expect(listMembers).not.toHaveBeenCalled();
    expect(screen.queryByText('Members')).toBeNull();
  });

  it('hides the connections section when not cookie-authed', () => {
    render(SettingsSheet, { open: true, onClose: vi.fn() });
    expect(connections).not.toHaveBeenCalled();
    expect(screen.queryByText('Connections')).toBeNull();
    expect(screen.queryByText(/AI assistants/i)).toBeNull();
  });

  it('renders MCP + ring connection details for a cookie-authed human', async () => {
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });

    // Connections defaults collapsed — expand it to reach the MCP/ring cards.
    await fireEvent.click(screen.getByRole('button', { name: 'Connections' }));
    await waitFor(() => expect(connections).toHaveBeenCalled());
    expect(await screen.findByText(/AI assistants \(MCP\)/i)).toBeTruthy();
    expect(screen.getByText(/Pebble ring \(webhook\)/i)).toBeTruthy();
    // URLs render in the clear.
    expect(screen.getByText('http://localhost:8000/mcp')).toBeTruthy();
    expect(screen.getByText('http://localhost:8000/api/capture')).toBeTruthy();
    // The out-of-the-box hint is present.
    expect(screen.getByText(/works out of the box/i)).toBeTruthy();
    // Tokens are masked until revealed.
    expect(screen.queryByText('tok-mcp-secret')).toBeNull();
    expect(screen.queryByText('tok-ring-secret')).toBeNull();
  });

  it('reveals a masked token when its reveal toggle is tapped', async () => {
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });

    await fireEvent.click(screen.getByRole('button', { name: 'Connections' }));
    await waitFor(() => expect(connections).toHaveBeenCalled());
    // The ring token is masked initially, then revealed on toggle. Two secret
    // rows share the "Reveal Token" label; the last is the ring token.
    const reveals = await screen.findAllByRole('button', { name: /reveal token/i });
    await fireEvent.click(reveals[reveals.length - 1]);
    expect(await screen.findByText('tok-ring-secret')).toBeTruthy();
  });

  it('signs out and reloads only on a confirmed logout success', async () => {
    logout.mockResolvedValue(undefined);
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await fireEvent.click(screen.getByRole('button', { name: 'Devices' }));
    await fireEvent.click(await screen.findByRole('button', { name: /sign out/i }));
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });

  it('surfaces an inline error and stays on the sheet when sign-out fails', async () => {
    logout.mockRejectedValue(new Error('offline'));
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await fireEvent.click(screen.getByRole('button', { name: 'Devices' }));
    await fireEvent.click(await screen.findByRole('button', { name: /sign out/i }));
    expect(await screen.findByText(/couldn't sign out/i)).toBeTruthy();
    // Still on the sheet (sign-out button remains).
    expect(screen.getByRole('button', { name: /sign out/i })).toBeTruthy();
  });

  // --- AI enrichment (BYOK) --------------------------------------------------

  it('hides the AI enrichment section when not cookie-authed', () => {
    render(SettingsSheet, { open: true, onClose: vi.fn() });
    expect(getLlmConfig).not.toHaveBeenCalled();
    expect(screen.queryByText('AI enrichment')).toBeNull();
  });

  async function openAi() {
    await fireEvent.click(screen.getByRole('button', { name: 'AI enrichment' }));
    await waitFor(() => expect(getLlmConfig).toHaveBeenCalled());
  }

  it('changing the provider prefills that provider default model', async () => {
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await openAi();
    const model = screen.getByLabelText('Model') as HTMLInputElement;
    // Default provider is Anthropic.
    expect(model.value).toBe('claude-haiku-4-5');
    await fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } });
    expect((screen.getByLabelText('Model') as HTMLInputElement).value).toBe('gpt-4o-mini');
  });

  it('renders a successful test as milk → category and enables Save', async () => {
    testLlmConfig.mockResolvedValue({
      ok: true,
      detail: 'Working — milk → Dairy & Eggs',
      result: { icon: 'milk', category: 'Dairy & Eggs' },
    });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await openAi();

    // Save is disabled until a successful test.
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);

    await fireEvent.input(screen.getByLabelText('API key'), { target: { value: 'sk-x' } });
    await fireEvent.click(screen.getByRole('button', { name: /test connection/i }));

    await waitFor(() => expect(testLlmConfig).toHaveBeenCalled());
    expect(await screen.findByText(/Working — milk →/)).toBeTruthy();
    expect(screen.getByText('Dairy & Eggs')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('renders a failed test with the error detail', async () => {
    testLlmConfig.mockResolvedValue({ ok: false, detail: '401 unauthorized — bad key' });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await openAi();
    await fireEvent.input(screen.getByLabelText('API key'), { target: { value: 'sk-bad' } });
    await fireEvent.click(screen.getByRole('button', { name: /test connection/i }));
    expect(await screen.findByText(/401 unauthorized — bad key/)).toBeTruthy();
    // Save stays disabled after a failed test.
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('saves the config via PUT after a successful test', async () => {
    testLlmConfig.mockResolvedValue({
      ok: true,
      detail: 'Working — milk → Dairy & Eggs',
      result: { icon: 'milk', category: 'Dairy & Eggs' },
    });
    putLlmConfig.mockResolvedValue({
      provider: 'openai',
      model: 'gpt-4o-mini',
      base_url: null,
      configured: true,
      key_hint: '••••sk-x',
      source: 'settings',
    });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await openAi();
    await fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } });
    await fireEvent.input(screen.getByLabelText('API key'), { target: { value: 'sk-secret' } });
    await fireEvent.click(screen.getByRole('button', { name: /test connection/i }));
    await waitFor(() => expect(testLlmConfig).toHaveBeenCalled());
    await fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(putLlmConfig).toHaveBeenCalled());
    expect(putLlmConfig).toHaveBeenCalledWith(
      expect.objectContaining({ provider: 'openai', api_key: 'sk-secret', model: 'gpt-4o-mini' }),
    );
  });

  it('clears a green test (re-disabling Save) when the model is edited', async () => {
    testLlmConfig.mockResolvedValue({
      ok: true,
      detail: 'Working — milk → Dairy & Eggs',
      result: { icon: 'milk', category: 'Dairy & Eggs' },
    });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await openAi();
    await fireEvent.input(screen.getByLabelText('API key'), { target: { value: 'sk-x' } });
    await fireEvent.click(screen.getByRole('button', { name: /test connection/i }));
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    // Editing the model after a green test must force a re-test before Save.
    await fireEvent.input(screen.getByLabelText('Model'), { target: { value: 'gpt-4o' } });
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('clears a green test when the API key is edited', async () => {
    testLlmConfig.mockResolvedValue({
      ok: true,
      detail: 'Working — milk → Dairy & Eggs',
      result: { icon: 'milk', category: 'Dairy & Eggs' },
    });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await openAi();
    await fireEvent.input(screen.getByLabelText('API key'), { target: { value: 'sk-x' } });
    await fireEvent.click(screen.getByRole('button', { name: /test connection/i }));
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    await fireEvent.input(screen.getByLabelText('API key'), { target: { value: 'sk-changed' } });
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });

  // --- live model listing ----------------------------------------------------

  it('fetches the provider models and switches the model control to a dropdown', async () => {
    listLlmModels.mockResolvedValue({ models: ['gemini-3.6-flash', 'gemini-2.5-flash'] });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await openAi();
    await fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'gemini' } });
    await fireEvent.input(screen.getByLabelText('API key'), { target: { value: 'g-key' } });
    await fireEvent.click(screen.getByRole('button', { name: /fetch models/i }));
    await waitFor(() => expect(listLlmModels).toHaveBeenCalled());
    const model = (await screen.findByLabelText('Model')) as HTMLElement;
    expect(model.tagName).toBe('SELECT');
    // The current model (gemini-2.5-flash) is in the fetched list, so it's kept
    // as the selection; both fetched ids are offered as options.
    expect((model as HTMLSelectElement).value).toBe('gemini-2.5-flash');
    expect(screen.getByRole('option', { name: 'gemini-3.6-flash' })).toBeTruthy();
  });

  it('shows the fetch error detail and keeps manual entry when the fetch fails', async () => {
    listLlmModels.mockResolvedValue({ models: [], detail: '401 unauthorized — bad key' });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await openAi();
    await fireEvent.input(screen.getByLabelText('API key'), { target: { value: 'sk-bad' } });
    await fireEvent.click(screen.getByRole('button', { name: /fetch models/i }));
    expect(await screen.findByText(/401 unauthorized — bad key/)).toBeTruthy();
    // The Model control stays a free-text input for manual entry.
    expect((screen.getByLabelText('Model') as HTMLElement).tagName).toBe('INPUT');
  });

  it('clears the fetched dropdown when the provider changes', async () => {
    listLlmModels.mockResolvedValue({ models: ['gemini-3.6-flash', 'gemini-2.5-flash'] });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await openAi();
    await fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'gemini' } });
    await fireEvent.input(screen.getByLabelText('API key'), { target: { value: 'g-key' } });
    await fireEvent.click(screen.getByRole('button', { name: /fetch models/i }));
    await waitFor(() => expect((screen.getByLabelText('Model') as HTMLElement).tagName).toBe('SELECT'));
    // Switching provider drops the list — back to the manual input control.
    await fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } });
    expect((screen.getByLabelText('Model') as HTMLElement).tagName).toBe('INPUT');
  });

  it('selecting a fetched model re-gates Save', async () => {
    testLlmConfig.mockResolvedValue({
      ok: true,
      detail: 'Working — milk → Dairy & Eggs',
      result: { icon: 'milk', category: 'Dairy & Eggs' },
    });
    listLlmModels.mockResolvedValue({ models: ['gpt-4o', 'gpt-4o-mini'] });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await openAi();
    await fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } });
    await fireEvent.input(screen.getByLabelText('API key'), { target: { value: 'sk-x' } });
    await fireEvent.click(screen.getByRole('button', { name: /fetch models/i }));
    await waitFor(() => expect((screen.getByLabelText('Model') as HTMLElement).tagName).toBe('SELECT'));
    await fireEvent.click(screen.getByRole('button', { name: /test connection/i }));
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false),
    );
    // Picking a different model must force a re-test before Save re-enables.
    await fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'gpt-4o' } });
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('renders an env-sourced config read-only, with no Replace and no Clear', async () => {
    getLlmConfig.mockResolvedValue({
      provider: 'anthropic',
      model: 'claude-haiku-4-5',
      base_url: null,
      configured: true,
      key_hint: '••••env1',
      source: 'env',
    });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await openAi();
    // Distinct env framing; not the "configured ••••/Replace" stored-key affordance.
    expect(await screen.findByText(/key from the server environment/i)).toBeTruthy();
    expect(screen.queryByText(/configured ••••env1/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Replace' })).toBeNull();
    // Clear is only for a settings-sourced (household-saved) config.
    expect(screen.queryByRole('button', { name: 'Clear' })).toBeNull();
    // An override path is offered instead.
    expect(screen.getByRole('button', { name: /override with your own key/i })).toBeTruthy();
  });

  it('tests an env-sourced config without sending a key (server uses the env key)', async () => {
    getLlmConfig.mockResolvedValue({
      provider: 'anthropic',
      model: 'claude-haiku-4-5',
      base_url: null,
      configured: true,
      key_hint: '••••env1',
      source: 'env',
    });
    testLlmConfig.mockResolvedValue({
      ok: true,
      detail: 'Working — milk → Dairy & Eggs',
      result: { icon: 'milk', category: 'Dairy & Eggs' },
    });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await openAi();
    await fireEvent.click(screen.getByRole('button', { name: /test connection/i }));
    await waitFor(() => expect(testLlmConfig).toHaveBeenCalled());
    // No api_key is sent — the server falls back to the effective (env) key.
    expect(testLlmConfig.mock.calls[0][0]).not.toHaveProperty('api_key');
    // Even after a green test, Save stays disabled for a bare env config.
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('prompts to re-enter a key when the stored key is unreadable', async () => {
    getLlmConfig.mockResolvedValue({
      provider: 'openai',
      model: 'gpt-4o-mini',
      base_url: null,
      configured: false,
      key_hint: '••••9876',
      source: 'settings',
      unreadable_key: true,
    });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await openAi();
    expect(await screen.findByText(/no longer be decrypted/i)).toBeTruthy();
    // The key input is shown for re-entry; no "configured/Replace" masked row.
    expect(screen.getByLabelText('API key')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Replace' })).toBeNull();
  });

  it('shows a masked configured key with a Replace toggle', async () => {
    getLlmConfig.mockResolvedValue({
      provider: 'openai',
      model: 'gpt-4o-mini',
      base_url: null,
      configured: true,
      key_hint: '••••9876',
      source: 'settings',
    });
    render(SettingsSheet, { open: true, cookieAuth: true, onClose: vi.fn() });
    await openAi();
    // The masked hint shows; no key input until Replace is tapped.
    expect(await screen.findByText(/configured ••••9876/)).toBeTruthy();
    expect(screen.queryByLabelText('API key')).toBeNull();
    await fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    expect(screen.getByLabelText('API key')).toBeTruthy();
  });

  // --- About ------------------------------------------------------------------

  it('shows the version and repo link in the About section', async () => {
    render(SettingsSheet, { open: true, onClose: vi.fn() });

    await fireEvent.click(screen.getByRole('button', { name: 'About' }));
    const link = (await screen.findByRole('link', {
      name: /github\.com\/maxdraki\/trug/i,
    })) as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('https://github.com/maxdraki/trug');
    // Version string is present; exact number depends on the build's define, so
    // just assert a "v…" line (v0.1.1 in a real build, v-dev without the define).
    expect(screen.getByText(/\bv\S/i)).toBeTruthy();
  });
});
