"""Hour 2 analysis test.

Runs real_analyze on a real input video. Verifies the schema, prints the
output for visual inspection.

Usage:
    python tests/test_analyze.py "path/to/input.mp4" [game_name] [ign]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Force UTF-8 stdout so emoji in Gemini output don't crash on Windows cp1252
try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from pipeline.analyze import real_analyze


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tests/test_analyze.py <input_video_path> [game_name] [ign]")
        return 1

    input_path = sys.argv[1]
    game_name = sys.argv[2] if len(sys.argv) > 2 else "Valorant"
    player_ign = sys.argv[3] if len(sys.argv) > 3 else ""

    if not Path(input_path).exists():
        print(f"ERROR: input file not found: {input_path}")
        return 2

    work_dir = ROOT / "runs" / "test-analyze"
    print(f"Input: {input_path}")
    print(f"Game:  {game_name}")
    print(f"IGN:   {player_ign or '(none)'}")
    print(f"Work:  {work_dir}")

    print("\n=== Running real_analyze (Gemini Flash) ===")
    t0 = time.time()
    try:
        analysis = real_analyze(input_path, game_name, player_ign, work_dir=work_dir)
    except Exception as e:
        print(f"FAIL: {type(e).__name__}: {e}")
        return 3
    t1 = time.time()
    print(f"Done in {t1-t0:.1f}s")

    print("\n=== Result ===")
    print(json.dumps(analysis, indent=2))

    # Sanity checks
    seg_dur = analysis["end_sec"] - analysis["start_sec"]
    print("\n=== Schema sanity ===")
    print(f"  segment: {analysis['start_sec']:.2f} -> {analysis['end_sec']:.2f} ({seg_dur:.2f}s)")
    print(f"  hook_a ({len(analysis['hook_a'].split())} words): {analysis['hook_a']}")
    print(f"  hook_b ({len(analysis['hook_b'].split())} words): {analysis['hook_b']}")
    print(f"  events: {len(analysis.get('events', []))} ranked")
    for ev in analysis.get("events", [])[:5]:
        print(f"    - [{ev['weight']:3d}] {ev['event_type']:11s} frames {ev['start_frame_index']}-{ev['peak_frame_index']}: {ev['description']}")
    print(f"  captions: {len(analysis['captions'])} beats")
    for c in analysis["captions"]:
        print(f"    [{c['start']:5.2f}-{c['end']:5.2f}] {c['text']}")
    print(f"  viral_score: {analysis['viral_score']}/100")
    print(f"  reasoning:   {analysis['viral_reasoning']}")
    print(f"  caption:     {analysis['tiktok_caption']}")

    print("\n=== ANALYZE TEST PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
