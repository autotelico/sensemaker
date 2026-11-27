# Build the standalone SenseMaker app and the Inno Setup installer.
# Usage: powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# Options:
#   -SkipAppBuild                 Reuse the existing dist\SenseMaker directory
#   -PfxPath <path>               Sign with a PFX after building
#   -PfxPassword <SecureString>   PFX password (prompts when omitted)
#   -CertificateThumbprint <id>   Sign with an installed CurrentUser\My cert
#   -TimestampUrl <url>           Authenticode timestamp server

[CmdletBinding()]
param(
    [switch]$SkipAppBuild,
    [string]$PfxPath,
    [Security.SecureString]$PfxPassword,
    [string]$CertificateThumbprint,
    [string]$TimestampUrl = 'https://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    throw "Python virtual environment not found at $Python. Create it and install the requirements first."
}

# Refuse to build if private history or logs are in the source tree.
$Forbidden = @('transcript_history.json', 'transcript_history.dat', 'sensemaker.log')
foreach ($name in $Forbidden) {
    if (Test-Path (Join-Path $Root $name)) {
        throw "Refusing to build while $name is present in the project root."
    }
}
if (-not (Test-Path (Join-Path $Root 'assets\model_manifests.json'))) {
    throw 'Pinned model manifest is missing: assets\model_manifests.json'
}

# The release build must use the GPL-compatible CTranslate2 wheel built by
# .github/workflows/ctranslate2-clean.yml. The PyPI wheel embeds Intel
# oneMKL/OpenMP and NVIDIA cuDNN, so refuse to package it.
$Ct2Package = & $Python -c "import ctranslate2, pathlib; print(pathlib.Path(ctranslate2.__file__).resolve().parent)"
$Ct2Package = ($Ct2Package | Select-Object -Last 1)
if ($Ct2Package) { $Ct2Package = $Ct2Package.Trim() }
if (-not $Ct2Package -or -not (Test-Path $Ct2Package)) {
    throw 'CTranslate2 is not installed in the build environment.'
}
$ForbiddenCt2 = Get-ChildItem -Path $Ct2Package -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^(cudnn|cublas|cublaslt|cudart|nvrtc|mkl|libiomp|iomp5).*\.dll$' }
if ($ForbiddenCt2) {
    $names = ($ForbiddenCt2 | Select-Object -ExpandProperty FullName) -join ', '
    throw "Refusing to build with the PyPI CTranslate2 wheel (found: $names). Install the clean wheel from .github/workflows/ctranslate2-clean.yml instead."
}
$Ct2DllInEnv = Join-Path $Ct2Package 'ctranslate2.dll'
if (Test-Path $Ct2DllInEnv) {
    $ct2Bytes = [IO.File]::ReadAllBytes($Ct2DllInEnv)
    $ct2Ascii = [Text.Encoding]::ASCII.GetString($ct2Bytes)
    if ($ct2Ascii.Contains('oneMKL')) {
        throw 'Refusing to build: the installed ctranslate2.dll statically contains Intel oneMKL. Install the clean wheel from .github/workflows/ctranslate2-clean.yml instead.'
    }
}

& $Python packaging\make_assets.py
if ($LASTEXITCODE -ne 0) { throw "Asset generation failed." }

$SignArgs = @{}
if ($PfxPath) {
    $SignArgs.PfxPath = $PfxPath
    if ($PfxPassword) { $SignArgs.PfxPassword = $PfxPassword }
}
elseif ($CertificateThumbprint) {
    $SignArgs.CertificateThumbprint = $CertificateThumbprint
}
$DoSign = $SignArgs.Count -gt 0

if (-not $SkipAppBuild) {
    $PyInstallerArgs = @(
        '--noconfirm',
        '--clean',
        '--windowed',
        '--onedir',
        '--name', 'SenseMaker',
        '--icon', 'assets\icon.ico',
        '--add-data', 'assets\icon.png;assets',
        '--add-data', 'assets\model_manifests.json;assets',
        '--add-data', 'PRIVACY.md;.',
        '--add-data', 'THIRD_PARTY_NOTICES.md;.',
        '--add-data', 'licenses;licenses',
        '--collect-all', 'faster_whisper',
        '--collect-all', 'ctranslate2',
        '--collect-all', 'av',
        '--collect-all', 'onnxruntime',
        '--version-file', 'packaging\version_info.txt',
        'app.pyw'
    )
    & $Python -m PyInstaller @PyInstallerArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
}

# Refuse to package proprietary runtimes. Release builds use the clean
# CTranslate2/oneDNN wheel; the PyPI wheel embeds Intel oneMKL/OpenMP and NVIDIA
# cuDNN. This is a second check on the collected bundle. See RELEASE.md.
$ProprietaryDlls = Get-ChildItem -Path 'dist\SenseMaker' -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -match '^(cudnn|cublas|cublaslt|cudart|nvrtc|mkl|libiomp|iomp5).*\.dll$' -or
        $_.Name -ieq 'libiomp5md.dll'
    }
