// LiveMonitorView — pins: per-platform channel validation gates Start, the
// monitor list renders multiple concurrent monitors, and youtube forces VOD.
import { test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { LiveMonitorView } from './live.jsx';

const mockStatus = vi.fn();

vi.mock('../../api/client', () => ({
  getZernio: vi.fn(async () => ({
    configured: true,
    accounts: { tiktok: 'tt-1', instagram: '', youtube: '' },
  })),
  startLiveMonitor: vi.fn(async () => ({ id: 'kick:xqc', running: true, state: 'waiting_live' })),
  stopLiveMonitor: vi.fn(async () => ({ running: false, state: 'idle' })),
  getLiveMonitorStatus: (...args) => mockStatus(...args),
  updateMonitorConfig: vi.fn(async () => ({ monitor: {} })),
  setMonitorPublishing: vi.fn(async () => ({ publishing_enabled: true })),
  discardPendingClip: vi.fn(async () => ({ discarded: 'p-1', pending_publish: 1 })),
  publishPendingClip: vi.fn(async () => ({ approved: 'p-1', pending_publish: 1 })),
  failedClipAction: vi.fn(async () => ({})),
  listFonts: vi.fn(async () => ({ fonts: [] })),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockStatus.mockResolvedValue({ monitors: [] });
});

test('start is disabled until a valid channel is entered', async () => {
  render(<LiveMonitorView />);
  await screen.findByLabelText('Channel');
  const startBtn = screen.getByRole('button', { name: /Start monitor/ });
  expect(startBtn).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(startBtn).not.toBeDisabled());
});

test('invalid channel shows an inline error after blur', () => {
  render(<LiveMonitorView />);
  const input = screen.getByLabelText('Channel');
  fireEvent.change(input, { target: { value: 'has space' } });
  fireEvent.blur(input);
  expect(screen.getByText(/lowercase letters, numbers/)).toBeInTheDocument();
});

test('empty channel still blocks start after touching the field', () => {
  render(<LiveMonitorView />);
  const input = screen.getByLabelText('Channel');
  fireEvent.blur(input);
  expect(screen.getByText('Channel is required')).toBeInTheDocument();
});

test('start form is always visible alongside a running monitor', async () => {
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing',
      channel: 'xqc', segments_captured: 2, clips_published: 5 }],
  });
  render(<LiveMonitorView />);
  expect(await screen.findByText('Capturing segment')).toBeInTheDocument();
  expect(screen.getByText('xqc')).toBeInTheDocument();
  expect(screen.getByText(/2 segment\(s\) captured/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Start monitor/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Stop/ })).toBeInTheDocument();
});

test('monitor list renders multiple concurrent monitors', async () => {
  mockStatus.mockResolvedValue({
    monitors: [
      { id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc' },
      { id: 'youtube:@MrBeast', platform: 'youtube', mode: 'vod', running: true, state: 'watching', channel: '@MrBeast' },
    ],
  });
  render(<LiveMonitorView />);
  expect(await screen.findByText('xqc')).toBeInTheDocument();
  expect(screen.getByText('@MrBeast')).toBeInTheDocument();
  expect(screen.getByText('Watching for new uploads')).toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: /Stop/ })).toHaveLength(2);
});

test('idle empty list shows "No monitors running."', async () => {
  render(<LiveMonitorView />);
  expect(await screen.findByText('No monitors running.')).toBeInTheDocument();
});

test('selecting YouTube forces VOD mode and hides live-only fields', async () => {
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('button', { name: 'YouTube' }));
  await waitFor(() => expect(screen.queryByLabelText('Segment minutes')).toBeNull());
  expect(screen.queryByLabelText('Prelive skip minutes')).toBeNull();
  expect(screen.getByText(/YouTube: clips every new long-form upload/)).toBeInTheDocument();
});

test('duplicate monitor (409) shows a warning toast', async () => {
  const { startLiveMonitor } = await import('../../api/client');
  startLiveMonitor.mockRejectedValueOnce(new Error('monitor already running: kick:xqc'));
  const pushToast = vi.fn();
  render(<LiveMonitorView pushToast={pushToast} />);
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(pushToast).toHaveBeenCalledWith('warn', expect.stringMatching(/already monitoring/i)));
});

test('banner defaults to Auto and sends null', async () => {
  const { startLiveMonitor } = await import('../../api/client');
  render(<LiveMonitorView />);
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].banner).toBeNull();
});

