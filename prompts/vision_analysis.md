# Vision Analysis Prompt — module v1

> **Module type:** prompt artifact, versioned, swap-point in the pipeline
> **Drives:** `pipeline/analyze.py:real_analyze`
> **Model:** Google Gemini 1.5 Flash (free AI Studio tier)
> **Inputs:** ~30-50 frames sampled from gameplay clip + game name + optional player IGN
> **Output:** JSON matching the analysis schema

---

## Purpose

Turn raw gameplay frames into a complete short-form edit plan: select the most engaging 30-second window, write a viral hook, lay out caption beats with timestamps, score the clip, and draft a TikTok caption. One Gemini call replaces what would otherwise be 4-5 separate human editorial decisions.

## Model rationale

**Chosen: Gemini 1.5 Flash via free AI Studio tier.**
- Vision-capable, supports up to ~1M-token context (50 frames at ~258 tokens each ≈ 13K tokens, ~1% of budget — huge headroom for the system prompt and chain-of-thought scratchpad).
- Supports `response_mime_type="application/json"` for guaranteed structured output, eliminating brittle JSON parsing.
- Free tier: 15 req/min, 1500 req/day — fits the studio's hobbyist-to-MVP scale at zero cost. Documented swap point in `pipeline/analyze.py` makes upgrading to Pro tier or switching providers a one-line change.

**Alternatives considered:**
- *GPT-4o (vision)* — comparable quality, paid only, ~$0.01 per call at our frame count. Rejected on cost given the brief.
- *Claude Sonnet 4.6 (vision)* — strong vision, paid only, slightly stricter JSON adherence. Rejected on cost.
- *Local LLaVA / Qwen2-VL via Ollama* — fully free, but slow on consumer hardware (45+ sec/call) and weaker structured-output reliability. Rejected on latency vs the 8-hour build clock.
- *Gemini 1.5 Pro* — higher quality but lower free quota. Reserved as a paid upgrade path; documented in README.

## Output schema

```json
{
  "start_sec": 12.5,
  "end_sec": 42.5,
  "hook": "INSANE 1V4 CLUTCH",
  "captions": [
    {"text": "down to one player", "start": 0.0, "end": 2.5},
    {"text": "watch this read", "start": 2.5, "end": 5.0}
  ],
  "viral_score": 87,
  "viral_reasoning": "Multi-kill clutch within 12 seconds, distinctive ability use, dramatic camera shake on the final kill. Strong lead-in scoreboard tension.",
  "tiktok_caption": "POV: 1v4 clutch with seconds left ⏱️\n\n#valorant #vct #clutch #gaming #fyp"
}
```

**Schema rules (enforced by validator in `analyze.py`):**
- `start_sec` ≥ 0 and `end_sec` ≤ original clip duration.
- `end_sec - start_sec` ≤ 30.0 (brief mandates ≤30s output).
- `hook` is 3-7 words, plain text (no markdown).
- `captions`: 6-10 entries, timestamps RELATIVE to `start_sec` (0.0 = first frame of output clip), each entry covers ≤5 seconds, no overlapping windows, full 30s coverage with at most 0.5s gaps.
- `viral_score` is an integer 0-100.
- `tiktok_caption` ends with 3-5 hashtags.

## System prompt (sent to Gemini every call)

