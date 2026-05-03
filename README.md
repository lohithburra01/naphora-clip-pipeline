# Naphora Clip Pipeline

**Long gameplay video in → ranked highlight events → top 2 events rendered as 4 TikTok-ready 9:16 clips, each with distinct hooks and styling.**

End-to-end autonomous: upload, click Generate, get a timeline of every cut-able event the AI detected, the ranked event table, and four polished short-form clips ready to post. Multi-modal under the hood — Gemini Flash for vision + faster-whisper for audio commentary + ffmpeg for rendering. Designed for the Naphora Games Group Prompt/AI take-home test (2026-04-30).

**Sample run included:** the source gameplay video (FNC vs FUT, VCT EMEA 2026 Kickoff Playoffs Map 3) is hosted on [Google Drive](https://drive.google.com/file/d/1sRvDxC1ep9xdcAQeUnvAuUurT9PNUsN1/view?usp=sharing) (314 MB, too large for git), and `output clips/` in this repo holds the four clips it produced — review the quality without installing anything, or download the source and run it yourself end-to-end.

**Game-agnostic:** the prompt is not Valorant-specific. The game name is a runtime input and the prompt deliberately omits per-game UI cues so the same vision pipeline generalizes across titles. Event vocabulary (kill / clutch / ace / multi-kill / round-win) leans FPS / round-based shooter; extending to other genres is a vocabulary edit in `prompts/vision_analysis.md`. Rationale in §5.6 of that doc.

---

## What it does

1. **Web upload form** (Gradio) — drop a gameplay video (≤500 MB), enter the game name, optional player IGN, optional Gemini API key, click Generate.
2. **Multi-event detection** — Gemini Flash analyzes ~40 sampled frames + the full whisper-transcribed audio commentary, returns a ranked list of EVERY cut-able moment (clutches, aces, multi-kills, kills, round wins, generic highlights) with weighted scores.
3. **Top 2 events become 4 outputs** — each of the two highest-weighted events renders in two distinct variants:
   - **Variant A (hype):** Arial Black white classic phrase beats curated by Gemini, hook drops in from top with bounce, warm color grade.
   - **Variant B (cinematic):** Impact yellow karaoke captions chunked into 2-3 word phrases from real audio commentary timing (perfect sync), hook scales in slow, cool desaturated grade with letterbox bars.
4. **Results page** — timeline plot showing every detected event by weight + color-coded type, ranked event table, four embedded video players with download buttons, copy-pasteable TikTok caption with hashtags, viral score with reasoning, full analysis JSON in an accordion for transparency.

---

## How to run — one command

### Three things to install ONCE (skip if you already have them)

1. **Python 3.10+** — https://www.python.org/downloads/ → check **"Add Python to PATH"** during install
2. **ffmpeg** — pick your OS:
   - Windows: `winget install Gyan.FFmpeg`
   - macOS:   `brew install ffmpeg`
   - Linux:   `sudo apt install ffmpeg`
3. **Free Gemini API key** — https://aistudio.google.com/apikey → "Create API key" → copy it (starts with `AIza...`). You'll paste it in the UI later, not in any file.

### Get the code

Either:
- `git clone https://github.com/lohithburra01/naphora-clip-pipeline.git`
- OR download the ZIP from the green "Code" button on GitHub and unzip.

### Run it (literally one command)

Open a terminal in the project folder, then:

| OS | Command |
|---|---|
| Windows | `python run.py` (or just **double-click `run.bat`**) |
| macOS / Linux | `python3 run.py` (or `./run.sh` after `chmod +x run.sh`) |

`run.py` does everything: creates the virtualenv, installs all dependencies, launches the app, and opens **http://127.0.0.1:7860** in your browser. First run takes ~2 minutes for the dependency install; subsequent runs start instantly.

### Use it

1. The browser opens to the Gradio UI at http://127.0.0.1:7860 automatically.
2. Drag a gameplay video into the upload box (≤500 MB).
3. Type the game name (e.g. `Valorant`).
4. Paste your Gemini API key in the textbox.
5. *(Optional)* Player IGN if personal gameplay.
6. Click **Generate Clips**. Wait ~30-60 seconds.
7. See: timeline plot of detected events, ranked event table, 4 video players (top 2 events × 2 variants), TikTok caption, viral score.

### Common issues

- **`ffmpeg not found`** — finish step 2 above. On Windows you may need to close + reopen the terminal after install so the new PATH takes effect.
- **Whisper model download taking forever on first generate** — that's the ~75 MB `tiny.en` model downloading. One-time, then cached.
- **Gemini 503 / quota errors** — pipeline auto-retries across a fallback chain of 4 models; if it still fails the UI shows a clear banner. Wait 30-60 sec and click Generate again.

---

## Architecture

```
┌───────────────┐
│  Gradio UI    │  upload + game name + (opt) IGN + (opt) API key
└──────┬────────┘
       │
       ▼
┌────────────────────────────────────────────────────────────────┐
│                pipeline.analyze.analyze()                       │
│                                                                 │
│  ┌─────────────┐   ┌─────────────┐   ┌────────────────────┐    │
│  │ extract.py  │ → │ Gemini Flash │ ← │ transcribe.py      │    │
│  │ (~40 frames)│   │ (vision call)│   │ (whisper segments) │    │
│  └─────────────┘   └──────┬───────┘   └────────────────────┘    │
│                           │ structured JSON                      │
│                           ▼                                      │
│                  events[] ranked by weight                       │
│                  per-event hook_a / hook_b                       │
│                  peak-anchored 30s segments                      │
└──────┬──────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────┐
│  ThreadPoolExecutor — 4 parallel render jobs         │
│                                                       │
│  Event 1 / Variant A  Event 1 / Variant B            │
│  Event 2 / Variant A  Event 2 / Variant B            │
│                                                       │
│  Each: ffmpeg → scale + crop (or letterbox) +        │
│        color grade + libass burn-in (ASS file        │
│        from captions.py with per-style animation)    │
└──────┬──────────────────────────────────────────────┘
       │
       ▼
┌───────────────┐
│  Results UI   │  timeline plot · ranked events table ·
│               │  4 video players with downloads ·
│               │  TikTok caption · viral score · raw JSON
└───────────────┘
```

---

## The prompt is the product

The pipeline is engineered AROUND the prompt. The prompt at `prompts/vision_analysis.md` is the actual editorial voice — every design decision (frame-index grounding, multi-event ranking, peak-anchored segments, neutral event framing, kill-cluster grouping, minimal event vocabulary) is documented there with the rationale that drove it.

**Read `prompts/vision_analysis.md` first if you want to understand HOW the system thinks.** The Python is plumbing around that document.

Highlights of the prompt design:

- **Frame-index grounding:** Gemini commits to integer frame indices (not free-floating seconds), so its picks are anchored to specific frames it actually saw. Avoids the "default to 0" failure mode where uncertain models silently picked the first 30 seconds of every clip.
- **Multi-event ranking:** Returns an `events[]` array sorted by weight (clutch=100, ace=95, round_win=80, multi_kill=75, kill=50, highlight=40). Top 2 events drive the 4-output grid. The full ranked list is visible in the timeline plot — proof the system understands the gameplay, not just "longest action wins".
- **Peak-anchored 25s segment:** Output puts the climax at ~second 25, with 25s of build-up before and ~5s of cooldown after — proper highlight-reel structure rather than climax-then-silence.
- **Per-event hooks:** Each event carries its own `hook_a` (event-descriptive) and `hook_b` (visceral/reactive). Two events render with two different titles, never duplicated.
- **Neutral event framing:** Hooks describe THE EVENT, not who wins/loses. Works for broadcasts (where camera switches POVs) and for personal POV gameplay alike. Matches how viral TikTok gaming captions actually read.
- **Multi-modal grounding:** Frames + audio commentary transcript both fed to Gemini in the same call. Whisper word-level timestamps separately drive Variant B's karaoke caption track for perfect audio sync.
- **Minimal event vocabulary:** Prompt names event categories (kill, clutch, ace, etc.) but does NOT enumerate visual cues. Vision LLMs already know what these look like — overspecified cues curve-fit to one game and lead the model wrong.

---

## Error handling (per brief)

Three concentric layers:

1. **Per-call retries** — 3 attempts per model with 4s/8s/12s backoff on transient errors (503, 429, INTERNAL).
2. **Model fallback chain** — if one model is quota-exhausted or persistently overloaded, the wrapper switches to the next: `gemini-flash-latest → gemini-2.5-flash-lite → gemini-2.5-flash → gemini-flash-lite-latest`. Configurable via `GEMINI_MODEL_CHAIN` env var.
3. **fake_analyze fallback + UI banner** — if every model fails, the pipeline still produces output (placeholder clips) and surfaces a clear "Gemini API unavailable" banner with retry guidance instead of pretending success.

Plus: oversize uploads → friendly Gradio error; missing API key → analyze() raises and falls back; ffmpeg failures → caught per-render-slot and reported in console.

---

## What was built (vs. the brief)

| Brief requirement | Status |
|---|---|
| Web upload form (game name + video ≤500 MB) | ✅ |
| AI vision analysis returning structured JSON (segment, hook, captions, viral score, TikTok caption) | ✅ |
| 2 vertical 9:16 ≤30s variants | ✅ (4 outputs — top 2 events × 2 variants) |
| Variant A: hook top + captions bottom (white text, black border) | ✅ |
| Variant B: same gameplay segment, different editing style, different hook + caption variation | ✅ (different segment AND different style across the 2nd event slot) |
| Results page: in-browser players + downloads + TikTok caption + viral score | ✅ |
| **BONUS** Voiceover generation | ❌ (deferred — `edge-tts` is in requirements but not wired) |
| **BONUS** Parallel processing | ✅ (ThreadPoolExecutor, 4 workers) |
| **BONUS** Loading/progress indicator | ✅ (`gr.Progress` per stage) |
| **BONUS** Graceful error handling | ✅ (retries + model chain + UI banner) |
| **BONUS** Third creative variant | ✅ (the architecture exceeds the brief — instead of one segment with three styling variants, the pipeline detects multiple events and renders the top TWO, each with TWO variants = 4 outputs. The "surprise" is the multi-event ranking + the timeline visualisation.) |

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Web UI | Gradio 6 | Zero-build full UI, video uploads, video players, downloads, progress, all out of the box. |
| Vision LLM | Gemini Flash family (free AI Studio tier) | Vision-capable, structured-JSON output via response_mime_type, free at studio scale. Fallback chain across 4 model variants. |
| Speech-to-text | faster-whisper tiny.en (CPU int8) | Fast on CPU, segment + word-level timestamps for both prompt context and karaoke captions. |
| Video render | ffmpeg via subprocess | Industry standard. libass burn-in for caption styling, eq + colorbalance for per-variant color grading. |
| Caption styling | Custom ASS generator (`pipeline/captions.py`) | Per-style fonts/colors/animations + safe-zone margins for TikTok overlays + phrase chunking from whisper words. |
| Parallelism | `concurrent.futures.ThreadPoolExecutor` | One thread per render job; ffmpeg runs in its own subprocess so OS-level parallelism Just Works. |

---

## Limitations (honest)

- **Free tier model fallback chain can rate-limit hard** during peak global Gemini usage. The pipeline retries + falls through models + shows a clear banner, but if the entire chain is busy, the UI shows the fallback message and asks the user to retry in 30-60 seconds.
- **whisper tiny.en is English-only** and lower-accuracy than larger models. Adequate for prompt context. Swap to `base.en` or `small.en` via `WHISPER_MODEL` env var for more accuracy at the cost of latency.
- **Center-crop reframe** loses content on the screen edges (e.g. Valorant's kill feed top-right gets partially cut). A subject-tracking reframe (YOLOv8 + per-scene crop) was considered but not implemented in the time budget — the existing `Autocrop-vertical` repo provides a clear upgrade path.
- **No voiceover** — the `edge-tts` dependency is installed and ready but the synthesis + audio-mixing path isn't wired. Cleanest add-on.
- **Outcome attribution on broadcast clips** is intentionally `neutral` rather than attempting protagonist tracking, because pro broadcast camera POV switches make tracking unreliable. This was a deliberate design decision (see prompt doc §5.5), not a missing feature.

---

## Next 30 / 60 / 90 days (if shipping at studio scale)

- **30 days:** Wire edge-tts voiceover variant, add subject-tracking reframe (Autocrop-vertical pattern), per-event Gen-Z caption beats (currently only top event has curated beats).
- **60 days:** Game-specific prompt variants loaded by `game_name` (Valorant / League / Apex / Brawl Stars), prompt eval harness with annotated test clips for regression testing prompt changes, sticky preference learning per studio account.
- **90 days:** Studio-scale ops: prompt cache via Anthropic-style cached system prompts (90% cost reduction), batch-mode for back-catalogue processing, per-event parallel rendering on cloud workers, automated A/B testing of caption styles against engagement data from posted clips.

---

## How a non-engineer at Naphora uses this

1. Open http://127.0.0.1:7860 in a browser.
2. Upload a gameplay video.
3. Type the game name. (Optional: the player's IGN if it's personal gameplay.)
4. Click Generate Clips.
5. Wait ~30 seconds.
6. Pick your favourite of the 4 outputs, click the download button below the player, post.

That's the whole loop. No code, no command line, no shell.

---

## Repo layout

```
naphora-clip-pipeline/
├── README.md                    ← you are here
├── requirements.txt
├── .env.example                 ← template for the Gemini API key
├── .gitignore
├── app.py                       ← Gradio entrypoint
├── output clips/                ← the 4 clips the sample run produced (top 2 events × 2 variants); source video on Drive (link in callout above)
├── pipeline/
│   ├── analyze.py               ← Gemini orchestration + validation + fallback chain
│   ├── extract.py               ← adaptive frame extraction (ffmpeg)
│   ├── transcribe.py            ← faster-whisper (segment + word-level)
│   ├── render.py                ← ffmpeg render orchestrator (per-variant grade + ASS burn-in)
│   └── captions.py              ← ASS generator (hook + per-style beats / karaoke)
├── prompts/
│   └── vision_analysis.md       ← THE PROMPT — design doc, rationale, every decision
└── tests/
    ├── test_skeleton.py         ← end-to-end render test (no API call needed)
    └── test_analyze.py          ← real Gemini call test
```
