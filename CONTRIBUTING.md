# Contributing to SenseMaker

Thanks for helping. SenseMaker is free software under
`GPL-3.0-or-later`; by contributing you agree that your changes are licensed
under the same terms.

## Development

Requirements: Windows 10/11 x64 and Python 3.14.

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.pyw
```

`requirements.txt` pulls the PyPI CTranslate2 wheel, which is fine for local
development but not for a distributable build.

For the full pinned release environment, install the clean CTranslate2 wheel
first (see `RELEASE.md`), then the lock:

```powershell
.venv\Scripts\python.exe -m pip install --no-deps C:\path\to\ctranslate2-4.8.5-cp314-cp314-win_amd64.whl
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-lock.txt
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-build.txt
```

## Guidelines

- Keep the app dependency-light and offline-first. No telemetry, no audio
  uploads, and no cloud APIs.
- Keep media decoding local: do not remove the FFmpeg
  `protocol_whitelist=file` restriction or re-enable ONNX Runtime telemetry.
- Keep log output free of transcript text and directory paths.
- Do not introduce dependencies with licenses incompatible with
  GPL-3.0-or-later. Proprietary components must not be bundled in the
  installer; document optional user-installed components instead.
- Update `THIRD_PARTY_NOTICES.md` and `licenses/` when adding a dependency.
- Keep Windows-specific paths and installer behavior covered by
  `packaging/build.ps1` and `packaging/SenseMaker.iss`.
- Run the app from source before opening a pull request.

## Security reports

Do not open a public issue for a vulnerability. Use the private reporting link
in `SECURITY.md`.
