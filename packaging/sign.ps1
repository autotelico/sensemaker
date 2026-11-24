# Sign SenseMaker binaries with an Authenticode certificate.
#
# The certificate is required before public distribution. Supply either a PFX
# (recommended in CI through secured secrets) or a thumbprint of a certificate
# that is already installed in CurrentUser\My.
#
# Example:
#   .\packaging\sign.ps1 -FilePaths .\dist\SenseMaker\SenseMaker.exe `
#       -PfxPath C:\secure\publisher.pfx `
#       -TimestampUrl https://timestamp.digicert.com

[CmdletBinding(DefaultParameterSetName = 'Pfx')]
param(
    [Parameter(Mandatory = $true)]
    [string[]]$FilePaths,

    [Parameter(ParameterSetName = 'Pfx')]
    [string]$PfxPath,

    [Parameter(ParameterSetName = 'Pfx')]
    [Security.SecureString]$PfxPassword,

    [Parameter(ParameterSetName = 'Thumbprint')]
    [string]$CertificateThumbprint,

    [string]$TimestampUrl = 'https://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'
$Signing = $false

if ($PSCmdlet.ParameterSetName -eq 'Pfx') {
    if (-not $PfxPath) {
        throw 'A PFX path is required, or use -CertificateThumbprint for an installed certificate.'
    }
    if (-not (Test-Path $PfxPath)) {
        throw "PFX file not found: $PfxPath"
    }
    if (-not $PfxPassword) {
        $PfxPassword = Read-Host -AsSecureString 'PFX password'
    }
    $cert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
        (Resolve-Path $PfxPath).Path,
        $PfxPassword,
        [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::DefaultKeySet
    )
    $Signing = $true
}
else {
    if (-not $CertificateThumbprint) {
        throw 'A certificate thumbprint is required.'
    }
    $cert = Get-ChildItem Cert:\CurrentUser\My |
        Where-Object { $_.Thumbprint -eq $CertificateThumbprint } |
        Select-Object -First 1
    if (-not $cert) {
        throw "No CurrentUser\My certificate found with thumbprint $CertificateThumbprint."
    }
    $Signing = $true
}

if (-not $Signing) {
    throw 'No signing certificate was supplied.'
}
if (-not $cert.HasPrivateKey) {
    throw 'The signing certificate does not have a private key.'
}

foreach ($file in $FilePaths) {
    if (-not (Test-Path $file)) {
        throw "File to sign was not found: $file"
    }
    $resolved = (Resolve-Path $file).Path
    Write-Host "Signing $resolved"
    $result = Set-AuthenticodeSignature -FilePath $resolved -Certificate $cert `
        -TimestampServer $TimestampUrl -HashAlgorithm SHA256
    if ($result.Status -ne 'Valid') {
        throw "Signing did not produce a valid signature for $resolved (status: $($result.Status))."
    }
}

foreach ($file in $FilePaths) {
    $signature = Get-AuthenticodeSignature (Resolve-Path $file)
    Write-Host ("{0}: {1}" -f $file, $signature.Status)
    if ($signature.Status -ne 'Valid') {
        throw "Signature verification failed for $file."
    }
}
