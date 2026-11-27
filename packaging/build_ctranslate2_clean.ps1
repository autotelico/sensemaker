# Build a GPL-compatible CTranslate2 wheel for SenseMaker.
#
# The official PyPI Windows wheel bundles Intel oneMKL/OpenMP and NVIDIA cuDNN,
# which are not GPL-compatible. This script builds CTranslate2 4.8.5 from source
# with:
#
#   * oneDNN (Apache-2.0) instead of Intel MKL
#   * oneTBB (Apache-2.0, linked statically into dnnl.dll) instead of OpenMP
#   * no cuDNN
#   * CUDA dynamic loading, so no NVIDIA DLLs are bundled or linked at load time
#
# The resulting wheel can run on CPU-only machines; GPU users install the NVIDIA
# cuBLAS runtime themselves (see requirements-gpu.txt).
#
# Requires: MSVC 2022 (Desktop C++ workload), CMake, Ninja, Python 3.14.
# Usage:
#   powershell -ExecutionPolicy Bypass -File packaging\build_ctranslate2_clean.ps1 `
#     -CT2Source C:\src\CTranslate2 -WorkDir C:\build\ct2 -OutputDir C:\build\wheels
#
# Pass -CpuOnly to skip CUDA entirely (no CUDA Toolkit required).

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CT2Source,
    [Parameter(Mandatory = $true)][string]$WorkDir,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [string]$CudaRoot = $env:CUDA_PATH,
    [switch]$CpuOnly,
    [switch]$SkipSmokeTest,
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    Write-Host ''
    Write-Host "==> $Name"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

function Import-MsvcEnvironment {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path $vswhere)) {
        throw "vswhere.exe not found. Install Visual Studio 2022 Build Tools with the Desktop development with C++ workload."
    }

    $install = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $install) {
        throw 'MSVC C++ tools not found. Install the Desktop development with C++ workload.'
    }

    $vcvars = Join-Path $install 'VC\Auxiliary\Build\vcvars64.bat'
    if (-not (Test-Path $vcvars)) {
        throw "vcvars64.bat not found under $install"
    }

    $lines = & cmd.exe /c "`"$vcvars`" >nul 2>&1 && set"
    foreach ($line in $lines) {
        if ($line -match '^([^=]+)=(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2].TrimEnd("`r", "`n")
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }

    if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
        throw 'cl.exe was not added to PATH by vcvars64.bat'
    }
    Write-Host "MSVC: $(Get-Command cl.exe | Select-Object -ExpandProperty Source)"
}

function Get-VerifiedArchive {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Sha256
    )
    if (-not (Test-Path $Destination)) {
        & curl.exe -L --fail --retry 3 --silent --show-error -o $Destination $Url
        if ($LASTEXITCODE -ne 0) {
            throw "Download failed: $Url"
        }
    }
    $actual = (Get-FileHash -Algorithm SHA256 $Destination).Hash
    if ($actual -ine $Sha256) {
        throw "SHA256 mismatch for $Destination (expected $Sha256, got $actual)"
    }
}

function Reset-BuildDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (Test-Path $Path) {
        Remove-Item -Recurse -Force $Path
    }
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

$CT2Source = (Resolve-Path $CT2Source).Path
$WorkDir = [IO.Path]::GetFullPath($WorkDir)
$OutputDir = [IO.Path]::GetFullPath($OutputDir)

if (-not (Test-Path (Join-Path $CT2Source 'CMakeLists.txt'))) {
    throw "CTranslate2 source not found at $CT2Source"
}
if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    throw 'cmake not found on PATH'
}
if (-not (Get-Command ninja -ErrorAction SilentlyContinue)) {
    throw 'ninja not found on PATH'
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Import-MsvcEnvironment

$Parallel = [Math]::Max(1, [Environment]::ProcessorCount)

# ----- CUDA -----------------------------------------------------------------
$CudaArgs = @()
if (-not $CpuOnly) {
    if ([string]::IsNullOrWhiteSpace($CudaRoot)) {
        $CudaRoot = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8'
    }
    $nvcc = Join-Path $CudaRoot 'bin\nvcc.exe'
    if (-not (Test-Path $nvcc)) {
        throw "nvcc not found at $nvcc. Install the CUDA Toolkit 12.x or pass -CpuOnly."
    }
    $env:CUDA_PATH = $CudaRoot
    $env:PATH = "$(Join-Path $CudaRoot 'bin');$env:PATH"
    Write-Host "CUDA: $CudaRoot"
    $CudaArgs = @(
        '-DWITH_CUDA=ON',
        '-DWITH_CUDNN=OFF',
        '-DCUDA_DYNAMIC_LOADING=ON',
        "-DCUDA_TOOLKIT_ROOT_DIR=$CudaRoot",
        '-DCUDA_ARCH_LIST=Common',
        '-DCUDA_NVCC_FLAGS=-Xfatbin=-compress-all'
    )
}

# ----- oneTBB (Apache-2.0, static) ------------------------------------------
$TbbVersion = '2021.11.0'
$TbbUrl = "https://github.com/uxlfoundation/oneTBB/archive/refs/tags/v$TbbVersion.tar.gz"
$TbbSha256 = '782ce0cab62df9ea125cdea253a50534862b563f1d85d4cda7ad4e77550ac363'
$TbbArchive = Join-Path $WorkDir "oneTBB-$TbbVersion.tar.gz"
$TbbSource = Join-Path $WorkDir 'src'
$TbbBuild = Join-Path $WorkDir 'tbb-build'
$TbbInstall = Join-Path $WorkDir 'tbb-install'
$TbbDir = Join-Path $TbbInstall 'lib\cmake\TBB'

Get-VerifiedArchive -Url $TbbUrl -Destination $TbbArchive -Sha256 $TbbSha256
Reset-BuildDirectory -Path $TbbSource
& tar.exe -xzf $TbbArchive -C $TbbSource
if ($LASTEXITCODE -ne 0) { throw 'Failed to extract oneTBB' }

Reset-BuildDirectory -Path $TbbBuild
Reset-BuildDirectory -Path $TbbInstall
Invoke-Step -Name 'Configure oneTBB' -Action {
    & cmake -S (Join-Path $TbbSource "oneTBB-$TbbVersion") -B $TbbBuild -G Ninja `
        -DCMAKE_BUILD_TYPE=Release `
        -DCMAKE_INSTALL_PREFIX=$TbbInstall `
        -DBUILD_SHARED_LIBS=OFF `
        -DTBB_TEST=OFF `
        -DTBB_EXAMPLES=OFF `
        -DTBB_STRICT=OFF `
        -DTBB_ENABLE_IPO=OFF
}
Invoke-Step -Name 'Build oneTBB' -Action {
    & cmake --build $TbbBuild --parallel $Parallel
}
Invoke-Step -Name 'Install oneTBB' -Action {
    & cmake --install $TbbBuild
}

