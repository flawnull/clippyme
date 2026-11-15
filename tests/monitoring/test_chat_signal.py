"""Live-chat spike detection and its hand-off into the monitor's Gemini hint."""
import asyncio
import json

from clippyme.integrations import chat_listener
from clippyme.jobs.job_results import MAX_INSTRUCTIONS_LEN
from clippyme.monitoring import chat_signal as cs
from clippyme.monitoring.live_monitor import LiveMonitor, validate_monitor_config


def _steady_chat(duration, every=2.0, text="hello chat"):
    """One ordinary message every ``every`` seconds."""
    weight, kind = cs.score_message(text)
    t, out = 0.0, []
    while t < duration:
        out.append((t, weight, kind))
        t += every
    return out


def _burst(start, seconds, per_second, text):
    weight, kind = cs.score_message(text)
    return [(start + i / per_second, weight, kind)
            for i in range(int(seconds * per_second))]


# -- message scoring ----------------------------------------------------------

def test_reaction_messages_weigh_double_and_are_classified():
    assert cs.score_message("KEKW KEKW") == (2.0, "laugh")
    assert cs.score_message("?????") == (2.0, "shock")
    assert cs.score_message("W W W") == (2.0, "hype")
    assert cs.score_message("what time is the stream tomorrow") == (1.0, None)


def test_kick_emote_markup_is_read_as_the_emote_name():
    assert cs.classify_message("[emote:37226:KEKW] [emote:37226:KEKW]") == "laugh"


# -- spike detection ----------------------------------------------------------

def test_burst_in_steady_chat_is_found_at_the_right_time():
    duration = 1800
    events = _steady_chat(duration) + _burst(600, 20, 6, "OMEGALUL")
    spikes = cs.find_spikes(events, duration)
    assert len(spikes) == 1
    spike = spikes[0]
    # Window starts LEAD_SECONDS before the burst to include the build-up.
    assert spike.start == 600 - cs.LEAD_SECONDS
    assert 620 <= spike.end <= 630
    assert spike.reaction == "laugh"
    assert spike.ratio >= cs.SPIKE_RATIO


def test_steady_chat_has_no_spike():
    assert cs.find_spikes(_steady_chat(1800), 1800) == []


def test_tiny_chat_gives_no_signal():
    events = _burst(100, 5, 2, "KEKW")  # 10 messages in the whole segment
    assert cs.find_spikes(events, 1800) == []


def test_small_bursts_under_the_absolute_floor_are_ignored():
    # A near-silent chat where 3 messages in 10 s is "many times" the median.
    events = _steady_chat(1800, every=60) + _burst(500, 10, 0.3, "hi")
    assert cs.find_spikes(events, 1800) == []


def test_strongest_spikes_kept_and_returned_in_time_order():
    duration = 3600
    events = _steady_chat(duration)
    for i, start in enumerate(range(200, 3400, 400)):  # 8 bursts, growing
        events += _burst(start, 10, 3 + i, "KEKW")
    spikes = cs.find_spikes(events, duration, max_hints=3)
    assert len(spikes) == 3
    assert [s.start for s in spikes] == sorted(s.start for s in spikes)
    # The three biggest bursts are the last three.
    assert spikes[0].start >= 2200 - cs.LEAD_SECONDS


def test_events_outside_the_segment_are_dropped():
    events = _steady_chat(600) + _burst(900, 20, 10, "KEKW")
    assert cs.find_spikes(events, 600) == []


# -- hint text and instruction merge ------------------------------------------

def test_hint_lists_windows_as_timestamps():
    hint = cs.format_hint([cs.Spike(580, 630, 4.2, "laugh"),
                           cs.Spike(3700, 3720, 3.0, None)])
    assert hint.startswith("LIVE CHAT SIGNAL")
    assert "- 09:40-10:30 (chat 4.2x normal, mostly laugh)" in hint
    assert "- 1:01:40-1:02:00 (chat 3.0x normal)" in hint
    assert cs.format_hint([]) == ""


def test_merge_keeps_user_instructions_and_trims_hint_to_fit():
    hint = cs.format_hint([cs.Spike(i * 100, i * 100 + 30, 3.0, "hype") for i in range(5)])
    user = "Focus on GTA gameplay."
    assert cs.merge_instructions(user, hint, 10_000) == f"{user}\n\n{hint}"
    # Room for the header plus two lines only.
    two = "\n".join(hint.split("\n")[:3])
    merged = cs.merge_instructions(user, hint, len(f"{user}\n\n{two}"))
    assert merged == f"{user}\n\n{two}"
    # No room for even one spike line: user text alone.
    assert cs.merge_instructions(user, hint, len(user) + 10) == user
    assert cs.merge_instructions("", "", 100) is None
    assert cs.merge_instructions(None, hint, 10_000) == hint


# -- wire formats ---------------------------------------------------------------

def test_parse_twitch_privmsg_with_and_without_tags():
    plain = ":foo!foo@foo.tmi.twitch.tv PRIVMSG #kaicenat :KEKW that was crazy\r\n"
    tagged = "@badge-info=;color=#FF0000 :bar!bar@bar.tmi.twitch.tv PRIVMSG #kaicenat :W"
    assert cs.parse_twitch_privmsg(plain) == "KEKW that was crazy"
    assert cs.parse_twitch_privmsg(tagged) == "W"
    assert cs.parse_twitch_privmsg("PING :tmi.twitch.tv") is None
    assert cs.parse_twitch_privmsg(":tmi.twitch.tv 001 justinfan1 :Welcome") is None