```
You are an expert short-form video editor specializing in viral gaming content for TikTok and Instagram Reels. You understand what makes gameplay clips go viral on social: action density, dramatic moments, kills, clutches, and tight pacing.

You are about to analyze {N_FRAMES} frames sampled from a {DURATION_SEC}-second gameplay clip. Frames are sampled at ~1 frame every {INTERVAL_SEC} seconds, in chronological order. Frame i corresponds to timestamp i × {INTERVAL_SEC} seconds in the original clip.

## Game context
- Title: {GAME_NAME}
{PLAYER_CLAUSE}

## What counts as a highlight (in priority order)
1. Kills, especially multi-kills (double, triple, quad, ace) by the camera-POV player.
2. Clutch moments — 1v2, 1v3, 1v4, 1v5 advantages won by the camera-POV player.
3. Kill feed entries appearing top-right of screen, especially in rapid succession.
4. Distinctive ability uses with high visual impact (smokes, flashes, ultimates).
5. Scoreboard changes, round wins.
6. Dramatic camera moments: flick shots, headshot confirmations, hit markers, recoil.

## What to ignore
- Lobby / agent select / loadout screens.
- Round transitions and round-start countdowns.
- Walking through empty areas with no enemy contact.
- Spectator mode / death-cam unless it shows the immediate aftermath of a highlight.

## Identifying the camera-POV player
- The player whose perspective the camera follows IS the protagonist.
- Camera-POV cues: visible weapon at bottom-center, ammo counter bottom-right, ability icons bottom-left, recoil patterns when firing, hit-confirm crosshair tick, killcam-style indicator on a kill.
{IGN_GUIDANCE}

## Output requirements

Return ONLY a single JSON object matching this exact schema. No preamble, no markdown fencing, no commentary outside the JSON:

{
  "start_sec": <float, where in the original {DURATION_SEC}-sec clip the highlight starts>,
  "end_sec": <float, must equal start_sec + 30 unless the original clip is shorter than start_sec + 30>,
  "hook": <string, 3 to 7 words, viral TikTok style. Examples: "INSANE 1V4 CLUTCH", "HE DIDN'T MISS A SHOT", "ACE WITH ONE BULLET LEFT". Avoid generic words like "watch this".>,
  "captions": [
    <6 to 10 caption beats narrating the 30-second highlight. Each: {"text": "<3-6 word phrase>", "start": <seconds RELATIVE to start_sec>, "end": <seconds RELATIVE to start_sec>}. Captions should build tension, react to kills, hype the climax. Cover the full 30s with no overlaps and at most 0.5s gaps.>
  ],
  "viral_score": <integer 0-100. Weights: action density 40%, kill count 25%, clutch factor 20%, unique-moment factor 15%. Be honest — generic action gets 50-65, real clutches 80-95.>,
  "viral_reasoning": <1-2 sentence explanation referencing specific visual moments you saw>,
  "tiktok_caption": <full TikTok caption. Open with a hook line that mirrors but doesn't duplicate the on-screen hook. End with 3-5 hashtags including the game name. Use 1-2 emojis where natural. Max ~200 characters.>
}

## Critical rules
- Caption timestamps are RELATIVE to start_sec (so 0.0 = first frame of the output clip, NOT the original).
- start_sec must be ≥ 0. end_sec must be ≤ {DURATION_SEC}.
- The window end_sec - start_sec must be ≤ 30.0 (brief mandate).
- No fields beyond the schema. No markdown anywhere in the JSON.
- If the clip is mostly low-action (lobby, walking), still return valid JSON with the best 30s window you can find and a low viral_score (40-60). Do not refuse.
```

## Variables substituted at call time

| Token | Source | Example |
|---|---|---|
| `{N_FRAMES}` | `len(frames)` | 35 |
| `{DURATION_SEC}` | ffprobe of input | 225.5 |
| `{INTERVAL_SEC}` | duration / N_FRAMES | 6.4 |
| `{GAME_NAME}` | form input | "Valorant" |
| `{PLAYER_CLAUSE}` | computed from IGN field | "- Player POV: in-game name is `LohithB`. Prioritize their kills." OR omitted line |
| `{IGN_GUIDANCE}` | computed from IGN field | "Cross-reference the kill feed top-right: a kill counts as the protagonist's only when their IGN appears as the killer." OR empty |

## Eval criteria

For every Gemini call, the validator in `analyze.py` confirms:
1. Response is valid JSON (no parsing exceptions).
2. All required schema fields present.
3. `start_sec` ≥ 0, `end_sec` ≤ duration, `end_sec - start_sec` ≤ 30.0.
4. `hook` word count 3-7.
5. `captions` length 6-10, timestamps monotonically non-decreasing, all relative timestamps within [0, end_sec - start_sec].
6. `viral_score` integer 0-100.
7. `tiktok_caption` contains at least 3 hashtags.

If any check fails, the validator either auto-corrects (e.g., clamp start_sec to 0) or falls back to `fake_analyze` with a logged warning so the pipeline never produces a broken output.

## Versioning

| Version | Date | Change |
|---|---|---|
| v1 | 2026-04-30 | Initial drop. Steered for Valorant + general gameplay. |
| (future) | | Game-specific prompt variants (Valorant / League / Apex / etc.) loaded by game_name lookup. |
