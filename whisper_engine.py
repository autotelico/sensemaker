# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Henrique Magalhaes
"""SenseMaker's local Whisper transcription engine and shared helpers."""

from __future__ import annotations

import gc
import hashlib
import hmac
import json
import logging
import os
import shutil
import site
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = (
    Path(getattr(sys, "_MEIPASS", APP_DIR))
    if getattr(sys, "frozen", False)
    else APP_DIR
)
MODEL_MANIFEST_PATH = RESOURCE_DIR / "assets" / "model_manifests.json"


def _runtime_data_dir() -> Path:
    """Return the writable folder for models and logs.

    Source runs keep using the project folder. Installed builds store data in
    the user's local app-data folder so the program directory stays clean and
    can be read-only.
    """
    override = os.environ.get("SENSEMAKER_DATA_DIR", "").strip()
    if override:
        folder = Path(override).expanduser()
    elif getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA")
        folder = (Path(base) if base else Path.home() / "AppData" / "Local") / "SenseMaker"
    else:
        folder = APP_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder


DATA_DIR = _runtime_data_dir()
MODELS_DIR = DATA_DIR / "models"
LOG_PATH = DATA_DIR / "sensemaker.log"

# Keep Hugging Face from warning about Windows symlink support.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

MODEL_OPTIONS: list[tuple[str, str, str]] = [
    ("tiny", "Tiny", "75 MB"),
    ("base", "Base", "145 MB"),
    ("small", "Small", "480 MB"),
    ("medium", "Medium", "1.5 GB"),
    ("large-v3-turbo", "Large v3 Turbo", "1.6 GB"),
    ("large-v3", "Large v3", "3.1 GB"),
]
MODEL_LABELS = {key: f"{name} · {size}" for key, name, size in MODEL_OPTIONS}
MODEL_NAMES = {key: name for key, name, _ in MODEL_OPTIONS}
DEFAULT_MODEL = "small"

LANGUAGE_OPTIONS: list[tuple[str, str | None]] = [
    ("Auto-detect", None),
    ("English", "en"),
    ("Spanish", "es"),
    ("French", "fr"),
    ("German", "de"),
    ("Portuguese", "pt"),
    ("Italian", "it"),
    ("Dutch", "nl"),
    ("Polish", "pl"),
    ("Russian", "ru"),
    ("Ukrainian", "uk"),
    ("Turkish", "tr"),
    ("Arabic", "ar"),
    ("Hindi", "hi"),
    ("Urdu", "ur"),
    ("Chinese", "zh"),
    ("Japanese", "ja"),
    ("Korean", "ko"),
    ("Vietnamese", "vi"),
    ("Thai", "th"),
    ("Indonesian", "id"),
    ("Swedish", "sv"),
    ("Norwegian", "no"),
    ("Danish", "da"),
    ("Finnish", "fi"),
    ("Greek", "el"),
    ("Hebrew", "he"),
    ("Czech", "cs"),
    ("Romanian", "ro"),
    ("Hungarian", "hu"),
]

# Used for "detected language" display.
_LANGUAGE_NAMES = {code: name for name, code in LANGUAGE_OPTIONS if code}
_LANGUAGE_NAMES.update({"fa": "Persian", "ms": "Malay", "tl": "Filipino", "sw": "Swahili"})


def _configure_cuda_dlls() -> list[Any]:
    """Make CUDA libraries discoverable on Windows.

    Installed builds can use an app-local ``gpu`` folder, a separately
    installed NVIDIA CUDA Toolkit, or pip-installed ``nvidia-*`` packages.
    Adding those folders to PATH before importing faster-whisper enables GPU
    inference. If they are absent, transcription still works on CPU.
    """
    if os.name != "nt":
        return []

    dll_dirs: list[Path] = []
    gpu_roots: list[Path] = []

    override = os.environ.get("SENSEMAKER_GPU_DIR", "").strip()
    if override:
        gpu_roots.append(Path(override).expanduser())

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        gpu_roots.extend([exe_dir / "gpu", RESOURCE_DIR / "gpu"])
    gpu_roots.append(APP_DIR / "gpu")

    for env_name in ("CUDA_PATH", "CUDA_HOME", "CUDA_ROOT"):
        value = os.environ.get(env_name, "").strip()
        if value:
            gpu_roots.append(Path(value))

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    toolkit_root = Path(program_files) / "NVIDIA GPU Computing Toolkit" / "CUDA"
    if toolkit_root.is_dir():
        try:
            gpu_roots.extend(
                sorted(
                    (path for path in toolkit_root.iterdir() if path.is_dir()),
                    reverse=True,
                )
            )
        except OSError:
            pass

    for root in gpu_roots:
        dll_dirs.extend([
            root / "cublas" / "bin",
            root / "cudnn" / "bin",
            root / "bin",
        ])

    site_roots: list[Path] = []
    try:
        site_roots.extend(Path(p) for p in site.getsitepackages())
    except Exception:
        pass
    if sys.prefix:
        site_roots.insert(0, Path(sys.prefix) / "Lib" / "site-packages")
    for root in site_roots:
        dll_dirs.extend([root / "nvidia" / "cublas" / "bin", root / "nvidia" / "cudnn" / "bin"])

    seen: set[str] = set()
    handles: list[Any] = []
    for dll_dir in dll_dirs:
        key = str(dll_dir).lower()
        if key in seen or not dll_dir.is_dir():
            continue
        seen.add(key)
        os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            try:
                handles.append(os.add_dll_directory(str(dll_dir)))
            except OSError:
                logging.getLogger(__name__).debug("Could not add DLL directory %s", dll_dir)
    return handles