test('AI instructions field is included in the start payload', async () => {
  const { startLiveMonitor } = await import('../../api/client');
  render(<LiveMonitorView />);
  fireEvent.change(screen.getByLabelText('AI instructions'), { target: { value: 'find the funniest bits' } });
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].instructions).toBe('find the funniest bits');
});

test('banner Off sends {enabled:false}', async () => {
  const { startLiveMonitor } = await import('../../api/client');
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('button', { name: 'Off' }));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].banner).toEqual({ enabled: false });
});

test('banner Custom reveals platform+handle and sends the override', async () => {
  const { startLiveMonitor } = await import('../../api/client');
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('button', { name: 'Custom' }));
  const twitchBtns = screen.getAllByRole('button', { name: 'Twitch' });
  fireEvent.click(twitchBtns[twitchBtns.length - 1]); // the banner drawer's platform picker
  fireEvent.change(screen.getByLabelText('Banner handle'), { target: { value: 'xqc' } });
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].banner).toEqual({ platform: 'twitch', handle: 'xqc', y_pct: 0.85 });
});

test('catchup select value rides the start payload', async () => {
  const { startLiveMonitor } = await import('../../api/client');
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('button', { name: 'From now only' }));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].catchup).toBe('live_only');
});

test('catchup defaults to backfill', async () => {
  const { startLiveMonitor } = await import('../../api/client');
  render(<LiveMonitorView />);
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].catchup).toBe('backfill');
});

test('prelive skip survives VOD mode but disappears for YouTube', async () => {
  render(<LiveMonitorView />);
  // A Kick VOD is the recording of a live — the waiting screen is still there.
  fireEvent.click(screen.getByRole('button', { name: 'VOD' }));
  expect(screen.getByLabelText('Prelive skip minutes')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'YouTube' }));
  await waitFor(() => expect(screen.queryByLabelText('Prelive skip minutes')).toBeNull());
});

test('catchup control is hidden in VOD mode', async () => {
  render(<LiveMonitorView />);
  expect(screen.getByRole('button', { name: 'From now only' })).toBeInTheDocument();
  // VOD processes whole feed items — there is no missed-window backfill to steer.
  fireEvent.click(screen.getByRole('button', { name: 'VOD' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: 'From now only' })).toBeNull());
});

test('subtitle override section untouched → start payload has no compose key', async () => {
  const { startLiveMonitor } = await import('../../api/client');
  render(<LiveMonitorView />);
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].compose).toBeUndefined();
});

test('subtitle override section switched on → start payload carries a compose.subtitle_params key', async () => {
  const { startLiveMonitor } = await import('../../api/client');
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('switch', { name: 'Customize subtitles' }));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].compose).toEqual({ subtitle_params: expect.objectContaining({ position: 'bottom' }) });
});

test('classic-mode subtitle override translates keys in the start payload (bg_opacity/bg_color/border_color, no bg/outline_color)', async () => {
  const { startLiveMonitor } = await import('../../api/client');
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('switch', { name: 'Customize subtitles' }));
  fireEvent.click(screen.getByRole('button', { name: 'Classic' }));
  const bgRow = screen.getByText('Background box').closest('.opt');
  fireEvent.click(within(bgRow).getByRole('switch'));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  const params = startLiveMonitor.mock.calls[0][0].compose.subtitle_params;
  expect(params.bg_opacity).toBe(0.6);
  expect(params.bg_color).toBe('#000000');
  expect(params.border_color).toBe('#000000');
  expect(params.bg).toBeUndefined();
  expect(params.outline_color).toBeUndefined();
});

test('classic-mode subtitle override translates keys in the config Applica payload too', async () => {
  const { updateMonitorConfig } = await import('../../api/client');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc' }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  const overridesRow = screen.getByText('Subtitle overrides').closest('.opt');
  fireEvent.click(within(overridesRow).getByRole('switch'));
  fireEvent.click(screen.getByRole('button', { name: 'Classic' }));
  const bgRow = screen.getByText('Background box').closest('.opt');
  fireEvent.click(within(bgRow).getByRole('switch'));
  fireEvent.click(screen.getByRole('button', { name: /Apply/ }));
  await waitFor(() => expect(updateMonitorConfig).toHaveBeenCalled());
  const params = updateMonitorConfig.mock.calls[0][1].compose.subtitle_params;
  expect(params.bg_opacity).toBe(0.6);
  expect(params.bg_color).toBe('#000000');
  expect(params.border_color).toBe('#000000');
  expect(params.bg).toBeUndefined();
  expect(params.outline_color).toBeUndefined();
});

