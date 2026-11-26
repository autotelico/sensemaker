"""Tests for the fast model-availability check used by the UI."""

import tempfile
import unittest
from pathlib import Path

from whisper_engine import TranscriptionEngine

REVISION = "abcdef1234567890"


class ModelReadyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = TranscriptionEngine(models_dir=self.root)
        self.engine._manifest = {
            "models": {
                "fake": {
                    "repo": "org/repo",
                    "revision": REVISION,
                    "files": {
                        "config.json": {"size": 3},
                        "model.bin": {"size": 5},
                    },
                }
            }
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _write_model(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "config.json").write_bytes(b"abc")
        (folder / "model.bin").write_bytes(b"12345")

    def test_app_folder_is_ready(self):
        self._write_model(self.root / f"fake-{REVISION[:12]}")
        self.assertTrue(self.engine.model_ready("fake"))

    def test_legacy_snapshot_is_ready(self):
        self._write_model(
            self.root / "models--org--repo" / "snapshots" / REVISION
        )
        self.assertTrue(self.engine.model_ready("fake"))

    def test_missing_file_is_not_ready(self):
        folder = self.root / f"fake-{REVISION[:12]}"
        folder.mkdir(parents=True)
        (folder / "config.json").write_bytes(b"abc")
        self.assertFalse(self.engine.model_ready("fake"))

    def test_wrong_file_size_is_not_ready(self):
        folder = self.root / f"fake-{REVISION[:12]}"
        folder.mkdir(parents=True)
        (folder / "config.json").write_bytes(b"abc")
        (folder / "model.bin").write_bytes(b"1234")
        self.assertFalse(self.engine.model_ready("fake"))

    def test_unknown_model_is_not_ready(self):
        self.assertFalse(self.engine.model_ready("not-in-manifest"))

    def test_files_present_ignores_extra_files(self):
        folder = self.root / "extra"
        self._write_model(folder)
        (folder / "notes.txt").write_text("unrelated", encoding="utf-8")
        files = {"config.json": {"size": 3}, "model.bin": {"size": 5}}
        self.assertTrue(TranscriptionEngine._files_present(folder, files))


if __name__ == "__main__":
    unittest.main()
