// SettingsView key-status badges — pins the bug fix where the "set"/"empty"
// badge must reflect backend-confirmed `present` state, never the raw input
// text, and must refresh after save/clear instead of going stale or being
// wiped by a transient getConfig failure.
import { test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { SettingsView } from './settings.jsx';

const getConfig = vi.fn();
const saveConfig = vi.fn();
const sendTestNotification = vi.fn();

vi.mock('../../api/client', () => ({
  getConfig: (...a) => getConfig(...a),
  saveConfig: (...a) => saveConfig(...a),
  getModels: vi.fn(async () => ({ models: [] })),
  cookiesStatus: vi.fn(async () => ({ configured: false })),
  uploadCookies: vi.fn(),
  deleteCookies: vi.fn(),
  getZernio: vi.fn(async () => ({ configured: false })),
  saveZernio: vi.fn(),
  discoverZernioAccounts: vi.fn(),
  listFonts: vi.fn(async () => ({ fonts: [] })),
  uploadFont: vi.fn(),
  deleteFont: vi.fn(),
  logoStatus: vi.fn(async () => ({ configured: false })),
  uploadLogo: vi.fn(),
  deleteLogo: vi.fn(),
  sendTestNotification: (...a) => sendTestNotification(...a),
}));

const EMPTY_CONFIG = { GEMINI_API_KEY: '', HF_TOKEN: '', DEEPGRAM_API_KEY: '', ELEVENLABS_API_KEY: '' };
const SET_CONFIG = { ...EMPTY_CONFIG, GEMINI_API_KEY: 'AIza...xyz1' };

beforeEach(() => {
  vi.clearAllMocks();
  saveConfig.mockResolvedValue({ success: true });
});

function mount(pushToast = vi.fn()) {
  render(<SettingsView pushToast={pushToast} />);
  return pushToast;
}

const geminiRow = () => screen.getByLabelText('Gemini').closest('.keyrow');

test('badge reflects backend state, not input text typed before any save', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  mount();
  await waitFor(() => expect(within(geminiRow()).getByText('empty')).toBeInTheDocument());

  // Typing into the field (no blur/save yet) must not flip the badge.
  fireEvent.change(screen.getByLabelText('Gemini'), { target: { value: 'AIzaSomeKey' } });
  expect(within(geminiRow()).getByText('empty')).toBeInTheDocument();
  expect(within(geminiRow()).queryByText('set')).toBeNull();
});

test('save triggers a refetch and the badge updates from the backend response', async () => {
  getConfig.mockResolvedValueOnce(EMPTY_CONFIG).mockResolvedValueOnce(SET_CONFIG);
  mount();
  await waitFor(() => expect(within(geminiRow()).getByText('empty')).toBeInTheDocument());

  const input = screen.getByLabelText('Gemini');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'AIzaSomeKey' } });
  fireEvent.blur(input);

  expect(saveConfig).toHaveBeenCalledWith({ GEMINI_API_KEY: 'AIzaSomeKey' });
  await waitFor(() => expect(getConfig).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(within(geminiRow()).getByText('set')).toBeInTheDocument());
});

test('a failed post-save refetch does not wipe previously-known present state', async () => {
  getConfig.mockResolvedValueOnce(SET_CONFIG).mockResolvedValueOnce(null);
  const pushToast = mount();
  await waitFor(() => expect(within(geminiRow()).getByText('set')).toBeInTheDocument());

  const input = screen.getByLabelText('Deepgram');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'dg_key' } });
  fireEvent.blur(input);

  await waitFor(() => expect(getConfig).toHaveBeenCalledTimes(2));
  // Gemini's badge (unrelated to the Deepgram save) must still read "set".
  expect(within(geminiRow()).getByText('set')).toBeInTheDocument();
  expect(pushToast).toHaveBeenCalledWith('warn', expect.any(String));
});

test('clearing a present key saves an empty value and the badge flips to empty', async () => {
  getConfig.mockResolvedValueOnce(SET_CONFIG).mockResolvedValueOnce(EMPTY_CONFIG);
  mount();
  await waitFor(() => expect(within(geminiRow()).getByText('set')).toBeInTheDocument());

  fireEvent.click(within(geminiRow()).getByRole('button', { name: 'Clear Gemini key' }));

  expect(saveConfig).toHaveBeenCalledWith({ GEMINI_API_KEY: '' });
  await waitFor(() => expect(within(geminiRow()).getByText('empty')).toBeInTheDocument());
});

test('Twitch client id/secret rows reflect backend present state', async () => {
  getConfig.mockResolvedValue({ ...EMPTY_CONFIG, TWITCH_CLIENT_ID: 'abcd1234', TWITCH_CLIENT_SECRET: 'shhh12345678' });
  mount();
  const idRow = () => screen.getByLabelText('Twitch client ID').closest('.keyrow');
  const secretRow = () => screen.getByLabelText('Twitch client secret').closest('.keyrow');
  await waitFor(() => expect(within(idRow()).getByText('set')).toBeInTheDocument());
  expect(within(secretRow()).getByText('set')).toBeInTheDocument();
});

test('Twitch client id/secret rows show empty when unset', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  mount();
  const idRow = () => screen.getByLabelText('Twitch client ID').closest('.keyrow');
  await waitFor(() => expect(within(idRow()).getByText('empty')).toBeInTheDocument());
});

test('Generate saves a private ntfy topic and shows it once to subscribe', async () => {
  getConfig.mockResolvedValueOnce(EMPTY_CONFIG).mockResolvedValue({ ...EMPTY_CONFIG, NTFY_TOPIC: 'clip...abcd' });
  mount();
  const testBtn = await screen.findByRole('button', { name: 'Send test' });
  expect(testBtn).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
  await waitFor(() => expect(saveConfig).toHaveBeenCalled());
  const topic = saveConfig.mock.calls[0][0].NTFY_TOPIC;
  expect(topic).toMatch(/^clippyme-[a-z0-9]{16}$/);
  expect(screen.getByText(topic)).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Send test' })).not.toBeDisabled());
});

test('Send test reports success from the backend', async () => {
  getConfig.mockResolvedValue({ ...EMPTY_CONFIG, NTFY_TOPIC: 'clip...abcd' });
  sendTestNotification.mockResolvedValue({ sent: true });
  const toast = mount();
  const btn = await screen.findByRole('button', { name: 'Send test' });
  await waitFor(() => expect(btn).not.toBeDisabled());
  fireEvent.click(btn);
  await waitFor(() => expect(toast).toHaveBeenCalledWith('success', 'Test sent. Check your phone.'));
});
