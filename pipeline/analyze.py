"""Analyze stage of the pipeline.

Public surface:
    fake_analyze(video_path, game_name) -> dict
        Hardcoded skeleton analysis. No external calls. Always succeeds.

    real_analyze(video_path, game_name, player_ign="", work_dir=None) -> dict
        Production path: extract frames + transcribe audio -> Gemini Flash
        with structured JSON output -> validate + convert frame indices to
        seconds. Raises on any failure.

    analyze(video_path, game_name, player_ign="", work_dir=None) -> (dict, source)
        Wrapper. Tries real_analyze, falls back to fake_analyze on any error.
        Returns (analysis, source) where source in {"gemini", "fake"}.

Output schema (stable across all three):
    {
        "start_sec": float,            # segment start in original video
        "end_sec": float,              # segment end (<= start_sec + 30)
        "peak_frame_index": int,       # frame index of peak action
        "highlight_start_frame": int,  # frame index where output clip begins
        "event_description": str,      # neutral one-sentence description of the main event
        "hook_a": str,                 # Variant A hook — event-focused, descriptive (3-7 words)
        "hook_b": str,                 # Variant B hook — DIFFERENT TEXT, visceral/reactive (3-5 words)
        "captions": [                  # Variant A beat captions (Gen-Z phrases, peak-anchored timing)
            {"text": str, "start": float, "end": float},
            ...
        ],
        "viral_score": int,            # 0-100
        "viral_reasoning": str,        # 1-2 sentences citing frame numbers + event
        "tiktok_caption": str,         # full TikTok caption with hashtags
    }

Variant B's caption track is NOT in this schema — it's generated downstream
from whisper word-level timestamps directly (karaoke style, perfect sync).

Design notes:
- Neutral event-centric framing: hooks and captions describe THE EVENT (clutch,
  multi-kill, ace, round-defining play), not who wins or loses. This works
  universally — broadcasts, streams, replays, first-person gameplay — without
  needing to attribute victory/defeat to a specific player or team. Most
  viral TikTok gaming captions follow this neutral pattern anyway.
- Frame-index grounding: Gemini commits to integer frame indices (not free
  seconds), code converts. Avoids "default to 0" failure mode.
- Multi-modal inputs: vision frames + audio commentary transcript. Audio is
  the strongest signal for what's happening dramatically, vision grounds it.
- Minimal event vocabulary in prompt: name event categories and let the
  vision model recognize the visuals — do NOT enumerate game-specific UI
  cues that overfit and lead the model.

Caption timestamps are RELATIVE to the trimmed clip (0.0 = first frame of
output). Render.py only sees the trimmed timeline.

Full prompt rationale and eval criteria live in prompts/vision_analysis.md.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from pipeline.extract import extract_frames
from pipeline.transcribe import transcribe, transcript_to_text_block


GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
TARGET_FRAMES = int(os.environ.get("GEMINI_TARGET_FRAMES", "40"))

# Model fallback chain — try each in order with retries. Handles transient
# 503 spikes and per-model quota exhaustion gracefully. Override via env var.
GEMINI_MODEL_CHAIN = [m.strip() for m in os.environ.get(
    "GEMINI_MODEL_CHAIN",
    f"{GEMINI_MODEL},gemini-flash-latest,gemini-2.5-flash-lite,gemini-2.5-flash,gemini-flash-lite-latest",
).split(",") if m.strip()]
# De-dupe preserving order
_seen: set[str] = set()
GEMINI_MODEL_CHAIN = [m for m in GEMINI_MODEL_CHAIN if not (m in _seen or _seen.add(m))]


# -----------------------------------------------------------------------------
# Skeleton path (fallback when Gemini is unavailable)
# -----------------------------------------------------------------------------

def fake_analyze(video_path: str, game_name: str) -> dict[str, Any]:
    """Return a hardcoded analysis matching the schema. Used as fallback."""
    game_clean = game_name.strip() or "the game"
    game_tag = "".join(c for c in game_clean.lower() if c.isalnum()) or "gaming"
    return {
        "start_sec": 0.0,
        "end_sec": 30.0,
        "peak_frame_index": 10,
        "highlight_start_frame": 8,
        "events": [
            {
                "event_type": "highlight",
                "peak_frame_index": 10,
                "start_frame_index": 8,
                "peak_time_sec": 5.0,
                "start_sec": 0.0,
                "end_sec": 30.0,
                "description": f"Fallback {game_clean} highlight.",
                "weight": 40,
                "hook_a": f"INSANE {game_clean.upper()} PLAY",
                "hook_b": "STOP. WATCH. NO.",
            }
        ],
        "event_description": f"Highlight moment from a {game_clean} clip.",
        "hook_a": f"INSANE {game_clean.upper()} PLAY",
        "hook_b": "STOP. WATCH. NO.",
        "captions": [
            {"text": "watch this", "start": 0.5, "end": 2.5},
            {"text": "no way", "start": 2.5, "end": 4.5},
            {"text": "did you see that", "start": 4.5, "end": 7.5},
            {"text": "absolutely cooked", "start": 7.5, "end": 10.5},
            {"text": "one in a million", "start": 10.5, "end": 13.5},
            {"text": "WAIT FOR IT", "start": 13.5, "end": 16.5},
            {"text": "how is this real", "start": 16.5, "end": 20.0},
            {"text": "GG", "start": 20.0, "end": 23.0},
            {"text": "follow for more", "start": 23.0, "end": 27.0},
            {"text": "like + share", "start": 27.0, "end": 30.0},
        ],
        "viral_score": 87,
        "viral_reasoning": "Fallback hardcoded analysis. Real reasoning appears when GEMINI_API_KEY is set and the call succeeds.",
        "tiktok_caption": (
            f"POV: peak moment in {game_clean}\n\n"
            f"#gaming #{game_tag} #fyp #viral #gamingclips #gameplay"
        ),
    }


# -----------------------------------------------------------------------------
# Real Gemini path
# -----------------------------------------------------------------------------

_PROMPT_TEMPLATE = """You are an expert short-form video editor specializing in viral gaming content for TikTok and Instagram Reels.

