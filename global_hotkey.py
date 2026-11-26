# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Henrique Magalhaes
"""Register a system-wide shortcut on Windows without extra dependencies."""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Sequence

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
HOTKEY_ID = 0x534D  # "SM"

VK_SPACE = 0x20
VK_D = 0x44


@dataclass(frozen=True)
class Hotkey:
    label: str
    modifiers: int
    key: int


# Tried in order. The first one Windows accepts is used, so a conflicting
# shortcut degrades to a working alternative instead of disabling the feature.
DEFAULT_CANDIDATES: tuple[Hotkey, ...] = (
    Hotkey("Ctrl+Alt+Space", MOD_CONTROL | MOD_ALT, VK_SPACE),
    Hotkey("Ctrl+Shift+Space", MOD_CONTROL | MOD_SHIFT, VK_SPACE),
    Hotkey("Ctrl+Alt+D", MOD_CONTROL | MOD_ALT, VK_D),
)


class GlobalHotkey:
    """Listen for one system-wide hotkey on a dedicated message thread.

    ``callback`` runs on that thread; keep it quick and hand off to the UI
    thread (for example by putting an item on a queue).
    """

    def __init__(
        self,
        callback: Callable[[], None],
        candidates: Sequence[Hotkey] = DEFAULT_CANDIDATES,
        name: str = "sense-maker-hotkey",
    ) -> None:
        self.callback = callback
        self.candidates = tuple(candidates)
        self.registered: str | None = None
        self._name = name
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._log = logging.getLogger(__name__)

    def start(self, timeout: float = 2.0) -> str | None:
        """Register the first available shortcut and return its label."""
        if os.name != "nt":
            return None
        if self._thread is not None:
            return self.registered
        self.registered = None
        self._thread_id = None
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=self._name
        )
        self._thread.start()
        self._ready.wait(timeout)
        return self.registered

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        thread_id = self._thread_id
        if thread_id is not None:
            try:
                _user32().PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            except Exception:
                self._log.debug("Could not post the hotkey quit message", exc_info=True)
        thread.join(timeout=2.0)
        self._thread = None
        self._thread_id = None
        self.registered = None

    def _run(self) -> None:
        user32 = _user32()
        kernel32 = _kernel32()
        self._thread_id = int(kernel32.GetCurrentThreadId())
        registered = False
        try:
            for candidate in self.candidates:
                if user32.RegisterHotKey(
                    None,
                    HOTKEY_ID,
                    candidate.modifiers | MOD_NOREPEAT,
                    candidate.key,
                ):
                    self.registered = candidate.label
                    registered = True
                    break
            if not registered:
                self._log.warning(
                    "No global dictation shortcut could be registered "
                    "(last error %s).",
                    ctypes.get_last_error() or "unknown",
                )
        except Exception:
            self._log.exception("Could not register the global dictation shortcut")
        finally:
            self._ready.set()

        if not registered:
            return

        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if (
                    message.message == WM_HOTKEY
                    and int(message.wParam) == HOTKEY_ID
                ):
                    try:
                        self.callback()
                    except Exception:
                        self._log.exception("Global dictation shortcut callback failed")
        finally:
            try:
                user32.UnregisterHotKey(None, HOTKEY_ID)
            except Exception:
                self._log.debug("Could not unregister the hotkey", exc_info=True)


_USER32 = None
_KERNEL32 = None


def _user32():
    global _USER32
    if _USER32 is None:
        dll = ctypes.WinDLL("user32", use_last_error=True)
        dll.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        dll.RegisterHotKey.restype = wintypes.BOOL
        dll.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        dll.UnregisterHotKey.restype = wintypes.BOOL
        dll.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        dll.GetMessageW.restype = ctypes.c_int
        dll.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        dll.PostThreadMessageW.restype = wintypes.BOOL
        _USER32 = dll
    return _USER32


def _kernel32():
    global _KERNEL32
    if _KERNEL32 is None:
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.GetCurrentThreadId.argtypes = []
        dll.GetCurrentThreadId.restype = wintypes.DWORD
        _KERNEL32 = dll
    return _KERNEL32
