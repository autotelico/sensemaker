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

## Proprietary components in the current build

The current CTranslate2 4.8.5 Windows wheel embeds Intel oneMKL and Intel
OpenMP and bundles NVIDIA cuDNN. These are not GPL-compatible and are not part
of a compliant SenseMaker distribution. Do not release the current binary;
rebuild CTranslate2 without them (see `RELEASE.md`) and exclude cuDNN. The
NVIDIA cuBLAS license in `licenses/` is retained for reference only. See
`THIRD_PARTY_NOTICES.md`.

## Other components

Licenses for Python, PyInstaller, CTranslate2, faster-whisper, ONNX Runtime,
NumPy, Tcl/Tk, and the other dependencies are in the `licenses` folder. Their
project licenses are GPL-compatible as listed; the CTranslate2 binary must be
rebuilt without its proprietary runtime components before distribution. Consult
the upstream projects for source code.

This document is an inventory and compliance aid, not legal advice. If you
redistribute SenseMaker or a modified build, review the obligations of
GPL-3.0-or-later and of every bundled component.