You will receive {n_frames} frames sampled from a {duration_sec:.1f}-second {game_name} clip. Each frame is preceded by a label "Frame i (t=Xs)" telling you exactly which timestamp it came from. Frames are 0-indexed in chronological order. The full clip spans frame indices 0 through {n_frames_minus_one}.

## Events to recognize
Use your knowledge of {game_name} to recognize these moments visually:
- KILL — a player eliminates an opponent
- DEATH — a player gets eliminated
- WIN / LOSS — a round or match ends
- CLUTCH — one player wins against multiple opponents (1vN)
- MULTI-KILL — a player eliminates multiple opponents in quick succession (double, triple, quad, ace)

{commentary_clause}{player_ign_clause}
## Framing principle: NEUTRAL EVENT-CENTRIC, GEN-Z VOICE

Frame highlights around THE EVENT, not around who wins or loses. Voice is short, punchy, Gen-Z TikTok-native — never corporate, never cringe.

You will write TWO different hooks (different text, different vibe) for two variant edits of the same segment.

`hook_a` — Variant A hook. Event-DESCRIPTIVE (3-7 words). Tells you what happens. Examples:
- "1V2 RED BULL CLUTCH"
- "TRIPLE KILL CLOSES IT"
- "ACE WITH ONE BULLET"
- "ROUND-DEFINING CLUTCH"
- "FINAL-SECONDS DRAMA"
- "TOURNAMENT-LEVEL PLAY"

`hook_b` — Variant B hook. Visceral/REACTIVE, 3-5 words. The vibe is "you have to see this" not "what happened". Examples:
- "STOP. WATCH. NO."
- "ACTUAL CINEMA"
- "I CANT BREATHE"
- "WHO IS HE"
- "NO SHOT NO SHOT"
- "HE DID NOT"
- "BRO???"

`hook_b` MUST be different text from `hook_a`. Different vibe. Don't paraphrase.

