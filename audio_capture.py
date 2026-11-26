# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Henrique Magalhaes
"""Microphone capture for live transcription.

Recording uses the Windows ``waveIn`` API directly, so live transcription
needs no extra runtime beyond the Whisper stack that SenseMaker already ships.
Captured audio is converted to 16 kHz mono float32 in memory and handed to the
live transcriber; nothing is written to disk.
"""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000
BLOCK_SECONDS = 0.10
BUFFER_COUNT = 4
# Keep a bounded backlog so a model that cannot keep up discards the oldest
# audio instead of falling minutes behind and growing memory without limit.
MAX_BACKLOG_SECONDS = 30.0

WAVE_MAPPER = 0xFFFFFFFF
WAVE_FORMAT_PCM = 1
CALLBACK_NULL = 0

WHDR_DONE = 0x00000001
WHDR_PREPARED = 0x00000002

MMSYSERR_NOERROR = 0
MMSYSERR_ALLOCATED = 4
MMSYSERR_INVALHANDLE = 5
MMSYSERR_NODRIVER = 6
MMSYSERR_NOMEM = 7
WAVERR_BADFORMAT = 32
WAVERR_STILLPLAYING = 33
WAVERR_UNPREPARED = 34

FORMAT_ATTEMPTS: tuple[tuple[int, int], ...] = (
    (16000, 1),
    (48000, 2),
    (44100, 2),
    (48000, 1),
)

_ERROR_TEXT = {
    MMSYSERR_ALLOCATED: "the device is in use by another application",
    MMSYSERR_INVALHANDLE: "the audio device handle is no longer valid",
    MMSYSERR_NODRIVER: "no audio input driver is installed",
    MMSYSERR_NOMEM: "Windows could not allocate memory for recording",
    WAVERR_BADFORMAT: "the device does not support the requested audio format",
    WAVERR_STILLPLAYING: "the audio buffers are still in use",
    WAVERR_UNPREPARED: "an audio buffer was not prepared",
}


class CaptureError(RuntimeError):
    """Raised when microphone capture cannot start or continue."""


def _format_error(code: int) -> str:
    detail = _ERROR_TEXT.get(code)
    if detail:
        return f"{detail} (waveIn error {code})"
    return f"Windows audio error {code}"


@dataclass(frozen=True)
class InputDevice:
    index: int | None
    name: str


class _WAVEFORMATEX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class _WAVEINCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * 32),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
    ]


class _WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
    ]


_WINMM = None


def _winmm():
    """Load winmm.dll and declare the functions used for capture."""
    global _WINMM
    if os.name != "nt":
        raise CaptureError("Live transcription is only available on Windows.")
    if _WINMM is not None:
        return _WINMM

    dll = ctypes.WinDLL("winmm", use_last_error=True)
    dll.waveInGetNumDevs.argtypes = []
    dll.waveInGetNumDevs.restype = wintypes.UINT
    dll.waveInGetDevCapsW.argtypes = [
        ctypes.c_uint,
        ctypes.POINTER(_WAVEINCAPSW),
        ctypes.c_uint,
    ]
    dll.waveInGetDevCapsW.restype = wintypes.UINT
    dll.waveInOpen.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint,
        ctypes.POINTER(_WAVEFORMATEX),
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_uint,
    ]
    dll.waveInOpen.restype = wintypes.UINT
    dll.waveInPrepareHeader.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_WAVEHDR),
        ctypes.c_uint,
    ]
    dll.waveInPrepareHeader.restype = wintypes.UINT
    dll.waveInUnprepareHeader.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_WAVEHDR),
        ctypes.c_uint,
    ]
    dll.waveInUnprepareHeader.restype = wintypes.UINT
    dll.waveInAddBuffer.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_WAVEHDR),
        ctypes.c_uint,
    ]
    dll.waveInAddBuffer.restype = wintypes.UINT
    for name in ("waveInStart", "waveInStop", "waveInReset", "waveInClose"):
        function = getattr(dll, name)
        function.argtypes = [ctypes.c_void_p]
        function.restype = wintypes.UINT

    _WINMM = dll
    return dll


def _device_name(device_id: int) -> str:
    caps = _WAVEINCAPSW()
    result = _winmm().waveInGetDevCapsW(
        device_id, ctypes.byref(caps), ctypes.sizeof(caps)
    )
    if result != MMSYSERR_NOERROR:
        return "Default microphone" if device_id == WAVE_MAPPER else "Microphone"
    name = caps.szPname.strip()
    if name:
        return name
    if device_id == WAVE_MAPPER:
        return "Default microphone"
    return f"Microphone {device_id + 1}"


