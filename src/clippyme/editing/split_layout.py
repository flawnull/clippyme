"""Split layout for stream clips: facecam on top, gameplay below.

The usual clipper format for gaming streams. Built from the clip's 16:9 source
slice (``source_<clip>``, kept by the pipeline), not from the letterboxed
render:

- top band: the streamer's camera, found by face detection and cropped around
  the face;
- bottom band: the centre of the gameplay, cropped to fill the band.

The camera sits somewhere different on every stream, so its position is
detected per clip. Only a face that stays in about the same place across the
sampled frames counts: a webcam overlay is static, faces in the game move. No
stable face (no camera, a full-screen IRL camera) means no split: the caller
keeps the normal letterbox clip.

The geometry is pure and host-tested; OpenCV is imported only to detect.
"""
from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger("clippyme")

OUT_W, OUT_H = 1080, 1920
# Share of the frame height given to the camera band (the rest is gameplay).
TOP_FRACTION = 0.38
# Crop width around a detected face, in face widths: head and shoulders, and
# narrow enough to stay inside a typical webcam overlay.
FACE_WIDTHS = 2.8
# Frames sampled for detection, and how many must agree on the camera spot.
SAMPLES = 8
MIN_AGREEING = 4
# Two detections are the same camera if their centres are this close
# (fraction of the source width).
SAME_SPOT = 0.06
# A face taller than this share of the frame is a full-screen camera, not an
# overlay; splitting it would just show the same view twice.
MAX_FACE_FRACTION = 0.33


def bands(out_h: int = OUT_H, top_fraction: float = TOP_FRACTION) -> tuple[int, int]:
    """(top_h, bottom_h) in output pixels, both even for yuv420p."""
    top = int(round(out_h * top_fraction / 2)) * 2
    return top, out_h - top


def stable_face(frames: list[list[tuple[int, int, int, int]]], src_w: int, src_h: int):
    """The camera face across sampled frames, or None.

    ``frames`` holds per-frame face boxes ``(x, y, w, h)``. A spot counts when
    at least ``MIN_AGREEING`` frames have a face there; the result is the
    median box of the biggest such cluster.
    """
    boxes = [b for f in frames for b in f if b[3] < src_h * MAX_FACE_FRACTION]
    best: list = []
    for anchor in boxes:
        ax, ay = anchor[0] + anchor[2] / 2, anchor[1] + anchor[3] / 2
        cluster = []
        for frame in frames:
            near = [b for b in frame
                    if b[3] < src_h * MAX_FACE_FRACTION
                    and abs(b[0] + b[2] / 2 - ax) < SAME_SPOT * src_w
                    and abs(b[1] + b[3] / 2 - ay) < SAME_SPOT * src_w]
            if near:
                cluster.append(max(near, key=lambda b: b[2] * b[3]))
        if len(cluster) > len(best):
            best = cluster
    if len(best) < MIN_AGREEING:
        return None
    mid = len(best) // 2
    return tuple(sorted(b[i] for b in best)[mid] for i in range(4))


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def cam_box(face, src_w: int, src_h: int, top_h: int, out_w: int = OUT_W):
    """Crop ``(x, y, w, h)`` around the face with the camera band's aspect."""
    fx, fy, fw, fh = face
    aspect = out_w / top_h
    w = min(fw * FACE_WIDTHS, src_w, src_h * aspect)
    h = w / aspect
    cx, cy = fx + fw / 2, fy + fh / 2 + fh * 0.15  # a little below the eyes
    x = min(max(0, cx - w / 2), src_w - w)
    y = min(max(0, cy - h / 2), src_h - h)
    return _even(x), _even(y), _even(w), _even(h)


def game_box(src_w: int, src_h: int, bottom_h: int, out_w: int = OUT_W, avoid=None):
    """Crop of the full frame with the gameplay band's aspect: centred, but
    moved sideways off the camera overlay (``avoid``, an x/y/w/h box) when the
    centre crop would show part of it."""
    aspect = out_w / bottom_h
    h = src_h
    w = min(src_w, h * aspect)
    if w == src_w:
        h = w / aspect
    x = (src_w - w) / 2
    if avoid is not None:
        ax, _, aw, _ = avoid
        if ax < x + w and ax + aw > x:  # overlaps the centre crop
            if ax + aw / 2 < src_w / 2:
                x = min(src_w - w, ax + aw)  # camera on the left: start after it
            else:
                x = max(0, ax - w)  # camera on the right: end before it
    return _even(x), _even((src_h - h) / 2), _even(w), _even(h)


def overlay_box(face, src_w: int, src_h: int):
    """Generous extent of the webcam overlay around a face: 16:9, about six
    face widths plus a margin, since overlays are often much wider than the
    face. Used only to steer the gameplay crop off the camera."""
    fx, fy, fw, fh = face
    w = min(src_w, fw * 6.0 + src_w * 0.02)
    h = w * 9 / 16
    x = min(max(0, fx + fw / 2 - w / 2), src_w - w)
    y = min(max(0, fy + fh / 2 - h / 2), src_h - h)
    return x, y, w, h


def build_filter(cam, game, top_h: int, bottom_h: int, out_w: int = OUT_W) -> str:
    cx, cy, cw, ch = cam
    gx, gy, gw, gh = game
    return (f"[0:v]split=2[c][g];"
            f"[c]crop={cw}:{ch}:{cx}:{cy},scale={out_w}:{top_h}:flags=lanczos,setsar=1[top];"
            f"[g]crop={gw}:{gh}:{gx}:{gy},scale={out_w}:{bottom_h}:flags=lanczos,setsar=1[bot];"
            f"[top][bot]vstack=inputs=2[v]")


def detect_faces(video_path: str, samples: int = SAMPLES):
    """Face boxes per sampled frame, plus the source size. Needs OpenCV."""
    import cv2

    cascade = cv2.CascadeClassifier(
        os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
    cap = cv2.VideoCapture(video_path)
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frames = []
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / samples))
            ok, frame = cap.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found = cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=6, minSize=(max(24, src_h // 20),) * 2)
            frames.append([tuple(int(v) for v in b) for b in found])
    finally:
        cap.release()
    return frames, src_w, src_h


def render_split(source_path: str, out_path: str, *, detector=None) -> str | None:
    """Render the split clip, or return None when no camera was found."""
    from clippyme.media.encode import ffmpeg_timeout, x264_video_args

    try:
        frames, src_w, src_h = (detector or detect_faces)(source_path)
    except Exception:
        logger.warning("split layout: face detection failed for %s", source_path, exc_info=True)
        return None
    if not src_w or not src_h:
        return None
    face = stable_face(frames, src_w, src_h)
    if face is None:
        logger.info("split layout: no steady camera in %s, keeping letterbox",
                    os.path.basename(source_path))
        return None
    top_h, bottom_h = bands()
    cam = cam_box(face, src_w, src_h, top_h)
    game = game_box(src_w, src_h, bottom_h, avoid=overlay_box(face, src_w, src_h))
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", source_path,
           "-filter_complex", build_filter(cam, game, top_h, bottom_h),
           "-map", "[v]", "-map", "0:a?", "-c:a", "copy", *x264_video_args(), out_path]
    subprocess.run(cmd, check=True, timeout=ffmpeg_timeout(), capture_output=True)
    logger.info("split layout: camera %s, gameplay %s -> %s", cam, game,
                os.path.basename(out_path))
    return out_path