Caption beat examples — Gen-Z gaming TikTok voice, 2-4 words each:
- Setup: "no shot", "watch this", "look at him", "stop", "bro", "WAIT", "1v3 alive"
- Climax: "STOP IT", "him", "actual cinema", "WHAT", "no way", "GET IN", "HE'S COOKING", "ate", "no missed"
- Reaction: "absolute movie", "down bad", "send help", "rip", "L round", "W round", "GG", "skill diff", "lore"
- Action-specific: "spike planted", "time running", "1v2 alive", "down 2", "ace incoming", "triple", "final exchange"

DO NOT write captions that sound corporate or like a sports broadcaster. NO "GAME ENDS" / "ROUND SECURED" / "VICTORY". Use slangy, lowercase-by-default with ALL-CAPS for emphasis. Keep each caption 2-4 words. Use 0-1 emojis per caption max.

## Caption timing — peak at 25 SECONDS into output

CRITICAL: the output clip is structured as a highlight reel — 25 seconds of BUILD-UP, then the climax at ~25s, then ~5s of COOLDOWN. The peak event is at OUTPUT TIMESTAMP ~25s. NOT at 0s.

Distribute captions as:
- **0-12s (early build-up):** 3-4 SETUP captions establishing context. Calm. "spike planted" / "1v2 alive" / "no shot" / "WAIT FOR IT". DO NOT use climax words yet.
- **12-22s (rising tension):** 2-3 BUILDING captions. Increase intensity. "he's cooking" / "STOP" / "no way" / "HERE WE GO".
- **22-27s (climax window):** 1-2 PEAK captions describing the actual moment. "CLUTCH" / "ACTUAL CINEMA" / "HE DID THAT" / "ACE".
- **27-30s (reaction):** 1-2 REACTION captions. "GG" / "absolute movie" / "W round" / "skill diff".

Total 7-10 beats, 1.5-3.0s each, at least 0.3s gaps. NEVER bunch climax words in the first 10 seconds — the climax IS at second 25.

## Your task — multi-event detection AND ranked selection

This pipeline takes long inputs and finds MULTIPLE cut-able moments, scoring each so reviewers can see the system actually understands the gameplay (not just "longest action wins"). You must:

1. Identify EVERY cut-able event you can spot in the {n_frames} frames. Each event needs:
   - event_type: one of `clutch`, `ace`, `multi_kill`, `kill`, `round_win`, `highlight`
   - peak_frame_index: the climax frame for THIS event
   - start_frame_index: 1-2 frames before the peak (lead-in)
   - description: short sentence describing this specific event
   - weight: integer event-type score from this rubric:
       clutch=100, ace=95, round_win=80, multi_kill=75, kill=50, highlight=40
   - hook_a: 3-7 word event-descriptive hook for this specific event. UNIQUE — must differ from other events' hooks.
   - hook_b: 3-5 word visceral/reactive hook for this specific event. UNIQUE — different from hook_a and from other events' hook_b.

2. Sort the events by weight (highest first). Return them in the `events` array.

3. The TOP event becomes the primary segment for Variants A and B. Set the top-level `peak_frame_index` and `highlight_start_frame` to match `events[0]`.

4. **Group dense kill clusters into a multi_kill event.** If you see kills at frames 25, 26, 27, 28 — that's ONE multi_kill event with peak at frame 28 (the last kill in the burst), not four separate kill events. Group consecutive kills (within 2-3 frame indices of each other) into a single multi_kill or ace event.

If you can only spot one event, return one. If you spot 3-5, return all of them — the scored list is the proof of pipeline value.

DO NOT default to frame 0 or frame {n_frames_minus_one}. COMMIT to specific integer frame indices grounded in what you saw.

## Output requirements
Return ONLY a single JSON object matching this exact schema. No preamble, no markdown fencing, no commentary outside the JSON:

