# SenseMaker 1.1.0

Private, on-device audio transcription for Windows 10/11 (x64).

## Highlights

- Transcribe audio and video files locally.
- Uses an audio-capable local Ollama model when available; falls back to
  SenseMaker's pinned Whisper models otherwise.
- Model availability is shown in-app, with an explicit download control, an
  **Open models folder** link, and a confirmation prompt before downloading a
  missing model.
- Live microphone transcription with committed text and an in-progress preview.
- Quick dictation popup from a system-wide shortcut (`Ctrl+Alt+Space`), with
  model/language selection, restart/continue, an editable text box, and
  one-click copy.
- Optional DPAPI-encrypted transcript history for files and library live
  sessions; popup dictations are never saved.

## Licensing and distribution

This release rebuilds CTranslate2 from source with the GPL-compatible
configuration used by SenseMaker:

- oneDNN (Apache-2.0) instead of Intel MKL;
- oneTBB (Apache-2.0) for oneDNN threading, with no Intel OpenMP;
- no NVIDIA cuDNN;
- CUDA loaded dynamically at runtime, with no NVIDIA binaries bundled.

The installer therefore does not include Intel oneMKL/OpenMP or NVIDIA
cuDNN/cuBLAS. The optional GPU runtime is installed separately by the user and
is not distributed with SenseMaker. Consult `THIRD_PARTY_NOTICES.md`,
`THIRD_PARTY_SOURCE.md`, and `RELEASE.md` for the full inventory and
corresponding-source information. This is an inventory and compliance aid, not
legal advice.

## Download

- `SenseMaker-Setup-1.1.0.exe` — per-user installer
- `SHA256SUMS.txt` — verify the download
- `release.json` — machine-readable release metadata

## This build is not code-signed

The installer and application are unsigned, so Windows SmartScreen may warn
that the publisher is unrecognized. You can verify the download against
`SHA256SUMS.txt` before running it.

## Privacy

- Audio is decoded and transcribed on your computer. Nothing is uploaded.
- Live microphone audio is transcribed in memory and is never written to disk.
- If an audio-capable local Ollama model is used, audio goes only to the
  loopback Ollama server on this PC; remote `OLLAMA_HOST` values are refused.
- Quick dictation popup transcripts are never written to the library or
  history; only Copy places them on the Windows clipboard.
- SenseMaker contacts the network only to download a pinned Whisper model from
  Hugging Face when that model is not already cached locally.
- Media decoding is restricted to local files; FFmpeg network protocols are
  disabled. ONNX Runtime telemetry is disabled at startup.
- Transcript history is optional. When enabled it is stored in
  `%LOCALAPPDATA%\SenseMaker` and encrypted with Windows DPAPI for your Windows
  account.
- There is no account system, analytics, or telemetry.

## GPU acceleration (optional)

The installer bundles no NVIDIA runtime. SenseMaker uses an NVIDIA GPU when a
compatible cuBLAS runtime is installed separately on the machine; CUDA is loaded
at runtime and is not a hard dependency. See the GPU section of `README.md` for
the directories SenseMaker searches. Without a runtime it uses the CPU.

## License

SenseMaker is free software licensed under the GNU General Public License,
version 3 or later. The bundled FFmpeg libraries and their x264/x265 codecs are
GPL-2.0-or-later; the Windows build is distributed under GPL-3.0-or-later. See
`LICENSE`, `THIRD_PARTY_NOTICES.md`, and `THIRD_PARTY_SOURCE.md`.

## Known limitations

- No automatic updater yet.
- No code signing yet.
- Model downloads are large; the default Small model is about 480 MB.
- GPU support requires a separately installed NVIDIA cuBLAS runtime.
