"""Verify that a CTranslate2 wheel is free of non-GPL-compatible binaries.

The official PyPI Windows wheel bundles Intel oneMKL/OpenMP and NVIDIA cuDNN.
SenseMaker ships a wheel rebuilt from CTranslate2 source with:

  * WITH_MKL=OFF       (oneDNN instead of Intel MKL)
  * OPENMP_RUNTIME=NONE (no Intel OpenMP)
  * WITH_CUDNN=OFF     (no NVIDIA cuDNN)
  * CUDA_DYNAMIC_LOADING=ON (no NVIDIA libraries bundled; loaded at runtime)

oneTBB (Apache-2.0) is expected and shipped as ``tbb12.dll``. This script fails
if any of the proprietary libraries above reappear in the wheel.
"""

from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from pathlib import Path

FORBIDDEN_NAME = re.compile(
    r"^(cudnn|cublas|cublaslt|cudart|nvrtc|iomp5|libiomp|mkl)", re.IGNORECASE
)
FORBIDDEN_STRINGS = (
    b"oneMKL",
    b"CT2_USE_MKL",
    b"libiomp5md",
    b"Intel(R) Math Kernel",
)
EXPECTED_FILES = (
    "ctranslate2/ctranslate2.dll",
    "ctranslate2/dnnl.dll",
    "ctranslate2/tbb12.dll",
)


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def _pe_imports(data: bytes) -> list[str]:
    try:
        import pefile
    except ImportError as exc:  # pragma: no cover - CI installs pefile
        _fail(f"pefile is required for verification: {exc}")

    pe = pefile.PE(data=data, fast_load=True)
    pe.parse_data_directories(
        directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
        ]
    )
    names: list[str] = []
    for attribute in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
        for entry in getattr(pe, attribute, []) or []:
            names.append(entry.dll.decode("ascii", errors="replace"))
    return names


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: verify_ctranslate2_wheel.py <wheel>", file=sys.stderr)
        return 2

    wheel = Path(argv[1])
    if not wheel.is_file():
        _fail(f"wheel not found: {wheel}")

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        basenames = [Path(name).name for name in names]

        forbidden = sorted(
            name for name in basenames if FORBIDDEN_NAME.search(name)
        )
        if forbidden:
            _fail("wheel contains forbidden binary names: " + ", ".join(forbidden))

        for expected in EXPECTED_FILES:
            matches = [name for name in names if name.endswith(expected)]
            if not matches:
                _fail(f"wheel is missing {expected}")

        compiled = [name for name in names if re.search(r"/_ext\.[^/]+\.pyd$", name)]
        if not compiled:
            _fail("wheel is missing the compiled ctranslate2._ext module")

        for relative in EXPECTED_FILES:
            member = next(name for name in names if name.endswith(relative))
            data = archive.read(member)
            for marker in FORBIDDEN_STRINGS:
                if marker in data:
                    _fail(f"{relative} contains forbidden marker {marker!r}")

            imports = _pe_imports(data)
            bad_imports = sorted(
                name for name in imports if FORBIDDEN_NAME.search(name)
            )
            if bad_imports:
                _fail(
                    f"{relative} imports forbidden libraries: "
                    + ", ".join(bad_imports)
                )

            if relative.endswith("ctranslate2.dll"):
                if "dnnl.dll" not in imports:
                    _fail("ctranslate2.dll does not import dnnl.dll")
                cuda_imports = [
                    name
                    for name in imports
                    if re.search(r"^(cudart|cublas|nvrtc)", name, re.IGNORECASE)
                ]
                if cuda_imports:
                    _fail(
                        "ctranslate2.dll has load-time CUDA imports; CPU-only "
                        "installs would fail: " + ", ".join(cuda_imports)
                    )

    print(f"OK: {wheel.name}")
    print(f"SHA256: {_sha256(wheel)}")
    print(
        "No Intel MKL/OpenMP or NVIDIA cuDNN/cuBLAS binaries are bundled; "
        "oneTBB is included under Apache-2.0."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