{{
  "events": [
    {{
      "event_type": <"clutch" | "ace" | "multi_kill" | "kill" | "round_win" | "highlight">,
      "peak_frame_index": <integer 0 to {n_frames_minus_one}>,
      "start_frame_index": <integer 0 to peak_frame_index, 1-2 frames before peak>,
      "description": <short sentence describing this specific event>,
      "weight": <integer per the rubric: clutch=100, ace=95, round_win=80, multi_kill=75, kill=50, highlight=40>,
      "hook_a": <3-7 word event-descriptive hook UNIQUE to this event. Different from other events' hooks.>,
      "hook_b": <3-5 word visceral/reactive hook UNIQUE to this event. Different from hook_a and from other events' hook_b.>
    }}
    // sorted by weight descending; include 2-5 events as you spot them
  ],
  "peak_frame_index": <integer matching events[0].peak_frame_index>,
  "highlight_start_frame": <integer matching events[0].start_frame_index>,
  "event_description": <one neutral sentence describing the TOP event (events[0]). Example: "1v2 clutch attempt with the spike planted, time running out, and two opponents pushing in.">,
  "hook_a": <3 to 7 words. Event-descriptive, ALL CAPS for impact. See `hook_a` examples above.>,
  "hook_b": <3 to 5 words. Visceral/reactive, DIFFERENT TEXT from hook_a. See `hook_b` examples above. Don't paraphrase hook_a.>,
  "captions": [
    <6 to 10 caption beats narrating the 30-second event neutrally. Each: {{"text": "<3-6 word phrase>", "start": <seconds RELATIVE to highlight_start_frame, 0.0 = first frame of output>, "end": <seconds RELATIVE>}}. Cover the full 30s with no overlaps and at most 0.5s gaps. Cluster more captions around the peak moment.>
  ],
  "viral_score": <integer 0-100. Pure drama metric — generic action 50-65, real clutches/aces/multi-kills 80-95.>,
  "viral_reasoning": <1-2 sentences. MUST cite specific frame numbers and describe the event neutrally. Example: "Frames 35-38 show a 1v2 clutch with the spike planted; commentary at 217s confirms the round-defining moment.">,
  "tiktok_caption": <full TikTok caption. End with 3-5 hashtags including the game name. 1-2 emoji where natural. Max ~200 characters. Event-focused, no partisan claims.>
}}

## Critical rules
- peak_frame_index and highlight_start_frame MUST be specific integers in [0, {n_frames_minus_one}].
- highlight_start_frame MUST be <= peak_frame_index.
- Hook MUST be event-focused, NOT partisan (no "X wins" / "Y loses").
- Captions MUST narrate the event without claiming victory/defeat for either side.
- Caption timestamps are RELATIVE to highlight_start_frame's timestamp (0.0 = first frame of OUTPUT clip).
- viral_reasoning MUST cite specific frame numbers.
- DO NOT default to peak_frame_index=0.
- No fields beyond the schema. No markdown anywhere in the JSON.
"""


def _build_prompt(
    n_frames: int,
    duration_sec: float,
    interval_sec: float,
    game_name: str,
    player_ign: str,
    transcript_block: str,
) -> str:
    ign = player_ign.strip()
    if ign:
        player_ign_clause = (
            "## Player to track\n"
            f"In-game name: `{ign}`. If you can identify this player in the frames, "
            f"feature their actions in the captions, but keep the hook event-focused.\n\n"
        )
    else:
        player_ign_clause = ""

    if transcript_block:
        commentary_clause = (
            "## Commentary track\n"
            "The audio of this clip has been transcribed below with timestamps. "
            "Use it to confirm what kind of event is happening (clutch, multi-kill, "
            "ace, etc.) and to phrase the captions accurately.\n\n"
            "Transcript:\n"
            f"{transcript_block}\n\n"
        )
    else:
        commentary_clause = (
            "## Commentary track\n"
            "(No speech detected in the clip — silent gameplay. Determine event from frames alone.)\n\n"
        )

    return _PROMPT_TEMPLATE.format(
        n_frames=n_frames,
        n_frames_minus_one=max(0, n_frames - 1),
        duration_sec=duration_sec,
        interval_sec=interval_sec,
        game_name=game_name,
        commentary_clause=commentary_clause,
        player_ign_clause=player_ign_clause,
    )


def _strip_fencing(raw: str) -> str:
    """Remove leading/trailing markdown fences if Gemini ignored response_mime_type."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s


