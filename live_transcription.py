# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Henrique Magalhaes
"""Streaming transcription of live microphone audio.

Microphone audio is split on short silences with the Silero voice-activity
detector that faster-whisper already ships. Completed phrases are transcribed
and committed, while the phrase still being spoken is transcribed with a fast
single-beam pass and shown as an in-progress preview. Audio stays in memory.
"""

from __future__ import annotations

import logging
import queue
import time
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np
from faster_whisper.vad import VadOptions, get_speech_timestamps

from audio_capture import SAMPLE_RATE, CaptureError, MicrophoneStream

PARTIAL_INTERVAL_S = 0.9
MIN_PARTIAL_AUDIO_S = 0.5
MIN_TRANSCRIBE_S = 0.30
MAX_UTTERANCE_S = 9.0
MAX_PARTIAL_S = 8.0
MAX_BACKLOG_FOR_PREVIEW_S = 2.5
COMMIT_TRAILING_S = 0.10
FORCE_SILENCE_DROP_S = 4.0
MIN_LANGUAGE_S = 1.0
PROMPT_CHARS = 240

VAD_OPTIONS = VadOptions(
    threshold=0.45,
    neg_threshold=0.30,
    min_speech_duration_ms=200,
    min_silence_duration_ms=400,
    speech_pad_ms=80,
)

_CJK_RANGES = (
    (0x3040, 0x30FF),  # Hiragana and Katakana
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0x20000, 0x2FA1F),  # CJK Extension B and later
)
_NO_SPACE_BEFORE = set(",.;:!?%)]}，。！？、；：）】》」』…—–")


def _is_cjk(character: str) -> bool:
    code = ord(character)
    return any(start <= code <= end for start, end in _CJK_RANGES)


def join_transcript(previous: str, addition: str) -> str:
    """Join two transcript chunks with spacing that fits the script."""
    left = previous or ""
    right = (addition or "").strip()
    if not right:
        return left
    if not left:
        return right
    if left[-1].isspace():
        return left + right
    if right[0] in _NO_SPACE_BEFORE:
        return left + right
    if left.endswith("-"):
        return left + right
    if _is_cjk(left[-1]) or _is_cjk(right[0]):
        return left + right
    return f"{left} {right}"


