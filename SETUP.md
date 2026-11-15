# Clipping setup

Personal notes for running this fork as a hands-off stream clipper: watch a
channel, cut the best moments into 9:16 shorts, and post them to TikTok,
Instagram and YouTube. The general install guide is
[docs/getting-started.md](docs/getting-started.md). This page covers only the
choices specific to clipping streamers.

## 1. Where to run it

The live monitor has to run around the clock, so run it on a machine that
stays on.

- **Your own PC.** Fine for testing. Clips stop when the PC sleeps.
- **A small cloud server.** 4 vCPU and 8 GB RAM is enough if transcription
  goes to Deepgram instead of local Whisper. Install Docker, clone this repo,
  and run it there. Keep port 5175 closed to the internet and reach it with an
  SSH tunnel (`ssh -L 5175:localhost:5175 you@server`).
  [docs/deployment.md](docs/deployment.md#network-exposure) explains why it
  must not be exposed.

```bash
git clone https://github.com/flawnull/clippyme.git
cd clippyme
docker compose up --build -d
```

Open <http://localhost:5175>.

## 2. Keys to get

Enter them in the dashboard under **Settings**. They are stored in `data/`,
which git ignores. Never put them in a committed file.

| Key | Where | Why you need it here |
|-----|-------|----------------------|
| Gemini | <https://aistudio.google.com/apikey> | Picks and ranks the moments. Without it the monitor publishes nothing useful. |
| Deepgram | <https://console.deepgram.com> | Fast transcription. Makes a CPU-only server workable. |
| Twitch app (client ID and secret) | <https://dev.twitch.tv/console/apps> | Required to monitor Twitch. Kick and YouTube need no key. |
| Zernio | <https://zernio.com> | Required by the live monitor, which refuses to start without a Zernio key, even with publishing paused. The first 2 connected accounts are free. Posts to TikTok, Instagram and YouTube. Connect your accounts inside Zernio. It saves you TikTok's app audit: a TikTok app you register yourself can only post privately until it passes. Check that test posts show as public. |

Also set your timezone in `.env` (copy `.env.example` first). The scheduler
defaults to Italy:

```
ZERNIO_DEFAULT_TZ=America/New_York
```

## 3. Start a monitor

In the dashboard, open **Live monitor** and add one per channel. Defaults
worth knowing (full list: [docs/architecture/live-monitor.md](docs/architecture/live-monitor.md)):

- **Mode `live`** cuts the running stream into 30 minute segments and
  processes each one. **Mode `vod`** waits for the finished VOD.
- **Clip selection `auto`** drops clips with a viral score under 70, so a dull
  segment posts nothing. Prefer it over `fixed`, which always posts the top 5.
- **Spacing** between posts is 15 minutes across all monitors by default.
- **Post timing:** **As soon as possible** (default). Prime time can hold a
  clip for hours until an evening window, and stream clips go stale fast.
- **Max posts per day:** 6 by default. It counts posts from every monitor,
  and extra clips move to the next day.
- **Review window:** 15 minutes by default; leave auto-publish on. Each
  new clip shows under the monitor with a countdown and a **Discard** button,
  then posts on its own. You only act on the bad ones.
- **Publishing can be paused** per monitor. While paused, waiting clips show
  under the monitor with a preview. Click **Discard** on the bad ones, then
  turn auto-publish on to post the rest.

## Phone notifications

1. Install the free **ntfy** app (iPhone or Android).
2. In ClippyMe **Settings**, find **Phone notifications** and click
   **Generate**. It saves a private topic name and shows it once.
3. In the ntfy app, tap **+** and subscribe to that exact name.
4. Click **Send test**. Your phone should buzz.

You'll get a notification when a monitor finds new clips (with the time they
post) and when a clip fails to post. You'll also get a warning when something
stops clipping: Twitch or Kick can't be reached, the Gemini quota runs out, a
segment fails, the disk is almost full, or a monitor didn't restart. Each
warning repeats at most once an hour. Keep the topic name private: anyone who
knows it can read your notifications.

## 4. Before you post anything

- Only clip streamers who allow it. Join their clipping program (Discord,
  Whop Content Rewards, or a clipping agency) and follow its rules on format,
  credit and platforms. Reposting without permission gets strikes and bans.
- Check each campaign's view-count rules. Some only pay for views on specific
  platforms, or require a tag or link in the caption. Put those in the
  monitor's caption template.
- TikTok and Instagram cut reach for raw reposts. Keep the hook, subtitles and
  banner on, which the monitor does by default.

## Chat signal

Monitors on Kick and Twitch read the live chat while each segment records.
When chat suddenly floods (laughing, `????`, W spam), the times of those
spikes go to Gemini with the segment, so it looks at moments the transcript
alone would miss. It is on by default, reads chat anonymously, and never
posts anything. If chat can't be reached, clipping works as before. Details:
[docs/architecture/live-monitor.md](docs/architecture/live-monitor.md).

To check it works, look at the backend log after a segment finishes:

```bash
docker compose logs backend | grep -i "chat"
```

A line like `1843 chat messages, spikes found` means it is working.
