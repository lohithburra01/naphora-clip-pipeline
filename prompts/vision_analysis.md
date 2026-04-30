# Vision Analysis Prompt — design doc

> **Module type:** prompt artifact, versioned, swap-point in the pipeline
> **Drives:** `pipeline/analyze.py:real_analyze`
> **Model family:** Google Gemini Flash (free AI Studio tier) with model-fallback chain
> **Inputs:** ~30-50 sampled frames + game name + audio commentary transcript (whisper) + optional player IGN
> **Output:** structured JSON with a ranked `events` array, per-event hooks, peak-anchored segments, viral score, TikTok caption

The pipeline lives or dies on this prompt. It is the only AI editorial voice in the system — Gemini's response decides what gets cut, what the hook says, what tone the captions take, and what viral score is reported. Everything else is plumbing around the prompt.

---

## 1. Purpose

Turn a raw long-form gameplay or broadcast video into:
1. A **ranked list of cut-able events** (clutches, aces, multi-kills, kills, round wins) with weights.
2. A **per-event 30-second segment** anchored so the climax sits at ~25s of output (build-up before, brief cooldown after — the highlight-reel structure).
3. **Two distinct hooks per event** (`hook_a` event-descriptive, `hook_b` visceral/reactive) so each variant gets a unique title.
4. **Caption beats** narrating the climax in Gen-Z TikTok voice.
5. A **viral score** with reasoning.
6. A **copy-paste TikTok caption** with hashtags.

One Gemini call replaces what would otherwise be 5-6 separate human editorial decisions per clip.

## 2. Model rationale

**Default:** `gemini-flash-latest` (alias to current Gemini Flash via free AI Studio tier).
- Vision-capable, takes mixed text + image parts in a single call.
- Supports `response_mime_type="application/json"` for guaranteed structured output (no JSON parsing roulette).
- Free tier covers studio-scale exploration at zero cost.

**Fallback chain** (configured in `pipeline/analyze.py`, overridable via `GEMINI_MODEL_CHAIN` env var):

```
gemini-flash-latest → gemini-2.5-flash-lite → gemini-2.5-flash → gemini-flash-lite-latest
```

Each model gets up to 3 attempts with 4s/8s/12s backoff on transient errors (503, 429, INTERNAL). On `RESOURCE_EXHAUSTED` the loop skips ahead to the next model immediately. If every model fails, the wrapper falls through to a hardcoded `fake_analyze` so the pipeline never crashes — and the UI surfaces a clear "Gemini API unavailable" banner instead of pretending success.

**Alternatives considered and rejected:**
| Model | Why not chosen |
|---|---|
| GPT-4o vision | Comparable quality, paid only (~$0.01-0.04/call at our frame count). Brief mandates free. |
| Claude Sonnet 4.6 vision | Best JSON adherence, paid only. Reserved as a documented swap point. |
| Gemini 2.5 Pro | Higher quality, much lower free quota — would be exhausted in <10 runs. |
| Local LLaVA / Qwen2-VL | Free but 45+ sec/call on consumer CPU + weaker structured-output reliability. |

## 3. Multi-modal inputs

**Vision (frames):** `pipeline/extract.py` adaptive-samples ~40 frames regardless of clip length. For a 30-sec clip that's ~1 fps; for a 4-min clip that's ~1 frame every 6 seconds. This keeps token cost predictable. Frames are passed inline as JPEG, each labelled with its timestamp ("Frame 17 (t=95.2s)") so Gemini can ground its picks in specific moments it has explicit confirmation of.

**Audio (commentary transcript):** `pipeline/transcribe.py` runs faster-whisper `tiny.en` (CPU int8) on the full clip. Segment-level transcript is injected into the prompt as a "commentary track" Gemini can cross-reference with the frames. This is the strongest autonomous signal for outcome attribution on broadcast/streamer clips, where commentators explicitly narrate "X dies", "Y team wins", "Red Bull Clutch for Z".

**Word-level timestamps** are extracted separately and used downstream by `pipeline/captions.py` for Variant B's karaoke caption track — captions sync perfectly because they ARE the audio timing.

## 4. Output schema

```json
{
  "events": [
    {
      "event_type": "clutch|ace|multi_kill|kill|round_win|highlight",
      "peak_frame_index": 38,
      "start_frame_index": 36,
      "peak_time_sec": 213.8,
      "start_sec": 189.3,
      "end_sec": 219.3,
      "description": "1v2 clutch with the spike planted, time running out.",
      "weight": 100,
      "hook_a": "RED BULL CLUTCH SECURED",
      "hook_b": "NO SHOT NO SHOT"
    }
    // ... ranked by weight descending
  ],
  "peak_frame_index": 38,
  "highlight_start_frame": 36,
  "event_description": "1v2 clutch attempt with the spike planted...",
  "hook_a": "RED BULL CLUTCH TIME",
  "hook_b": "ACTUAL CINEMA",
  "captions": [
    {"text": "spike planted", "start": 0.0, "end": 1.5},
    {"text": "1v2 alive", "start": 2.0, "end": 3.5},
    {"text": "ACTUAL CINEMA", "start": 24.0, "end": 26.0}
  ],
  "viral_score": 92,
  "viral_reasoning": "Frames 37-38 show 1v2 clutch with spike planted...",
  "tiktok_caption": "0:02 left, 1v2 clutch. Can he do it? 🤯 #valorant #clutch #gaming #fps"
}
```

