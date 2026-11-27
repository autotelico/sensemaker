# Release Checklist

SenseMaker is distributed under GPL-3.0-or-later. The current release is free,
open source, and unsigned.

## Blocking items

1. **Corresponding source.** Keep `THIRD_PARTY_SOURCE.md` current. The bundled
   FFmpeg/x264/x265, CTranslate2, oneDNN, and oneTBB sources must be obtainable
   for every release.
2. **Do not bundle proprietary components.** NVIDIA cuBLAS/cuDNN, Intel oneMKL,
   and Intel OpenMP are not GPL-compatible. Since 1.1.0 the release builds
   CTranslate2 from source with oneDNN/oneTBB, no cuDNN, and CUDA dynamic
   loading; the installer contains no NVIDIA or Intel runtime libraries.
   `packaging/build.ps1` refuses to package a wheel that reintroduces them.
   GPU users install cuBLAS themselves; it is not distributed.
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

# Install the GPL-compatible CTranslate2 wheel first. Release CI builds it in
# .github/workflows/ctranslate2-clean.yml; for a local build, download that
# workflow artifact or build it (see below).
.venv\Scripts\python.exe -m pip install --no-deps C:\path\to\ctranslate2-4.8.5-cp314-cp314-win_amd64.whl
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-lock.txt
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-build.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
winget install JRSoftware.InnoSetup
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

To build the clean CTranslate2 wheel locally, install MSVC 2022 (Desktop C++
workload), CMake, and Ninja, and optionally the CUDA Toolkit 12.x:

```powershell
git clone --recursive --branch v4.8.5 https://github.com/OpenNMT/CTranslate2.git C:\src\CTranslate2
powershell -ExecutionPolicy Bypass -File packaging\build_ctranslate2_clean.ps1 `
  -CT2Source C:\src\CTranslate2 `
  -WorkDir C:\build\ctranslate2 `
  -OutputDir C:\build\wheels
```

Pass `-CpuOnly` to omit CUDA. The script verifies the wheel with
`packaging/verify_ctranslate2_wheel.py` and runs an import smoke test.

The GitHub workflow builds the clean wheel in a separate job, installs it before
the hash-pinned dependencies, runs the security tests, and publishes from a
separate least-privilege release job. Signing is not part of CI; use
`packaging/sign.ps1` manually or add a protected signing job.

The build script:

- refuses to build with the PyPI CTranslate2 wheel (it embeds Intel
  oneMKL/OpenMP and NVIDIA cuDNN);
- regenerates icons and wizard artwork;
- builds `dist\SenseMaker`;
- refuses to package proprietary NVIDIA/Intel runtime DLLs, a CTranslate2
  binary containing Intel oneMKL, or a bundle missing the oneDNN runtime;
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
Get-AuthenticodeSignature dist\SenseMaker-Setup-1.1.0.exe
```

## Publication

- Publish the installer and `SHA256SUMS.txt` together.
- Publish release notes that state what data is stored locally, how to turn off
  history, and what network requests the model download makes.
- Publish the corresponding source for the bundled GPL components, or the
  written offer in `THIRD_PARTY_SOURCE.md`.
- Do not publish NVIDIA cuBLAS or other proprietary runtimes with the installer.
- Verify the bundled `ctranslate2` package contains `dnnl.dll` and no
  `cudnn`/`cublas`/`cudart`/`nvrtc`/`mkl`/`iomp` DLLs. The wheel verifier and
  `packaging/build.ps1` enforce this; check the built installer too.
- Test a clean install, upgrade from the previous version, and uninstall on a
  separate Windows account or clean VM.
- Verify that no personal files, logs, transcript history, or model caches are
  present in the published artifacts.
- Rebuild the model manifest only when intentionally changing pinned model
  revisions; review its diff.

## Current gaps

- No code signing certificate is installed on the build machine.
- There is no hosted update manifest or auto-updater yet.
- The installer still contains Microsoft's VC++ runtime (`vcruntime140.dll`,
  `msvcp140.dll`, `ucrtbase.dll`), a Microsoft redistributable that is not
  GPL-licensed. This is standard for MSVC-built Python applications but should
  be covered by the legal review.
- A full SBOM and legal review are still recommended before public distribution.