test('Settings drawer prefills from the running monitor status().config', async () => {
  mockStatus.mockResolvedValue({
    monitors: [{
      id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc',
      config: {
        instructions: 'find hype moments', caption_template: '{hook}', title_template: '{title}',
        segment_seconds: 900, prelive_skip_seconds: 60, min_gap_seconds: 300,
        compose: {
          subtitle_params: {
            mode: 'classic', preset: 'classic_white', font: 'Montserrat-Black', font_color: '#FFFFFF',
            border_color: '#111111', border_width: 3, bg_opacity: 0.6, bg_color: '#000000',
            position: 'top', align: 'left', offset_y: 10,
          },
        },
      },
    }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  expect(screen.getByLabelText('Settings instructions kick:xqc')).toHaveValue('find hype moments');
  expect(screen.getByLabelText('Settings caption template kick:xqc')).toHaveValue('{hook}');
  expect(screen.getByLabelText('Settings title template kick:xqc')).toHaveValue('{title}');
  expect(screen.getByLabelText('Settings segment minutes kick:xqc')).toHaveValue(15);
  expect(screen.getByLabelText('Settings prelive skip minutes kick:xqc')).toHaveValue(1);
  expect(screen.getByLabelText('Settings min gap minutes kick:xqc')).toHaveValue(5);
  // Subtitle override drawer already open in classic mode with Background box on
  // (proves fromComposeSubtitleParams seeded mode/bg from the persisted config).
  expect(screen.getByText('Background box')).toBeInTheDocument();
  const bgRow = screen.getByText('Background box').closest('.opt');
  expect(within(bgRow).getByRole('switch')).toHaveAttribute('aria-checked', 'true');
});

test('publishing toggle calls setMonitorPublishing with the flipped value', async () => {
  const { setMonitorPublishing } = await import('../../api/client');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing',
      channel: 'xqc', publishing_enabled: false, pending_publish: 3 }],
  });
  render(<LiveMonitorView />);
  expect(await screen.findByText('Paused — 3 clip(s) waiting')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('switch', { name: 'Zernio auto-publish kick:xqc' }));
  await waitFor(() => expect(setMonitorPublishing).toHaveBeenCalledWith('kick:xqc', true));
});

test('config Applica posts only changed/allowed fields (instructions + caption_template)', async () => {
  const { updateMonitorConfig } = await import('../../api/client');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc' }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  fireEvent.change(screen.getByLabelText('Settings instructions kick:xqc'), { target: { value: 'find hype moments' } });
  fireEvent.change(screen.getByLabelText('Settings caption template kick:xqc'), { target: { value: '{hook}' } });
  fireEvent.click(screen.getByRole('button', { name: /Apply/ }));
  await waitFor(() => expect(updateMonitorConfig).toHaveBeenCalledWith('kick:xqc', {
    instructions: 'find hype moments',
    caption_template: '{hook}',
  }));
});

test('shows Gemini rate-limit notice when gemini_exhausted_at set', async () => {
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing',
      channel: 'xqc', gemini_exhausted_at: '2026-07-23T09:00:00Z' }],
  });
  render(<LiveMonitorView />);
  expect(await screen.findByText(/rate.?limit/i)).toBeInTheDocument();
});

test('no Gemini rate-limit notice when gemini_exhausted_at is unset', async () => {
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc' }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  expect(screen.queryByText(/rate.?limit/i)).toBeNull();
});

test('delete-after-publish checkbox seeds true by default and sends nothing when untouched', async () => {
  const { updateMonitorConfig } = await import('../../api/client');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc', config: {} }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  const toggle = screen.getByRole('switch', { name: 'Delete after publish kick:xqc' });
  expect(toggle).toHaveAttribute('aria-checked', 'true');
  fireEvent.change(screen.getByLabelText('Settings instructions kick:xqc'), { target: { value: 'find hype moments' } });
  fireEvent.click(screen.getByRole('button', { name: /Apply/ }));
  await waitFor(() => expect(updateMonitorConfig).toHaveBeenCalledWith('kick:xqc', { instructions: 'find hype moments' }));
});

test('delete-after-publish checkbox seeds false from config and sends only when toggled', async () => {
  const { updateMonitorConfig } = await import('../../api/client');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc',
      config: { delete_after_publish: false } }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  const toggle = screen.getByRole('switch', { name: 'Delete after publish kick:xqc' });
  expect(toggle).toHaveAttribute('aria-checked', 'false');
  fireEvent.click(toggle);
  fireEvent.click(screen.getByRole('button', { name: /Apply/ }));
  await waitFor(() => expect(updateMonitorConfig).toHaveBeenCalledWith('kick:xqc', { delete_after_publish: true }));
});

