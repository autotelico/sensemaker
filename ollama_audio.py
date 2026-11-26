# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Henrique Magalhaes
"""Detect and use an audio-capable local Ollama model.

Ollama exposes an OpenAI-compatible ``/v1/audio/transcriptions`` endpoint in
recent versions. Only loopback servers are used, so audio never leaves this
machine through this path. If no audio-capable model is available, SenseMaker
falls back to its pinned Whisper models.
"""

from __future__ import annotations

import io
import ipaddress
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from dataclasses import dataclass
from typing import Any

import numpy as np

DEFAULT_HOST = "127.0.0.1:11434"
DISCOVERY_TIMEOUT = 0.8
TRANSCRIBE_TIMEOUT = 180.0
MAX_RESPONSE_BYTES = 8_000_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, "redirects are not allowed", headers, fp
        )


# Never use system proxies or follow redirects for local audio requests.
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _NoRedirect()
)


class OllamaError(RuntimeError):
    """Raised when the local Ollama server cannot transcribe audio."""


@dataclass(frozen=True)
class OllamaDiscovery:
    base_url: str
    available: bool
    models: tuple[str, ...]
    selected: str | None
    error: str | None = None

    @property
    def using_ollama(self) -> bool:
        return self.selected is not None


def _is_loopback(host: str) -> bool:
    normalised = (host or "").strip().strip("[]").lower()
    if normalised == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalised).is_loopback
    except ValueError:
        return False


def _base_url() -> tuple[str | None, str | None]:
    raw = os.environ.get("OLLAMA_HOST", "").strip() or DEFAULT_HOST
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = parsed.hostname
        port = parsed.port or 11434
    except ValueError as exc:
        return None, f"OLLAMA_HOST is invalid: {exc}"
    if parsed.scheme not in ("http", "https"):
        return None, "OLLAMA_HOST must use http or https"
    if not host:
        return None, "OLLAMA_HOST has no host"
    if not _is_loopback(host):
        return None, "OLLAMA_HOST is not a loopback address; refusing to send audio remotely"
    host_display = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{host_display}:{port}", None


def _get_json(base_url: str, path: str, timeout: float) -> Any:
    request = urllib.request.Request(
        f"{base_url}{path}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with _OPENER.open(request, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES)
    return json.loads(raw.decode("utf-8"))


def _supports_transcription(base_url: str, timeout: float) -> bool:
    """Return whether the server exposes the transcription endpoint.

    An empty multipart POST returns 400 on servers that have the route and 404
    on versions that do not.
    """
    request = urllib.request.Request(
        f"{base_url}/v1/audio/transcriptions",
        data=b"",
        headers={"Content-Type": "multipart/form-data; boundary=sensemaker-probe"},
        method="POST",
    )
    try:
        with _OPENER.open(request, timeout=timeout):
            return True
    except urllib.error.HTTPError as exc:
        try:
            return exc.code not in (404, 405)
        finally:
            exc.close()
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def discover(timeout: float = DISCOVERY_TIMEOUT) -> OllamaDiscovery:
    """Find a running loopback Ollama with an audio-capable model."""
    base_url, error = _base_url()
    if base_url is None:
        return OllamaDiscovery("", False, (), None, error)

    try:
        payload = _get_json(base_url, "/api/tags", timeout)
    except urllib.error.HTTPError as exc:
        exc.close()
        logging.getLogger(__name__).debug(
            "Ollama model list returned HTTP %s", exc.code, exc_info=True
        )
        return OllamaDiscovery(
            base_url,
            False,
            (),
            None,
            f"Ollama returned HTTP {exc.code} at {base_url}",
        )
    except Exception as exc:
        logging.getLogger(__name__).debug("Ollama discovery failed", exc_info=True)
        return OllamaDiscovery(
            base_url,
            False,
            (),
            None,
            f"Ollama is not reachable at {base_url}",
        )

    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return OllamaDiscovery(base_url, False, (), None, "Ollama returned an unexpected model list")

    names: list[str] = []
    audio_models: list[str] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        names.append(name)
        capabilities = item.get("capabilities")
        if isinstance(capabilities, list):
            is_audio = "audio" in {str(value).lower() for value in capabilities}
        else:
            # Older servers do not report capabilities. Gemma 4 is the audio
            # family exposed by Ollama at the time of writing.
            is_audio = name.split(":", 1)[0].lower() == "gemma4"
        if is_audio:
            audio_models.append(name)

    if not audio_models:
        return OllamaDiscovery(
            base_url,
            True,
            tuple(names),
            None,
            "Ollama has no audio-capable model",
        )

    if not _supports_transcription(base_url, timeout):
        return OllamaDiscovery(
            base_url,
            True,
            tuple(names),
            None,
            "this Ollama version does not expose audio transcription",
        )

    return OllamaDiscovery(
        base_url,
        True,
        tuple(names),
        sorted(audio_models)[0],
        None,
    )


def audio_to_wav(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Encode mono float32 audio as a 16-bit PCM WAV file in memory."""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    samples = np.clip(samples, -1.0, 1.0)
    pcm = (samples * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def _multipart_body(
    fields: dict[str, str],
    filename: str,
    file_bytes: bytes,
    content_type: str = "audio/wav",
) -> tuple[bytes, str]:
    boundary = f"----SenseMaker{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode("ascii"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _error_message(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except Exception:
        return text[:500]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message
        if isinstance(error, str):
            return error
        message = payload.get("message")
        if isinstance(message, str):
            return message
    return text[:500]


def transcribe(
    audio: np.ndarray,
    model: str,
    base_url: str,
    language: str | None = None,
    prompt: str | None = None,
    sample_rate: int = 16000,
    timeout: float = TRANSCRIBE_TIMEOUT,
) -> str:
    """Transcribe mono float32 audio with an Ollama audio model."""
    if not _is_loopback(urllib.parse.urlsplit(base_url).hostname or ""):
        raise OllamaError("Ollama base URL is not loopback; refusing to send audio.")
    fields = {"model": model}
    if language:
        fields["language"] = language
    if prompt:
        fields["prompt"] = prompt
    body, content_type = _multipart_body(
        fields, "audio.wav", audio_to_wav(audio, sample_rate)
    )
    request = urllib.request.Request(
        f"{base_url}/v1/audio/transcriptions",
        data=body,
        headers={
            "Content-Type": content_type,
            "Accept": "application/json",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        try:
            detail = _error_message(exc.read(MAX_RESPONSE_BYTES))
        finally:
            exc.close()
        raise OllamaError(
            f"Ollama transcription failed ({exc.code})"
            + (f": {detail}" if detail else "")
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OllamaError(f"Ollama is not reachable: {exc}") from exc

    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except Exception:
        return text
    if isinstance(payload, dict):
        value = payload.get("text")
        if isinstance(value, str):
            return value.strip()
        segments = payload.get("segments")
        if isinstance(segments, list):
            pieces = [
                str(segment.get("text", ""))
                for segment in segments
                if isinstance(segment, dict)
            ]
            return " ".join(piece.strip() for piece in pieces if piece.strip())
        error = payload.get("error")
        if error:
            raise OllamaError(
                error.get("message") if isinstance(error, dict) else str(error)
            )
    return ""
