"""Render stage: turns one analysis into one <=30s vertical 9:16 mp4 with overlays.

ffmpeg via subprocess (more reliable than ffmpeg-python for complex filters).
Output is 1080x1920, H.264 + AAC, faststart for browser playback.

Two genuinely different variants:

  Variant A — clean baseline. Center-cropped 9:16 frame, white classic
              Gen-Z beat captions (curated short phrases on Gemini's
              timeline), Arial Black bold, fade-in pop animation.

  Variant B — karaoke style. Same crop, but captions are word-by-word from
              the audio commentary (whisper word-level timestamps), Impact
              font, vivid yellow with thick black outline + drop shadow,
              snappier pop animation. Different hook text from Variant A.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from pipeline.captions import write_ass


# Same scale+crop for both variants. Differentiation lives in captions.py
# (font, color, mode = beats vs karaoke).
SCALE_CROP = "scale=w=1080:h=1920:force_original_aspect_ratio=increase,crop=1080:1920"
# Variant B uses a cinematic letterbox: crop tighter, then pad with black bars
# top + bottom for the 2.4:1 cinema bar feel.
SCALE_CROP_LETTERBOX = (
    "scale=w=1080:h=1920:force_original_aspect_ratio=increase,"
    "crop=1080:1620,pad=1080:1920:0:150:black"
)

# Per-variant color grade applied AFTER scale+crop, BEFORE ass burn-in.
# Drives the visual differentiation beyond just text styling.
#   A: hype — heavy saturation, contrast, warm shift (TikTok candy look)
#   B: cinematic — strong desaturation, cool blue lift, vignette
COLOR_GRADE: dict[str, str] = {
    "A": "eq=contrast=1.15:saturation=1.35:gamma=0.95,colorbalance=rs=0.10:gs=-0.03:bs=-0.08",
    "B": "eq=contrast=1.10:saturation=0.65:gamma=1.04,colorbalance=rs=-0.08:gs=0.0:bs=0.12,vignette='PI/3.5'",
}

# Per-variant pre-filter selection (some variants letterbox, others fill)
PRE_FILTER: dict[str, str] = {
    "A": SCALE_CROP,
    "B": SCALE_CROP_LETTERBOX,
}


def _ass_filter_path(ass_path: Path) -> str:
    """Format a path for ffmpeg's ass=... filter argument.

    Uses a path relative to cwd when possible (avoids drive-letter colon
    escaping). Falls back to absolute with libass colon escape.
    """
    try:
        rel = ass_path.resolve().relative_to(Path.cwd().resolve())
        return str(rel).replace("\\", "/")
    except ValueError:
        return str(ass_path).replace("\\", "/").replace(":", r"\:")


def _build_vf(ass_path: Path, style: str) -> str:
    """Compose -vf filter chain: scale+crop (letterboxed for cinematic),
    color grade per variant, then ASS burn-in."""
    pre = PRE_FILTER.get(style, PRE_FILTER["A"])
    grade = COLOR_GRADE.get(style, COLOR_GRADE["A"])
    return f"{pre},{grade},ass={_ass_filter_path(ass_path)}"


def render_variant(
    *,
    input_path: str,
    segment: tuple[float, float],
    hook: str,
    captions: list[dict] | None = None,
    words: list | None = None,
    output_path: str,
    style: str = "A",
) -> str:
    """Render a single variant. Returns output_path on success, raises on failure.

    For beats mode (Variant A): pass `captions` list, leave `words` None.
    For karaoke mode (Variant B): pass `words` list (TranscriptWord), leave
    `captions` None or empty.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH. Install ffmpeg before running.")

    start, end = segment
    duration = max(0.5, min(30.0, end - start))

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ass_path = out_path.with_suffix(".ass")
    write_ass(
        hook=hook,
        captions=captions,
        words=words,
        segment_duration=duration,
        output_path=ass_path,
        style_key=style,
    )

    vf = _build_vf(ass_path, style)

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", f"{start:.3f}",
        "-i", input_path,
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {result.returncode}).\n"
            f"Command: {' '.join(cmd)}\n"
            f"stderr (last 2000 chars):\n{result.stderr[-2000:]}"
        )
    return output_path