test('twitch missing-credentials (400) points to Settings', async () => {
  const { startLiveMonitor } = await import('../../api/client');
  startLiveMonitor.mockRejectedValueOnce(new Error(
    'Twitch monitoring requires TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET (set them in Settings or the environment)'));
  const pushToast = vi.fn();
  render(<LiveMonitorView pushToast={pushToast} />);
  fireEvent.click(screen.getByRole('button', { name: 'Twitch' }));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(pushToast).toHaveBeenCalledWith('error', expect.stringMatching(/Twitch not configured/i)));
});

test('queued clips are listed with a Discard button that calls the API', async () => {
  const client = await import('../../api/client');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:n3on', platform: 'kick', mode: 'live', running: true, state: 'capturing',
      channel: 'n3on', publishing_enabled: false, pending_publish: 3,
      pending_clips: [
        { publication_id: 'p-1', title: 'Chat goes wild', viral_score: 88, state: 'queued',
          video_url: '/videos/j1/clip_0.mp4' },
        { publication_id: 'p-2', title: 'Mid-upload', viral_score: 75, state: 'in_flight',
          video_url: '/videos/j1/clip_1.mp4' },
      ] }],
  });
  render(<LiveMonitorView />);
  expect(await screen.findByText('Chat goes wild')).toBeInTheDocument();
  expect(screen.getByText('Score 88')).toBeInTheDocument();
  expect(screen.getByText('+1 more queued')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Discard Mid-upload' })).toBeDisabled();

  fireEvent.click(screen.getByRole('button', { name: 'Discard Chat goes wild' }));
  await waitFor(() => expect(client.discardPendingClip).toHaveBeenCalledWith('kick:n3on', 'p-1'));
});

test('no queued-clips section when nothing is waiting', async () => {
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:n3on', platform: 'kick', mode: 'live', running: true, state: 'capturing',
      channel: 'n3on', publishing_enabled: false, pending_publish: 0, pending_clips: [] }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('n3on');
  expect(screen.queryByText(/Queued to publish/)).not.toBeInTheDocument();
});

test('post timing, daily cap and chat signal ride the start payload', async () => {
  const { startLiveMonitor } = await import('../../api/client');
  render(<LiveMonitorView />);
  expect(screen.getByText(/Never 01:00-08:00/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Prime time' }));
  expect(screen.getByText(/can be hours later/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Max posts per day'), { target: { value: '6' } });
  fireEvent.click(screen.getByRole('switch', { name: 'Chat signal' }));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'n3on' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  const payload = startLiveMonitor.mock.calls[0][0];
  expect(payload.post_timing).toBe('prime_time');
  expect(payload.max_posts_per_day).toBe(6);
  expect(payload.chat_signal).toBe(false);
});

test('defaults: as soon as possible, 6 a day, 15 min review, chat signal on', async () => {
  const { startLiveMonitor } = await import('../../api/client');
  render(<LiveMonitorView />);
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'n3on' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  const payload = startLiveMonitor.mock.calls[0][0];
  expect(payload.post_timing).toBe('asap');
  expect(payload.max_posts_per_day).toBe(6);
  expect(payload.review_minutes).toBe(15);
  expect(payload.chat_signal).toBe(true);
});

test('Settings drawer applies post timing, daily cap and chat signal', async () => {
  const { updateMonitorConfig } = await import('../../api/client');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:n3on', platform: 'kick', mode: 'live', running: true, state: 'capturing',
      channel: 'n3on', config: { post_timing: 'prime_time', max_posts_per_day: 0, chat_signal: true } }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('n3on');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  const drawer = document.querySelector('.cfg-drawer');
  fireEvent.click(within(drawer).getByRole('button', { name: 'As soon as possible' }));
  fireEvent.change(screen.getByLabelText('Settings max posts per day kick:n3on'), { target: { value: '5' } });
  fireEvent.click(screen.getByRole('switch', { name: 'Chat signal kick:n3on' }));
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(updateMonitorConfig).toHaveBeenCalled());
  const partial = updateMonitorConfig.mock.calls[0][1];
  expect(partial).toMatchObject({ post_timing: 'asap', max_posts_per_day: 5, chat_signal: false });
});

test('review window rides the start payload and the Settings drawer', async () => {
  const { startLiveMonitor, updateMonitorConfig } = await import('../../api/client');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:n3on', platform: 'kick', mode: 'live', running: true, state: 'capturing',
      channel: 'n3on', publishing_enabled: true, pending_publish: 1, config: { review_minutes: 0 },
      pending_clips: [{ publication_id: 'p-1', title: 'Big moment', viral_score: 90, state: 'queued',
        video_url: '/videos/j1/clip_0.mp4', review_until: Date.now() / 1000 + 9.5 * 60 }] }],
  });
  render(<LiveMonitorView />);
  expect(await screen.findByText(/posts in 10 min/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  fireEvent.change(screen.getByLabelText('Settings review minutes kick:n3on'), { target: { value: '20' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(updateMonitorConfig).toHaveBeenCalled());
  expect(updateMonitorConfig.mock.calls[0][1].review_minutes).toBe(20);

  fireEvent.change(screen.getByLabelText('Review window minutes'), { target: { value: '15' } });
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].review_minutes).toBe(15);
});

