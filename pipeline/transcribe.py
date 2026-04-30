"""Audio transcription via faster-whisper.

Two outputs:
- transcribe(): segment-level transcript with phrase timestamps. Used as a
  commentary track for Gemini's analysis (broad event recognition).
- transcribe_words(): word-level transcript with per-word timestamps. Used by
  Variant B's karaoke caption style (one word visible at a time, popping on
  as it's spoken — the 2026 TikTok default per Hormozi/Submagic style).

For broadcast and streamer clips the spoken commentary is the strongest
autonomous signal for both EVENT (what kind of moment is this?) and TIMING
(when exactly does it happen?). For silent gameplay both functions return
empty lists and the pipeline falls back to vision-only beat captions.

Model: faster-whisper tiny.en by default (fast, English-only, ~75 MB).
Override via WHISPER_MODEL env var. Uses int8 CPU compute by default.
"""
from __future__ import annotations

import os
from typing import NamedTuple

from faster_whisper import WhisperModel


class TranscriptSegment(NamedTuple):
    text: str
    start: float
    end: float


class TranscriptWord(NamedTuple):
    text: str
    start: float
    end: float


WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL", "tiny.en")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")

_model_cache: WhisperModel | None = None


def _get_model() -> WhisperModel:
    """Lazy-load the whisper model. First call may download ~75 MB."""
    global _model_cache
    if _model_cache is None:
        _model_cache = WhisperModel(
            WHISPER_MODEL_SIZE,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE,
        )
    return _model_cache


def transcribe(video_path: str) -> list[TranscriptSegment]:
    """Segment-level transcript. Returns [] for silent or unreadable audio."""
    try:
        model = _get_model()
        segments_iter, _info = model.transcribe(
            video_path,
            beam_size=1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        return [
            TranscriptSegment(
                text=s.text.strip(),
                start=float(s.start),
                end=float(s.end),
            )
            for s in segments_iter
            if s.text and s.text.strip()
        ]
    except Exception as e:
        print(f"[transcribe] failed: {type(e).__name__}: {e}")
        return []


def transcribe_words(video_path: str) -> list[TranscriptWord]:
    """Word-level transcript with per-word timestamps. Returns [] on failure.

    Used for karaoke-style captions (Variant B) where each word pops onto
    screen synced to when it's spoken.
    """
    try:
        model = _get_model()
        segments_iter, _info = model.transcribe(
            video_path,
            beam_size=1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            word_timestamps=True,
        )
        words: list[TranscriptWord] = []
        for seg in segments_iter:
            for w in seg.words or []:
                txt = (w.word or "").strip()
                if not txt:
                    continue
                words.append(TranscriptWord(
                    text=txt,
                    start=float(w.start),
                    end=float(w.end),
                ))
        return words
    except Exception as e:
        print(f"[transcribe_words] failed: {type(e).__name__}: {e}")
        return []


def chunk_words_to_phrases(
    words: list[TranscriptWord],
    *,
    max_words: int = 3,
    max_duration: float = 1.6,
    min_gap: float = 0.35,
) -> list[dict]:
    """Group consecutive whisper words into 2-3 word phrase chunks.

    CapCut "phrase mode" style: each chunk shows ~1-1.6 seconds, breaks on
    long pauses or word-count limits. Returns a list of {"text", "start", "end"}
    dicts compatible with the beats caption renderer.

    A new phrase starts when:
      - current chunk reaches max_words, OR
      - current chunk's duration would exceed max_duration, OR
      - the gap between this word and the previous word exceeds min_gap.
    """
    if not words:
        return []

    phrases: list[dict] = []
    current: list[TranscriptWord] = []

    for w in words:
        if not current:
            current.append(w)
            continue
        last = current[-1]
        gap = w.start - last.end
        proposed_dur = w.end - current[0].start
        if (
            len(current) >= max_words
            or proposed_dur > max_duration
            or gap > min_gap
        ):
            phrases.append({
                "text": " ".join(x.text for x in current),
                "start": current[0].start,
                "end": current[-1].end,
            })
            current = [w]
        else:
            current.append(w)

    if current:
        phrases.append({
            "text": " ".join(x.text for x in current),
            "start": current[0].start,
            "end": current[-1].end,
        })
    return phrases


def transcript_to_text_block(
    segments: list[TranscriptSegment],
    max_chars: int = 4000,
) -> str:
    """Format segment-level transcript as timestamped commentary block.

    Used inside Gemini's analysis prompt as the audio context.
    """
    if not segments:
        return ""
    lines = [f"[{s.start:.1f}s] {s.text}" for s in segments]
    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[:max_chars] + "\n... [transcript truncated]"
    return block
