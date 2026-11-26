"""Unit tests for live-transcription text joining and chunking decisions."""

import unittest
from types import SimpleNamespace

import numpy as np

from live_transcription import (
    SAMPLE_RATE,
    LiveTranscriber,
    commit_point,
    join_transcript,
    prompt_tail,
    quiet_cut_point,
    speech_bounds,
)


class JoinTranscriptTests(unittest.TestCase):
    def test_latin_words_get_one_space(self):
        self.assertEqual(join_transcript("Hello", "world"), "Hello world")

    def test_existing_whitespace_is_not_doubled(self):
        self.assertEqual(join_transcript("Hello ", "world"), "Hello world")

    def test_punctuation_is_not_separated(self):
        self.assertEqual(join_transcript("Hello", ","), "Hello,")

    def test_sentence_keeps_space(self):
        self.assertEqual(join_transcript("Hello.", "World"), "Hello. World")

    def test_cjk_is_not_separated(self):
        self.assertEqual(join_transcript("你好", "世界"), "你好世界")

    def test_hyphen_is_not_separated(self):
        self.assertEqual(join_transcript("well-", "known"), "well-known")

    def test_empty_addition_keeps_previous(self):
        self.assertEqual(join_transcript("Hello", "  "), "Hello")

    def test_empty_previous_returns_addition(self):
        self.assertEqual(join_transcript("", "Hello"), "Hello")


class PromptTests(unittest.TestCase):
    def test_short_text_is_unchanged(self):
        self.assertEqual(prompt_tail("Hello there."), "Hello there.")

    def test_tail_starts_on_a_word_boundary(self):
        text = "alpha " * 100 + "omega"
        tail = prompt_tail(text, limit=20)
        self.assertLessEqual(len(tail), 20)
        self.assertTrue(tail.startswith(("alpha", "omega")))


class CommitPointTests(unittest.TestCase):
    def test_ongoing_speech_is_not_committed(self):
        segments = [{"start": 0, "end": SAMPLE_RATE * 2}]
        self.assertIsNone(commit_point(SAMPLE_RATE * 2, segments))

    def test_completed_segment_with_trailing_silence_is_committed(self):
        segments = [{"start": 0, "end": int(SAMPLE_RATE * 1.5)}]
        self.assertEqual(
            commit_point(int(SAMPLE_RATE * 2.0), segments),
            int(SAMPLE_RATE * 1.5),
        )

    def test_long_utterance_is_forced(self):
        segments = [{"start": 0, "end": int(SAMPLE_RATE * 10)}]
        self.assertEqual(
            commit_point(int(SAMPLE_RATE * 10), segments),
            int(SAMPLE_RATE * 10),
        )

    def test_no_speech_has_no_commit(self):
        self.assertIsNone(commit_point(SAMPLE_RATE, []))


class QuietCutTests(unittest.TestCase):
    def test_cut_lands_in_the_quiet_tail(self):
        audio = np.full(int(SAMPLE_RATE * 9), 0.5, dtype=np.float32)
        audio[int(SAMPLE_RATE * 7.5) :] = 0.0005
        cut = quiet_cut_point(audio)
        self.assertGreaterEqual(cut, int(SAMPLE_RATE * 7.4))
        self.assertLessEqual(cut, audio.size)

    def test_short_audio_returns_its_length(self):
        audio = np.zeros(int(SAMPLE_RATE * 2), dtype=np.float32)
        self.assertEqual(quiet_cut_point(audio), audio.size)


class SpeechBoundsTests(unittest.TestCase):
    def test_bounds_and_padding(self):
        segments = [{"start": 1000, "end": 5000}]
        start, end = speech_bounds(segments, 10000)
        self.assertEqual(start, 0)
        self.assertGreater(end, 5000)
        self.assertLessEqual(end, 10000)


class FakeEngine:
    def __init__(self, text="hello", language="en"):
        self.text = text
        self.language = language
        self.calls = []

    def transcribe_array(self, audio, language=None, beam_size=5, initial_prompt=None):
        self.calls.append(
            {
                "samples": int(audio.size),
                "beam_size": beam_size,
                "language": language,
                "prompt": initial_prompt,
            }
        )
        return (
            [SimpleNamespace(text=self.text)],
            SimpleNamespace(language=self.language, language_probability=0.92),
        )


class ProcessTests(unittest.TestCase):
    def _transcriber(self, vad, engine=None):
        events = []
        transcriber = LiveTranscriber(
            engine or FakeEngine(),
            lambda kind, *payload: events.append((kind, *payload)),
            vad=vad,
        )
        return transcriber, events

    def test_completed_phrase_is_committed_and_audio_dropped(self):
        vad = lambda audio, options, rate: [{"start": 0, "end": int(rate * 0.8)}]
        transcriber, events = self._transcriber(vad)
        pending = np.zeros(int(SAMPLE_RATE * 1.5), dtype=np.float32)
        remaining = transcriber._process(pending)
        self.assertEqual(remaining.size, pending.size - int(SAMPLE_RATE * 0.8))
        self.assertIn(("segment", "hello"), events)
        self.assertIn(("partial", ""), events)
        self.assertEqual(transcriber._segment_count, 1)

    def test_ongoing_speech_produces_a_preview(self):
        vad = lambda audio, options, rate: [{"start": 0, "end": audio.size}]
        transcriber, events = self._transcriber(vad)
        pending = np.zeros(int(SAMPLE_RATE * 1.5), dtype=np.float32)
        remaining = transcriber._process(pending)
        self.assertEqual(remaining.size, pending.size)
        self.assertIn(("partial", "hello"), events)
        self.assertIn(("language", "en", 0.92), events)

    def test_silence_is_kept_until_the_drop_threshold(self):
        vad = lambda audio, options, rate: []
        transcriber, events = self._transcriber(vad)
        pending = np.zeros(int(SAMPLE_RATE * 2), dtype=np.float32)
        self.assertEqual(transcriber._process(pending).size, pending.size)
        self.assertEqual(events, [])

    def test_backlog_skips_the_preview_to_catch_up(self):
        vad = lambda audio, options, rate: [{"start": 0, "end": audio.size}]
        transcriber, events = self._transcriber(vad)
        pending = np.zeros(int(SAMPLE_RATE * 1.5), dtype=np.float32)
        transcriber._process(pending, allow_partial=False)
        self.assertFalse(any(event[0] == "partial" for event in events))

    def test_tiny_blip_is_dropped_without_transcribing(self):
        vad = lambda audio, options, rate: [{"start": 0, "end": int(rate * 0.1)}]
        engine = FakeEngine()
        transcriber, events = self._transcriber(vad, engine)
        pending = np.zeros(int(SAMPLE_RATE * 1.0), dtype=np.float32)
        remaining = transcriber._process(pending)
        self.assertEqual(remaining.size, pending.size - int(SAMPLE_RATE * 0.1))
        self.assertEqual(engine.calls, [])
        self.assertNotIn("segment", [event[0] for event in events])

    def test_flush_commits_the_remainder(self):
        vad = lambda audio, options, rate: [{"start": 0, "end": audio.size}]
        transcriber, events = self._transcriber(vad)
        transcriber._flush(np.zeros(int(SAMPLE_RATE * 1), dtype=np.float32))
        self.assertIn(("segment", "hello"), events)


if __name__ == "__main__":
    unittest.main()