def test_parse_kick_event_reads_double_encoded_data():
    frame = json.dumps({"event": "App\\Events\\ChatMessageEvent",
                        "data": json.dumps({"content": "[emote:1:KEKW] lol"}),
                        "channel": "chatrooms.42.v2"})
    assert cs.parse_kick_event(frame) == "[emote:1:KEKW] lol"
    assert cs.parse_kick_event('{"event":"pusher:connection_established"}') is None
    assert cs.parse_kick_event("not json") is None


def test_kick_chatroom_id():
    assert cs.kick_chatroom_id({"chatroom": {"id": "668"}}) == 668
    assert cs.kick_chatroom_id({"chatroom": None}) is None
    assert cs.kick_chatroom_id(None) is None


# -- listener sessions against a fake socket ----------------------------------

class _FakeSocket:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if not self.frames:
            raise ConnectionError("closed")
        return self.frames.pop(0)


def test_twitch_session_logs_in_anonymously_answers_ping_and_reads_messages():
    ws = _FakeSocket([
        "PING :tmi.twitch.tv\r\n",
        (":a!a@a.tmi.twitch.tv PRIVMSG #kaicenat :KEKW\r\n"
         ":b!b@b.tmi.twitch.tv PRIVMSG #kaicenat :no way\r\n"),
    ])
    got = []

    async def run():
        try:
            await chat_listener._twitch_session(ws, "KaiCenat", got.append, asyncio.Event())
        except ConnectionError:
            pass

    asyncio.run(run())
    assert ws.sent[0] == "PASS SCHMOOPIIE"
    assert ws.sent[1].startswith("NICK justinfan")
    assert ws.sent[2] == "JOIN #kaicenat"
    assert "PONG :tmi.twitch.tv" in ws.sent
    assert got == ["KEKW", "no way"]
    # Read-only: nothing but login, join and pong is ever sent.
    assert not any(s.startswith("PRIVMSG") for s in ws.sent)


def test_kick_session_subscribes_to_the_chatroom_and_answers_ping():
    msg = json.dumps({"event": "App\\Events\\ChatMessageEvent",
                      "data": json.dumps({"content": "W"})})
    ws = _FakeSocket(['{"event":"pusher:ping","data":{}}', msg])
    got = []

    async def run():
        try:
            await chat_listener._kick_session(ws, 42, got.append, asyncio.Event())
        except ConnectionError:
            pass

    asyncio.run(run())
    assert json.loads(ws.sent[0])["data"]["channel"] == "chatrooms.42.v2"
    assert '"pusher:pong"' in ws.sent[1]
    assert got == ["W"]


def test_listen_returns_quietly_without_a_kick_chatroom():
    asyncio.run(asyncio.wait_for(
        chat_listener.listen("kick", "n3on", lambda _t: None, asyncio.Event()), 1))


# -- monitor integration --------------------------------------------------------

def _monitor(tmp_path, **cfg):
    mon = LiveMonitor(id="twitch:foo", jobs={}, job_queue=None,
                      output_dir=str(tmp_path / "out"))
    mon.cfg = validate_monitor_config({
        "platform": "twitch", "channel": "foo", "timezone": "UTC",
        "instructions": "Focus on GTA gameplay.",
        "platforms": [{"platform": "tiktok", "accountId": "a"}], **cfg},
        default_timezone="UTC")
    mon._gemini_key = "fake-key"
    return mon


def test_chat_signal_defaults_on_and_is_runtime_updatable():
    from clippyme.monitoring.live_monitor import validate_monitor_partial_update
    cfg = validate_monitor_config({
        "platform": "kick", "channel": "n3on",
        "platforms": [{"platform": "tiktok", "accountId": "a"}]})
    assert cfg["chat_signal"] is True
    assert validate_monitor_partial_update({"chat_signal": False}, cfg)["chat_signal"] is False


def test_segment_job_gets_the_chat_hint_appended_to_instructions(tmp_path):
    mon = _monitor(tmp_path)
    seg = tmp_path / "seg.mp4"
    seg.write_bytes(b"segment")
    events = _steady_chat(1800) + _burst(600, 20, 6, "OMEGALUL")
    mon._remember_chat_hint(str(seg), events, 1800)

    async def submit():
        mon._job_queue = asyncio.Queue()
        return await mon._submit_segment_job(str(seg))

    job_id = asyncio.run(submit())
    cmd = mon._jobs[job_id]["cmd"]
    instructions = cmd[cmd.index("--instructions") + 1]
    assert instructions.startswith("Focus on GTA gameplay.\n\nLIVE CHAT SIGNAL")
    assert "09:40-" in instructions
    assert len(instructions) <= MAX_INSTRUCTIONS_LEN


def test_no_recorder_when_chat_signal_is_off_or_strategy_has_no_chat(tmp_path):
    mon = _monitor(tmp_path, chat_signal=False)
    mon._strategy = type("S", (), {"chat_target": lambda self: ("twitch", "foo", None)})()
    assert mon._start_chat_recorder() is None
    mon = _monitor(tmp_path)
    mon._strategy = object()  # e.g. YouTube, or a test double
    assert mon._start_chat_recorder() is None
