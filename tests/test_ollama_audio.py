"""Tests for the local Ollama audio client and engine integration."""

import io
import json
import os
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import numpy as np

import ollama_audio
from whisper_engine import TranscriptionEngine


class _FakeOllamaHandler(BaseHTTPRequestHandler):
    models: list[dict] = []
    response_text = "hello from ollama"
    fail = False
    audio_endpoint = True
    requests: list[tuple[str, int, bytes]] = []

    def log_message(self, *_args) -> None:
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/api/tags":
            self._send(200, {"models": self.models})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.requests.append((self.path, length, body))
        if self.path != "/v1/audio/transcriptions":
            self._send(404, {"error": "not found"})
            return
        if not self.audio_endpoint:
            self._send(404, {"error": "not found"})
            return
        if length == 0:
            self._send(400, {"error": {"message": "empty multipart"}})
            return
        if self.fail:
            self._send(500, {"error": {"message": "model exploded"}})
            return
        self._send(200, {"text": self.response_text})


class OllamaAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllamaHandler)
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _FakeOllamaHandler.models = [
            {"name": "gemma4:e4b", "capabilities": ["completion", "audio"]},
            {"name": "llama3.2:3b", "capabilities": ["completion"]},
        ]
        _FakeOllamaHandler.response_text = "hello from ollama"
        _FakeOllamaHandler.fail = False
        _FakeOllamaHandler.audio_endpoint = True
        _FakeOllamaHandler.requests = []
        self.env = mock.patch.dict(
            os.environ, {"OLLAMA_HOST": f"127.0.0.1:{self.port}"}
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_discovery_selects_audio_model(self):
        discovery = ollama_audio.discover()
        self.assertTrue(discovery.available)
        self.assertEqual(discovery.selected, "gemma4:e4b")
        self.assertIn("llama3.2:3b", discovery.models)

    def test_discovery_without_audio_model(self):
        _FakeOllamaHandler.models = [
            {"name": "llama3.2:3b", "capabilities": ["completion"]}
        ]
        discovery = ollama_audio.discover()
        self.assertTrue(discovery.available)
        self.assertIsNone(discovery.selected)
        self.assertIn("no audio-capable", discovery.error or "")

    def test_discovery_requires_transcription_endpoint(self):
        _FakeOllamaHandler.audio_endpoint = False
        discovery = ollama_audio.discover()
        self.assertIsNone(discovery.selected)
        self.assertIn("does not expose", discovery.error or "")

    def test_remote_host_is_rejected(self):
        with mock.patch.dict(
            os.environ, {"OLLAMA_HOST": "192.0.2.10:11434"}
        ):
            discovery = ollama_audio.discover()
        self.assertFalse(discovery.available)
        self.assertIsNone(discovery.selected)
        self.assertIn("loopback", discovery.error or "")

    def test_wav_encoding(self):
        raw = ollama_audio.audio_to_wav(np.zeros(1600, dtype=np.float32))
        self.assertTrue(raw.startswith(b"RIFF"))
        with wave.open(io.BytesIO(raw), "rb") as handle:
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getsampwidth(), 2)
            self.assertEqual(handle.getframerate(), 16000)
            self.assertEqual(handle.getnframes(), 1600)

    def test_transcribe_posts_audio(self):
        text = ollama_audio.transcribe(
            np.zeros(1600, dtype=np.float32),
            model="gemma4:e4b",
            base_url=self.base_url,
            language="en",
        )
        self.assertEqual(text, "hello from ollama")
        audio_requests = [
            (length, body)
            for path, length, body in _FakeOllamaHandler.requests
            if length > 0
        ]
        self.assertTrue(audio_requests)
        length, body = audio_requests[-1]
        self.assertIn(b'name="model"', body)
        self.assertIn(b"gemma4:e4b", body)
        self.assertIn(b'name="language"', body)
        self.assertIn(b"RIFF", body)

    def test_transcribe_error_is_reported(self):
        _FakeOllamaHandler.fail = True
        with self.assertRaises(ollama_audio.OllamaError):
            ollama_audio.transcribe(
                np.zeros(1600, dtype=np.float32),
                model="gemma4:e4b",
                base_url=self.base_url,
            )

    def test_engine_uses_ollama_for_arrays(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = TranscriptionEngine(models_dir=Path(tmp))
            discovery = engine.refresh_ollama(force=True)
            self.assertEqual(discovery.selected, "gemma4:e4b")
            self.assertTrue(engine.using_ollama)
            self.assertFalse(engine.supports_partial_preview)
            segments, info = engine.transcribe_array(
                np.zeros(1600, dtype=np.float32), language="en"
            )
        self.assertEqual(segments[0].text, "hello from ollama")
        self.assertEqual(info.language, "en")
        self.assertAlmostEqual(info.duration, 0.1)


class OllamaChunkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = TranscriptionEngine(models_dir=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_short_spans_are_grouped(self):
        spans = [
            {"start": 0, "end": 16000},
            {"start": 17000, "end": 33000},
        ]
        with mock.patch(
            "whisper_engine.get_speech_timestamps", return_value=spans
        ):
            chunks = list(self.engine._ollama_chunks(np.zeros(40000, np.float32)))
        self.assertEqual(len(chunks), 1)
        start, end, audio = chunks[0]
        self.assertEqual((start, end), (0, 33000))
        self.assertEqual(audio.size, 32000)

    def test_groups_are_capped(self):
        spans = [
            {"start": 0, "end": 160000},
            {"start": 170000, "end": 330000},
            {"start": 340000, "end": 500000},
        ]
        with mock.patch(
            "whisper_engine.get_speech_timestamps", return_value=spans
        ):
            chunks = list(self.engine._ollama_chunks(np.zeros(600000, np.float32)))
        self.assertEqual(len(chunks), 2)
        self.assertLessEqual(chunks[0][2].size, 25 * 16000)
        self.assertLessEqual(chunks[1][2].size, 25 * 16000)

    def test_single_long_span_is_split(self):
        spans = [{"start": 0, "end": 60 * 16000}]
        with mock.patch(
            "whisper_engine.get_speech_timestamps", return_value=spans
        ):
            chunks = list(
                self.engine._ollama_chunks(np.zeros(60 * 16000, np.float32))
            )
        self.assertEqual(len(chunks), 3)
        self.assertEqual(
            [chunk[2].size for chunk in chunks], [25 * 16000, 25 * 16000, 10 * 16000]
        )

    def test_no_speech_produces_no_chunks(self):
        with mock.patch(
            "whisper_engine.get_speech_timestamps", return_value=[]
        ):
            chunks = list(self.engine._ollama_chunks(np.zeros(16000, np.float32)))
        self.assertEqual(chunks, [])

    def test_vad_failure_uses_fixed_windows(self):
        with mock.patch(
            "whisper_engine.get_speech_timestamps",
            side_effect=RuntimeError("vad failed"),
        ):
            chunks = list(
                self.engine._ollama_chunks(np.zeros(60 * 16000, np.float32))
            )
        self.assertEqual(len(chunks), 3)


if __name__ == "__main__":
    unittest.main()
