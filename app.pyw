# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Henrique Magalhaes
"""SenseMaker — a small local speech-to-text desktop app."""

from __future__ import annotations

import ctypes
import json
import logging
import logging.handlers
import os
import queue
import re
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from secure_store import (
    SETTINGS_FILENAME,
    HistoryStore,
    RedactingFormatter,
    SecureStoreError,
    atomic_write_json,
)
from whisper_engine import (
    DATA_DIR,
    DEFAULT_MODEL,
    LANGUAGE_OPTIONS,
    LOG_PATH,
    MODEL_LABELS,
    MODEL_NAMES,
    MODEL_OPTIONS,
    RESOURCE_DIR,
    TranscriptionEngine,
    format_bytes,
    format_duration,
    format_timestamp,
    language_name,
    looks_like_gpu_error,
)

APP_NAME = "SenseMaker"
SETTINGS_PATH = DATA_DIR / SETTINGS_FILENAME

_log_handler = logging.handlers.RotatingFileHandler(
    LOG_PATH,
    maxBytes=1_000_000,
    backupCount=2,
    encoding="utf-8",
)
_log_handler.setFormatter(
    RedactingFormatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
)
logging.basicConfig(level=logging.INFO, handlers=[_log_handler], force=True)
log = logging.getLogger(__name__)

# --- Palette -----------------------------------------------------------------
BG = "#0b0f14"
SURFACE = "#121820"
SURFACE_ALT = "#19222e"
SURFACE_HOVER = "#202c3a"
TEXT_BG = "#0d131b"
BORDER = "#24303e"
TEXT = "#e9eff7"
MUTED = "#8b98a9"
MUTED_DIM = "#647283"
ACCENT = "#6aa9ff"
ACCENT_HOVER = "#89bbff"
ACCENT_PRESSED = "#4e8de0"
ACCENT_SOFT = "#18314e"
SUCCESS = "#55d6a7"
WARNING = "#f0b45a"
ERROR = "#ff7b7b"
SELECT_BG = "#294b74"

AUDIO_EXTENSIONS = (
    "wav",
    "mp3",
    "m4a",
    "m4b",
    "aac",
    "flac",
    "ogg",
    "oga",
    "opus",
    "wma",
    "aiff",
    "aif",
    "amr",
    "ac3",
    "mp2",
    "mpga",
)
VIDEO_EXTENSIONS = (
    "mp4",
    "m4v",
    "mov",
    "mkv",
    "webm",
    "avi",
    "wmv",
    "flv",
    "mpeg",
    "mpg",
)

AUDIO_FILE_TYPES = [
    (
        "Audio and video",
        " ".join(f"*.{ext}" for ext in AUDIO_EXTENSIONS + VIDEO_EXTENSIONS),
    ),
    ("Audio files", " ".join(f"*.{ext}" for ext in AUDIO_EXTENSIONS)),
]

ALLOWED_EXTENSIONS = frozenset(AUDIO_EXTENSIONS + VIDEO_EXTENSIONS)

# Processed files (with their transcripts) are encrypted with Windows DPAPI.
HISTORY_VERSION = 1


@dataclass
class AudioJob:
    """One audio file in the library and its transcription state."""

    id: str
    path: Path
    status: str = "queued"  # queued | running | done | error | cancelled
    transcript: str = ""
    language: str | None = None
    duration: float | None = None
    model_key: str = DEFAULT_MODEL
    timestamps: bool = False
    words: int = 0
    error: str | None = None
    elapsed: float | None = None
    completed_at: float | None = None
    added_at: float = field(default_factory=time.time)
    size: int = 0

    @property
    def name(self) -> str:
        return self.path.name or str(self.path)

    @property
    def exists(self) -> bool:
        try:
            return self.path.is_file()
        except OSError:
            return False

    def to_record(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "path": str(self.path),
            "transcript": self.transcript,
            "language": self.language,
            "duration": self.duration,
            "model": self.model_key,
            "timestamps": self.timestamps,
            "words": self.words,
            "status": self.status,
            "error": self.error,
            "elapsed": self.elapsed,
            "completed_at": self.completed_at,
            "size": self.size,
        }

    @classmethod
    def from_record(cls, record: object) -> "AudioJob | None":
        if not isinstance(record, dict):
            return None
        path_value = record.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            return None
        status = record.get("status")
        if status not in ("done", "error", "cancelled"):
            return None

        transcript = record.get("transcript")
        if not isinstance(transcript, str):
            transcript = ""

        def number(key: str) -> float | None:
            value = record.get(key)
            return float(value) if isinstance(value, (int, float)) else None

        def text(key: str) -> str | None:
            value = record.get(key)
            return value if isinstance(value, str) and value else None

        job_id = record.get("id")
        if not isinstance(job_id, str) or not job_id:
            job_id = uuid.uuid4().hex

        words = record.get("words")
        if not isinstance(words, int) or words < 0:
            words = len(re.findall(r"\S+", transcript))

        model = record.get("model")
        if not isinstance(model, str) or not model:
            model = DEFAULT_MODEL

        completed_at = number("completed_at") or number("added_at")

        return cls(
            id=job_id,
            path=Path(path_value),
            status=status,
            transcript=transcript,
            language=text("language"),
            duration=number("duration"),
            model_key=model,
            timestamps=bool(record.get("timestamps")),
            words=words,
            error=text("error"),
            elapsed=number("elapsed"),
            completed_at=completed_at,
            added_at=number("added_at") or completed_at or time.time(),
            size=int(number("size") or 0),
        )



def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SenseMaker")
    except Exception:
        pass


def _apply_dark_titlebar(root: tk.Tk) -> None:
    if sys.platform != "win32":
        return
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        enabled = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)
            )
        # Match the Windows 11 title bar to the app surface.
        color = ctypes.c_int(0x0016110E)  # COLORREF for #0e1116
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 35, ctypes.byref(color), ctypes.sizeof(color)
        )
        text_color = ctypes.c_int(0x00F7EFE9)  # COLORREF for #e9eff7
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 36, ctypes.byref(text_color), ctypes.sizeof(text_color)
        )
    except Exception:
        log.debug("Could not apply dark window title bar", exc_info=True)


def _format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


class TranscriberApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self.engine = TranscriptionEngine()
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event: threading.Event | None = None
        self._busy = False
        self._closing = False
        self._jobs: list[AudioJob] = []
        self._jobs_by_id: dict[str, AudioJob] = {}
        self._selected_id: str | None = None
        self._running_id: str | None = None
        self._detected_language: str | None = None
        self._auto_language = True
        self._run_started = 0.0
        self._batch_total = 0
        self._device_label = ""
        self._last_dir = Path.home()
        self._copy_reset_job: str | None = None
        self._save_job: str | None = None
        self._poll_job: str | None = None
        self._pinned_selection = False
        self._text_editable = False
        self._placeholder_message = ""
        self._file_started = 0.0
        self._history = HistoryStore(DATA_DIR)
        self._retain_history = True
        self._history_error: str | None = None
        self._privacy_window: tk.Toplevel | None = None
        self._privacy_status_var = tk.StringVar(value="")
        self._load_settings()

        self.model_labels = [MODEL_LABELS[key] for key, _, _ in MODEL_OPTIONS]
        self.model_key_by_label = {MODEL_LABELS[key]: key for key, _, _ in MODEL_OPTIONS}
        self.language_codes = dict(LANGUAGE_OPTIONS)

        self.model_var = tk.StringVar(value=MODEL_LABELS[DEFAULT_MODEL])
        self.language_var = tk.StringVar(value="Auto-detect")
        self.timestamps_var = tk.BooleanVar(value=False)
        self.keep_history_var = tk.BooleanVar(value=self._retain_history)
        self.status_var = tk.StringVar(value="Add audio files to start.")
        self.count_var = tk.StringVar(value="0 words · 0 characters")
        self.badge_var = tk.StringVar(value="ON-DEVICE")
        self.library_count_var = tk.StringVar(value="0 files")
        self.transcript_title_var = tk.StringVar(value="No audio selected")
        self.transcript_meta_var = tk.StringVar(value="")

        self._configure_styles()
        self._build_ui()
        self._bind_shortcuts()
        self._load_history()
        if self._selected_id is None and self._jobs:
            self._selected_id = self._ordered_jobs()[0].id
        self._refresh_library()
        self._render_selected()

        self._set_status("Add audio files to start a batch transcription.", "muted")
        if self._history_error:
            self._set_status(self._history_error, "warning")
        self._set_badge("ON-DEVICE", "success")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_job = self.root.after(80, self._poll_events)

    # --- Layout helpers ------------------------------------------------------
    def px(self, value: float) -> int:
        return max(1, int(round(value * self.scale)))

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        body_font = ("Segoe UI", 10)
        style.configure(".", font=body_font, background=BG, foreground=TEXT)
        style.configure("TButton", font=("Segoe UI", 10, "bold"))

        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="#08121f",
            borderwidth=0,
            relief="flat",
            padding=(self.px(20), self.px(11)),
            focusthickness=0,
        )
        style.map(
            "Accent.TButton",
            background=[
                ("disabled", "#25313d"),
                ("pressed", ACCENT_PRESSED),
                ("active", ACCENT_HOVER),
            ],
            foreground=[("disabled", "#687586")],
        )

        style.configure(
            "SmallAccent.TButton",
            background=ACCENT,
            foreground="#08121f",
            borderwidth=0,
            relief="flat",
            padding=(self.px(14), self.px(7)),
            focusthickness=0,
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "SmallAccent.TButton",
            background=[
                ("disabled", "#26323f"),
                ("pressed", ACCENT_PRESSED),
                ("active", ACCENT_HOVER),
            ],
            foreground=[("disabled", "#687586")],
        )

        for name, padding in (
            ("Secondary.TButton", (self.px(16), self.px(10))),
            ("SmallSecondary.TButton", (self.px(12), self.px(7))),
        ):
            style.configure(
                name,
                background=SURFACE_ALT,
                foreground=TEXT,
                borderwidth=1,
                bordercolor=BORDER,
                lightcolor=SURFACE_ALT,
                darkcolor=SURFACE_ALT,
                relief="flat",
                padding=padding,
                focusthickness=0,
                font=("Segoe UI", 9 if name.startswith("Small") else 10, "bold"),
            )
            style.map(
                name,
                background=[
                    ("disabled", SURFACE),
                    ("pressed", SURFACE),
                    ("active", SURFACE_HOVER),
                ],
                foreground=[("disabled", MUTED_DIM)],
                bordercolor=[("active", "#334456")],
            )

        style.configure(
            "Dark.TCombobox",
            fieldbackground=SURFACE_ALT,
            background=SURFACE_ALT,
            foreground=TEXT,
            arrowcolor=MUTED,
            bordercolor=BORDER,
            lightcolor=SURFACE_ALT,
            darkcolor=SURFACE_ALT,
            borderwidth=1,
            relief="flat",
            padding=(self.px(10), self.px(8)),
            selectbackground=SURFACE_ALT,
            selectforeground=TEXT,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[
                ("readonly", SURFACE_ALT),
                ("disabled", SURFACE),
                ("focus", SURFACE_ALT),
            ],
            foreground=[
                ("readonly", TEXT),
                ("disabled", MUTED_DIM),
                ("focus", TEXT),
            ],
            bordercolor=[("focus", ACCENT), ("disabled", BORDER)],
            arrowcolor=[("disabled", MUTED_DIM)],
            selectbackground=[("readonly", SURFACE_ALT)],
            selectforeground=[("readonly", TEXT)],
        )

        style.configure(
            "Accent.Horizontal.TProgressbar",
            troughcolor=SURFACE_ALT,
            background=ACCENT,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            bordercolor=SURFACE_ALT,
            borderwidth=0,
            thickness=self.px(5),
        )

        style.configure(
            "Vertical.TScrollbar",
            background="#34465a",
            troughcolor=TEXT_BG,
            bordercolor=TEXT_BG,
            darkcolor="#34465a",
            lightcolor="#34465a",
            arrowcolor=MUTED,
            borderwidth=0,
            arrowsize=self.px(11),
            gripcount=0,
            width=self.px(10),
        )
        style.map(
            "Vertical.TScrollbar",
            background=[("active", MUTED_DIM), ("pressed", MUTED)],
        )
        # A thumb-only scrollbar looks cleaner than the clam theme's arrows.
        style.layout(
            "Vertical.TScrollbar",
            [
                (
                    "Vertical.Scrollbar.trough",
                    {
                        "sticky": "ns",
                        "children": [
                            (
                                "Vertical.Scrollbar.thumb",
                                {"expand": "1", "sticky": "nswe"},
                            )
                        ],
                    },
                )
            ],
        )

        style.configure(
            "Dark.TCheckbutton",
            background=SURFACE,
            foreground=TEXT,
            font=("Segoe UI", 9),
            indicatorsize=self.px(14),
            indicatormargin=(0, self.px(2), self.px(9), self.px(2)),
            indicatorbackground=SURFACE_ALT,
            indicatorforeground="#08121f",
            upperbordercolor="#3b4c60",
            lowerbordercolor="#3b4c60",
            focuscolor=SURFACE,
            focusthickness=0,
            padding=0,
        )
        style.map(
            "Dark.TCheckbutton",
            background=[("active", SURFACE)],
            foreground=[("disabled", MUTED_DIM), ("active", TEXT)],
            indicatorbackground=[
                ("selected", ACCENT),
                ("disabled", SURFACE_ALT),
                ("active", SURFACE_HOVER),
            ],
            indicatorforeground=[("selected", "#08121f"), ("disabled", MUTED_DIM)],
            upperbordercolor=[("selected", ACCENT), ("focus", ACCENT)],
            lowerbordercolor=[("selected", ACCENT)],
        )

        self.root.option_add("*TCombobox*Listbox.background", SURFACE_ALT)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_SOFT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        self.root.option_add("*TCombobox*Listbox.font", "Segoe UI 10")
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)

    def _card(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=SURFACE,
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=BORDER,
        )

    def _draw_logo(self, canvas: tk.Canvas) -> None:
        size = self.px(46)
        c = canvas
        c.create_oval(
            self.px(2),
            self.px(2),
            size - self.px(2),
            size - self.px(2),
            fill="#102338",
            outline=ACCENT,
            width=max(1, self.px(1.3)),
        )
        heights = [0.24, 0.46, 0.66, 0.46, 0.26]
        colors = ["#6aa9ff", "#8ab8ff", "#c0d6ff", "#8ab8ff", "#6aa9ff"]
        gap = size * 0.12
        start = size * 0.30
        for index, (fraction, color) in enumerate(zip(heights, colors)):
            x = start + gap * index
            length = size * fraction
            c.create_line(
                x,
                size / 2 - length / 2,
                x,
                size / 2 + length / 2,
                fill=color,
                width=max(2, self.px(3.2)),
                capstyle="round",
            )

    def _build_ui(self) -> None:
        root = self.root
        root.configure(bg=BG)
        root.grid_rowconfigure(0, weight=1)
        root.grid_columnconfigure(0, weight=1)

        main = tk.Frame(root, bg=BG)
        main.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=self.px(28),
            pady=self.px(22),
        )
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        self._build_header(main)

        content = tk.Frame(main, bg=BG)
        content.grid(row=1, column=0, sticky="nsew")
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, minsize=self.px(322))
        content.grid_columnconfigure(1, weight=1)

        self._build_library_card(content)

        right = tk.Frame(content, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)

        self._build_settings_card(right)
        self._build_action_bar(right)
        self._build_transcript_card(right)

        self._position_window()

    def _position_window(self) -> None:
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        # Leave room for the title bar and the taskbar so the window never
        # extends below the usable desktop.
        width = min(self.px(1220), screen_w - self.px(80))
        height = min(self.px(880), screen_h - self.px(110))
        min_width = min(self.px(980), screen_w - self.px(60))
        min_height = min(self.px(660), screen_h - self.px(110))
        width = max(width, min_width)
        height = max(height, min_height)
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - self.px(8))
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min_width, min_height)

    def _build_header(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg=BG)
        header.grid(row=0, column=0, sticky="ew", pady=(0, self.px(20)))
        header.grid_columnconfigure(1, weight=1)

        size = self.px(46)
        logo = tk.Canvas(
            header,
            width=size,
            height=size,
            bg=BG,
            highlightthickness=0,
            bd=0,
        )
        logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, self.px(14)))
        self._draw_logo(logo)

        tk.Label(
            header,
            text="SenseMaker",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 24, "bold"),
            anchor="w",
        ).grid(row=0, column=1, sticky="sw")
        tk.Label(
            header,
            text="Private, on-device audio transcription",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(row=1, column=1, sticky="nw", pady=(self.px(1), 0))

        badge = tk.Frame(
            header,
            bg=SURFACE,
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=BORDER,
        )
        badge.grid(row=0, column=2, rowspan=2, sticky="e")
        badge_inner = tk.Frame(badge, bg=SURFACE)
        badge_inner.pack(padx=self.px(12), pady=self.px(7))

        dot_size = self.px(7)
        self.badge_dot = tk.Canvas(
            badge_inner,
            width=dot_size,
            height=dot_size,
            bg=SURFACE,
            highlightthickness=0,
            bd=0,
        )
        self.badge_dot.pack(side="left", padx=(0, self.px(7)))
        self._badge_dot_id = self.badge_dot.create_oval(
            0, 0, dot_size, dot_size, fill=SUCCESS, outline=""
        )
        self.badge_label = tk.Label(
            badge_inner,
            textvariable=self.badge_var,
            bg=SURFACE,
            fg=SUCCESS,
            font=("Segoe UI", 8, "bold"),
        )
        self.badge_label.pack(side="left")

    def _build_library_card(self, parent: tk.Frame) -> None:
        card = self._card(parent)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, self.px(14)))

        header = tk.Frame(card, bg=SURFACE)
        header.pack(fill="x", padx=self.px(18), pady=(self.px(15), self.px(11)))
        tk.Label(
            header,
            text="AUDIO LIBRARY",
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            textvariable=self.library_count_var,
            bg=SURFACE,
            fg=MUTED_DIM,
            font=("Segoe UI", 8),
        ).pack(side="left", padx=(self.px(10), 0))

        self.add_btn = ttk.Button(
            card,
            text="Add audio files",
            style="SmallAccent.TButton",
            command=self.choose_files,
            cursor="hand2",
        )
        self.add_btn.pack(fill="x", padx=self.px(18), pady=(0, self.px(12)))

        list_wrap = tk.Frame(card, bg=SURFACE)
        list_wrap.pack(
            fill="both",
            expand=True,
            padx=(self.px(8), self.px(6)),
            pady=(0, self.px(4)),
        )
        list_wrap.grid_rowconfigure(0, weight=1)
        list_wrap.grid_columnconfigure(0, weight=1)

        self.list_canvas = tk.Canvas(
            list_wrap,
            bg=SURFACE,
            highlightthickness=0,
            bd=0,
            width=self.px(250),
        )
        self.list_canvas.grid(row=0, column=0, sticky="nsew")
        self.list_scroll = ttk.Scrollbar(
            list_wrap,
            orient="vertical",
            command=self.list_canvas.yview,
            style="Vertical.TScrollbar",
        )
        self.list_scroll.grid(row=0, column=1, sticky="ns", padx=(self.px(4), 0))
        self.list_canvas.configure(yscrollcommand=self.list_scroll.set)

        self.library_list = tk.Frame(self.list_canvas, bg=SURFACE)
        self._library_window = self.list_canvas.create_window(
            (0, 0), window=self.library_list, anchor="nw"
        )
        self.library_list.bind("<Configure>", self._on_library_configure)
        self.list_canvas.bind("<Configure>", self._on_library_canvas_configure)

        footer = tk.Frame(card, bg=SURFACE)
        footer.pack(fill="x", padx=self.px(18), pady=(self.px(4), self.px(12)))
        self.storage_label = tk.Label(
            footer,
            text=("Saved locally · encrypted" if self._retain_history else "History off"),
            bg=SURFACE,
            fg=MUTED_DIM,
            font=("Segoe UI", 8),
        )
        self.storage_label.pack(side="left")
        folder_link = tk.Label(
            footer,
            text="Open folder",
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 8, "underline"),
            cursor="hand2",
        )
        folder_link.pack(side="right")
        folder_link.bind("<Button-1>", lambda _event: self._open_data_folder())
        privacy_link = tk.Label(
            footer,
            text="Privacy",
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 8, "underline"),
            cursor="hand2",
        )
        privacy_link.pack(side="right", padx=(0, self.px(12)))
        privacy_link.bind("<Button-1>", lambda _event: self._open_privacy_dialog())

        self.root.bind_all("<MouseWheel>", self._on_mouse_wheel, add="+")

    def _on_library_configure(self, _event: tk.Event | None = None) -> None:
        self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all"))

    def _on_library_canvas_configure(self, event: tk.Event) -> None:
        self.list_canvas.itemconfigure(self._library_window, width=event.width)

    def _on_mouse_wheel(self, event: tk.Event) -> None:
        """Scroll the library only while the pointer is over it."""
        try:
            x, y = self.root.winfo_pointerxy()
            left = self.list_canvas.winfo_rootx()
            top = self.list_canvas.winfo_rooty()
            if not (
                left <= x <= left + self.list_canvas.winfo_width()
                and top <= y <= top + self.list_canvas.winfo_height()
            ):
                return
        except (tk.TclError, AttributeError):
            return
        if self.library_list.winfo_reqheight() <= self.list_canvas.winfo_height():
            return
        step = int(-event.delta / 120) if event.delta else -1
        self.list_canvas.yview_scroll(step * 2, "units")

    def _open_data_folder(self) -> None:
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(DATA_DIR))  # type: ignore[attr-defined]
            else:
                import subprocess

                subprocess.Popen(["xdg-open", str(DATA_DIR)])
        except Exception:
            log.exception("Could not open the data folder")
            self._set_status(f"Could not open the data folder:\n{DATA_DIR}", "error")

    # --- Privacy -------------------------------------------------------------
    def _update_storage_label(self) -> None:
        if hasattr(self, "storage_label"):
            self.storage_label.configure(
                text=(
                    "Saved locally · encrypted"
                    if self._retain_history
                    else "History off"
                )
            )

    def _privacy_status_text(self) -> str:
        if self._retain_history:
            return (
                "History is on. Saved transcripts are encrypted for this Windows "
                "account with Windows DPAPI."
            )
        return "History is off. Transcripts will not be saved between sessions."

    def _notice_path(self, filename: str) -> Path | None:
        candidates: list[Path] = []
        if getattr(sys, "frozen", False):
            candidates.append(Path(sys.executable).resolve().parent / filename)
        candidates.append(RESOURCE_DIR / filename)
        candidates.append(Path(__file__).resolve().parent / filename)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _open_document(self, filename: str, fallback: str) -> None:
        path = self._notice_path(filename)
        if path is None:
            messagebox.showinfo(
                filename,
                fallback,
                parent=self._privacy_window or self.root,
            )
            return
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception:
            log.exception("Could not open %s", filename)
            self._privacy_status_var.set(f"Could not open {filename}.")

    def _open_privacy_notice(self) -> None:
        self._open_document(
            "PRIVACY.md",
            "The privacy notice is not available in this build. "
            "SenseMaker processes audio locally and does not upload it.",
        )

    def _open_third_party_notices(self) -> None:
        self._open_document(
            "THIRD_PARTY_NOTICES.md",
            "The third-party notices are not available in this build.",
        )

    def _open_privacy_dialog(self) -> None:
        if self._privacy_window is not None and self._privacy_window.winfo_exists():
            self._privacy_window.lift()
            self._privacy_window.focus_force()
            return

        win = tk.Toplevel(self.root)
        win.title("Privacy & history")
        win.configure(bg=BG)
        win.transient(self.root)
        win.resizable(False, False)
        win.grab_set()
        try:
            icon = getattr(self.root, "_app_icon", None)
            if icon is not None:
                win.iconphoto(False, icon)
        except tk.TclError:
            pass

        card = tk.Frame(
            win,
            bg=SURFACE,
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=BORDER,
        )
        card.pack(fill="both", expand=True, padx=self.px(20), pady=self.px(20))

        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=self.px(22), pady=self.px(20))

        tk.Label(
            inner,
            text="Privacy & history",
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI", 16, "bold"),
            anchor="w",
        ).pack(fill="x")

        tk.Label(
            inner,
            text=(
                "SenseMaker processes audio locally. Your recordings and transcripts "
                "are not uploaded. Model files are downloaded from Hugging Face; those "
                "requests include network metadata and the model name, but never audio "
                "or transcript content.\n\n"
                "When history is on, completed transcripts and file references are "
                "encrypted with Windows DPAPI and readable by this Windows account. "
                "Logs are limited in size and directory paths are redacted."
            ),
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 9),
            justify="left",
            anchor="w",
            wraplength=self.px(560),
        ).pack(fill="x", pady=(self.px(12), self.px(16)))

        self._privacy_status_var.set(self._privacy_status_text())
        self.privacy_history_check = ttk.Checkbutton(
            inner,
            text="Keep transcript history on this PC",
            variable=self.keep_history_var,
            style="Dark.TCheckbutton",
            command=self._on_history_retention_toggled,
            cursor="hand2",
        )
        self.privacy_history_check.pack(anchor="w")

        tk.Label(
            inner,
            textvariable=self._privacy_status_var,
            bg=SURFACE,
            fg=MUTED_DIM,
            font=("Segoe UI", 8),
            justify="left",
            anchor="w",
            wraplength=self.px(540),
        ).pack(fill="x", pady=(self.px(7), self.px(16)))

        tk.Label(
            inner,
            text=(
                "The Copy button uses the Windows clipboard. Clipboard history or "
                "cloud clipboard sync may retain copied transcripts."
            ),
            bg=SURFACE,
            fg=MUTED_DIM,
            font=("Segoe UI", 8),
            justify="left",
            anchor="w",
            wraplength=self.px(540),
        ).pack(fill="x", pady=(0, self.px(16)))

        buttons = tk.Frame(inner, bg=SURFACE)
        buttons.pack(fill="x")
        ttk.Button(
            buttons,
            text="Clear all history",
            style="SmallSecondary.TButton",
            command=self._clear_all_history,
            cursor="hand2",
        ).pack(side="left")
        ttk.Button(
            buttons,
            text="Full notice",
            style="SmallSecondary.TButton",
            command=self._open_privacy_notice,
            cursor="hand2",
        ).pack(side="left", padx=(self.px(8), 0))
        ttk.Button(
            buttons,
            text="Licenses",
            style="SmallSecondary.TButton",
            command=self._open_third_party_notices,
            cursor="hand2",
        ).pack(side="left", padx=(self.px(8), 0))
        ttk.Button(
            buttons,
            text="Close",
            style="SmallAccent.TButton",
            command=self._close_privacy_dialog,
            cursor="hand2",
        ).pack(side="right")

        self._privacy_window = win
        win.protocol("WM_DELETE_WINDOW", self._close_privacy_dialog)
        win.bind("<Escape>", lambda _event: self._close_privacy_dialog())
        self.root.update_idletasks()
        win.update_idletasks()
        width = win.winfo_reqwidth()
        height = win.winfo_reqheight()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 2)
        win.geometry(f"+{x}+{y}")
        try:
            _apply_dark_titlebar(win)
        except Exception:
            pass
        win.focus_force()

    def _close_privacy_dialog(self) -> None:
        if self._privacy_window is not None:
            try:
                self._privacy_window.grab_release()
            except tk.TclError:
                pass
            try:
                self._privacy_window.destroy()
            except tk.TclError:
                pass
            self._privacy_window = None

    def _on_history_retention_toggled(self) -> None:
        requested = bool(self.keep_history_var.get())
        if requested == self._retain_history:
            return

        if not requested:
            if not messagebox.askyesno(
                "Privacy & history",
                "Turn off transcript history?\n\n"
                "This deletes saved transcripts from this computer and stops saving "
                "new transcripts. Your current session remains available until you "
                "close the app or clear it. Original audio files are not touched.",
                parent=self._privacy_window or self.root,
            ):
                self.keep_history_var.set(True)
                return
            self._retain_history = False
            self._history.clear()
            self._save_settings()
            self._update_storage_label()
            self._privacy_status_var.set(self._privacy_status_text())
            self._set_status("History turned off and saved transcripts deleted.", "muted")
        else:
            self._retain_history = True
            self._save_settings()
            self._save_history()
            self._update_storage_label()
            self._privacy_status_var.set(self._privacy_status_text())
            self._set_status("History turned on. Encrypted history will be saved.", "muted")

    def _clear_all_history(self) -> None:
        if self._busy:
            messagebox.showinfo(
                "Privacy & history",
                "Wait for the current transcription to finish before clearing history.",
                parent=self._privacy_window or self.root,
            )
            return
        if not self._jobs and not self._history.exists():
            self._privacy_status_var.set("There is no saved transcript history.")
            return
        if not messagebox.askyesno(
            "Privacy & history",
            "Delete every transcript in the library and all saved history?\n\n"
            "Original audio files are not touched.",
            parent=self._privacy_window or self.root,
        ):
            return
        self._commit_edit()
        self._jobs.clear()
        self._jobs_by_id.clear()
        self._selected_id = None
        self._running_id = None
        self._pinned_selection = False
        self._history.clear()
        self._refresh_library()
        self._render_selected()
        self._privacy_status_var.set("All transcript history was deleted.")
        self._set_status("All transcript history was deleted.", "success")

    # --- Library rows --------------------------------------------------------
    def _ordered_jobs(self) -> list[AudioJob]:
        running = [job for job in self._jobs if job.status == "running"]
        queued = sorted(
            (job for job in self._jobs if job.status == "queued"),
            key=lambda job: job.added_at,
        )
        finished = sorted(
            (job for job in self._jobs if job.status not in ("running", "queued")),
            key=lambda job: job.completed_at or job.added_at,
            reverse=True,
        )
        return running + queued + finished

    def _job_dot_color(self, status: str) -> str:
        return {
            "queued": MUTED_DIM,
            "running": ACCENT,
            "done": SUCCESS,
            "error": ERROR,
            "cancelled": WARNING,
        }.get(status, MUTED_DIM)

    def _refresh_library(self) -> None:
        for child in self.library_list.winfo_children():
            child.destroy()

        jobs = self._ordered_jobs()
        total = len(jobs)
        self.library_count_var.set(
            "0 files" if total == 0 else f"{total} file{'s' if total != 1 else ''}"
        )

        if not jobs:
            tk.Label(
                self.library_list,
                text="No audio yet.\nAdd files to transcribe them.",
                bg=SURFACE,
                fg=MUTED_DIM,
                font=("Segoe UI", 9),
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=self.px(10), pady=self.px(16))
        else:
            for job in jobs:
                self._build_job_row(job)

        self._update_action_button()
        self._on_library_configure()

    def _build_job_row(self, job: AudioJob) -> None:
        selected = job.id == self._selected_id
        base_bg = SELECT_BG if selected else SURFACE
        hover_bg = "#34567f" if selected else SURFACE_HOVER

        row = tk.Frame(self.library_list, bg=base_bg, cursor="hand2")
        row.pack(fill="x", pady=self.px(1))
        accent = tk.Frame(row, width=self.px(3), bg=self._job_dot_color(job.status))
        accent.pack(side="left", fill="y")
        body = tk.Frame(row, bg=base_bg)
        body.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(self.px(10), self.px(8)),
            pady=self.px(7),
        )

        title = tk.Label(
            body,
            text=self._shorten_name(job.name),
            bg=base_bg,
            fg=TEXT,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
            justify="left",
        )
        title.pack(fill="x")
        meta = tk.Label(
            body,
            text=self._job_meta(job),
            bg=base_bg,
            fg=MUTED,
            font=("Segoe UI", 8),
            anchor="w",
            justify="left",
        )
        meta.pack(fill="x")

        paintable = (row, body, title, meta)
        for widget in paintable:
            widget.bind(
                "<Button-1>", lambda _event, job_id=job.id: self.select_job(job_id)
            )
            widget.bind(
                "<Enter>",
                lambda _event, widgets=paintable, color=hover_bg: self._paint_row(
                    widgets, color
                ),
            )
            widget.bind(
                "<Leave>",
                lambda _event, widgets=paintable, color=base_bg: self._paint_row(
                    widgets, color
                ),
            )

    @staticmethod
    def _paint_row(widgets: tuple[tk.Widget, ...], color: str) -> None:
        for widget in widgets:
            try:
                widget.configure(bg=color)
            except tk.TclError:
                pass

    @staticmethod
    def _shorten_name(name: str, limit: int = 34) -> str:
        if len(name) <= limit:
            return name
        stem, suffix = os.path.splitext(name)
        keep = max(4, limit - len(suffix) - 1)
        return f"{stem[:keep]}…{suffix}"

    def _job_meta(self, job: AudioJob) -> str:
        label = {
            "queued": "Queued",
            "running": "Transcribing",
            "done": "Done",
            "error": "Failed",
            "cancelled": "Cancelled",
        }.get(job.status, job.status.title())
        bits = [label]

        if job.status == "queued":
            if job.size:
                bits.append(format_bytes(job.size))
        else:
            if job.words:
                bits.append(f"{job.words:,} words")
            if job.duration:
                bits.append(format_duration(job.duration))
            when = self._format_when(job.completed_at)
            if when:
                bits.append(when)
            if not job.exists:
                bits.append("file missing")

        return " · ".join(bits)

    @staticmethod
    def _format_when(timestamp: float | None) -> str:
        if not timestamp:
            return ""
        try:
            moment = time.localtime(timestamp)
            now = time.localtime()
        except (ValueError, OSError, OverflowError):
            return ""
        if (moment.tm_year, moment.tm_yday) == (now.tm_year, now.tm_yday):
            return time.strftime("%H:%M", moment)
        if moment.tm_year == now.tm_year:
            return time.strftime("%b %d", moment)
        return time.strftime("%b %d, %Y", moment)

    def _selected_job(self) -> AudioJob | None:
        if self._selected_id is None:
            return None
        return self._jobs_by_id.get(self._selected_id)

    def select_job(self, job_id: str) -> None:
        if job_id == self._selected_id:
            return
        job = self._jobs_by_id.get(job_id)
        if job is None:
            return
        self._commit_edit()
        self._selected_id = job_id
        if self._busy:
            self._pinned_selection = True
        self._render_selected()
        self._refresh_library()

    def _auto_select(self, job_id: str) -> None:
        if self._pinned_selection:
            return
        self._selected_id = job_id
        self._render_selected()
        self._refresh_library()

    # --- Settings and persistence -------------------------------------------
    def _load_settings(self) -> None:
        try:
            payload = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception:
            log.warning("Could not read privacy settings; using defaults", exc_info=True)
            return
        if isinstance(payload, dict):
            self._retain_history = bool(payload.get("keep_history", True))

    def _save_settings(self) -> None:
        try:
            atomic_write_json(
                SETTINGS_PATH,
                {"version": 1, "keep_history": self._retain_history},
            )
        except Exception:
            log.exception("Could not save privacy settings")
            self._set_status("Could not save the privacy setting.", "error")

    def _load_history(self) -> None:
        if not self._retain_history:
            self._history.clear()
            return
        try:
            payload = self._history.read()
        except SecureStoreError as exc:
            self._history_error = str(exc)
            log.warning("Could not open transcript history: %s", exc)
            return
        except Exception as exc:
            self._history_error = "Saved transcript history could not be opened."
            log.exception("Unexpected history read failure")
            return
        if payload is None:
            return

        records = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            log.warning("Transcript history has an unexpected format; ignoring it.")
            return

        loaded = 0
        for record in records:
            job = AudioJob.from_record(record)
            if job is None or job.id in self._jobs_by_id:
                continue
            self._jobs.append(job)
            self._jobs_by_id[job.id] = job
            loaded += 1
        log.info("Loaded %d processed audio entries from encrypted history", loaded)

    def _schedule_history_save(self) -> None:
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except tk.TclError:
                pass
        self._save_job = self.root.after(700, self._save_history)

    def _save_history(self) -> None:
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except tk.TclError:
                pass
        self._save_job = None
        if not self._retain_history:
            self._history.clear()
            return
        entries = [
            job.to_record()
            for job in self._ordered_jobs()
            if job.status in ("done", "error", "cancelled")
        ]
        payload = {"version": HISTORY_VERSION, "entries": entries}
        try:
            self._history.write(payload)
        except Exception:
            log.exception("Could not encrypt and save transcript history")
            self._set_status("Could not save encrypted transcript history.", "error")

    def _build_settings_card(self, parent: tk.Frame) -> None:
        card = self._card(parent)
        card.grid(row=0, column=0, sticky="ew", pady=(0, self.px(12)))
        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=self.px(20), pady=self.px(16))
        inner.grid_columnconfigure(0, weight=1)
        inner.grid_columnconfigure(1, weight=1)
        inner.grid_columnconfigure(2, weight=0)

        label_opts = {
            "bg": SURFACE,
            "fg": MUTED,
            "font": ("Segoe UI", 8, "bold"),
            "anchor": "w",
        }
        tk.Label(inner, text="MODEL", **label_opts).grid(
            row=0, column=0, sticky="w", padx=(0, self.px(12))
        )
        tk.Label(inner, text="LANGUAGE", **label_opts).grid(
            row=0, column=1, sticky="w", padx=(0, self.px(12))
        )
        tk.Label(inner, text="OUTPUT", **label_opts).grid(
            row=0, column=2, sticky="w", padx=(self.px(8), 0)
        )

        self.model_combo = ttk.Combobox(
            inner,
            textvariable=self.model_var,
            values=self.model_labels,
            state="readonly",
            style="Dark.TCombobox",
            font=("Segoe UI", 10),
            height=8,
        )
        self.model_combo.grid(
            row=1, column=0, sticky="ew", padx=(0, self.px(12)), pady=(self.px(6), 0)
        )

        self.language_combo = ttk.Combobox(
            inner,
            textvariable=self.language_var,
            values=[label for label, _ in LANGUAGE_OPTIONS],
            state="readonly",
            style="Dark.TCombobox",
            font=("Segoe UI", 10),
            height=10,
        )
        self.language_combo.grid(
            row=1, column=1, sticky="ew", padx=(0, self.px(12)), pady=(self.px(6), 0)
        )

        self.timestamps_check = ttk.Checkbutton(
            inner,
            text="Include timestamps",
            variable=self.timestamps_var,
            style="Dark.TCheckbutton",
            cursor="hand2",
        )
        self.timestamps_check.grid(
            row=1,
            column=2,
            sticky="w",
            padx=(self.px(8), 0),
            pady=(self.px(6), 0),
        )

        tk.Label(
            inner,
            text=(
                "Models download once and then run locally. Larger models are "
                "slower but more accurate."
            ),
            bg=SURFACE,
            fg=MUTED_DIM,
            font=("Segoe UI", 9),
            anchor="w",
        ).grid(
            row=2,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(self.px(10), 0),
        )

    def _build_action_bar(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=BG)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, self.px(6)))
        bar.grid_columnconfigure(2, weight=1)

        self.transcribe_btn = ttk.Button(
            bar,
            text="Transcribe audio",
            style="Accent.TButton",
            command=self.start_transcription,
            cursor="hand2",
            state=["disabled"],
        )
        self.transcribe_btn.grid(row=0, column=0, sticky="w")

        self.cancel_btn = ttk.Button(
            bar,
            text="Cancel",
            style="Secondary.TButton",
            command=self.cancel_transcription,
            cursor="hand2",
        )
        self.cancel_btn.grid(row=0, column=1, padx=(self.px(10), 0), sticky="w")
        self.cancel_btn.grid_remove()

        tk.Label(
            bar,
            text="Ctrl + Enter",
            bg=BG,
            fg=MUTED_DIM,
            font=("Segoe UI", 8),
        ).grid(row=0, column=3, sticky="e")

        self.progress = ttk.Progressbar(
            bar,
            orient="horizontal",
            mode="determinate",
            maximum=100,
            value=0,
            style="Accent.Horizontal.TProgressbar",
            length=self.px(400),
        )
        self.progress.grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=(self.px(12), self.px(9))
        )

        status_row = tk.Frame(bar, bg=BG)
        status_row.grid(row=2, column=0, columnspan=4, sticky="ew")
        dot = self.px(8)
        self.status_dot = tk.Canvas(
            status_row,
            width=dot,
            height=dot,
            bg=BG,
            highlightthickness=0,
            bd=0,
        )
        self.status_dot.pack(side="left", padx=(self.px(1), self.px(8)))
        self._status_dot_id = self.status_dot.create_oval(
            0, 0, dot, dot, fill=MUTED_DIM, outline=""
        )
        self.status_label = tk.Label(
            status_row,
            textvariable=self.status_var,
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
        )
        self.status_label.pack(side="left", fill="x", expand=True)

    def _build_transcript_card(self, parent: tk.Frame) -> None:
        card = self._card(parent)
        card.grid(row=2, column=0, sticky="nsew")
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)

        inner = tk.Frame(card, bg=SURFACE)
        inner.grid(row=0, column=0, sticky="nsew")
        inner.grid_rowconfigure(1, weight=1)
        inner.grid_columnconfigure(0, weight=1)

        header = tk.Frame(inner, bg=SURFACE)
        header.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=self.px(20),
            pady=(self.px(14), self.px(10)),
        )
        header.grid_columnconfigure(0, weight=1)

        top = tk.Frame(header, bg=SURFACE)
        top.grid(row=0, column=0, sticky="ew")

        tk.Label(
            top,
            text="TRANSCRIPT",
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left")
        tk.Label(
            top,
            textvariable=self.count_var,
            bg=SURFACE,
            fg=MUTED_DIM,
            font=("Segoe UI", 8),
        ).pack(side="left", padx=(self.px(12), 0))

        self.copy_btn = ttk.Button(
            top,
            text="Copy",
            style="SmallAccent.TButton",
            command=self.copy_transcript,
            cursor="hand2",
            state=["disabled"],
            width=7,
        )
        self.copy_btn.pack(side="right")

        self.remove_btn = ttk.Button(
            top,
            text="Remove",
            style="SmallSecondary.TButton",
            command=self.remove_selected,
            cursor="hand2",
            state=["disabled"],
            width=8,
        )
        self.remove_btn.pack(side="right", padx=(0, self.px(8)))

        self.retry_btn = ttk.Button(
            top,
            text="Retry",
            style="SmallSecondary.TButton",
            command=self.retry_selected,
            cursor="hand2",
            state=["disabled"],
            width=8,
        )
        # Packed/unpacked by _update_transcript_buttons().

        self.transcript_title_label = tk.Label(
            header,
            textvariable=self.transcript_title_var,
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
            justify="left",
        )
        self.transcript_title_label.grid(
            row=1, column=0, sticky="w", pady=(self.px(9), 0)
        )

        self.transcript_meta_label = tk.Label(
            header,
            textvariable=self.transcript_meta_var,
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
            justify="left",
        )
        self.transcript_meta_label.grid(row=2, column=0, sticky="w", pady=(self.px(2), 0))
        header.bind("<Configure>", self._on_transcript_header_resize)

        text_wrap = tk.Frame(inner, bg=SURFACE)
        text_wrap.grid(
            row=1,
            column=0,
            sticky="nsew",
            padx=self.px(20),
            pady=(0, self.px(20)),
        )
        text_wrap.grid_rowconfigure(0, weight=1)
        text_wrap.grid_columnconfigure(0, weight=1)

        self.text = tk.Text(
            text_wrap,
            wrap="word",
            undo=True,
            maxundo=200,
            bg=TEXT_BG,
            fg=TEXT,
            insertbackground=TEXT,
            insertwidth=max(1, self.px(1.5)),
            selectbackground=SELECT_BG,
            selectforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            padx=self.px(16),
            pady=self.px(14),
            font=("Segoe UI", 11),
            spacing1=self.px(1),
            spacing2=self.px(1),
            spacing3=self.px(3),
            state="disabled",
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        self.text.tag_configure(
            "body",
            spacing1=self.px(1),
            spacing3=self.px(5),
            lmargin1=0,
            lmargin2=0,
        )
        self.text.tag_configure("error", foreground=ERROR)

        scrollbar = ttk.Scrollbar(
            text_wrap,
            orient="vertical",
            command=self.text.yview,
            style="Vertical.TScrollbar",
        )
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(self.px(7), 0))
        self.text.configure(yscrollcommand=scrollbar.set)

        self.placeholder = tk.Label(
            text_wrap,
            text="",
            bg=TEXT_BG,
            fg=MUTED_DIM,
            font=("Segoe UI", 11),
            justify="left",
            anchor="nw",
        )
        self.placeholder.bind("<Button-1>", self._focus_text)
        self.text.bind("<FocusIn>", lambda _event: self._refresh_placeholder())
        self.text.bind("<FocusOut>", lambda _event: self._refresh_placeholder())
        self.text.bind("<KeyRelease>", self._on_text_edited)
        self.text.bind("<<Paste>>", lambda _event: self.root.after(1, self._on_text_edited))
        self.text.bind("<<Cut>>", lambda _event: self.root.after(1, self._on_text_edited))
        self.text.bind("<Configure>", self._on_text_configure)

    def _on_transcript_header_resize(self, event: tk.Event) -> None:
        width = max(self.px(200), event.width - self.px(6))
        self.transcript_title_label.configure(wraplength=width)
        self.transcript_meta_label.configure(wraplength=width)

    def _on_text_configure(self, event: tk.Event) -> None:
        self.placeholder.configure(wraplength=max(self.px(220), event.width - self.px(32)))

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-o>", self._shortcut_open)
        self.root.bind("<Control-Return>", self._shortcut_transcribe)
        self.root.bind("<Control-Shift-C>", self._shortcut_copy)

    # --- Window / status -----------------------------------------------------
    def _set_status(self, message: str, tone: str = "muted") -> None:
        colors = {
            "muted": MUTED,
            "accent": ACCENT,
            "success": SUCCESS,
            "warning": WARNING,
            "error": ERROR,
        }
        dot_colors = {
            "muted": MUTED_DIM,
            "accent": ACCENT,
            "success": SUCCESS,
            "warning": WARNING,
            "error": ERROR,
        }
        self.status_var.set(message)
        self.status_label.configure(fg=colors.get(tone, MUTED))
        self.status_dot.itemconfigure(
            self._status_dot_id, fill=dot_colors.get(tone, MUTED_DIM)
        )

    def _set_badge(self, text: str, tone: str = "success") -> None:
        colors = {"success": SUCCESS, "warning": WARNING, "error": ERROR, "muted": MUTED}
        color = colors.get(tone, SUCCESS)
        self.badge_var.set(text)
        self.badge_label.configure(fg=color)
        self.badge_dot.itemconfigure(self._badge_dot_id, fill=color)

    def _start_indeterminate(self) -> None:
        self.progress.stop()
        self.progress.configure(mode="indeterminate", maximum=100, value=0)
        self.progress.start(12)

    def _set_progress(self, percent: float) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100)
        self.progress["value"] = max(0.0, min(100.0, percent))

    def _reset_progress(self) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=0)

    # --- File selection ------------------------------------------------------
    def choose_files(self) -> None:
        if self._busy:
            return
        selected = filedialog.askopenfilenames(
            parent=self.root,
            title="Choose audio files to transcribe",
            initialdir=str(self._last_dir),
            filetypes=AUDIO_FILE_TYPES,
        )
        if selected:
            self.add_files([Path(item) for item in selected])

    @staticmethod
    def _path_key(path: Path) -> str:
        try:
            return os.path.normcase(os.path.abspath(str(path)))
        except (OSError, ValueError):
            return str(path).lower()

    def add_files(self, paths: list[Path], start: bool = False) -> None:
        added = 0
        duplicates = 0
        missing = 0
        unsupported = 0
        active_paths = {
            self._path_key(job.path)
            for job in self._jobs
            if job.status in ("queued", "running")
        }

        for raw_path in paths:
            path = Path(raw_path)
            if not path.is_file():
                missing += 1
                continue
            if path.suffix.lower().lstrip(".") not in ALLOWED_EXTENSIONS:
                unsupported += 1
                continue
            key = self._path_key(path)
            if key in active_paths:
                duplicates += 1
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            job = AudioJob(id=uuid.uuid4().hex, path=path, size=size)
            self._jobs.append(job)
            self._jobs_by_id[job.id] = job
            active_paths.add(key)
            self._last_dir = path.parent
            added += 1

        if added and self._selected_id is None:
            self._selected_id = self._ordered_jobs()[0].id

        self._refresh_library()
        if added:
            self._render_selected()

        parts: list[str] = []
        if added:
            parts.append(f"Added {added} audio file{'s' if added != 1 else ''}.")
        if duplicates:
            parts.append(f"{duplicates} already in the list.")
        if missing:
            parts.append(f"{missing} could not be found.")
        if unsupported:
            parts.append(
                f"{unsupported} unsupported file{'s' if unsupported != 1 else ''} skipped."
            )
        if not parts:
            parts.append("No audio files were added.")
        self._set_status(
            " ".join(parts),
            "warning" if missing or unsupported else "muted",
        )

        if start and added:
            self.start_transcription()

    # --- Transcript helpers --------------------------------------------------
    def _focus_text(self, _event: tk.Event | None = None) -> None:
        try:
            self.text.focus_set()
        except tk.TclError:
            return
        self._refresh_placeholder()

    def _refresh_placeholder(self) -> None:
        content = self.text.get("1.0", "end-1c")
        focused = self.root.focus_get() is self.text
        if content.strip() or (focused and self._text_editable):
            self.placeholder.place_forget()
        else:
            # Coordinates are relative to the Text widget's padded content
            # origin, so 0,0 lines the hint up with the first character.
            self.placeholder.configure(
                text=self._placeholder_message,
                cursor="xterm" if self._text_editable else "arrow",
            )
            self.placeholder.place(in_=self.text, x=0, y=0, anchor="nw")

    def _update_counts(self) -> None:
        content = self.text.get("1.0", "end-1c").strip()
        if content:
            words = len(re.findall(r"\S+", content))
            self.count_var.set(f"{words:,} words · {len(content):,} characters")
        else:
            self.count_var.set("0 words · 0 characters")

    def _set_text_content(self, content: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if content:
            self.text.insert("1.0", content, "body")
        self.text.edit_reset()
        self.text.configure(state="disabled")

    def _set_text_state(self, editable: bool) -> None:
        self._text_editable = editable
        self.text.configure(state="normal" if editable else "disabled")

    def _render_selected(self) -> None:
        job = self._selected_job()
        self._set_text_content(job.transcript if job else "")

        if job is None:
            self.transcript_title_var.set("No audio selected")
            self.transcript_meta_var.set("")
            self._placeholder_message = (
                "Your transcript will appear here.\n"
                "Add audio files, then press Transcribe."
            )
            self._set_text_state(False)
            self.root.title(APP_NAME)
        else:
            self.transcript_title_var.set(job.name)
            self.transcript_meta_var.set(self._job_detail(job))
            self._placeholder_message = ""
            if job.status == "running":
                self._placeholder_message = "Transcribing…"
            elif job.status == "queued":
                self._placeholder_message = "Queued. Press Transcribe to start."
            elif not job.transcript.strip():
                if job.status == "error":
                    self._placeholder_message = job.error or "Transcription failed."
                elif job.status == "done":
                    self._placeholder_message = "No speech was detected in this recording."
                elif job.status == "cancelled":
                    self._placeholder_message = "Cancelled before any speech was captured."
            editable = job.status in ("done", "cancelled") or (
                job.status == "error" and bool(job.transcript)
            )
            self._set_text_state(editable)
            self.root.title(f"{job.name} · {APP_NAME}")

        if job is not None and job.status == "running":
            self.text.see("end")
        else:
            self.text.yview_moveto(0.0)
        self._refresh_placeholder()
        self._update_counts()
        self._update_transcript_buttons()

    def _job_detail(self, job: AudioJob) -> str:
        label = {
            "queued": "Queued",
            "running": "Transcribing",
            "done": "Done",
            "error": "Failed",
            "cancelled": "Cancelled",
        }.get(job.status, job.status.title())
        bits = [label]

        if job.status in ("done", "error", "cancelled"):
            if job.duration:
                bits.append(format_duration(job.duration))
            if job.language:
                bits.append(language_name(job.language))
            if job.words:
                bits.append(f"{job.words:,} words")
            if job.model_key:
                bits.append(MODEL_NAMES.get(job.model_key, job.model_key))
            if job.elapsed is not None:
                bits.append(f"in {_format_elapsed(job.elapsed)}")
            when = self._format_when(job.completed_at)
            if when:
                bits.append(when)
            if not job.exists:
                bits.append("original file not found")
        elif job.status == "queued" and job.size:
            bits.append(format_bytes(job.size))

        if job.status == "error" and job.error:
            bits.append(self._shorten_text(job.error))
        return " · ".join(bits)

    @staticmethod
    def _shorten_text(value: str, limit: int = 110) -> str:
        value = " ".join(value.split())
        if len(value) <= limit:
            return value
        return value[: limit - 1].rstrip() + "…"

    def _update_transcript_buttons(self) -> None:
        job = self._selected_job()
        content = self.text.get("1.0", "end-1c").strip()
        self.copy_btn.state(["!disabled"] if content else ["disabled"])

        if job is None:
            self.remove_btn.state(["disabled"])
        elif self._busy:
            self.remove_btn.state(["disabled"])
        else:
            self.remove_btn.state(["!disabled"])

        can_retry = (
            job is not None
            and not self._busy
            and job.status in ("error", "cancelled")
            and job.exists
        )
        if can_retry:
            if not self.retry_btn.winfo_manager():
                self.retry_btn.pack(
                    side="right", padx=(0, self.px(8)), before=self.remove_btn
                )
            self.retry_btn.state(["!disabled"])
        else:
            self.retry_btn.pack_forget()

    def _commit_edit(self) -> None:
        job = self._selected_job()
        if job is None or not self._text_editable:
            return
        content = self.text.get("1.0", "end-1c")
        if content == job.transcript:
            return
        job.transcript = content
        job.words = len(re.findall(r"\S+", content)) if content.strip() else 0
        self._schedule_history_save()

    def _append_segment(self, job: AudioJob, start: float, text: str) -> None:
        if job.timestamps:
            chunk = f"[{format_timestamp(start)}] {text}\n"
        else:
            chunk = text + " "
        job.transcript += chunk
        job.words = len(re.findall(r"\S+", job.transcript))

        if self._selected_id != job.id:
            return

        at_bottom = self.text.yview()[1] >= 0.995
        self.text.configure(state="normal")
        self.text.insert("end", chunk, "body")
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")
        self._refresh_placeholder()
        self._update_counts()
        self._update_transcript_buttons()

    @staticmethod
    def _trim_job_transcript(job: AudioJob) -> None:
        if job.timestamps:
            job.transcript = job.transcript.rstrip("\n")
        else:
            job.transcript = job.transcript.rstrip(" ")
        job.words = len(re.findall(r"\S+", job.transcript))

    def _on_text_edited(self, _event: tk.Event | None = None) -> None:
        self._commit_edit()
        job = self._selected_job()
        if job is not None and self._text_editable:
            self.transcript_meta_var.set(self._job_detail(job))
        self._update_counts()
        self._update_transcript_buttons()
        self._refresh_placeholder()

    def copy_transcript(self) -> None:
        self._commit_edit()
        content = self.text.get("1.0", "end-1c")
        if not content.strip():
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            self.root.update_idletasks()
        except tk.TclError:
            log.exception("Clipboard copy failed")
            self._set_status("Could not copy to the clipboard.", "error")
            return

        self._set_status("Transcript copied to the clipboard.", "success")
        self.copy_btn.configure(text="Copied")
        if self._copy_reset_job is not None:
            self.root.after_cancel(self._copy_reset_job)
        self._copy_reset_job = self.root.after(1600, self._reset_copy_label)

    def _reset_copy_label(self) -> None:
        self._copy_reset_job = None
        try:
            self.copy_btn.configure(text="Copy")
        except tk.TclError:
            pass

    def remove_selected(self) -> None:
        if self._busy:
            return
        job = self._selected_job()
        if job is None:
            return
        if job.status in ("done", "cancelled", "error") and job.transcript.strip():
            if not messagebox.askyesno(
                APP_NAME,
                f"Remove “{job.name}” from the library?\n\n"
                "The transcript will be deleted. The audio file itself is not touched.",
                parent=self.root,
            ):
                return
        order = self._ordered_jobs()
        index = next((i for i, item in enumerate(order) if item.id == job.id), 0)

        self._jobs = [item for item in self._jobs if item.id != job.id]
        self._jobs_by_id.pop(job.id, None)
        self._selected_id = None

        remaining = self._ordered_jobs()
        if remaining:
            self._selected_id = remaining[min(index, len(remaining) - 1)].id

        self._render_selected()
        self._refresh_library()
        self._save_history()
        self._set_status("Removed from the library.", "muted")

    def retry_selected(self) -> None:
        if self._busy:
            return
        job = self._selected_job()
        if job is None or job.status not in ("error", "cancelled"):
            return
        if not job.exists:
            messagebox.showerror(
                APP_NAME,
                "The original audio file could not be found.\n\n"
                f"{job.path}",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Transcribe “{job.name}” again?\n\nThe previous result will be replaced.",
            parent=self.root,
        ):
            return
        self._start_batch([job.id])

    # --- Shortcuts -----------------------------------------------------------
    def _shortcut_open(self, _event: tk.Event) -> str:
        if not self._busy:
            self.choose_files()
        return "break"

    def _shortcut_transcribe(self, _event: tk.Event) -> str:
        if self._busy:
            return "break"
        if any(job.status == "queued" for job in self._jobs):
            self.start_transcription()
        elif not self._jobs:
            self.choose_files()
        return "break"

    def _shortcut_copy(self, _event: tk.Event) -> str:
        self.copy_transcript()
        return "break"

    # --- Busy state ----------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self.add_btn.state(["disabled"])
            self.model_combo.state(["disabled"])
            self.language_combo.state(["disabled"])
            self.timestamps_check.state(["disabled"])
            self.cancel_btn.grid()
            self.cancel_btn.state(["!disabled"])
        else:
            self.add_btn.state(["!disabled"])
            self.model_combo.state(["readonly"])
            self.language_combo.state(["readonly"])
            self.timestamps_check.state(["!disabled"])
            self.cancel_btn.grid_remove()
            self.cancel_btn.state(["!disabled"])
        self._update_action_button()
        self._update_transcript_buttons()

    def _update_action_button(self) -> None:
        if self._busy:
            self.transcribe_btn.configure(text="Transcribing…")
            self.transcribe_btn.state(["disabled"])
            return
        queued = sum(1 for job in self._jobs if job.status == "queued")
        if queued:
            suffix = "s" if queued != 1 else ""
            self.transcribe_btn.configure(text=f"Transcribe {queued} file{suffix}")
            self.transcribe_btn.state(["!disabled"])
        else:
            self.transcribe_btn.configure(text="Transcribe audio")
            self.transcribe_btn.state(["disabled"])

    # --- Transcription -------------------------------------------------------
    def start_transcription(self) -> None:
        if self._busy:
            return
        queued = [job.id for job in self._ordered_jobs() if job.status == "queued"]
        if not queued:
            if not self._jobs:
                self.choose_files()
            return
        self._start_batch(queued)

    def _start_batch(self, job_ids: list[str]) -> None:
        if self._busy:
            return
        ids = [job_id for job_id in job_ids if job_id in self._jobs_by_id]
        if not ids:
            return

        model_key = self.model_key_by_label.get(self.model_var.get(), DEFAULT_MODEL)
        language = self.language_codes.get(self.language_var.get())
        timestamps = self.timestamps_var.get()

        self._commit_edit()
        self._auto_language = language is None
        self._detected_language = None
        self._device_label = ""
        self._run_started = time.monotonic()
        self._batch_total = len(ids)
        self._pinned_selection = False

        specs: list[tuple[str, Path, str]] = []
        for job_id in ids:
            job = self._jobs_by_id[job_id]
            job.status = "queued"
            job.transcript = ""
            job.words = 0
            job.error = None
            job.language = None
            job.duration = None
            job.elapsed = None
            job.completed_at = None
            job.model_key = model_key
            job.timestamps = timestamps
            specs.append((job.id, job.path, job.name))

        self._selected_id = ids[0]
        self._render_selected()
        self._refresh_library()
        self.cancel_event = threading.Event()
        self._set_busy(True)
        self._set_badge("LOADING", "warning")
        self._set_status("Preparing SenseMaker…", "accent")
        self._start_indeterminate()

        self.worker = threading.Thread(
            target=self._worker_main,
            args=(specs, model_key, language),
            daemon=True,
            name="sense-maker-worker",
        )
        self.worker.start()

    def _worker_main(
        self,
        specs: list[tuple[str, Path, str]],
        model_key: str,
        language: str | None,
    ) -> None:
        started = time.monotonic()
        force_cpu = False
        loaded = None
        total = len(specs)
        model_name = MODEL_NAMES.get(model_key, model_key)

        self.events.put(
            (
                "status",
                f"Loading the {model_name} model… first use may download it.",
                "accent",
            )
        )
        self.events.put(("progress_mode", "indeterminate"))

        while True:
            try:
                loaded = self.engine.load_model(model_key, force_cpu=force_cpu)
                break
            except Exception as exc:
                if not force_cpu and looks_like_gpu_error(exc):
                    log.exception("GPU model load failed; retrying on CPU")
                    self.engine.block_gpu()
                    self.events.put(
                        ("notice", "GPU unavailable — retrying on the CPU.", "warning")
                    )
                    force_cpu = True
                    continue
                details = traceback.format_exc()
                log.error("Could not load the Whisper model\n%s", details)
                message = str(exc).strip() or type(exc).__name__
                self.events.put(("batch_error", message, details))
                return

        self.events.put(("device", loaded.device_label))
        if self.cancel_event is not None and self.cancel_event.is_set():
            self.events.put(("batch_cancelled", 0, 0, 0))
            return

        done_count = 0
        failed_count = 0

        for index, (job_id, path, name) in enumerate(specs):
            if self.cancel_event is not None and self.cancel_event.is_set():
                self.events.put(("batch_cancelled", index, done_count, failed_count))
                return

            self.events.put(("file_start", job_id, index, total))
            self.events.put(
                (
                    "status",
                    f"Transcribing {index + 1} of {total} · {name}",
                    "accent",
                )
            )
            self.events.put(("progress_mode", "determinate"))
            self.events.put(("file_progress", index, total, 0.0))
            file_started = time.monotonic()
            finished = False

            while not finished:
                emitted = 0
                try:
                    segments, info = self.engine.transcribe(path, language)
                    duration = (
                        float(info.duration)
                        if info.duration and info.duration > 0
                        else None
                    )
                    self.events.put(
                        (
                            "info",
                            job_id,
                            info.language,
                            float(info.language_probability),
                            duration,
                        )
                    )

                    for segment in segments:
                        if (
                            self.cancel_event is not None
                            and self.cancel_event.is_set()
                        ):
                            self.events.put(
                                ("batch_cancelled", index, done_count, failed_count)
                            )
                            return
                        text = segment.text.strip()
                        if text:
                            emitted += 1
                            self.events.put(
                                ("segment", job_id, float(segment.start), text)
                            )
                        if duration:
                            fraction = max(
                                0.0, min(1.0, float(segment.end) / duration)
                            )
                            self.events.put(
                                ("file_progress", index, total, fraction)
                            )

                    self.events.put(
                        (
                            "file_done",
                            job_id,
                            index,
                            total,
                            time.monotonic() - file_started,
                            emitted,
                            loaded.device_label,
                        )
                    )
                    done_count += 1
                    finished = True

                except Exception as exc:
                    if (
                        loaded is not None
                        and loaded.device == "cuda"
                        and emitted == 0
                        and not force_cpu
                        and looks_like_gpu_error(exc)
                    ):
                        log.exception("GPU transcription failed; retrying on CPU")
                        self.engine.block_gpu()
                        self.events.put(
                            (
                                "notice",
                                "GPU unavailable — retrying on the CPU.",
                                "warning",
                            )
                        )
                        self.events.put(("reset_job", job_id))
                        force_cpu = True
                        try:
                            loaded = self.engine.load_model(
                                model_key, force_cpu=True
                            )
                            self.events.put(("device", loaded.device_label))
                        except Exception:
                            details = traceback.format_exc()
                            log.error("Could not reload the model on the CPU\n%s", details)
                            message = str(exc).strip() or type(exc).__name__
                            self.events.put(
                                (
                                    "file_error",
                                    job_id,
                                    index,
                                    total,
                                    message,
                                    details,
                                    time.monotonic() - file_started,
                                    done_count,
                                    failed_count,
                                )
                            )
                            failed_count += 1
                            finished = True
                        continue

                    details = traceback.format_exc()
                    log.error("Transcription failed for %s\n%s", path, details)
                    message = str(exc).strip() or type(exc).__name__
                    self.events.put(
                        (
                            "file_error",
                            job_id,
                            index,
                            total,
                            message,
                            details,
                            time.monotonic() - file_started,
                            done_count,
                            failed_count,
                        )
                    )
                    failed_count += 1
                    finished = True

        self.events.put(
            (
                "batch_done",
                time.monotonic() - started,
                done_count,
                failed_count,
                loaded.device_label if loaded else "",
            )
        )

    # --- Event pump ----------------------------------------------------------
    def _poll_events(self) -> None:
        if self._closing:
            return
        try:
            while True:
                event = self.events.get_nowait()
                try:
                    self._handle_event(event)
                except Exception:
                    log.exception(
                        "Could not handle UI event %s for job %s",
                        event[0] if isinstance(event, tuple) and event else "?",
                        event[1] if isinstance(event, tuple) and len(event) > 1 else "?",
                    )
        except queue.Empty:
            pass
        self._poll_job = self.root.after(80, self._poll_events)

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]

        if kind == "status":
            self._set_status(event[1], event[2])
        elif kind == "notice":
            self._set_status(event[1], event[2])
        elif kind == "progress_mode":
            if event[1] == "indeterminate":
                self._start_indeterminate()
            else:
                self._set_progress(0)
        elif kind == "device":
            self._device_label = event[1]
            if event[1].startswith("GPU"):
                self._set_badge("GPU ACTIVE", "success")
            else:
                self._set_badge("CPU ACTIVE", "success")
        elif kind == "file_start":
            self._on_file_start(event[1], event[2], event[3])
        elif kind == "segment":
            self._on_segment(event[1], event[2], event[3])
        elif kind == "info":
            self._on_file_info(event[1], event[2], event[3], event[4])
        elif kind == "file_progress":
            self._on_file_progress(event[1], event[2], event[3])
        elif kind == "reset_job":
            self._on_reset_job(event[1])
        elif kind == "file_done":
            self._on_file_done(event[1], event[2], event[3], event[4], event[5])
        elif kind == "file_error":
            self._on_file_error(
                event[1], event[2], event[3], event[4], event[5], event[6]
            )
        elif kind == "batch_done":
            self._on_batch_done(event[1], event[2], event[3])
        elif kind == "batch_cancelled":
            self._on_batch_cancelled(event[1], event[2], event[3])
        elif kind == "batch_error":
            self._on_batch_error(event[1], event[2])

    def _on_file_start(self, job_id: str, index: int, total: int) -> None:
        job = self._jobs_by_id.get(job_id)
        if job is None:
            return
        job.status = "running"
        job.transcript = ""
        job.words = 0
        job.error = None
        job.language = None
        job.duration = None
        job.completed_at = None
        job.elapsed = None
        self._running_id = job_id
        self._file_started = time.monotonic()
        self._detected_language = None
        self._batch_total = total

        if not self._pinned_selection:
            self._selected_id = job_id
            self._render_selected()
        self._refresh_library()
        self._set_progress((index / total) * 100 if total else 0)
        self._set_status(
            f"Transcribing {index + 1} of {total} · {job.name}", "accent"
        )

    def _on_segment(self, job_id: str, start: float, text: str) -> None:
        job = self._jobs_by_id.get(job_id)
        if job is None:
            return
        self._append_segment(job, start, text)

    def _on_file_info(
        self,
        job_id: str,
        language: str | None,
        probability: float,
        duration: float | None,
    ) -> None:
        job = self._jobs_by_id.get(job_id)
        if job is None:
            return
        job.language = language
        job.duration = duration
        if duration is None:
            # Without a duration there is no percentage to report for this file.
            self._start_indeterminate()
        if self._auto_language:
            self._detected_language = language_name(language)
        if self._selected_id == job_id:
            self.transcript_meta_var.set(self._job_detail(job))

    def _on_file_progress(self, index: int, total: int, fraction: float) -> None:
        if not total:
            return
        overall = (index + max(0.0, min(1.0, fraction))) / total
        self._set_progress(overall * 100)
        job = self._jobs_by_id.get(self._running_id) if self._running_id else None
        if job is None:
            return
        detected = ""
        if self._auto_language and self._detected_language:
            detected = f" · {self._detected_language}"
        elapsed = _format_elapsed(time.monotonic() - self._run_started)
        self._set_status(
            f"Transcribing {index + 1} of {total} · {job.name} · "
            f"{int(overall * 100)}%{detected} · {elapsed} elapsed",
            "accent",
        )

    def _on_reset_job(self, job_id: str) -> None:
        job = self._jobs_by_id.get(job_id)
        if job is None:
            return
        job.transcript = ""
        job.words = 0
        if self._selected_id == job_id:
            self._set_text_content("")
            self._update_counts()
            self._update_transcript_buttons()

    def _on_file_done(
        self,
        job_id: str,
        index: int,
        total: int,
        elapsed: float,
        segments: int,
    ) -> None:
        job = self._jobs_by_id.get(job_id)
        if job is None:
            return
        job.status = "done"
        job.elapsed = elapsed
        job.completed_at = time.time()
        self._trim_job_transcript(job)
        if self._running_id == job_id:
            self._running_id = None
        if self._selected_id == job_id:
            self._render_selected()
            if job.transcript:
                self.text.see("end")
        self._refresh_library()
        self._save_history()
        self._set_progress(((index + 1) / total) * 100 if total else 100)
        if segments:
            self._set_status(
                f"Finished {index + 1} of {total} · {job.name}", "success"
            )
        else:
            self._set_status(f"No speech detected in {job.name}.", "warning")

    def _on_file_error(
        self,
        job_id: str,
        index: int,
        total: int,
        message: str,
        details: str,
        elapsed: float,
    ) -> None:
        job = self._jobs_by_id.get(job_id)
        if job is None:
            return
        job.status = "error"
        job.error = message
        job.elapsed = elapsed
        job.completed_at = time.time()
        self._trim_job_transcript(job)
        if self._running_id == job_id:
            self._running_id = None
        log.error("Transcription error for %s:\n%s", job.path, details)
        if self._selected_id == job_id:
            self._render_selected()
            if job.transcript:
                self.text.see("end")
        self._refresh_library()
        self._save_history()
        self._set_progress(((index + 1) / total) * 100 if total else 100)
        self._set_status(
            f"Failed {index + 1} of {total} · {job.name}. Continuing…", "error"
        )

    def _on_batch_done(
        self, elapsed: float, done_count: int, failed_count: int
    ) -> None:
        self._running_id = None
        self._set_busy(False)
        self._set_badge("ON-DEVICE", "success")
        self._refresh_library()
        self._update_counts()

        completed = done_count + failed_count
        total = self._batch_total
        if completed:
            self._set_progress(100)
        else:
            self._reset_progress()

        if total:
            details = [
                f"Finished {completed} of {total} "
                f"file{'s' if total != 1 else ''} in {_format_elapsed(elapsed)}"
            ]
            if failed_count:
                details.append(f"{failed_count} failed")
            self._set_status(
                " · ".join(details), "warning" if failed_count else "success"
            )
        else:
            self._set_status("Batch finished.", "success")
        self._save_history()

    def _on_batch_cancelled(
        self, index: int, done_count: int, failed_count: int
    ) -> None:
        job = self._jobs_by_id.get(self._running_id) if self._running_id else None
        if job is not None and job.status == "running":
            job.status = "cancelled"
            job.completed_at = time.time()
            job.elapsed = max(0.0, time.monotonic() - self._file_started)
            self._trim_job_transcript(job)
            if self._selected_id == job.id:
                self._render_selected()

        self._running_id = None
        self._set_busy(False)
        self._reset_progress()
        self._set_badge("ON-DEVICE", "warning")
        self._refresh_library()
        self._update_counts()
        self._save_history()

        queued = sum(1 for item in self._jobs if item.status == "queued")
        completed = done_count + failed_count
        bits = ["Cancelled"]
        if completed:
            bits.append(f"{completed} finished")
        if queued:
            bits.append(f"{queued} still queued")
        self._set_status(" · ".join(bits), "warning")

    def _on_batch_error(self, message: str, details: str) -> None:
        self._running_id = None
        self._set_busy(False)
        self._reset_progress()
        self._set_badge("ON-DEVICE", "error")
        self._refresh_library()
        self._set_status("Could not load the transcription model.", "error")
        log.error("Batch could not start:\n%s", details)
        messagebox.showerror(
            APP_NAME,
            f"{message}\n\nThe full details were saved to:\n{LOG_PATH}",
            parent=self.root,
        )

    def cancel_transcription(self) -> None:
        if not self._busy or self.cancel_event is None:
            return
        self.cancel_event.set()
        self.cancel_btn.state(["disabled"])
        self._set_status("Cancelling after the current step…", "warning")

    # --- Shutdown ------------------------------------------------------------
    def _on_close(self) -> None:
        if self._busy:
            if not messagebox.askyesno(
                APP_NAME,
                "A batch transcription is still running.\n\nQuit anyway?",
                parent=self.root,
            ):
                return
            if self.cancel_event is not None:
                self.cancel_event.set()
        self._commit_edit()
        self._save_history()
        self._closing = True
        for job_id in (self._poll_job, self._copy_reset_job, self._save_job):
            if job_id is not None:
                try:
                    self.root.after_cancel(job_id)
                except tk.TclError:
                    pass
        self._poll_job = self._copy_reset_job = self._save_job = None
        self.root.destroy()


def main() -> None:
    _enable_dpi_awareness()
    _set_windows_app_id()

    root = tk.Tk()
    root.withdraw()
    try:
        dpi = root.winfo_fpixels("1i")
        root.tk.call("tk", "scaling", dpi / 72.0)
    except tk.TclError:
        pass

    root.title(APP_NAME)
    root.configure(bg=BG)
    try:
        icon_path = RESOURCE_DIR / "assets" / "icon.png"
        if icon_path.is_file():
            root._app_icon = tk.PhotoImage(file=str(icon_path))
            root.iconphoto(True, root._app_icon)
    except tk.TclError:
        log.debug("Could not load window icon", exc_info=True)

    app = TranscriberApp(root)
    _apply_dark_titlebar(root)
    root.deiconify()

    initial_files = [Path(item) for item in sys.argv[1:] if Path(item).is_file()]
    if initial_files:
        root.after(200, lambda: app.add_files(initial_files))

    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Unexpected application error")
        raise