test('queued clip preview plays and downloads the composed file', async () => {
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:n3on', platform: 'kick', mode: 'live', running: true, state: 'capturing',
      channel: 'n3on', publishing_enabled: false, pending_publish: 1,
      pending_clips: [{ publication_id: 'p-1', title: 'Big moment', viral_score: 88, state: 'queued',
        video_url: '/videos/j1/clip_0.mp4', preview_url: '/videos/monitor_kick_n3on/Big%20moment.mp4' }] }],
  });
  render(<LiveMonitorView />);
  const video = await screen.findByLabelText('Preview: Big moment');
  expect(video.getAttribute('src')).toBe('/videos/monitor_kick_n3on/Big%20moment.mp4');
  const download = screen.getByRole('link', { name: 'Download Big moment' });
  expect(download.getAttribute('href')).toBe('/videos/monitor_kick_n3on/Big%20moment.mp4');
  expect(download).toHaveAttribute('download');
});

test('Post now posts one queued clip; an approved clip shows Posting…', async () => {
  const client = await import('../../api/client');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:n3on', platform: 'kick', mode: 'live', running: true, state: 'capturing',
      channel: 'n3on', publishing_enabled: false, pending_publish: 2,
      pending_clips: [
        { publication_id: 'p-1', title: 'Big moment', viral_score: 88, state: 'queued', approved: false,
          preview_url: '/videos/m/a.mp4' },
        { publication_id: 'p-2', title: 'Already sent', viral_score: 80, state: 'queued', approved: true,
          preview_url: '/videos/m/b.mp4' },
      ] }],
  });
  render(<LiveMonitorView />);
  fireEvent.click(await screen.findByRole('button', { name: 'Post Big moment now' }));
  await waitFor(() => expect(client.publishPendingClip).toHaveBeenCalledWith('kick:n3on', 'p-1'));
  expect(screen.getByRole('button', { name: 'Post Already sent now' })).toBeDisabled();
  expect(screen.getByText(/posting soon/)).toBeInTheDocument();
});

test('layout defaults to cam + gameplay and can be changed per monitor', async () => {
  const { startLiveMonitor, updateMonitorConfig } = await import('../../api/client');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:n3on', platform: 'kick', mode: 'live', running: true, state: 'capturing',
      channel: 'n3on', config: { layout: 'split' } }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('n3on');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  const drawer = document.querySelector('.cfg-drawer');
  fireEvent.click(within(drawer).getByRole('button', { name: 'Full frame' }));
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(updateMonitorConfig).toHaveBeenCalled());
  expect(updateMonitorConfig.mock.calls[0][1].layout).toBe('letterbox');

  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].layout).toBe('split');
});

test('failed clips show the reason and can be retried or discarded', async () => {
  const client = await import('../../api/client');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'twitch:lacy', platform: 'twitch', mode: 'live', running: true, state: 'capturing',
      channel: 'lacy', publishing_enabled: true, pending_publish: 0, pending_clips: [],
      failed_clips: [{ publication_id: 'f-1', title: 'Prison', state: 'failed',
        error: 'Zernio POST /posts → HTTP 400: Invalid accountId format', preview_url: '/videos/m/p.mp4' }] }],
  });
  render(<LiveMonitorView />);
  expect(await screen.findByText(/Invalid accountId format/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry Prison' }));
  await waitFor(() => expect(client.failedClipAction).toHaveBeenCalledWith('twitch:lacy', 'f-1', 'retry'));
  fireEvent.click(screen.getByRole('button', { name: 'Discard failed Prison' }));
  await waitFor(() => expect(client.failedClipAction).toHaveBeenCalledWith('twitch:lacy', 'f-1', 'discard'));
});
