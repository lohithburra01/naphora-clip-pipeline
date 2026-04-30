"""ASS subtitle generation for hook + captions.

Two caption modes (chosen per variant):

  - "beats"  — Gen-Z phrase captions on Gemini's curated timeline (Variant A).
                Whole short phrases pop on/off, classic TikTok style with
                white text + black outline + drop shadow.

  - "karaoke" — Per-word reveal aligned to whisper word timestamps (Variant B).
                One word at a time pops onto screen as it's spoken in the
                commentary. Bigger yellow text. Hormozi / Submagic-style. The
                2026 default for word-level TikTok captions.

Hook is always at the top (its own ASS Style), visible whole segment with a
fade-in. Hook text differs per variant ("hook_a" vs "hook_b").

Safe-zone respect: hook MarginV=240 from top (clear of TikTok app bar), beats
MarginV=450 from bottom, karaoke MarginV=560 from bottom (slightly higher so
the bigger text doesn't clip into TikTok's caption/audio bar).

ASS color format is &HAABBGGRR. Time format H:MM:SS.cc.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable


# -----------------------------------------------------------------------------
# Style table (per-variant)
# -----------------------------------------------------------------------------

STYLE_TABLE: dict[str, dict] = {
    "A": {
        "mode": "beats",
        "hook_font": "Arial Black",
        "hook_size": 78,
        "hook_primary": "&H00FFFFFF",
        "hook_outline": "&H00000000",
        "hook_outline_w": 6,
        "caption_font": "Arial Black",
        "caption_size": 70,
        "caption_primary": "&H00FFFFFF",
        "caption_outline": "&H00000000",
        "caption_outline_w": 5,
        "caption_margin_v": 450,
    },
    "B": {
        "mode": "karaoke",
        "hook_font": "Impact",
        "hook_size": 92,
        "hook_primary": "&H0000F5FF",   # vivid yellow (BBGGRR -> 00 F5 FF -> ~yellow-orange)
        "hook_outline": "&H00000000",
        "hook_outline_w": 8,
        "caption_font": "Impact",
        "caption_size": 110,
        "caption_primary": "&H0000F5FF",
        "caption_outline": "&H00000000",
        "caption_outline_w": 8,
        "caption_margin_v": 560,
    },
}


# -----------------------------------------------------------------------------
# ASS header builder
# -----------------------------------------------------------------------------

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,{hook_font},{hook_size},{hook_primary},&H00000000,{hook_outline},&HAA000000,1,0,0,0,100,100,0,0,1,{hook_outline_w},4,8,80,80,240,1
Style: Caption,{caption_font},{caption_size},{caption_primary},&H00000000,{caption_outline},&HAA000000,1,0,0,0,100,100,0,0,1,{caption_outline_w},4,2,80,80,{caption_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _chunk_to_phrases(
    words: list,
    *,
    max_words: int = 3,
    max_duration: float = 1.6,
    min_gap: float = 0.35,
) -> list[dict]:
    """Group word-objects (with .text/.start/.end OR dict keys) into 2-3 word phrases."""
    if not words:
        return []

    def _w_attr(obj, attr: str, default=None):
        if hasattr(obj, attr):
            return getattr(obj, attr)
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return default

    phrases: list[dict] = []
    current: list = []

    for w in words:
        text = str(_w_attr(w, "text", "")).strip()
        if not text:
            continue
        if not current:
            current.append(w)
            continue
        last = current[-1]
        gap = float(_w_attr(w, "start", 0.0)) - float(_w_attr(last, "end", 0.0))
        proposed_dur = float(_w_attr(w, "end", 0.0)) - float(_w_attr(current[0], "start", 0.0))
        if (
            len(current) >= max_words
            or proposed_dur > max_duration
            or gap > min_gap
        ):
            phrases.append({
                "text": " ".join(str(_w_attr(x, "text", "")).strip() for x in current),
                "start": float(_w_attr(current[0], "start", 0.0)),
                "end": float(_w_attr(current[-1], "end", 0.0)),
            })
            current = [w]
        else:
            current.append(w)

    if current:
        phrases.append({
            "text": " ".join(str(_w_attr(x, "text", "")).strip() for x in current),
            "start": float(_w_attr(current[0], "start", 0.0)),
            "end": float(_w_attr(current[-1], "end", 0.0)),
        })
    return phrases


def _ts(seconds: float) -> str:
    """Format seconds as ASS H:MM:SS.cc."""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape_ass_text(text: str) -> str:
    """Escape characters that have meaning in ASS dialogue text."""
    return (
        text.replace("\\", "\\\\")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", "\\N")
    )


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def write_ass(
    *,
    hook: str,
    captions: Iterable[dict] | None,
    words: list | None,
    segment_duration: float,
    output_path: Path,
    style_key: str = "A",
) -> Path:
    """Write a TikTok-styled ASS file for one variant.

    captions: list of {"text", "start", "end"} for beats mode (Variant A).
    words:    list of TranscriptWord-like (text, start, end) for karaoke mode
              (Variant B). Timestamps in seconds RELATIVE to segment start.
    segment_duration: total output clip length (anchors hook end time).
    """
    cfg = STYLE_TABLE.get(style_key, STYLE_TABLE["A"])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    header = ASS_HEADER.format(**cfg)
    events: list[str] = []

    # Hook layer — drops in from above, settles, holds. Style-specific entrance.
    hook_text = _escape_ass_text(hook.strip())
    if hook_text:
        if style_key == "B":
            # Variant B: subtle slow scale-up + fade for cinematic feel
            hook_effect = r"{\fad(380,250)\fscx118\fscy118\t(0,500,\fscx100\fscy100)}"
        else:
            # Variant A: dramatic drop from above with overshoot bounce
            hook_effect = (
                r"{\an8\move(540,-180,540,240,0,520)"
                r"\fad(0,200)"
                r"\fscx108\fscy108\t(420,640,\fscx100\fscy100)}"
            )
        events.append(
            f"Dialogue: 0,{_ts(0.0)},{_ts(segment_duration)},Hook,,0,0,0,,{hook_effect}{hook_text}"
        )

    # Caption layer — karaoke if mode says so AND words supplied;
    # otherwise fall through to beats (works for Variant A always, and
    # Variant B when commentary audio is missing or unintelligible).
    use_karaoke = cfg["mode"] == "karaoke" and bool(words)

    # Beats path: render Gemini's curated phrases. If we don't have Gemini
    # beats but DO have whisper words (e.g. secondary event), auto-derive
    # phrase chunks from the words so the beats layer is never empty.
    beats_source = list(captions) if captions else []
    if not use_karaoke and not beats_source and words:
        beats_source = _chunk_to_phrases(words, max_words=3, max_duration=1.6, min_gap=0.35)

    if not use_karaoke and beats_source:
        for cap in beats_source:
            text = str(cap.get("text", "")).strip()
            if not text:
                continue
            start = float(cap.get("start", 0.0))
            end = float(cap.get("end", start + 2.0))
            if end <= start:
                continue
            end = min(end, segment_duration)
            text_safe = _escape_ass_text(text)
            effect = r"{\fad(120,100)\fscx108\fscy108\t(0,180,\fscx100\fscy100)}"
            events.append(
                f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{effect}{text_safe}"
            )

    elif use_karaoke:
        # Group consecutive words into 2-3 word phrase chunks (CapCut phrase
        # mode). Each phrase pops on as the first word is spoken, holds
        # through the chunk, then transitions to the next.
        phrases = _chunk_to_phrases(words, max_words=3, max_duration=1.6, min_gap=0.35)
        for ph in phrases:
            text = str(ph["text"]).strip()
            if not text:
                continue
            ps = float(ph["start"])
            pe = float(ph["end"])
            if ps >= segment_duration:
                continue
            display_end = min(segment_duration, max(pe, ps + 0.55))
            if display_end <= ps:
                continue
            text_safe = _escape_ass_text(text.upper())
            # Phrase pop: snappier than beats, scale punch
            effect = r"{\fad(70,90)\fscx118\fscy118\t(0,150,\fscx100\fscy100)}"
            events.append(
                f"Dialogue: 0,{_ts(ps)},{_ts(display_end)},Caption,,0,0,0,,{effect}{text_safe}"
            )

    output_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return output_path
