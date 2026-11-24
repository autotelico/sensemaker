# SenseMaker 1.0.0

Private, on-device audio transcription for Windows 10/11 (x64).

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
- Transcript history is optional. When enabled it is stored in
  `%LOCALAPPDATA%\SenseMaker` and encrypted with Windows DPAPI for your Windows
  account.
- There is no account system, analytics, or telemetry.

## GPU acceleration (optional)

The installer is CPU-only. SenseMaker automatically uses an NVIDIA GPU when a
CUDA runtime (cuBLAS) is already installed on the machine. The proprietary
NVIDIA runtime is not bundled with this GPL release; see `README.md` for how to
install it separately.

## License

SenseMaker is free software licensed under the GNU General Public License,
version 3 or later. The bundled FFmpeg libraries and their x264/x265 codecs are
GPL-2.0-or-later; the combined Windows build is distributed under GPL-3.0-or-
later. See `LICENSE`, `THIRD_PARTY_NOTICES.md`, and `THIRD_PARTY_SOURCE.md`.

## Known limitations

- No automatic updater yet.
- No code signing yet.
- Model downloads are large; the default Small model is about 480 MB.
