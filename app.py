"""Naphora Clip Pipeline - Gradio entrypoint.

End-to-end autonomous pipeline that takes a long gameplay/broadcast video and:
  1. Detects ALL cut-able events (kill, multi-kill, ace, clutch, round_win)
     and ranks them by weight.
  2. Renders the TOP 2 events as separate output segments.
  3. Each segment renders in two visually distinct variants:
       - Variant A: hype style (vivid color, Gen-Z phrase beats, drop-in hook)
       - Variant B: cinematic style (cool grade, letterbox bars, karaoke
                   captions chunked into 2-3 word phrases from commentary)
  4. Surfaces the full ranked event list with a timeline visualisation so
     reviewers can see WHY the pipeline picked what it did.

Run:
    python app.py
Then open http://127.0.0.1:7860
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import gradio as gr
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from dotenv import load_dotenv

from pipeline.analyze import analyze
from pipeline.render import render_variant
from pipeline.transcribe import transcribe_words, TranscriptWord

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent
RUNS_DIR = PROJECT_ROOT / "runs"
RUNS_DIR.mkdir(exist_ok=True)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _words_in_window(
    all_words: list[TranscriptWord],
    start_sec: float,
    end_sec: float,
) -> list[TranscriptWord]:
    """Filter whisper words to those overlapping [start_sec, end_sec],
    rebased so timestamps are RELATIVE to start_sec (0.0 = first frame of output)."""
    window: list[TranscriptWord] = []
    for w in all_words:
        if w.end <= start_sec:
            continue
        if w.start >= end_sec:
            break
        ws = max(0.0, w.start - start_sec)
        we = min(end_sec - start_sec, w.end - start_sec)
        if we > ws:
            window.append(TranscriptWord(text=w.text, start=ws, end=we))
    return window


_TYPE_COLORS = {
    "clutch":     "#FF3B6E",  # hot pink — top tier
    "ace":        "#FF7A00",  # orange — top tier
    "round_win":  "#FFD600",  # yellow
    "multi_kill": "#00C2FF",  # cyan
    "kill":       "#7A8DFF",  # soft blue
    "highlight":  "#888888",  # gray
}


def make_timeline_plot(events: list[dict], total_duration: float):
    """Horizontal bar plot of events on a timeline. Each event = bar at its
    peak_time, height = weight, color = event_type. Top 3 are labelled."""
    fig, ax = plt.subplots(figsize=(12, 2.6))
    fig.patch.set_facecolor("#0e1116")
    ax.set_facecolor("#0e1116")
    ax.tick_params(colors="#cccccc")
    for spine_name in ("top", "right"):
        ax.spines[spine_name].set_color("#444")
    for spine_name in ("bottom", "left"):
        ax.spines[spine_name].set_color("#888")

    # baseline
    ax.axhline(y=0, color="#333", linewidth=1, zorder=1)

    if not events:
        ax.text(
            total_duration / 2 if total_duration > 0 else 0.5,
            50,
            "No events detected",
            ha="center", va="center", color="#888", fontsize=12,
        )
    else:
        for i, ev in enumerate(events):
            t = float(ev.get("peak_time_sec", 0.0))
            w = float(ev.get("weight", 0))
            color = _TYPE_COLORS.get(ev.get("event_type", "highlight"), "#888")
            ax.bar(t, w, width=max(1.5, total_duration / 60.0),
                   color=color, edgecolor="white", linewidth=0.6, zorder=2)
            if i < 3:
                ax.text(
                    t, w + 4,
                    f'{ev.get("event_type", "?").upper()}\nw={int(w)}',
                    ha="center", va="bottom",
                    color="white", fontsize=9, fontweight="bold",
                    zorder=3,
                )

    ax.set_xlim(0, max(1.0, total_duration))
    ax.set_ylim(0, 115)
    ax.set_xlabel("Time in original clip (s)", color="#cccccc", fontsize=10)
    ax.set_ylabel("Event weight", color="#cccccc", fontsize=10)
    ax.set_title("Detected events ranked by importance", color="white",
                 fontweight="bold", fontsize=12, pad=10)
    fig.tight_layout()
    return fig


def events_to_table(events: list[dict]) -> list[list]:
    """Format events array as a 2D table for gr.Dataframe."""
    rows = []
    for i, ev in enumerate(events):
        rows.append([
            i + 1,
            ev.get("event_type", "?").title(),
            int(ev.get("weight", 0)),
            f"{ev.get('peak_time_sec', 0.0):.1f}s",
            f"{ev.get('start_sec', 0.0):.1f}s → {ev.get('end_sec', 0.0):.1f}s",
            ev.get("description", "")[:80],
        ])
    return rows


def _empty_video_placeholder(run_dir: Path) -> str:
    """Create an empty/placeholder mp4 path so Gradio can show 'no video'.
    Just returns an empty string — Gradio handles None values."""
    return None


# -----------------------------------------------------------------------------
# Pipeline orchestrator
# -----------------------------------------------------------------------------

def process(
    video_file: str | None,
    game_name: str,
    player_ign: str,
    progress=gr.Progress(),
):
    """Run the pipeline: analyze → render top 2 events × 2 variants = 4 clips."""
    if video_file is None:
        raise gr.Error("Please upload a gameplay video.")
    if not game_name or not game_name.strip():
        raise gr.Error("Please enter the game name.")

    run_id = uuid.uuid4().hex[:8]
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    progress(0.05, desc="Analyzing frames + transcribing audio with Gemini + Whisper...")
    analysis, source = analyze(
        video_path=video_file,
        game_name=game_name.strip(),
        player_ign=player_ign.strip() if player_ign else "",
        work_dir=run_dir,
    )

    progress(0.40, desc="Extracting word-level timestamps for karaoke...")
    all_words = transcribe_words(video_file)

    events = analysis.get("events", []) or []
    # Pick the top two events. If only one was detected, second slot stays None.
    top_events: list[dict] = events[:2]
    # Fallback: ensure top event matches the analysis-level start/end
    if not top_events:
        top_events = [{
            "event_type": "highlight",
            "peak_frame_index": analysis["peak_frame_index"],
            "start_frame_index": analysis["highlight_start_frame"],
            "peak_time_sec": float(analysis["peak_frame_index"]),
            "start_sec": float(analysis["start_sec"]),
            "end_sec": float(analysis["end_sec"]),
            "description": analysis.get("event_description", ""),
            "weight": int(analysis.get("viral_score", 50)),
        }]

    # Build a duration estimate from the events (the latest end_sec) for the
    # timeline plot. Fallback to analysis end_sec if events array is empty.
    total_duration = max(
        analysis.get("end_sec", 0.0),
        max((e.get("end_sec", 0.0) for e in events), default=0.0),
    )

    rendered_paths: list[str | None] = [None, None, None, None]
    progress_increments = [0.55, 0.70, 0.82, 0.94]

    for ev_idx, event in enumerate(top_events):
        # Defensive: derive start/end from peak_frame_index if event dict is
        # missing them (some fallback paths used to omit these keys).
        if "start_sec" in event and "end_sec" in event:
            seg_start = float(event["start_sec"])
            seg_end = float(event["end_sec"])
        else:
            seg_start = float(analysis.get("start_sec", 0.0))
            seg_end = float(analysis.get("end_sec", 30.0))
        window_words = _words_in_window(all_words, seg_start, seg_end)

        for var_idx, style in enumerate(["A", "B"]):
            slot = ev_idx * 2 + var_idx
            out_path = run_dir / f"event{ev_idx+1}_variant_{style.lower()}.mp4"

            progress(
                progress_increments[slot],
                desc=f"Rendering Event {ev_idx+1} · Variant {style}...",
            )

            # Per-event hooks (Gemini gives unique hook_a / hook_b for each
            # event in the events array; fall back to top-level if missing).
            event_hook_a = event.get("hook_a") or analysis["hook_a"]
            event_hook_b = event.get("hook_b") or analysis["hook_b"]

            if style == "A" and ev_idx == 0:
                # Top event Variant A uses Gemini's curated beats (hype phrases)
                hook = event_hook_a
                caps = analysis["captions"]
                wds = None
            elif style == "A":
                # Secondary events Variant A: phrase-chunked karaoke
                # (we don't have per-event Gen-Z beats yet — use commentary)
                hook = event_hook_a
                caps = None
                wds = window_words if window_words else None
                if not wds:
                    caps = analysis["captions"]
            else:
                # All Variant B: karaoke phrases from commentary
                hook = event_hook_b
                caps = analysis["captions"] if not window_words else None
                wds = window_words if window_words else None

            try:
                render_variant(
                    input_path=video_file,
                    segment=(seg_start, seg_end),
                    hook=hook,
                    captions=caps,
                    words=wds,
                    output_path=str(out_path),
                    style=style,
                )
                rendered_paths[slot] = str(out_path)
            except Exception as e:
                print(f"[render] event {ev_idx+1} variant {style} failed: {e}")
                rendered_paths[slot] = None

    progress(1.0, desc="Done")

    # Build UI artifacts
    timeline_fig = make_timeline_plot(events, total_duration)
    table_rows = events_to_table(events)

    # Top header summary
    top_event = top_events[0]
    second_event = top_events[1] if len(top_events) > 1 else None
    if source == "gemini":
        score_md = (
            f"## ✅ Pipeline output (Gemini)  \n"
            f"**Events detected:** {len(events)}  \n"
            f"**Top event:** {top_event.get('event_type', '?').upper()} "
            f"(weight {int(top_event.get('weight', 0))}) at "
            f"{top_event.get('peak_time_sec', 0.0):.1f}s — {top_event.get('description', '')}  \n"
        )
        if second_event:
            score_md += (
                f"**2nd event:** {second_event.get('event_type', '?').upper()} "
                f"(weight {int(second_event.get('weight', 0))}) at "
                f"{second_event.get('peak_time_sec', 0.0):.1f}s — {second_event.get('description', '')}  \n"
            )
        else:
            score_md += "**2nd event:** _(none — only one event detected)_  \n"
        score_md += (
            f"\n**Hook A (hype):** {analysis['hook_a']}  \n"
            f"**Hook B (cinematic):** {analysis['hook_b']}  \n"
            f"**Viral score:** {analysis['viral_score']}/100  \n"
            f"_{analysis.get('viral_reasoning', '')}_"
        )
    else:
        score_md = (
            "## ⚠️ Gemini API unavailable — using safe fallback  \n"
            "All Gemini models in the fallback chain returned errors (typically 503 / quota / timeout). "
            "The pipeline rendered placeholder clips so the run doesn't crash, but the analysis "
            "is hardcoded — not AI-driven. **Click Generate again in 30-60 seconds** — Gemini's "
            "transient overload usually clears in under a minute.  \n\n"
            f"**Fallback event:** {analysis.get('event_description', '')}  \n"
            f"**Viral score:** {analysis['viral_score']}/100 (placeholder)  \n"
        )

    return (
        timeline_fig,                      # 0  timeline plot
        table_rows,                        # 1  events table
        rendered_paths[0],                 # 2  event 1 variant A
        rendered_paths[1],                 # 3  event 1 variant B
        rendered_paths[2],                 # 4  event 2 variant A
        rendered_paths[3],                 # 5  event 2 variant B
        analysis["tiktok_caption"],        # 6  caption
        score_md,                          # 7  summary md
        json.dumps(analysis, indent=2),    # 8  raw json
    )


# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------

with gr.Blocks(title="Naphora Clip Pipeline", css="""
.gradio-container .video-grid > div { padding: 6px; }
""") as demo:
    gr.Markdown(
        "# Naphora Clip Pipeline\n"
        "**Long input → ranked highlight events → top 2 events × 2 variants = 4 TikTok-ready clips.**  \n"
        "Multi-modal: Gemini Flash (vision) + faster-whisper (commentary) + ffmpeg (rendering)."
    )
    with gr.Row():
        with gr.Column(scale=1):
            video_in = gr.Video(label="Gameplay Video (max 500 MB)", sources=["upload"])
            game_in = gr.Textbox(label="Game Name", placeholder="e.g., Valorant", value="")
            ign_in = gr.Textbox(
                label="Player IGN (optional, personal gameplay)",
                placeholder="In-game name to feature in captions",
                value="",
            )
            submit = gr.Button("Generate Clips", variant="primary", size="lg")
        with gr.Column(scale=2):
            score_md = gr.Markdown()

    gr.Markdown("### Detected events (ranked)")
    timeline_plot = gr.Plot(label="Timeline · weight = importance · color = event type")
    events_df = gr.Dataframe(
        headers=["#", "Type", "Weight", "Peak", "Segment", "Description"],
        datatype=["number", "str", "number", "str", "str", "str"],
        label="All detected events",
        interactive=False,
        wrap=True,
    )

    gr.Markdown("### Output clips · top 2 events × 2 variants")
    with gr.Row(equal_height=True):
        with gr.Column():
            gr.Markdown("**Event 1 · Variant A — hype**")
            video_e1_a = gr.Video(label="Event 1 / A", interactive=False)
        with gr.Column():
            gr.Markdown("**Event 1 · Variant B — cinematic**")
            video_e1_b = gr.Video(label="Event 1 / B", interactive=False)
    with gr.Row(equal_height=True):
        with gr.Column():
            gr.Markdown("**Event 2 · Variant A — hype**")
            video_e2_a = gr.Video(label="Event 2 / A", interactive=False)
        with gr.Column():
            gr.Markdown("**Event 2 · Variant B — cinematic**")
            video_e2_b = gr.Video(label="Event 2 / B", interactive=False)

    caption_box = gr.Textbox(label="TikTok Caption (copy-paste ready)", lines=3)
    with gr.Accordion("Raw analysis JSON", open=False):
        json_out = gr.Code(language="json", label="analysis")

    submit.click(
        process,
        inputs=[video_in, game_in, ign_in],
        outputs=[
            timeline_plot,
            events_df,
            video_e1_a, video_e1_b,
            video_e2_a, video_e2_b,
            caption_box,
            score_md,
            json_out,
        ],
    )

if __name__ == "__main__":
    demo.queue().launch(
        server_name="127.0.0.1",
        server_port=7860,
        show_error=True,
        max_file_size="500mb",
        theme=gr.themes.Soft(),
    )