def prompt_tail(text: str, limit: int = PROMPT_CHARS) -> str:
    """Return the most recent text to use as Whisper's initial prompt."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    tail = cleaned[-limit:]
    space = tail.find(" ")
    if space != -1 and space < len(tail) - 1:
        tail = tail[space + 1 :]
    return tail


def start_new_take(existing: str) -> str:
    """Prefix a fresh dictation take after any text already in the box."""
    text = (existing or "").rstrip()
    return f"{text}\n" if text else ""


def commit_point(
    audio_length: int,
    segments: Sequence[dict],
    sample_rate: int = SAMPLE_RATE,
    max_utterance_s: float = MAX_UTTERANCE_S,
) -> int | None:
    """Return the sample index to commit, or None while speech continues.

    A segment counts as complete when the voice-activity detector ended it
    before the end of the buffer (it only does that after a short silence).
    """
    if not segments or audio_length <= 0:
        return None
    margin = int(COMMIT_TRAILING_S * sample_rate)
    completed = [
        int(segment["end"])
        for segment in segments
        if int(segment["end"]) <= audio_length - margin
    ]
    if completed:
        return min(audio_length, max(completed))
    if audio_length >= int(max_utterance_s * sample_rate):
        return audio_length
    return None


def quiet_cut_point(
    audio: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    min_cut_s: float = 4.0,
    window_s: float = 2.5,
    frame_s: float = 0.03,
) -> int:
    """Pick a low-energy sample to cut a long, unbroken utterance."""
    total = int(audio.size)
    minimum = int(min_cut_s * sample_rate)
    if total <= minimum:
        return total
    frame = max(1, int(frame_s * sample_rate))
    start = max(minimum, total - int(window_s * sample_rate))
    frames = (total - start) // frame
    if frames <= 0:
        return total
    region = audio[start : start + frames * frame].reshape(frames, frame)
    energies = np.mean(np.square(region, dtype=np.float64), axis=1)
    cut = start + (int(np.argmin(energies)) + 1) * frame
    return min(total, cut)


def speech_bounds(
    segments: Sequence[dict],
    audio_length: int,
    sample_rate: int = SAMPLE_RATE,
    pad_s: float = 0.12,
) -> tuple[int, int]:
    """Trim leading and trailing silence around detected speech."""
    if not segments:
        return 0, audio_length
    pad = int(pad_s * sample_rate)
    start = max(0, int(segments[0]["start"]) - pad)
    end = min(audio_length, int(segments[-1]["end"]) + pad)
    if end <= start:
        return 0, audio_length
    return start, end


@dataclass(frozen=True)
class LiveResult:
    captured_seconds: float
    elapsed: float
    segments: int
    device: str
    language: str | None


class LiveTranscriber:
    """Turn a microphone stream into committed text plus a live preview.

    ``emit(kind, *payload)`` is called from the worker thread with one of:
    ``ready`` (device label, rate, channels), ``language`` (code, probability),
    ``segment`` (committed text), ``partial`` (in-progress text; empty clears),
    and ``done`` (LiveResult).
    """

    def __init__(
        self,
        engine,
        emit: Callable[..., None],
        language: str | None = None,
        device_index: int | None = None,
        vad=get_speech_timestamps,
    ) -> None:
        self.engine = engine
        self.emit = emit
        self.language = language
        self.device_index = device_index
        self._vad = vad
        self.log = logging.getLogger(__name__)
        self._committed = ""
        self._prompt = ""
        self._segment_count = 0
        self._last_partial_at = 0.0
        self._language_reported = False

    def run(self, stop_event) -> LiveResult:
        started = time.monotonic()
        captured_seconds = 0.0
        pending = np.zeros(0, dtype=np.float32)

        with MicrophoneStream(self.device_index) as microphone:
            self.emit(
                "ready",
                microphone.device_label,
                microphone.capture_rate,
                microphone.capture_channels,
            )
            while not stop_event.is_set():
                try:
                    item = microphone.audio_queue.get(timeout=0.08)
                except queue.Empty:
                    continue
                if isinstance(item, BaseException):
                    raise CaptureError(str(item)) from item
                pending = np.concatenate((pending, item))
                captured_seconds += item.size / SAMPLE_RATE
                backlog = microphone.audio_queue.qsize() * int(0.1 * SAMPLE_RATE)
                allow_partial = backlog < int(MAX_BACKLOG_FOR_PREVIEW_S * SAMPLE_RATE)
                pending = self._process(pending, allow_partial=allow_partial)

            # The context manager stopped the microphone; keep any audio that
            # arrived after the last loop iteration, processing it in order so
            # a backlog still produces several phrases instead of one huge one.
            while True:
                try:
                    item = microphone.audio_queue.get_nowait()
                except queue.Empty:
                    break
                if isinstance(item, BaseException):
                    raise CaptureError(str(item)) from item
                pending = np.concatenate((pending, item))
                captured_seconds += item.size / SAMPLE_RATE
                pending = self._process(pending, allow_partial=False)

        self._flush(pending)
        self.emit("partial", "")
        elapsed = time.monotonic() - started
        result = LiveResult(
            captured_seconds=captured_seconds,
            elapsed=elapsed,
            segments=self._segment_count,
            device=microphone.device_label,
            language=self.language,
        )
        return result

    def _process(
        self, pending: np.ndarray, allow_partial: bool = True
    ) -> np.ndarray:
        if pending.size < int(0.2 * SAMPLE_RATE):
            return pending

        segments = self._vad(pending, VAD_OPTIONS, SAMPLE_RATE)
        if not segments:
            if pending.size >= int(FORCE_SILENCE_DROP_S * SAMPLE_RATE):
                # Nothing but silence: keep a little context and free memory.
                return pending[-int(0.3 * SAMPLE_RATE) :]
            return pending

        cut = commit_point(pending.size, segments)
        if cut is not None:
            if cut >= pending.size:
                cut = quiet_cut_point(pending)
            start = max(0, int(segments[0]["start"]) - int(0.08 * SAMPLE_RATE))
            if cut <= start:
                return pending
            if cut - start >= int(MIN_TRANSCRIBE_S * SAMPLE_RATE):
                self._commit(pending[start:cut])
            # Very short blips are dropped rather than fed to the model.
            return pending[cut:]

        now = time.monotonic()
        if (
            allow_partial
            and now - self._last_partial_at >= PARTIAL_INTERVAL_S
            and pending.size >= int(MIN_PARTIAL_AUDIO_S * SAMPLE_RATE)
        ):
            start, end = speech_bounds(segments, pending.size)
            audio = pending[start:end]
            if audio.size > int(MAX_PARTIAL_S * SAMPLE_RATE):
                audio = audio[-int(MAX_PARTIAL_S * SAMPLE_RATE) :]
            if audio.size >= int(MIN_TRANSCRIBE_S * SAMPLE_RATE):
                self._last_partial_at = now
                text = self._transcribe(audio, beam_size=1)
                self.emit("partial", text)
        return pending

    def _commit(self, audio: np.ndarray) -> None:
        text = self._transcribe(audio, beam_size=5)
        if not text:
            self.emit("partial", "")
            return
        self._committed = join_transcript(self._committed, text)
        self._prompt = prompt_tail(self._committed)
        self._segment_count += 1
        self.emit("segment", text)
        self.emit("partial", "")

    def _flush(self, pending: np.ndarray) -> None:
        if pending.size < int(MIN_TRANSCRIBE_S * SAMPLE_RATE):
            return
        segments = self._vad(pending, VAD_OPTIONS, SAMPLE_RATE)
        if not segments:
            return
        start, end = speech_bounds(segments, pending.size)
        audio = pending[start:end]
        if audio.size >= int(MIN_TRANSCRIBE_S * SAMPLE_RATE):
            self._commit(audio)

    def _transcribe(self, audio: np.ndarray, beam_size: int) -> str:
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        segments, info = self.engine.transcribe_array(
            audio,
            language=self.language,
            beam_size=beam_size,
            initial_prompt=self._prompt or None,
        )
        text = ""
        for segment in segments:
            piece = segment.text.strip()
            if piece:
                text = join_transcript(text, piece)
        text = text.strip()
        if (
            self.language is None
            and not self._language_reported
            and audio.size >= int(MIN_LANGUAGE_S * SAMPLE_RATE)
        ):
            self.language = info.language
            self._language_reported = True
            self.emit("language", info.language, float(info.language_probability))
        return text
