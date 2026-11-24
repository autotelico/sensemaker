# Release Checklist

SenseMaker is distributed under GPL-3.0-or-later. The current release is free,
open source, and unsigned.

## Blocking items

1. **Corresponding source.** Keep `THIRD_PARTY_SOURCE.md` current. The bundled
   FFmpeg/x264/x265 sources must be obtainable for every release.
2. **Do not bundle proprietary components.** NVIDIA cuBLAS is not GPL-compatible
   and must stay out of the installer. GPU users install it separately.
3. **Inno Setup terms.** Free for non-commercial use. Obtain a commercial
   license if SenseMaker is sold.
4. **Security/privacy contact.** GitHub private vulnerability reporting is the
   contact; keep it enabled in repository settings.
5. **Clean build environment.** Build from a fresh Windows CI runner or a clean
   VM with a new virtual environment.
6. **Trademark.** Review the project name before commercial use; "SenseMaker"
   is used by other software products.

## Not blocking the free GPL release

- **Code signing.** Recommended for trust and SmartScreen reputation, but not
  required for the GPL release. Unsigned builds are published with a notice.
- **Auto-updater.** There is no automatic updater yet. GitHub Releases is the
  update channel.

## Build steps

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
winget install JRSoftware.InnoSetup
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The build script:

- regenerates icons and wizard artwork;
- builds `dist\SenseMaker`;
- compiles `dist\SenseMaker-Setup-<version>.exe` with Inno Setup;
- writes `dist\SHA256SUMS.txt` and `dist\release.json`.

For local GPU testing (not bundled in the installer):

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
```

## Signing (optional)

After obtaining a certificate:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\sign.ps1 `
  -PfxPath C:\secure\publisher.pfx `
  -PfxPassword (Read-Host -AsSecureString) `
  -TimestampUrl https://timestamp.digicert.com
```

Verify both:

```powershell
Get-AuthenticodeSignature dist\SenseMaker\SenseMaker.exe
Get-AuthenticodeSignature dist\SenseMaker-Setup-1.0.0.exe
```

## Publication

- Publish the installer and `SHA256SUMS.txt` together.
- Publish release notes that state what data is stored locally, how to turn off
  history, and what network requests the model download makes.
- Publish the corresponding source for the bundled GPL components, or the
  written offer in `THIRD_PARTY_SOURCE.md`.
- Do not publish NVIDIA cuBLAS or other proprietary runtimes with the installer.
- Test a clean install, upgrade from the previous version, and uninstall on a
  separate Windows account or clean VM.
- Verify that no personal files, logs, transcript history, or model caches are
  present in the published artifacts.
- Rebuild the model manifest only when intentionally changing pinned model
  revisions; review its diff.

## Current gaps

- No code signing certificate is installed on the build machine.
- There is no hosted update manifest or auto-updater yet.
- A full SBOM and legal review are still recommended.