# ----- oneDNN (Apache-2.0, shared, TBB threading) ---------------------------
$OneDnnVersion = '3.1.1'
$OneDnnUrl = "https://github.com/oneapi-src/oneDNN/archive/refs/tags/v$OneDnnVersion.tar.gz"
$OneDnnSha256 = 'd1cd58f57b2308d362a9bc474acbc21524cb879cf5d8779b460c718f0fac82d6'
$OneDnnArchive = Join-Path $WorkDir "oneDNN-$OneDnnVersion.tar.gz"
$OneDnnBuild = Join-Path $WorkDir 'onednn-build'
$OneDnnInstall = Join-Path $WorkDir 'onednn-install'

Get-VerifiedArchive -Url $OneDnnUrl -Destination $OneDnnArchive -Sha256 $OneDnnSha256
& tar.exe -xzf $OneDnnArchive -C $TbbSource
if ($LASTEXITCODE -ne 0) { throw 'Failed to extract oneDNN' }

Reset-BuildDirectory -Path $OneDnnBuild
Reset-BuildDirectory -Path $OneDnnInstall
Invoke-Step -Name 'Configure oneDNN' -Action {
    & cmake -S (Join-Path $TbbSource "oneDNN-$OneDnnVersion") -B $OneDnnBuild -G Ninja `
        -DCMAKE_BUILD_TYPE=Release `
        -DCMAKE_INSTALL_PREFIX=$OneDnnInstall `
        -DCMAKE_PREFIX_PATH=$TbbInstall `
        "-DTBB_DIR=$TbbDir" `
        -DDNNL_LIBRARY_TYPE=SHARED `
        -DDNNL_BUILD_EXAMPLES=OFF `
        -DDNNL_BUILD_TESTS=OFF `
        -DDNNL_ENABLE_WORKLOAD=INFERENCE `
        -DDNNL_CPU_RUNTIME=TBB
}
Invoke-Step -Name 'Build oneDNN' -Action {
    & cmake --build $OneDnnBuild --parallel $Parallel
}
Invoke-Step -Name 'Install oneDNN' -Action {
    & cmake --install $OneDnnBuild
}

# ----- CTranslate2 (MIT) ----------------------------------------------------
$Ct2Build = Join-Path $WorkDir 'ct2-build'
$Ct2Install = Join-Path $WorkDir 'ct2-install'
$Ct2ConfigureArgs = @(
    '-S', $CT2Source,
    '-B', $Ct2Build,
    '-G', 'Ninja',
    '-DCMAKE_BUILD_TYPE=Release',
    "-DCMAKE_INSTALL_PREFIX=$Ct2Install",
    "-DCMAKE_PREFIX_PATH=$OneDnnInstall;$TbbInstall",
    '-DBUILD_CLI=OFF',
    '-DBUILD_TESTS=OFF',
    '-DWITH_MKL=OFF',
    '-DWITH_DNNL=ON',
    '-DOPENMP_RUNTIME=NONE',
    '-DCMAKE_POLICY_VERSION_MINIMUM=3.5'
) + $CudaArgs

Reset-BuildDirectory -Path $Ct2Build
Reset-BuildDirectory -Path $Ct2Install
Invoke-Step -Name 'Configure CTranslate2' -Action {
    & cmake @Ct2ConfigureArgs
}
Invoke-Step -Name 'Build CTranslate2' -Action {
    & cmake --build $Ct2Build --parallel $Parallel
}
Invoke-Step -Name 'Install CTranslate2' -Action {
    & cmake --install $Ct2Build
}

$Ct2Dll = Join-Path $Ct2Install 'bin\ctranslate2.dll'
$DnnlDll = Join-Path $OneDnnInstall 'bin\dnnl.dll'
if (-not (Test-Path $Ct2Dll)) { throw "ctranslate2.dll not found at $Ct2Dll" }
if (-not (Test-Path $DnnlDll)) { throw "dnnl.dll not found at $DnnlDll" }

$PythonPackage = Join-Path $CT2Source 'python\ctranslate2'
Copy-Item -Force $Ct2Dll (Join-Path $PythonPackage 'ctranslate2.dll')
Copy-Item -Force $DnnlDll (Join-Path $PythonPackage 'dnnl.dll')

# ----- Python wheel ---------------------------------------------------------
Invoke-Step -Name 'Install wheel build tools' -Action {
    & $Python -m pip install --disable-pip-version-check --quiet 'pybind11==2.13.6' 'setuptools' 'wheel' 'pefile'
}
Invoke-Step -Name 'Build Python wheel' -Action {
    $env:CTRANSLATE2_ROOT = $Ct2Install
    & $Python -m pip wheel (Join-Path $CT2Source 'python') --no-deps --wheel-dir $OutputDir
}

$Wheel = Get-ChildItem -Path $OutputDir -Filter 'ctranslate2-*.whl' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $Wheel) { throw "No CTranslate2 wheel was produced in $OutputDir" }

Invoke-Step -Name 'Verify wheel contents' -Action {
    & $Python (Join-Path $PSScriptRoot 'verify_ctranslate2_wheel.py') $Wheel.FullName
}

if (-not $SkipSmokeTest) {
    $SmokeVenv = Join-Path $WorkDir 'smoke-venv'
    if (Test-Path $SmokeVenv) { Remove-Item -Recurse -Force $SmokeVenv }
    Invoke-Step -Name 'Create smoke-test environment' -Action {
        & $Python -m venv $SmokeVenv
    }
    Invoke-Step -Name 'Install wheel for smoke test' -Action {
        & (Join-Path $SmokeVenv 'Scripts\python.exe') -m pip install --disable-pip-version-check --quiet $Wheel.FullName 'numpy' 'pyyaml'
    }
    Invoke-Step -Name 'Import CTranslate2 without CUDA' -Action {
        & (Join-Path $SmokeVenv 'Scripts\python.exe') -c "import ctranslate2; types = set(ctranslate2.get_supported_compute_types('cpu')); print('CTranslate2', ctranslate2.__version__, 'CPU compute types:', sorted(types)); assert 'float32' in types"
    }
}

$Hash = (Get-FileHash -Algorithm SHA256 $Wheel.FullName).Hash.ToLowerInvariant()
"$Hash  $($Wheel.Name)" | Set-Content -Encoding ascii (Join-Path $OutputDir 'SHA256SUMS.txt')

Write-Host ''
Write-Host "Wheel ready: $($Wheel.FullName)"
Write-Host "SHA256: $Hash"