# This must run before faster-whisper/ctranslate2 is imported.
_CUDA_DLL_HANDLES = _configure_cuda_dlls()

from faster_whisper import WhisperModel  # noqa: E402


class AudioFileError(RuntimeError):
    """Raised when a file cannot be opened as audio."""


class ModelIntegrityError(RuntimeError):
    """Raised when a model cannot be pinned, downloaded, or verified."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_model_manifest() -> dict[str, Any]:
    try:
        payload = json.loads(MODEL_MANIFEST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ModelIntegrityError(
            "The pinned model manifest is missing; refusing to download an unverified model."
        ) from exc
    except Exception as exc:
        raise ModelIntegrityError(f"Could not read the model manifest: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ModelIntegrityError("The model manifest has an unsupported schema.")
    models = payload.get("models")
    if not isinstance(models, dict) or not models:
        raise ModelIntegrityError("The model manifest does not contain any models.")
    return payload


@dataclass(frozen=True)
class MediaInfo:
    duration: float | None


@dataclass
class LoadedModel:
    model: WhisperModel
    model_key: str
    device: str
    compute_type: str
    used_cpu_fallback: bool = False

    @property
    def device_label(self) -> str:
        if self.device == "cuda":
            return "GPU · CUDA"
        return "CPU · fallback" if self.used_cpu_fallback else "CPU"


def probe_media(path: Path) -> MediaInfo:
    """Read basic audio metadata without decoding the whole file."""
    try:
        import av

        with av.open(str(path), mode="r", metadata_errors="ignore") as container:
            audio_streams = [stream for stream in container.streams if stream.type == "audio"]
            if not audio_streams:
                raise AudioFileError("This file does not contain an audio track.")

            duration: float | None = None
            if container.duration:
                duration = float(container.duration) / 1_000_000
            if duration is None:
                for stream in audio_streams:
                    if stream.duration is not None and stream.time_base is not None:
                        duration = float(stream.duration * stream.time_base)
                        break
            if duration is not None and duration <= 0:
                duration = None
            return MediaInfo(duration=duration)
    except AudioFileError:
        raise
    except Exception as exc:
        raise AudioFileError(str(exc) or "Unsupported or unreadable audio file.") from exc


def looks_like_gpu_error(exc: Exception) -> bool:
    message = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "cublas",
        "cudnn",
        "cuda",
        "nvidia",
        "gpu",
        "no kernel image",
        "dll load failed",
        "out of memory",
        "library",
    )
    return any(marker in message for marker in markers)


class TranscriptionEngine:
    """Keeps one Whisper model loaded and chooses GPU when possible."""

    def __init__(self, models_dir: Path = MODELS_DIR) -> None:
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.log = logging.getLogger(__name__)
        self._loaded: LoadedModel | None = None
        self._manifest: dict[str, Any] | None = None
        self._gpu_blocked = False
        self._device_override = os.environ.get("WHISPER_DEVICE", "auto").strip().lower()

    def _manifest_entry(self, model_key: str) -> dict[str, Any]:
        if self._manifest is None:
            self._manifest = load_model_manifest()
        models = self._manifest["models"]
        entry = models.get(model_key)
        if not isinstance(entry, dict):
            raise ModelIntegrityError(f"Model {model_key!r} is not in the pinned manifest.")
        return entry

    def _verify_model_files(self, folder: Path, files: dict[str, Any]) -> bool:
        if not folder.is_dir():
            return False
        for name, metadata in files.items():
            path = folder / name
            if not path.is_file():
                self.log.warning("Pinned model file is missing: %s", name)
                return False
            expected = str(metadata.get("sha256", "")).lower()
            expected_size = metadata.get("size")
            if not expected:
                self.log.warning("Pinned model file has no checksum: %s", name)
                return False
            try:
                if expected_size is not None and path.stat().st_size != int(expected_size):
                    self.log.warning("Pinned model file has an unexpected size: %s", name)
                    return False
                actual = sha256_file(path)
            except OSError:
                self.log.exception("Could not read model file %s", name)
                return False
            if not hmac.compare_digest(actual, expected):
                self.log.error("Checksum mismatch for model file %s", name)
                return False
        return True

    def ensure_model(self, model_key: str) -> Path:
        """Return a verified local model directory for a pinned model.

        The Hugging Face revision is fixed in assets/model_manifests.json and
        every runtime file is checked against a SHA-256 digest before it is
        given to CTranslate2.
        """
        entry = self._manifest_entry(model_key)
        repo = str(entry.get("repo") or "")
        revision = str(entry.get("revision") or "")
        files = entry.get("files")
        if not repo or not revision or not isinstance(files, dict) or not files:
            raise ModelIntegrityError(f"The manifest entry for {model_key!r} is incomplete.")

        legacy = (
            self.models_dir
            / f"models--{repo.replace('/', '--')}"
            / "snapshots"
            / revision
        )
        if self._verify_model_files(legacy, files):
            self.log.info("Using verified cached model files for %s", model_key)
            return legacy

        target = self.models_dir / f"{model_key}-{revision[:12]}"
        if target.is_dir():
            if self._verify_model_files(target, files):
                return target
            self.log.warning("Removing unverified model directory for %s", model_key)
            shutil.rmtree(target, ignore_errors=True)

        self.log.info("Downloading pinned model %s at revision %s", model_key, revision)
        try:
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id=repo,
                revision=revision,
                local_dir=str(target),
                allow_patterns=sorted(files),
            )
        except Exception as exc:
            raise ModelIntegrityError(
                f"Could not download the pinned {model_key} model: {exc}"
            ) from exc

        if not self._verify_model_files(target, files):
            shutil.rmtree(target, ignore_errors=True)
            raise ModelIntegrityError(
                f"The downloaded {model_key} model failed checksum verification. "
                "It was discarded and will not be loaded."
            )
        self.log.info("Verified pinned model files for %s", model_key)
        return target

    def _cuda_compute_types(self) -> set[str]:
        try:
            import ctranslate2

            return set(ctranslate2.get_supported_compute_types("cuda"))
        except Exception:
            self.log.debug("CUDA capability check failed", exc_info=True)
            return set()

    def preferred_device(self) -> str:
        if self._device_override == "cpu" or self._gpu_blocked:
            return "cpu"
        if self._device_override == "cuda":
            return "cuda"
        return "cuda" if self._cuda_compute_types() else "cpu"

    def block_gpu(self) -> None:
        self._gpu_blocked = True
        self.log.warning("GPU disabled for this session after a CUDA failure.")

    def unload(self) -> None:
        self._loaded = None
        gc.collect()

    @property
    def loaded_model(self) -> LoadedModel | None:
        return self._loaded

    def load_model(self, model_key: str, force_cpu: bool = False) -> LoadedModel:
        device = "cpu" if force_cpu else self.preferred_device()
        compute_type = "float16" if device == "cuda" else "int8"
        cpu_threads = max(1, min(8, os.cpu_count() or 4))

        if (
            self._loaded is not None
            and self._loaded.model_key == model_key
            and self._loaded.device == device
            and self._loaded.compute_type == compute_type
        ):
            return self._loaded

        self.unload()
        used_cpu_fallback = False
        model_path = self.ensure_model(model_key)

        if device == "cuda":
            try:
                self.log.info("Loading %s on CUDA (%s)", model_key, compute_type)
                model = WhisperModel(
                    str(model_path),
                    device="cuda",
                    compute_type=compute_type,
                    cpu_threads=cpu_threads,
                )
            except Exception:
                self.log.exception("CUDA model load failed; retrying on CPU")
                self.block_gpu()
                device = "cpu"
                compute_type = "int8"
                used_cpu_fallback = True

        if device == "cpu":
            self.log.info("Loading %s on CPU (%s)", model_key, compute_type)
            model = WhisperModel(
                str(model_path),
                device="cpu",
                compute_type=compute_type,
                cpu_threads=cpu_threads,
            )

        self._loaded = LoadedModel(
            model=model,
            model_key=model_key,
            device=device,
            compute_type=compute_type,
            used_cpu_fallback=used_cpu_fallback or force_cpu,
        )
        self.log.info("Loaded %s using %s", model_key, self._loaded.device_label)
        return self._loaded

    def transcribe(self, path: Path, language: str | None):
        if self._loaded is None:
            raise RuntimeError("No Whisper model is loaded.")

        segments, info = self._loaded.model.transcribe(
            str(path),
            language=language,
            task="transcribe",
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=True,
        )
        return segments, info


def language_name(code: str | None) -> str:
    if not code:
        return "Unknown"
    return _LANGUAGE_NAMES.get(code, code.upper())


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown length"
    if seconds < 60:
        return f"{seconds:.1f} sec"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s"


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_bytes(size: int) -> str:
    if size < 1_000:
        return f"{size} B"
    if size < 1_000_000:
        return f"{size / 1_000:.0f} KB"
    if size < 1_000_000_000:
        return f"{size / 1_000_000:.1f} MB"
    return f"{size / 1_000_000_000:.2f} GB"
