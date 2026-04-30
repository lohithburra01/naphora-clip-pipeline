"""Frame extraction with adaptive sampling.

Extracts ~target_frame_count frames from a video regardless of input length.
Used by real_analyze to feed Gemini Flash a representative frame set without
blowing past the model's context window or the free-tier quota.

For a 30-second clip with target=40, samples 1 frame every 0.75s (~40 frames).
For a 4-minute clip with target=40, samples 1 frame every 6s (~40 frames).
Quota stays predictable, regardless of input length.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple


class FrameSample(NamedTuple):
    path: Path
    timestamp_sec: float


def get_duration(video_path: str) -> float:
    """Return clip duration in seconds via ffprobe."""
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not found on PATH")
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            video_path,
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def extract_frames(
    video_path: str,
    output_dir: Path,
    target_frame_count: int = 40,
    min_interval_sec: float = 0.5,
) -> tuple[list[FrameSample], float]:
    """Extract frames at adaptive interval. Returns (frames, duration_sec).

    Frames are written to ``output_dir/frame_XXXX.jpg`` in chronological order.
    Each FrameSample carries its timestamp in the original clip so the prompt
    can reference moments by time.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    duration = get_duration(video_path)
    interval = max(min_interval_sec, duration / target_frame_count)

    output_dir.mkdir(parents=True, exist_ok=True)
    # Wipe any prior extraction in this dir to keep the index aligned with timestamps
    for old in output_dir.glob("frame_*.jpg"):
        old.unlink()

    pattern = str(output_dir / "frame_%04d.jpg")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"fps=1/{interval:.4f}",
        "-q:v", "3",
        pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg frame extraction failed:\n{result.stderr[-1500:]}"
        )

    frame_paths = sorted(output_dir.glob("frame_*.jpg"))
    samples = [
        FrameSample(path=p, timestamp_sec=i * interval)
        for i, p in enumerate(frame_paths)
    ]
    return samples, duration
