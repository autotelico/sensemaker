"""Security regression tests for log redaction and local-only media decoding."""

import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from secure_store import redact_text
from whisper_engine import AudioFileError, _decode_audio

ROOT = Path(__file__).resolve().parents[1]


class RedactionTests(unittest.TestCase):
    def test_windows_path_keeps_filename(self):
        self.assertEqual(
            redact_text(r"C:\Users\henri\Documents\Clients\Acme\hearing.wav"),
            r"[path]\hearing.wav",
        )

    def test_non_media_path_is_redacted(self):
        self.assertEqual(
            redact_text(r"C:\Users\henri\Documents\taxes_2025.pdf"),
            r"[path]\taxes_2025.pdf",
        )

    def test_unc_path_is_redacted(self):
        self.assertEqual(
            redact_text(r"\\server\share\Patients\Jane\session.mp3"),
            r"[path]\session.mp3",
        )

    def test_forward_slash_path_is_redacted(self):
        self.assertEqual(redact_text("C:/Users/henri/file.wav"), "[path]/file.wav")

    def test_url_is_redacted(self):
        self.assertEqual(redact_text("https://example.com/a.mp3"), "[url]")

    def test_bare_home_path_is_fully_redacted(self):
        self.assertEqual(redact_text(r"C:\Users\henri"), "[path]")


class AudioDecodeTests(unittest.TestCase):
    def test_local_wav_decodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "tone.wav"
            with wave.open(str(wav), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                samples = b"".join(
                    struct.pack(
                        "<h",
                        int(8000 * math.sin(2 * math.pi * 440 * i / 16000)),
                    )
                    for i in range(8000)
                )
                handle.writeframes(samples)
            audio = _decode_audio(wav)
        self.assertGreater(audio.size, 0)

    def test_video_without_audio_is_rejected(self):
        with self.assertRaises(AudioFileError):
            _decode_audio(ROOT / "demo" / "SenseMaker-demo.mp4")

    def test_playlist_cannot_open_network(self):
        playlist_text = (
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            "#EXT-X-TARGETDURATION:1\n"
            "#EXTINF:1.0,\n"
            "http://127.0.0.1:9/segment.ts\n"
            "#EXT-X-ENDLIST\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            playlist = Path(tmp) / "evil.m3u8"
            playlist.write_text(playlist_text, encoding="utf-8")
            with self.assertRaises(AudioFileError):
                _decode_audio(playlist)


if __name__ == "__main__":
    unittest.main()