## 5. Design decisions

Each of these is a deliberate choice with a tradeoff. Reviewers asking "why this approach" should find the answer here.

### 5.1 Frame-index grounding (not free-floating seconds)
**Decision:** Gemini commits to integer `peak_frame_index` and `start_frame_index`. Code converts indices → seconds.
**Why:** Earlier iterations asked Gemini for `start_sec` as a free float. When confidence was low, Gemini defaulted to `0.0` — segments started at the lobby/buy-phase of every clip. Forcing integer frame indices makes Gemini commit to a specific frame it actually saw and labelled. Default-to-frame-0 failure mode disappeared.

### 5.2 Multi-event detection + weighted ranking
**Decision:** Gemini returns an `events[]` array with weighted scoring (clutch=100, ace=95, round_win=80, multi_kill=75, kill=50, highlight=40), sorted highest-first.
**Why:** Naphora doesn't want a "longest action wins" detector. They want proof the system **understands** gameplay. The ranked list appears in the UI's timeline visualisation + events table — visible evidence that the AI saw multiple cut-able moments and chose the best one. Top 2 events drive the 4-output grid (top 2 segments × 2 variants).

### 5.3 Peak-anchored 25s segment (climax at end, not start)
**Decision:** Output segment puts `peak_frame` at ~25 seconds, with 25s of build-up before and ~5s of cooldown after.
**Why:** Standard highlight-reel structure. Earlier iterations placed peak at second 5 followed by 25 seconds of post-climax silence/aftermath — the cuts felt anti-climactic. Build-up → climax → brief reaction is how viral gaming clips on TikTok actually structure their 30 seconds.

### 5.4 Per-event hooks (not shared across events)
**Decision:** Each event in the array carries its own `hook_a`/`hook_b`. Top-level hooks mirror events[0].
**Why:** When the pipeline renders 4 outputs (2 events × 2 variants), having both events show the same title makes the pipeline look broken. Per-event hooks force Gemini to come up with distinct framing per event. `hook_a` is event-descriptive ("TRIPLE KILL CLOSES IT"), `hook_b` is visceral/reactive ("STOP. WATCH. NO."). Tone differentiation per event.

### 5.5 Neutral event-centric framing (no won/lost partisan claim)
**Decision:** Hooks and captions describe THE EVENT, not who wins. No "X WON" / "Y LOSES" language.
**Why:** Pro broadcast clips switch camera POV between players unpredictably — the camera at the climax frame may be on the killer, not the protagonist whose POV opened the clip. Earlier iterations attempted protagonist-tracking with multiple verification rules; Gemini got it wrong on broadcast clips ~30% of the time because the visual signal is ambiguous. Neutral event framing sidesteps this entirely AND matches how viral TikTok gaming captions actually read ("INSANE 1V2 CLUTCH" — not "Vitality won the clutch"). One design choice removes a category of misattribution bugs and moves the output closer to the genre's native voice.

