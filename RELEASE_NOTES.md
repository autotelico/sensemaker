# SenseMaker 1.0.0

Private, on-device audio transcription for Windows 10/11 (x64).

> **Not releasable yet.** The current CTranslate2 wheel embeds proprietary Intel
> oneMKL/OpenMP and NVIDIA cuDNN. Do not publish this build; see `RELEASE.md`.

## Download

- `SenseMaker-Setup-1.0.0.exe` — per-user installer
- `SHA256SUMS.txt` — verify the download
- `release.json` — machine-readable release metadata

## This build is not code-signed

The installer and application are unsigned, so Windows SmartScreen may warn
that the publisher is unrecognized. You can verify the download against
`SHA256SUMS.txt` before running it.

## Privacy

- Audio is decoded and transcribed on your computer. Nothing is uploaded.
- SenseMaker contacts the network only to download a pinned Whisper model from
  Hugging Face when that model is not already cached locally.
- Media decoding is restricted to local files; FFmpeg network protocols are
  disabled. ONNX Runtime telemetry is disabled at startup.
- Transcript history is optional. When enabled it is stored in
  `%LOCALAPPDATA%\SenseMaker` and encrypted with Windows DPAPI for your Windows
  account.
- There is no account system, analytics, or telemetry.

## GPU acceleration (optional)

A compliant build will be CPU-only unless the engine is replaced. The current
CTranslate2 wheel embeds proprietary NVIDIA/Intel components and is not
distributable; see `RELEASE.md`.

## License

SenseMaker is free software licensed under the GNU General Public License,
version 3 or later. The bundled FFmpeg libraries and their x264/x265 codecs are
GPL-2.0-or-later; a compliant Windows build will be distributed under
GPL-3.0-or-later. See `LICENSE`, `THIRD_PARTY_NOTICES.md`, and
`THIRD_PARTY_SOURCE.md`.

## Known limitations

- No automatic updater yet.
- No code signing yet.
- Model downloads are large; the default Small model is about 480 MB.
