"""Hour 1 skeleton smoke test.

Runs fake_analyze + render_variant on a real input video, no Gradio.
Confirms the ffmpeg pipeline plumbs correctly end-to-end.

Usage:
    python tests/test_skeleton.py "path/to/input.mp4" [game_name]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.analyze import fake_analyze
from pipeline.render import render_variant


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tests/test_skeleton.py <input_video_path> [game_name]")
        return 1

    input_path = sys.argv[1]
    game_name = sys.argv[2] if len(sys.argv) > 2 else "Valorant"

    if not Path(input_path).exists():
        print(f"ERROR: input file not found: {input_path}")
        return 2

    out_dir = ROOT / "runs" / "test-skel"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Input: {input_path}")
    print(f"Game:  {game_name}")
    print(f"Out:   {out_dir}")

    print("\n=== fake_analyze ===")
    analysis = fake_analyze(input_path, game_name)
    print(f"  hook_a: {analysis['hook_a']}")
    print(f"  hook_b: {analysis['hook_b']}")
    print(f"  segment: {analysis['start_sec']}–{analysis['end_sec']}s")
    print(f"  captions: {len(analysis['captions'])} beats")
    print(f"  viral_score: {analysis['viral_score']}")

    segment = (analysis["start_sec"], analysis["end_sec"])

    # Synthesize fake word-level timestamps from caption beats so Variant B's
    # karaoke path is exercised even without real whisper output.
    fake_words = []
    for cap in analysis["captions"]:
        words = cap["text"].split()
        if not words:
            continue
        dur = max(0.1, cap["end"] - cap["start"])
        per = dur / len(words)
        for i, w in enumerate(words):
            fake_words.append({
                "text": w,
                "start": cap["start"] + i * per,
                "end": cap["start"] + (i + 1) * per,
            })

    # Wrap dicts to match TranscriptWord-like attribute access
    class _W:
        def __init__(self, text, start, end):
            self.text = text
            self.start = start
            self.end = end
    fake_word_objs = [_W(**w) for w in fake_words]

    results = []
    for style, name, hook_key, words_arg in [
        ("A", "variant_a.mp4", "hook_a", None),
        ("B", "variant_b.mp4", "hook_b", fake_word_objs),
    ]:
        out = out_dir / name
        print(f"\n=== render variant {style} -> {out.name} ===")
        t0 = time.time()
        try:
            render_variant(
                input_path=input_path,
                segment=segment,
                hook=analysis[hook_key],
                captions=analysis["captions"],
                words=words_arg,
                output_path=str(out),
                style=style,
            )
        except Exception as e:
            print(f"  FAIL: {e}")
            return 3
        t1 = time.time()
        size_mb = out.stat().st_size / (1024 * 1024)
        print(f"  OK in {t1-t0:.1f}s, {size_mb:.2f} MB -> {out}")
        results.append((style, out, t1 - t0, size_mb))

    print("\n=== SKELETON TEST PASSED ===")
    for style, out, dur, size in results:
        print(f"  Variant {style}: {out} ({size:.1f} MB, {dur:.1f}s render)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
