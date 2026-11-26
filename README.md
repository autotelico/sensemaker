# SenseMaker

[![License: GPL-3.0-or-later](https://img.shields.io/badge/License-GPL--3.0--or--later-blue.svg)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/autotelico/sensemaker?include_prereleases)](https://github.com/autotelico/sensemaker/releases)

**SenseMaker** is a private, on-device audio transcription app for Windows.
It uses OpenAI's Whisper models through `faster-whisper`, or an audio-capable
local Ollama model when one is available, to transcribe audio files and live
microphone speech; audio is processed locally and is never uploaded. It is free
software under [GPL-3.0-or-later](LICENSE).

> **Release status:** the source is GPL-3.0-or-later, but no compliant binary is
> published yet. The current CTranslate2 wheel embeds proprietary Intel
> oneMKL/OpenMP and NVIDIA cuDNN, so the installer must not be distributed until
> the engine is rebuilt. See `RELEASE.md`.

![SenseMaker demo](demo/SenseMaker-demo-poster.png)

## Download

No binary release is published yet. When a compliant build is available it will
appear on the [Releases page](https://github.com/autotelico/sensemaker/releases).
Builds will be unsigned unless a certificate is supplied, so Windows SmartScreen
may warn that the publisher is unrecognized; verify downloads against
`SHA256SUMS.txt`.

Building from source is described below.

## Installed app

SenseMaker is installed as a normal per-user Windows application:

- **Start Menu:** `SenseMaker` folder (app, privacy notice, uninstall)
- **Desktop:** `SenseMaker` shortcut
- **Install location:** `%LOCALAPPDATA%\Programs\SenseMaker`
- **Installer:** `dist\SenseMaker-Setup-1.0.0.exe`
- **Uninstall:** Settings → Apps → Installed apps → SenseMaker

The installer is CPU-only. SenseMaker uses an NVIDIA GPU automatically when a
CUDA runtime is already installed on the machine. The current CTranslate2 wheel
still embeds proprietary NVIDIA/Intel components, so this build is not yet
distributable; see [GPU acceleration](#gpu-acceleration) and `RELEASE.md`.

Installed app data lives in `%LOCALAPPDATA%\SenseMaker`:

- `models\` — verified, pinned Whisper models
- `transcript_history.dat` — optional DPAPI-encrypted transcript history,
  including completed live sessions; quick dictation popups are never saved
- `settings.json` — non-sensitive preferences, including history retention
- `sensemaker.log` — rotated diagnostic log with directory paths redacted

The model weights are not bundled into the installer. The model row shows
whether the selected model is present; if it is not, use **Download model** or
start a transcription and confirm the download prompt. Downloads use a pinned
Hugging Face revision and every runtime file is verified against the SHA-256
manifest in `assets\model_manifests.json`. The default Small model is about
480 MB.

## Privacy and history

Audio and transcripts are processed locally. Model downloads contact Hugging
Face using only ordinary network metadata and the model name; no audio or
transcript content is sent. Media decoding is restricted to the local file
protocol, and ONNX Runtime telemetry is disabled at startup.

The **Privacy** link in the audio-library footer opens the privacy and history
controls:

- **Keep transcript history on this PC** — enabled by default. When enabled,
  transcripts and source references are encrypted with Windows DPAPI and can be
  read by this Windows account. Turn it off to delete saved history and stop
  saving new transcripts.
- **Clear all history** — deletes every transcript entry in the library and the
  encrypted history file. Original audio files are never modified.
- **Full notice** — opens `PRIVACY.md`.
- **Licenses** — opens `THIRD_PARTY_NOTICES.md`.

The Copy button writes to the Windows clipboard. Clipboard history or sync
features may retain copied text depending on your Windows settings.

## Using SenseMaker

1. Click **Add audio files** and select one or more recordings.
2. Pick a model and language, or leave the language on **Auto-detect**.
3. Click **Transcribe N files**. Files are processed one at a time in the
   background; the model loads once and is reused for the batch.
4. Click any file in the **Audio library** to view or edit its transcript.
5. Use **Copy** for the selected transcript, **Retry** for a failed or
   cancelled file, and **Remove** to delete an entry from the library.
   Removing an entry never deletes the original audio file.

Supported inputs include WAV, MP3, M4A, M4B, AAC, FLAC, OGG, OPUS, WMA, AIFF,
AMR, AC3, MP2, and MPGA. Video files with audio tracks — MP4, M4V, MOV, MKV,
WebM, AVI, WMV, FLV, and MPEG — are accepted as well.

### Models

The model row shows whether the selected model is already downloaded and
offers **Download model** when it is not. Starting a transcription or live
session with a missing model asks for confirmation before downloading. A
download runs in the background and is verified against the pinned SHA-256
manifest before the model is used. The quick dictation popup shows the same
model status under its selectors; click the status to download.

Models are stored in the `models` folder of the active data directory:

- **Running from source:** the project's `models\` folder
- **Installed app:** `%LOCALAPPDATA%\SenseMaker\models`
- **With `SENSEMAKER_DATA_DIR` set:** `<that folder>\models`

**Open models folder** beside the model status opens the folder in use and
shows its path in the status bar.

### Local Ollama audio models

SenseMaker checks a local Ollama server at startup, before each transcription,
and when **Refresh** is clicked next to **SPEECH ENGINE**. If Ollama has a model
with audio capability, SenseMaker uses its
`/v1/audio/transcriptions` endpoint and does not need a Whisper model.
Otherwise it uses the selected Whisper model, downloading it with confirmation
if necessary.

- Only loopback Ollama servers are used. If `OLLAMA_HOST` points to another
  machine, SenseMaker refuses to send audio there and falls back to Whisper.
- Text-only or vision-only Ollama models cannot transcribe audio and are
  ignored; SenseMaker requires a model whose Ollama capabilities include audio.
- Ollama audio support is new and requires a recent Ollama build plus an
  audio-capable model, such as Gemma 4 audio. If your Ollama library provides
  it, pull one for example with `ollama pull gemma4:e4b`, then click
  **Refresh**.
- Ollama audio is batch-oriented, so the grey live preview is skipped; text
  appears when a phrase is committed. Long file audio is split into short
  speech chunks before transcription.
- The **SPEECH ENGINE** row shows `Ollama · <model>` or `Whisper · <reason>`.
  The Whisper model row remains the fallback and can be downloaded in advance.
- Ollama is optional, separate software and is not bundled with SenseMaker.
  Ollama manages its own model storage (typically `%USERPROFILE%\.ollama`);
  SenseMaker only asks the running server which audio models it has.

### Live transcription

1. Pick a model and language, then choose a microphone under **Live
   microphone**.
2. Click **Live transcription** (or press `Ctrl+L`) and start speaking.
3. The phrase currently being recognised appears in grey. Once a short pause is
   detected, the phrase is committed and the next one is shown in preview.
4. Click **Stop live** (or press `Ctrl+L` again) to finish. The session appears
   in the audio library like a file, so its transcript can be edited, copied,
   or removed.

Live audio is captured in memory and converted to 16 kHz mono for the model. It
is never written to disk or uploaded; only the transcript is kept when history
is enabled. The language can stay on **Auto-detect**; after the first confident
phrase SenseMaker keeps using the detected language for the rest of the
session.

Live transcription uses the same local model as file transcription. A fast GPU
or a smaller model keeps previews close to real time; a large model on the CPU
can fall behind continuous speech. Live transcripts are plain text; the
**Include timestamps** option applies to file transcription.

### Quick dictation popup

Press `Ctrl+Alt+Space` anywhere on your desktop, or click **Quick dictation
popup** under the microphone picker. SenseMaker opens a small always-on-top
window and starts listening with the selected model, language, and microphone.

- The **Model** and **Language** selectors in the popup stay in sync with the
  main window, so you can change them without leaving the popup.
- If the selected model is not downloaded, a status line under the selectors
  says so; click it to download the model.
- The phrase currently being recognised appears in grey; completed phrases are
  added to the editable text box as you speak.
- Press `Ctrl+Alt+Space` again or click **Stop** to finish. The text stays
  editable, and **Copy** (or `Ctrl+Enter`) puts it on the clipboard.
- **Restart** clears the text box and starts a fresh take. The shortcut also
  restarts by default once a take has finished.
- **Continue** keeps the existing text and appends the next take on a new line.
- Pressing `Escape` closes the popup and stops listening.
- If another program already uses `Ctrl+Alt+Space`, SenseMaker tries
  `Ctrl+Shift+Space` and then `Ctrl+Alt+D`. The link shows the active shortcut.
- Popup dictations are not added to the audio library or transcript history.
  The text exists only in the popup until you copy it.

The global shortcut works while SenseMaker is running, even if the main window
is minimized or behind other applications. Inserting the text automatically
into the application that had focus is planned for a later update and is not
implemented yet.

### Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+O` | Add audio files |
| `Ctrl+Enter` | Transcribe queued files |
| `Ctrl+Shift+C` | Copy the selected transcript |
| `Ctrl+L` | Start or stop live transcription |
| `Ctrl+Alt+Space` | Open, stop, or restart the quick dictation popup (system-wide) |

## Running from source

Double-click **`run.bat`**. It uses the project's `.venv` and stores models in
the project's `models\` folder.

## Building the installer

Use a clean virtual environment for release builds:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-lock.txt
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-build.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
winget install JRSoftware.InnoSetup
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The build script:

- validates that no plaintext history or logs are in the project root;
- regenerates icons and wizard artwork;
- builds the standalone app in `dist\SenseMaker`;
- compiles `dist\SenseMaker-Setup-1.0.0.exe` with Inno Setup;
- writes `dist\SHA256SUMS.txt` and `dist\release.json`.

To sign the executable and installer, pass a code-signing certificate to the
build script:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 `
  -PfxPath C:\secure\publisher.pfx `
  -TimestampUrl https://timestamp.digicert.com
```

The app and installer are **unsigned until a certificate is supplied**. See
`RELEASE.md` for the publication checklist and outstanding blockers, including
the proprietary CTranslate2 runtime.

Rebuilding just the installer while keeping the existing app build:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -SkipAppBuild
```

## Updating pinned models

The supported model revisions and SHA-256 digests are in
`assets\model_manifests.json`. To intentionally update revisions:

```powershell
.venv\Scripts\python.exe packaging\make_model_manifest.py
```

Review the resulting diff before rebuilding. SenseMaker refuses to download or
load a model that is missing from the manifest or fails verification.

## GPU acceleration

A GPL-compliant CPU build will not bundle NVIDIA's proprietary runtime. To use a
GPU, install a CUDA runtime separately — for example the NVIDIA CUDA Toolkit, or
the `nvidia-cublas-cu12` and `nvidia-cuda-nvrtc-cu12` packages when running from
source. The current build is not GPL-compliant because CTranslate2 embeds Intel
oneMKL/OpenMP and NVIDIA cuDNN; see `RELEASE.md`. SenseMaker looks for cuBLAS in:

- `SENSEMAKER_GPU_DIR`
- `gpu\cublas\bin` next to the executable
- the active Python environment's `nvidia\cublas\bin`
- `CUDA_PATH` and the standard NVIDIA CUDA Toolkit install location

Without one, SenseMaker uses the CPU.

## Runtime notes

- Larger models are more accurate but slower and use more RAM/VRAM.
- GPU acceleration requires a separately installed NVIDIA CUDA runtime; it is
  not bundled. Without one, SenseMaker runs on the CPU.
- To force CPU mode, set the environment variable `WHISPER_DEVICE=cpu`.
- To skip registering the system-wide quick dictation shortcut, set
  `SENSEMAKER_NO_HOTKEY=1`; the popup link still works.
- `OLLAMA_HOST` may select a different Ollama address, but only loopback
  addresses (`localhost`, `127.0.0.1`, `::1`) are accepted for audio.
- For development, `SENSEMAKER_DATA_DIR` can point data at another folder.

## License

Copyright (C) 2026 Henrique Magalhaes.

SenseMaker is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. See [`LICENSE`](LICENSE).

The bundled FFmpeg build includes GPL components; corresponding source details
are in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and
[`THIRD_PARTY_SOURCE.md`](THIRD_PARTY_SOURCE.md).

## Documents

- `LICENSE` — GNU General Public License v3.0
- `PRIVACY.md` — user-facing privacy notice
- `SECURITY.md` — private vulnerability reporting
- `THIRD_PARTY_NOTICES.md` — licensing inventory
- `THIRD_PARTY_SOURCE.md` — corresponding source for bundled GPL components
- `RELEASE.md` — build, signing, checksums, and publication steps
- `CONTRIBUTING.md` — development and contribution guidelines