### 5.6 Minimal event vocabulary, no UI-cue overspec
**Decision:** The prompt names event categories (kill, death, clutch, etc.) but does NOT enumerate visual cues ("kill feed appears top-right with red icon", "screen tints red on death", etc).
**Why:** Earlier prompt drafts loaded UI-cue specifics that (a) were partially wrong (Valorant doesn't tint screen red on death), (b) overfit to Valorant and break on other titles, and (c) led the model rather than letting it observe. Vision LLMs already know what these events look like in any specific game. Removing the cue lists improved generalization across game titles without sacrificing recognition accuracy.

### 5.7 Kill-cluster grouping
**Decision:** Prompt instructs Gemini to group consecutive kills (within 2-3 frame indices) into a single `multi_kill` or `ace` event rather than emitting one `kill` event per frame.
**Why:** Without this, Gemini returned 9 separate `kill` events for a single multi-kill round, drowning the more important `clutch` event in the ranked list. Grouping puts the dramatic high-density moments at the top of the ranking where they belong.

### 5.8 Whisper word-level timestamps as caption ground-truth (Variant B)
**Decision:** Variant B's caption track is derived from faster-whisper word-level timestamps (chunked into 2-3 word phrases, CapCut-style), not from Gemini's invented timing.
**Why:** Gemini doesn't actually know when events happen at sub-second precision — it sees one frame every ~6 seconds and guesses caption timing. Whisper timestamps are literal audio ground truth. Captions on Variant B sync to the commentary because they ARE the commentary. Variant A still uses Gemini's curated Gen-Z phrase beats for differentiation.

## 6. The system prompt (sent every call)

The full prompt template lives at `pipeline/analyze.py:_PROMPT_TEMPLATE`. It composes:

1. **Role + style direction** — "viral short-form video editor for TikTok/Reels, Gen-Z TikTok-native voice."
2. **Frame context** — N frames, total duration, sampling interval, label format ("Frame i (t=Xs)").
3. **Game context + optional IGN** — for personal-gameplay clips where the user can name a player to feature.
4. **Commentary track** (or "no speech detected" fallback) — full whisper transcript with timestamps.
5. **Event vocabulary** — KILL / DEATH / WIN / LOSS / CLUTCH / MULTI-KILL definitions only, no UI cues.
6. **Framing principle** — neutral event-centric, with concrete hook + caption examples in the desired voice.
7. **Caption timing rules** — "climax at second 25, build-up 0-22s, reaction 25-30s. Distribute, don't bunch."
8. **Multi-event task** — return ranked `events[]` with per-event hooks, group kill clusters.
9. **Strict output schema** — JSON only, no markdown, no preamble, with field-by-field constraints.
10. **Critical rules block** — peak_frame ≠ 0 by default, hook tone ≠ generic, etc.

The full text is several hundred lines; rather than duplicate it here, refer to the source. The reasoning behind every section is one of the design decisions in §5.

## 7. Variables substituted at call time

| Token | Source | Example |
|---|---|---|
| `{n_frames}` | `len(samples)` | 40 |
| `{n_frames_minus_one}` | computed | 39 |
| `{duration_sec}` | ffprobe of input | 225.5 |
| `{interval_sec}` | duration / N_FRAMES | 5.6 |
| `{game_name}` | UI text input | "Valorant" |
| `{commentary_clause}` | whisper transcript or "(silent gameplay)" placeholder | timestamped lines |
| `{player_ign_clause}` | optional UI input | "- Player POV: IGN `RieNs`" or empty |

## 8. Validation + auto-clamp

`pipeline/analyze.py:_validate_and_clamp` enforces:
1. Required fields present: `events`, `peak_frame_index`, `highlight_start_frame`, `event_description`, `hook_a`, `hook_b`, `captions`, `viral_score`, `tiktok_caption`.
2. `peak_frame_index ∈ [0, n_frames-1]`, clamped if out of range.
3. `start_frame_index ≤ peak_frame_index`, clamped.
4. Segment derived from peak-anchored window (peak at ~25s of output).
5. Hook word count 1-12; out-of-range raises (caller falls back).
6. Hook A and Hook B not identical (forced suffix added if so).
7. Caption text non-empty, end > start, clamped to segment duration; minimum 3 valid captions or raise.
8. `viral_score` clamped to integer 0-100.
9. `outcome` (legacy) auto-defaulted to "neutral" if present.
10. Each event in the array gets a peak-anchored segment computed independently so app.py can render multiple events as separate clips.

If any check raises, the wrapper logs and falls back to `fake_analyze`.

## 9. Failure handling

Three concentric layers:

1. **Per-call retries (3 attempts, 4s/8s/12s backoff)** for transient errors on a given model.
2. **Model fallback chain** if a model is quota-exhausted or persistently 503ing.
3. **fake_analyze fallback + UI banner** if the entire chain fails, so the pipeline never crashes — the user sees a clear "Gemini API unavailable" message instead of a silent broken output.

This satisfies the brief's "Graceful handling of failed uploads, AI timeouts, processing errors" requirement explicitly.

## 10. Versioning + roadmap

| Version | Date | Change |
|---|---|---|
| v1 | 2026-04-30 morning | Initial single-event schema, hardcoded UI cues. |
| v2 | 2026-04-30 midday | Frame-index grounding; removed cue overspec; added multi-modal commentary track; switched to neutral event framing; peak-at-25s segment structure. |
| v3 | 2026-04-30 afternoon | Multi-event detection with weighted ranking; per-event hook pairs; kill-cluster grouping; model fallback chain. |
| (next) | | Per-event Gen-Z caption beats (currently top event only); game-specific prompt variants loaded by `game_name`; sticky preference learning per studio account. |

## 11. How to swap or experiment

- **Swap model:** set `GEMINI_MODEL` env var. Falls back through `GEMINI_MODEL_CHAIN` automatically.
- **Swap provider:** replace the `genai.Client` block in `real_analyze` with another SDK (Anthropic, OpenAI). Schema validation downstream is provider-agnostic.
- **A/B different prompts:** keep this file as v3; copy to `vision_analysis_v4.md` and toggle via env var; compare outputs side-by-side via the existing `tests/test_analyze.py` harness.
- **Add a new event type:** extend the `event_type` enum in this doc + the validator's allowed list + add a row to the weight rubric.
