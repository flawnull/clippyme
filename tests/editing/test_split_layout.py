"""Split layout: facecam on top, gameplay below, built from the 16:9 slice."""
import os
import subprocess

import pytest

from clippyme.editing import split_layout as sl

W, H = 1920, 1080
FACE = (250, 170, 100, 120)  # in a webcam overlay at the top-left


def test_bands_fill_the_frame_with_even_heights():
    top, bottom = sl.bands()
    assert top + bottom == sl.OUT_H and top % 2 == 0 and bottom % 2 == 0
    assert 0.35 < top / sl.OUT_H < 0.40


def test_stable_face_needs_agreement_and_ignores_moving_game_faces():
    steady = [[FACE, (900 + i * 120, 500, 90, 90)] for i in range(8)]  # game face moves
    assert sl.stable_face(steady, W, H) == FACE
    flicker = [[FACE]] * 2 + [[]] * 6
    assert sl.stable_face(flicker, W, H) is None
    full_screen_cam = [[(700, 200, 500, 600)]] * 8  # a face taller than a third of the frame
    assert sl.stable_face(full_screen_cam, W, H) is None


def test_cam_box_has_the_band_aspect_and_stays_in_frame():
    top, _ = sl.bands()
    for face in (FACE, (5, 5, 80, 80), (1800, 950, 100, 110)):
        x, y, w, h = sl.cam_box(face, W, H, top)
        assert 0 <= x and 0 <= y and x + w <= W and y + h <= H
        assert abs(w / h - sl.OUT_W / top) < 0.02


def test_game_box_moves_off_the_camera_side():
    _, bottom = sl.bands()
    centre = sl.game_box(W, H, bottom)
    left_cam = sl.game_box(W, H, bottom, avoid=sl.overlay_box(FACE, W, H))
    right_cam = sl.game_box(W, H, bottom, avoid=sl.overlay_box((1600, 170, 100, 120), W, H))
    assert left_cam[0] > centre[0] and right_cam[0] < centre[0]
    lx, _, lw, _ = sl.overlay_box(FACE, W, H)
    assert left_cam[0] >= lx + lw - 2  # gameplay starts after the overlay
    for x, y, w, h in (centre, left_cam, right_cam):
        assert x >= 0 and x + w <= W and abs(w / h - sl.OUT_W / bottom) < 0.02


def _source(tmp_path):
    src = tmp_path / "source_clip_0.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30",
                    "-f", "lavfi", "-i", "sine=duration=2", "-t", "2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    return str(src)


def test_render_split_outputs_a_vertical_clip_with_audio(tmp_path):
    out = tmp_path / "split.mp4"
    done = sl.render_split(_source(tmp_path), str(out),
                           detector=lambda p: ([[FACE]] * 8, W, H))
    assert done == str(out)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
    assert "video,1080,1920" in probe and "audio" in probe


def test_no_camera_means_no_split(tmp_path):
    out = tmp_path / "split.mp4"
    assert sl.render_split(_source(tmp_path), str(out), detector=lambda p: ([[]] * 8, W, H)) is None
    assert not out.exists()


def test_real_detector_runs_on_a_video_without_faces(tmp_path):
    pytest.importorskip("cv2")
    frames, w, h = sl.detect_faces(_source(tmp_path))
    assert (w, h) == (W, H) and len(frames) == sl.SAMPLES
    assert sl.stable_face(frames, w, h) is None


def test_monitor_recipe_for_split_drops_the_banner_and_moves_the_hook():
    from clippyme.monitoring.live_monitor import build_monitor_compose
    clip = {"viral_hook_text": "N3on can't survive GTA 6 jail"}
    split = build_monitor_compose("kick", "n3on", clip, None, layout="split")
    assert split["layout_params"] == {"mode": "split"}
    assert split["banner_params"] == {} and split["hook_params"]["size"] == "L"
    assert split["hook_params"]["offset_y"] > 15
    kept = build_monitor_compose("kick", "n3on", clip, {"banner": {"enabled": True}}, layout="split")
    assert kept["banner_params"]["handle"]
    letterbox = build_monitor_compose("kick", "n3on", clip, None, layout="letterbox")
    assert letterbox["layout_params"] is None and letterbox["banner_params"]


def test_layout_config_defaults_to_split():
    from clippyme.monitoring.live_monitor import validate_monitor_config, validate_monitor_partial_update
    cfg = validate_monitor_config({"platform": "kick", "channel": "n3on",
                                   "platforms": [{"platform": "tiktok", "accountId": "a"}]})
    assert cfg["layout"] == "split"
    assert validate_monitor_partial_update({"layout": "letterbox"}, cfg)["layout"] == "letterbox"


def test_a_failing_split_render_falls_back_instead_of_raising(tmp_path, monkeypatch):
    """A layout problem must never cost the clip: compose then keeps the
    letterbox base."""
    def boom(*a, **kw):
        raise subprocess.CalledProcessError(1, "ffmpeg", stderr=b"Invalid crop size")

    monkeypatch.setattr(sl.subprocess, "run", boom)
    out = tmp_path / "split.mp4"
    assert sl.render_split(str(tmp_path / "missing.mp4"), str(out),
                           detector=lambda p: ([[FACE]] * 8, W, H)) is None
    assert not out.exists()
