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

& $Iscc 'packaging\SenseMaker.iss'
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }

$SetupPath = Join-Path $Root 'dist\SenseMaker-Setup-1.0.0.exe'
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
    version = '1.0.0'
    installer = 'SenseMaker-Setup-1.0.0.exe'
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