def _validate_and_clamp(
    data: dict,
    *,
    n_frames: int,
    interval_sec: float,
    duration_sec: float,
) -> dict[str, Any]:
    """Validate Gemini's response; convert frame indices to seconds; clamp where safe."""
    required = {
        "events",
        "peak_frame_index", "highlight_start_frame",
        "event_description", "hook_a", "hook_b",
        "captions", "viral_score", "tiktok_caption",
    }
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Missing required fields: {missing}")

    peak_idx = int(data["peak_frame_index"])
    start_idx = int(data["highlight_start_frame"])
    peak_idx = max(0, min(n_frames - 1, peak_idx))
    start_idx = max(0, min(peak_idx, start_idx))

    # Highlight reel structure: climax should sit at ~25s of the 30s output
    # (with build-up before and short cooldown after), NOT at the start.
    # Override start_idx-derived start_sec with peak-anchored window.
    peak_sec = peak_idx * interval_sec
    cooldown_after = 5.0
    end_sec = min(duration_sec, peak_sec + cooldown_after)
    start_sec = max(0.0, end_sec - 30.0)
    # If we hit the right edge and segment is shorter than 30s, that's fine —
    # extend if there's headroom on the right.
    if end_sec - start_sec < 30.0 and end_sec < duration_sec:
        end_sec = min(duration_sec, start_sec + 30.0)

    seg_dur = end_sec - start_sec
    if seg_dur < 0.5:
        raise ValueError(f"Invalid segment after conversion: start={start_sec:.2f}, end={end_sec:.2f}")

    captions: list[dict] = []
    for cap in data.get("captions", []):
        text = str(cap.get("text", "")).strip()
        if not text:
            continue
        cs = max(0.0, float(cap.get("start", 0.0)))
        ce = min(seg_dur, float(cap.get("end", cs + 2.0)))
        if ce > cs:
            captions.append({"text": text, "start": cs, "end": ce})
    if len(captions) < 3:
        raise ValueError(f"Too few valid captions: {len(captions)}")

    hook_a = str(data["hook_a"]).strip()
    hook_b = str(data["hook_b"]).strip()
    for label, h in [("hook_a", hook_a), ("hook_b", hook_b)]:
        wc = len(h.split())
        if not (1 <= wc <= 12):
            raise ValueError(f"{label} word count out of range (1-12): {wc}")
    if hook_a.lower() == hook_b.lower():
        # Force differentiation if Gemini cheats
        hook_b = f"{hook_b} (B)"

    viral_score = max(0, min(100, int(data["viral_score"])))

    # Validate the events array (light touch: filter, compute time, sort).
    # Each event gets its own peak-anchored 30s segment AND its own hook pair.
    raw_events = data.get("events", []) or []
    events: list[dict] = []
    cooldown = 5.0
    for ev in raw_events:
        if not isinstance(ev, dict):
            continue
        try:
            ev_type = str(ev.get("event_type", "highlight")).strip().lower()
            ev_peak = max(0, min(n_frames - 1, int(ev.get("peak_frame_index", 0))))
            ev_start = max(0, min(ev_peak, int(ev.get("start_frame_index", ev_peak))))
            ev_weight = max(0, min(100, int(ev.get("weight", 40))))
            ev_desc = str(ev.get("description", "")).strip()
            ev_hook_a = str(ev.get("hook_a", "")).strip()
            ev_hook_b = str(ev.get("hook_b", "")).strip()
        except Exception:
            continue
        # Peak-anchored segment: peak sits ~25s in, 5s cooldown after
        ev_peak_sec = ev_peak * interval_sec
        ev_end_sec = min(duration_sec, ev_peak_sec + cooldown)
        ev_start_sec = max(0.0, ev_end_sec - 30.0)
        if ev_end_sec - ev_start_sec < 30.0 and ev_end_sec < duration_sec:
            ev_end_sec = min(duration_sec, ev_start_sec + 30.0)
        events.append({
            "event_type": ev_type,
            "peak_frame_index": ev_peak,
            "start_frame_index": ev_start,
            "peak_time_sec": float(ev_peak_sec),
            "start_sec": float(ev_start_sec),
            "end_sec": float(ev_end_sec),
            "description": ev_desc,
            "weight": ev_weight,
            "hook_a": ev_hook_a,
            "hook_b": ev_hook_b,
        })
    # Sort highest weight first
    events.sort(key=lambda e: e["weight"], reverse=True)

    return {
        "start_sec": float(start_sec),
        "end_sec": float(end_sec),
        "peak_frame_index": peak_idx,
        "highlight_start_frame": start_idx,
        "events": events,
        "event_description": str(data["event_description"]).strip(),
        "hook_a": hook_a,
        "hook_b": hook_b,
        "captions": captions,
        "viral_score": viral_score,
        "viral_reasoning": str(data.get("viral_reasoning", "")).strip(),
        "tiktok_caption": str(data["tiktok_caption"]).strip(),
    }


