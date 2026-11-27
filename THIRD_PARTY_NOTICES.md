# Third-Party Notices

SenseMaker is licensed under GPL-3.0-or-later (see `LICENSE`). It includes and
uses third-party software. This file is an inventory, not a substitute for a
complete legal review. Full license texts for the principal components are in
the `licenses` folder.

## Runtime components

| Component | Version | License | Notes |
| --- | --- | --- | --- |
| faster-whisper | 1.2.1 | MIT | Whisper implementation used by SenseMaker |
| CTranslate2 | 4.8.5 | MIT | Inference engine; rebuilt from source for 1.1.0 (see below) |
| oneDNN | 3.1.1 | Apache-2.0 | CTranslate2 CPU backend (`dnnl.dll`) |
| oneTBB | 2021.11.0 | Apache-2.0 | oneDNN threading; shipped as `tbb12.dll` |
| PyAV | 16.0.1 | BSD-3-Clause | Python bindings to FFmpeg |
| FFmpeg | as bundled by PyAV 16.0.1 | LGPL/GPL (see below) | Audio/video decoding |
| ONNX Runtime | 1.31.1 | MIT | Silero VAD support; telemetry disabled at runtime; includes its own ThirdPartyNotices |
| NumPy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | Numerical runtime |
| tokenizers | 0.23.3 | Apache-2.0 | Hugging Face tokenizer |
| Hugging Face Hub | 2.4.0 | Apache-2.0 | Model downloads |
| hf-xet | 1.8.0 | Apache-2.0 | Hugging Face transfer support |
| httpx2 / httpcore2 | 2.16.0 | BSD-3-Clause | HTTP client |
| truststore | 0.10.5 | MIT | OS certificate store integration |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | Progress display |
| PyYAML | 6.0.3 | MIT | Configuration parsing |
| protobuf | 7.36.2 | BSD-3-Clause | ONNX Runtime dependency |
| flatbuffers | 25.12.19 | Apache-2.0 | ONNX Runtime dependency |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | Dependency |
| filelock | 4.2.9 | MIT | File locking |
| fsspec | 2026.9.0 | BSD-3-Clause | Filesystem abstraction |
| anyio | 4.16.0 | MIT | Async compatibility |
| idna | 3.20 | BSD-3-Clause | Dependency |
| click | 8.5.1 | BSD-3-Clause | CLI support |
| colorama | 0.4.6 | BSD-3-Clause | Terminal support |
| Python | 3.14.3 | PSF-2.0 | Bundled runtime |
| Tcl/Tk | 8.6 | Tcl/Tk license | GUI toolkit |
| Pillow | 13.0.0 | MIT-CMU | Build-time only; may be present in the PyInstaller bundle |
| PyInstaller | 6.22.3 | GPL-2.0-or-later with bootloader exception | Packaging tool; exception permits distribution of non-free applications |

## CTranslate2 build (resolved in 1.1.0)

SenseMaker 1.1.0 does **not** use the PyPI CTranslate2 wheel. The release builds
CTranslate2 4.8.5 from source with:

- `-DWITH_MKL=OFF`, using oneDNN (Apache-2.0) as the CPU backend;
- oneTBB (Apache-2.0), shipped as `tbb12.dll`, for threading;
- `-DOPENMP_RUNTIME=NONE` — no Intel OpenMP;
- `-DWITH_CUDNN=OFF` — no NVIDIA cuDNN;
- `-DCUDA_DYNAMIC_LOADING=ON` — CUDA is loaded at runtime and no NVIDIA DLL is
  bundled.

The reproducible build is in `.github/workflows/ctranslate2-clean.yml` and
`packaging/build_ctranslate2_clean.ps1`. `packaging/verify_ctranslate2_wheel.py`
rejects any wheel that reintroduces Intel MKL/OpenMP or NVIDIA
cuDNN/cuBLAS/cudart/nvrtc.

NVIDIA cuBLAS is not bundled. GPU users install a compatible runtime themselves;
CTranslate2 loads it at runtime. The NVIDIA cuBLAS license in `licenses/` is
retained for reference only.

## Microsoft Visual C++ runtime

The PyInstaller bundle includes Microsoft's Visual C++ runtime
(`vcruntime140.dll`, `msvcp140.dll`, and `ucrtbase.dll`) because Python and the
compiled wheels are built with MSVC. These are redistributable under Microsoft's
Visual Studio license, but they are not GPL-licensed. Windows GPL distributions
built with MSVC commonly include them. This is listed for transparency and
should be part of the legal review; CTranslate2 does not support building with
MinGW on Windows.

## Whisper model weights

SenseMaker downloads model files from the following pinned Hugging Face
repositories. Their model cards identify the licenses as MIT.

| Model | Repository | Pinned revision |
| --- | --- | --- |
| Tiny | Systran/faster-whisper-tiny | d90ca5fe260221311c53c58e660288d3deb8d356 |
| Base | Systran/faster-whisper-base | ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66 |
| Small | Systran/faster-whisper-small | 536b0662742c02347bc0e980a01041f333bce120 |
| Medium | Systran/faster-whisper-medium | 08e178d48790749d25932bbc082711ddcfdfbc4f |
| Large v3 Turbo | mobiuslabsgmbh/faster-whisper-large-v3-turbo | 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf |
| Large v3 | Systran/faster-whisper-large-v3 | edaa852ec7e145841d8ffdb056a99866b5f0a478 |

The exact file list and SHA-256 digests are in
`assets/model_manifests.json`. The model weights are downloaded by the user on
demand and are not included in the installer.

## FFmpeg / GPL — resolved by this project's license

The PyAV Windows wheel used by this project embeds FFmpeg libraries and codec
DLLs. The bundled build includes GPL components such as libx264 and libx265
(GPL-2.0-or-later), so the combined Windows build is distributed under
**GPL-3.0-or-later**, matching SenseMaker's own license.

Corresponding source for the bundled GPL components — exact versions, upstream
links, build information, and how to request the source archive used for a
release — is in `THIRD_PARTY_SOURCE.md`.

FFmpeg licensing information: https://ffmpeg.org/legal.html  
FFmpeg source downloads: https://ffmpeg.org/download.html

The GNU GPL-2.0, GPL-3.0, and LGPL-2.1 texts are included in the `licenses`
folder.

## Inno Setup

The installer is built with Inno Setup 6.7.x (6.7.3 locally; CI pins
Chocolatey's 6.7.1). Inno Setup is free for non-commercial use; since version
6.5 commercial use requires a paid license.
SenseMaker is distributed free of charge under the GPL, so the non-commercial
terms apply. If you sell SenseMaker or a derivative, obtain an Inno Setup
commercial license. See `licenses/inno-setup-License.txt`.

## License files

The `licenses` folder includes:

- the principal runtime licenses listed above;
- the oneDNN and oneTBB Apache-2.0 licenses and their third-party program
  notices;
- ONNX Runtime's own third-party notices;
- the NVIDIA cuBLAS license (reference only; cuBLAS is not bundled);
- the Python and Tcl/Tk licenses;
- GNU GPL-2.0, GPL-3.0, and LGPL-2.1 texts;
- the Inno Setup license for build-tool transparency.

This inventory should be regenerated and reviewed whenever dependencies,
models, or the PyInstaller build change. A full SBOM and legal review are still
recommended before public distribution.