def list_input_devices() -> list[InputDevice]:
    """Return the recording devices Windows exposes, default first."""
    devices = [InputDevice(None, _device_name(WAVE_MAPPER))]
    try:
        count = int(_winmm().waveInGetNumDevs())
    except CaptureError:
        return devices
    seen = set()
    for index in range(count):
        try:
            name = _device_name(index)
        except CaptureError:
            continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        devices.append(InputDevice(index, name))
    return devices


class MicrophoneStream:
    """Stream 16 kHz mono float32 microphone audio into ``audio_queue``.

    Items placed on the queue are ``numpy`` arrays; if capture fails, the
    exception is placed on the queue and the stream stops. Call ``stop()`` (or
    use the context manager) to release the microphone.
    """

    def __init__(
        self,
        device_index: int | None = None,
        block_seconds: float = BLOCK_SECONDS,
    ) -> None:
        self.device_id = WAVE_MAPPER if device_index is None else int(device_index)
        self.block_seconds = max(0.02, float(block_seconds))
        self.audio_queue: queue.Queue = queue.Queue()
        self.capture_rate = SAMPLE_RATE
        self.capture_channels = 1
        self.device_label = "Default microphone"
        self.log = logging.getLogger(__name__)

        self._handle = ctypes.c_void_p()
        self._buffers: list[tuple[_WAVEHDR, ctypes.Array]] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = False
        self._resampler = None
        self._resampler_layout = "mono"
        self._max_chunks = 1
        self._backlog_warned = False

    def __enter__(self) -> "MicrophoneStream":
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.stop()

    def start(self) -> None:
        if self._started:
            return
        winmm = _winmm()
        self.device_label = _device_name(self.device_id)

        format_info = None
        last_error = MMSYSERR_NOERROR
        for rate, channels in FORMAT_ATTEMPTS:
            block_align = max(1, channels * 2)
            wave_format = _WAVEFORMATEX(
                wFormatTag=WAVE_FORMAT_PCM,
                nChannels=channels,
                nSamplesPerSec=rate,
                nAvgBytesPerSec=rate * block_align,
                nBlockAlign=block_align,
                wBitsPerSample=16,
                cbSize=0,
            )
            result = winmm.waveInOpen(
                ctypes.byref(self._handle),
                self.device_id,
                ctypes.byref(wave_format),
                0,
                0,
                CALLBACK_NULL,
            )
            if result == MMSYSERR_NOERROR:
                format_info = (rate, channels, block_align)
                break
            last_error = result
            if result not in (WAVERR_BADFORMAT, MMSYSERR_INVALHANDLE):
                # A busy or missing device will not work at another rate.
                break

        if format_info is None:
            self._handle = ctypes.c_void_p()
            raise CaptureError(
                f"Could not open the microphone “{self.device_label}”: "
                f"{_format_error(last_error)}."
            )

        self.capture_rate, self.capture_channels, block_align = format_info
        self._max_chunks = max(8, int(MAX_BACKLOG_SECONDS / self.block_seconds))
        self._backlog_warned = False
        frames = max(512, int(self.capture_rate * self.block_seconds))
        buffer_size = frames * block_align

        try:
            for _ in range(BUFFER_COUNT):
                raw = ctypes.create_string_buffer(buffer_size)
                header = _WAVEHDR(
                    lpData=ctypes.cast(raw, ctypes.c_void_p),
                    dwBufferLength=buffer_size,
                    dwBytesRecorded=0,
                    dwFlags=0,
                    dwLoops=0,
                )
                result = winmm.waveInPrepareHeader(
                    self._handle, ctypes.byref(header), ctypes.sizeof(header)
                )
                if result != MMSYSERR_NOERROR:
                    raise CaptureError(
                        "Could not prepare microphone buffers: "
                        f"{_format_error(result)}."
                    )
                result = winmm.waveInAddBuffer(
                    self._handle, ctypes.byref(header), ctypes.sizeof(header)
                )
                if result != MMSYSERR_NOERROR:
                    raise CaptureError(
                        "Could not queue microphone buffers: "
                        f"{_format_error(result)}."
                    )
                self._buffers.append((header, raw))

            result = winmm.waveInStart(self._handle)
            if result != MMSYSERR_NOERROR:
                raise CaptureError(
                    f"Could not start microphone recording: {_format_error(result)}."
                )
        except Exception:
            self._release_device()
            raise

        if not (self.capture_rate == SAMPLE_RATE and self.capture_channels == 1):
            self._resampler = _make_resampler()
            self._resampler_layout = "mono" if self.capture_channels == 1 else "stereo"

        self._started = True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name="sense-maker-microphone",
        )
        self._thread.start()
        self.log.info(
            "Microphone capture started at %d Hz, %d channel(s)",
            self.capture_rate,
            self.capture_channels,
        )

    def _capture_loop(self) -> None:
        winmm = _winmm()
        size = ctypes.sizeof(_WAVEHDR)
        try:
            while not self._stop.is_set():
                progressed = False
                for header, raw in self._buffers:
                    if self._stop.is_set():
                        break
                    if not header.dwFlags & WHDR_DONE:
                        continue
                    recorded = int(header.dwBytesRecorded)
                    if recorded > 0:
                        self._emit(raw.raw[:recorded])
                    self._rearm(winmm, header, size)
                    progressed = True
                if not progressed:
                    time.sleep(0.01)
        except BaseException as exc:  # deliver driver errors to the consumer
            self.audio_queue.put(exc)
            self._stop.set()

    def _rearm(self, winmm, header: _WAVEHDR, size: int) -> None:
        winmm.waveInUnprepareHeader(self._handle, ctypes.byref(header), size)
        header.dwFlags = 0
        header.dwBytesRecorded = 0
        header.dwLoops = 0
        result = winmm.waveInPrepareHeader(self._handle, ctypes.byref(header), size)
        if result == MMSYSERR_NOERROR:
            result = winmm.waveInAddBuffer(self._handle, ctypes.byref(header), size)
        if result != MMSYSERR_NOERROR:
            raise CaptureError(
                f"Microphone capture stopped: {_format_error(result)}."
            )

    def _enqueue(self, audio: np.ndarray) -> None:
        if self.audio_queue.qsize() >= self._max_chunks:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                pass
            if not self._backlog_warned:
                self._backlog_warned = True
                self.log.warning(
                    "Live audio backlog is too large; dropping the oldest audio."
                )
        self.audio_queue.put(audio)

    def _emit(self, data: bytes) -> None:
        pcm = np.frombuffer(data, dtype="<i2")
        if pcm.size == 0:
            return
        if self._resampler is None:
            self._enqueue(pcm.astype(np.float32) / 32768.0)
            return
        packed = pcm.reshape(1, -1)
        frame = _audio_frame(packed, self._resampler_layout)
        frame.sample_rate = self.capture_rate
        for converted in self._resampler.resample(frame):
            array = converted.to_ndarray()
            self._enqueue(array[0].astype(np.float32) / 32768.0)

    def stop(self) -> None:
        if not self._started and not self._buffers:
            return
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=3.0)
        if self._resampler is not None:
            try:
                for converted in self._resampler.resample(None):
                    array = converted.to_ndarray()
                    self._enqueue(array[0].astype(np.float32) / 32768.0)
            except Exception:
                self.log.debug(
                    "Could not flush the microphone resampler", exc_info=True
                )
            self._resampler = None
        self._release_device()
        self._started = False
        self.log.info("Microphone capture stopped")

    def _release_device(self) -> None:
        winmm = _winmm() if os.name == "nt" else None
        handle = self._handle
        if winmm is not None and handle:
            for function in (winmm.waveInStop, winmm.waveInReset):
                try:
                    function(handle)
                except Exception:
                    pass
        for header, _raw in self._buffers:
            if winmm is not None and handle and header.dwFlags & WHDR_PREPARED:
                try:
                    winmm.waveInUnprepareHeader(
                        handle, ctypes.byref(header), ctypes.sizeof(header)
                    )
                except Exception:
                    pass
        if winmm is not None and handle:
            try:
                winmm.waveInClose(handle)
            except Exception:
                pass
        self._buffers.clear()
        self._handle = ctypes.c_void_p()


def _make_resampler():
    import av

    return av.audio.resampler.AudioResampler(
        format="s16",
        layout="mono",
        rate=SAMPLE_RATE,
    )


def _audio_frame(packed: np.ndarray, layout: str):
    import av

    return av.AudioFrame.from_ndarray(packed, format="s16", layout=layout)


def record_test(duration: float = 1.0, device_index: int | None = None) -> float:
    """Capture briefly and return the RMS level. Used by manual checks only."""
    captured = []
    stream = MicrophoneStream(device_index)
    stream.start()
    deadline = time.monotonic() + duration
    try:
        while time.monotonic() < deadline:
            try:
                item = stream.audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if isinstance(item, BaseException):
                raise item
            captured.append(item)
    finally:
        stream.stop()
    if not captured:
        return 0.0
    audio = np.concatenate(captured)
    return float(np.sqrt(np.mean(np.square(audio))))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for device in list_input_devices():
        print(device)
    print("RMS:", record_test())