def real_analyze(
    video_path: str,
    game_name: str,
    player_ign: str = "",
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """Production analysis path. Raises on any failure (caller should catch and fall back)."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    if work_dir is None:
        work_dir = Path("runs") / "analyze-tmp"
    frames_dir = work_dir / "frames"

    samples, duration = extract_frames(video_path, frames_dir, target_frame_count=TARGET_FRAMES)
    if len(samples) < 4:
        raise RuntimeError(f"Too few frames extracted: {len(samples)} (need >= 4)")
    interval = duration / max(1, len(samples))

    transcript_segments = transcribe(video_path)
    transcript_block = transcript_to_text_block(transcript_segments, max_chars=4000)

    prompt = _build_prompt(
        n_frames=len(samples),
        duration_sec=duration,
        interval_sec=interval,
        game_name=game_name.strip() or "Unknown",
        player_ign=player_ign,
        transcript_block=transcript_block,
    )

    client = genai.Client(api_key=api_key)

    parts: list[Any] = [prompt]
    for i, s in enumerate(samples):
        parts.append(f"\n--- Frame {i} (t={s.timestamp_sec:.1f}s in original clip) ---")
        parts.append(types.Part.from_bytes(
            data=s.path.read_bytes(),
            mime_type="image/jpeg",
        ))

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.5,
    )
    response = None
    last_err: Exception | None = None
    used_model = None
    # Try each model in the fallback chain; for each, retry transient errors
    # twice with backoff before moving to the next model.
    for model_name in GEMINI_MODEL_CHAIN:
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=parts,
                    config=config,
                )
                used_model = model_name
                print(f"[real_analyze] success with model: {model_name}")
                break
            except Exception as e:
                last_err = e
                err_str = str(e)
                transient = any(s in err_str for s in [
                    "503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL", "deadline",
                ])
                if not transient:
                    raise  # hard error — auth, bad request, etc.
                # On quota exhaustion, switch model immediately (no retry)
                if "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                    print(f"[real_analyze] {model_name} quota exhausted, trying next model")
                    break
                # Otherwise retry on the same model with backoff
                if attempt < 2:
                    wait = 4 * (attempt + 1)
                    print(f"[real_analyze] {model_name} transient (attempt {attempt+1}), waiting {wait}s")
                    time.sleep(wait)
                    continue
                # Out of retries for this model, fall through to next
                print(f"[real_analyze] {model_name} exhausted retries, trying next model")
                break
        if response is not None:
            break
    if response is None:
        raise RuntimeError(
            f"All Gemini models in chain failed: {GEMINI_MODEL_CHAIN}. Last error: {last_err}"
        )

    raw = response.text or ""
    cleaned = _strip_fencing(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Gemini did not return valid JSON: {e}\nRaw response (first 1000 chars):\n{cleaned[:1000]}"
        )

    return _validate_and_clamp(
        data,
        n_frames=len(samples),
        interval_sec=interval,
        duration_sec=duration,
    )


# -----------------------------------------------------------------------------
# Wrapper with fallback
# -----------------------------------------------------------------------------

def analyze(
    video_path: str,
    game_name: str,
    player_ign: str = "",
    work_dir: Path | None = None,
) -> tuple[dict[str, Any], str]:
    """Try real_analyze, fall back to fake_analyze.

    Returns (analysis, source). source in {"gemini", "fake"}.
    """
    try:
        return real_analyze(video_path, game_name, player_ign, work_dir), "gemini"
    except Exception as e:
        print(f"[analyze] real_analyze failed, falling back to fake_analyze: {type(e).__name__}: {e}")
        return fake_analyze(video_path, game_name), "fake"
