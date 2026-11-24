# SenseMaker Privacy Notice

**Version 1.0 · Effective 2026-11-23**

SenseMaker is a local desktop application for transcribing audio. This notice
describes what it does with data on your computer.

## No audio uploads

Audio and video files are decoded and transcribed on your computer using a
local Whisper model. SenseMaker does not upload your recordings, extracts, or
transcripts to a server. There is no account system and no analytics or
telemetry. ONNX Runtime (used for voice-activity detection) has optional
telemetry; SenseMaker disables it at startup with `ORT_DISABLE_TELEMETRY=1` and
`onnxruntime.disable_telemetry_events()`.

## Network access

SenseMaker uses the network only to download Whisper models from Hugging Face
when a model is not already cached locally:

- The request includes ordinary network metadata such as your IP address,
  user agent, and the model repository/name.
- The request does **not** include your audio, transcript, or file names.
- Models are pinned to reviewed, immutable revisions and their files are
  verified against SHA-256 checksums before use.
- If the pinned model is already cached, SenseMaker does not need to contact
  Hugging Face.
- Media decoding is restricted to local files. FFmpeg's network protocols are
  disabled, so a playlist or container that references a remote URL is
  rejected rather than fetched.

## What is stored locally

SenseMaker stores its data under `%LOCALAPPDATA%\SenseMaker` (or the project
folder when run from source):

- **Models** — the downloaded Whisper model files you selected.
- **Transcript history** — optional. When enabled, completed transcripts and
  references to their source files are stored in
  `transcript_history.dat`. The file is encrypted with Windows DPAPI for your
  Windows account, so other Windows accounts cannot read it. It is not
  encrypted against malware or programs running as your Windows user.
- **Settings** — a small `settings.json` file containing your history-retention
  preference. It contains no transcript text.
- **Diagnostic log** — `sensemaker.log` (and up to two rotated copies) may
  contain timestamps, model/device, duration, detected language, error
  messages, and file names. Directory paths are redacted. Transcript text is
  not written to the log.

History is **on by default**. You can turn it off and delete saved transcripts
at any time from **Privacy** in the audio-library footer:

- Turning history off deletes the saved history file immediately and prevents
  new history from being saved.
- **Clear all history** deletes all transcript entries in the library and the
  saved history file. It does not delete or modify the original audio files.
- Uninstalling SenseMaker removes `%LOCALAPPDATA%\SenseMaker`, including the
  encrypted history, models, settings, and logs.

## Clipboard

The **Copy** button writes the transcript to the Windows clipboard so you can
paste it elsewhere. The clipboard is managed by Windows. Clipboard history,
clipboard sync, and other applications may retain or read copied text,
depending on your Windows settings.

## Your responsibilities

Only transcribe audio that you have the right to record and process. Recording
other people can require their consent under local law. Audio may contain
personal or sensitive information; handle transcripts accordingly.

## Third-party software and models

SenseMaker uses open-source libraries, FFmpeg components, and Whisper model
weights. See `THIRD_PARTY_NOTICES.md` and the `licenses` folder for licenses and
attribution. Model files are downloaded from Hugging Face only after a pinned
revision and checksums are verified.

## Security contact

Security and privacy questions: use GitHub's private vulnerability reporting at
https://github.com/autotelico/sensemaker/security/advisories/new.

## Changes

This notice may be updated with future releases. The version and date above
identify the notice shipped with this release.
