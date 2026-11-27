# Corresponding Source for Third-Party Components

SenseMaker is distributed under the GNU General Public License, version 3 or
later (`GPL-3.0-or-later`). The Windows build bundles FFmpeg libraries that
include GPL components. The GPL requires that recipients of the binary be able
to obtain the corresponding source code.

## SenseMaker

The complete source for SenseMaker is this repository. Build instructions are in
`README.md` and `RELEASE.md`.

## Bundled GPL components

The PyAV 16.0.1 Windows wheel used by this project bundles FFmpeg with GPL
components enabled. The installed build includes, among others:

- `avcodec` (SONAME 62), `avformat` (SONAME 62), `avutil` (SONAME 60),
  `avfilter`, `avdevice`, `swscale`, and `swresample`, corresponding to the
  FFmpeg 8.x series;
- `libx264` (build 165);
- `libx265`.

Upstream source and build information:

| Component | Source |
| --- | --- |
| FFmpeg | https://ffmpeg.org/download.html · https://git.ffmpeg.org/ffmpeg.git |
| x264 | https://code.videolan.org/videolan/x264 |
| x265 | https://bitbucket.org/multicoreware/x265_git |
| PyAV (wheel build) | https://github.com/PyAV-Org/PyAV · https://pypi.org/project/av/16.0.1/ |

If you need the exact source archive and build configuration used for a
particular SenseMaker release, open an issue at
https://github.com/autotelico/sensemaker/issues and we will provide it. This
offer is valid for three years from the date the release was published.

## CTranslate2, oneDNN, and oneTBB

SenseMaker 1.1.0 links a CTranslate2 build that contains no Intel MKL/OpenMP or
NVIDIA cuDNN/cuBLAS binaries. The exact upstream sources are:

| Component | Version | Source |
| --- | --- | --- |
| CTranslate2 | 4.8.5 (`7f59ae114cba0f7d5fb1fe6099142e4c3b0037c8`) | https://github.com/OpenNMT/CTranslate2 |
| oneDNN | 3.1.1 | https://github.com/oneapi-src/oneDNN/releases/tag/v3.1.1 |
| oneTBB | 2021.11.0 | https://github.com/uxlfoundation/oneTBB/releases/tag/v2021.11.0 |

The build configuration is reproducible from
`.github/workflows/ctranslate2-clean.yml` and
`packaging/build_ctranslate2_clean.ps1`. It uses `WITH_MKL=OFF`,
`OPENMP_RUNTIME=NONE`, `WITH_CUDNN=OFF`, and `CUDA_DYNAMIC_LOADING=ON`.
oneTBB is shipped as `tbb12.dll`. NVIDIA cuBLAS is not bundled; users who want
GPU acceleration install it themselves, and CTranslate2 loads it at runtime.
The NVIDIA cuBLAS license in `licenses/` is retained for reference only.

## Other components

Licenses for Python, PyInstaller, CTranslate2, faster-whisper, oneDNN, oneTBB,
ONNX Runtime, NumPy, Tcl/Tk, and the other dependencies are in the `licenses`
folder. Their project licenses are GPL-compatible as listed. Consult the
upstream projects for source code.

This document is an inventory and compliance aid, not legal advice. If you
redistribute SenseMaker or a modified build, review the obligations of
GPL-3.0-or-later and of every bundled component.