if ($ProprietaryDlls) {
    $names = ($ProprietaryDlls | Select-Object -ExpandProperty FullName) -join ', '
    throw "Refusing to build: proprietary runtime DLLs were collected: $names. See RELEASE.md."
}
$Ct2Binaries = @(
    (Join-Path $Root 'dist\SenseMaker\_internal\ctranslate2\ctranslate2.dll'),
    (Join-Path $Root 'dist\SenseMaker\_internal\ctranslate2\dnnl.dll'),
    (Join-Path $Root 'dist\SenseMaker\_internal\ctranslate2.dll'),
    (Join-Path $Root 'dist\SenseMaker\_internal\dnnl.dll')
)
foreach ($binary in $Ct2Binaries) {
    if (Test-Path $binary) {
        $bytes = [IO.File]::ReadAllBytes($binary)
        $ascii = [Text.Encoding]::ASCII.GetString($bytes)
        if ($ascii.Contains('oneMKL')) {
            throw "Refusing to build: $binary contains Intel oneMKL. See RELEASE.md."
        }
    }
}
$DnnlRuntime = Join-Path $Root 'dist\SenseMaker\_internal\ctranslate2\dnnl.dll'
if (-not (Test-Path $DnnlRuntime)) {
    $DnnlRuntime = Join-Path $Root 'dist\SenseMaker\_internal\dnnl.dll'
}
if (-not (Test-Path $DnnlRuntime)) {
    throw "Refusing to build: the oneDNN runtime (dnnl.dll) was not collected. The clean CTranslate2 wheel is incomplete."
}
$TbbRuntime = Join-Path $Root 'dist\SenseMaker\_internal\ctranslate2\tbb12.dll'
if (-not (Test-Path $TbbRuntime)) {
    $TbbRuntime = Join-Path $Root 'dist\SenseMaker\_internal\tbb12.dll'
}
if (-not (Test-Path $TbbRuntime)) {
    throw "Refusing to build: the oneTBB runtime (tbb12.dll) was not collected. The clean CTranslate2 wheel is incomplete."
}

if ($DoSign) {
    & (Join-Path $PSScriptRoot 'sign.ps1') -FilePaths 'dist\SenseMaker\SenseMaker.exe' @SignArgs
    if ($LASTEXITCODE -ne 0) { throw "Application signing failed." }
}

$IsccCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Inno Setup 6\ISCC.exe'),
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe'
)
$Iscc = $IsccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) {
    throw "Inno Setup 6 was not found. Install it with: winget install JRSoftware.InnoSetup"
}

Get-ChildItem -Path (Join-Path $Root 'dist') -Filter 'SenseMaker-Setup-*.exe' -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue
& $Iscc 'packaging\SenseMaker.iss'
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }

$SetupPath = Join-Path $Root 'dist\SenseMaker-Setup-1.1.0.exe'
if (-not (Test-Path $SetupPath)) { throw "Installer was not created at $SetupPath" }

if ($DoSign) {
    & (Join-Path $PSScriptRoot 'sign.ps1') -FilePaths $SetupPath @SignArgs
    if ($LASTEXITCODE -ne 0) { throw "Installer signing failed." }
}

$AppPath = Join-Path $Root 'dist\SenseMaker\SenseMaker.exe'
$hashes = @()
foreach ($path in @($AppPath, $SetupPath)) {
    $hash = Get-FileHash -Algorithm SHA256 $path
    $relative = $path.Substring($Root.Length).TrimStart('\')
    $hashes += "$($hash.Hash.ToLower())  $relative"
}
$hashes | Set-Content -Encoding ascii (Join-Path $Root 'dist\SHA256SUMS.txt')

$release = [ordered]@{
    product = 'SenseMaker'
    version = '1.1.0'
    installer = 'SenseMaker-Setup-1.1.0.exe'
    installer_sha256 = (Get-FileHash -Algorithm SHA256 $SetupPath).Hash.ToLower()
    installer_size = (Get-Item $SetupPath).Length
    built_utc = [DateTime]::UtcNow.ToString('o')
    signed = $DoSign
}
$release | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 (Join-Path $Root 'dist\release.json')

$SizeMB = [math]::Round((Get-Item $SetupPath).Length / 1MB, 1)
Write-Host ''
Write-Host "Installer ready: $SetupPath ($SizeMB MB)"
Write-Host "Checksums: dist\SHA256SUMS.txt"
if (-not $DoSign) { Write-Warning 'Build is UNSIGNED. Provide a signing certificate before publication.' }
