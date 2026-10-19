# Sign the built executables with a local, self-signed code-signing certificate.
#
# Windows shows the "unknown publisher" security warning for an unsigned executable. This machine also has the
# file: protocol mapped into the Internet zone (see zone-file-backup.txt), so Explorer asks before running any
# unsigned local exe. The signature stops that for TTSkyAnnot: the certificate lives in the current user's
# personal store, its public half is trusted as a root and as a publisher for that user, and every build is
# signed again afterwards. The trust is local to this Windows account — a copy taken to another machine still
# has to earn trust there (a bought certificate is the way to ship beyond this PC).
#
#   build.bat  ->  PyInstaller  ->  this script
#
# The certificate is created once (`-Create`, done already); later builds only re-sign.
param(
    [string]$Dist = (Join-Path $PSScriptRoot "..\dist\TTSkyAnnot"),
    [string[]]$Extra = @(),          # other files to sign, e.g. the installer
    [switch]$Create,
    [switch]$Require
)
$ErrorActionPreference = "Stop"

function Find-SignTool {
    $roots = @("${env:ProgramFiles(x86)}\Windows Kits\10\bin", "$env:ProgramFiles\Windows Kits\10\bin")
    foreach ($root in $roots) {
        if (-not $root -or -not (Test-Path $root)) { continue }
        $hit = Get-ChildItem $root -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\' } | Sort-Object FullName -Descending | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -like "*TTSkyAnnot*" } | Select-Object -First 1

if (-not $cert) {
    if (-not $Create) {
        if ($Require) { throw "no TTSkyAnnot code-signing certificate in the current user's store" }
        Write-Warning "no TTSkyAnnot code-signing certificate; the build stays unsigned (run sign.ps1 -Create once)"
        return
    }
    $tmp = Join-Path $env:TEMP ("ttsky-cert-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $password = "TTSkyAnnot-" + [guid]::NewGuid().ToString("N").Substring(0, 12)
        $pfx = Join-Path $tmp "TTSkyAnnot.pfx"
        $cer = Join-Path $tmp "TTSkyAnnot.cer"
        & python (Join-Path $PSScriptRoot "make_cert.py") $pfx $cer $password | Out-Null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $pfx)) { throw "make_cert.py failed" }
        # import with the private key (PersistKeySet stores it in the user's key container)
        $bytes = [System.IO.File]::ReadAllBytes($pfx)
        $flags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::PersistKeySet
        $cert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($bytes, $password, $flags)
        $store = [System.Security.Cryptography.X509Certificates.X509Store]::new("My", "CurrentUser")
        $store.Open("ReadWrite")
        $store.Add($cert)
        $store.Close()
        Write-Host "created certificate $($cert.Thumbprint)"
        # trusted root + trusted publisher for this user (certutil adds the entry; it does not show a prompt)
        foreach ($name in "Root", "TrustedPublisher") {
            & certutil -user -addstore $name $cer | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "certutil could not add the certificate to $name" }
            Write-Host "trusted in CurrentUser\$name"
        }
    } finally {
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$signtool = Find-SignTool
if (-not $signtool) {
    if ($Require) { throw "signtool.exe not found (it comes with the Windows SDK)" }
    Write-Warning "signtool.exe not found; the build stays unsigned"
    return
}
Write-Host ("certificate {0}  {1}  until {2:yyyy-MM-dd}" -f $cert.Thumbprint, $cert.Subject, $cert.NotAfter)

$signed = 0
$targets = @()
foreach ($name in @("TTSkyAnnot.exe", "ttskyannot-cli.exe")) { $targets += (Join-Path $Dist $name) }
foreach ($x in $Extra) { $targets += $x }
foreach ($exe in $targets) {
    $name = Split-Path $exe -Leaf
    if (-not (Test-Path $exe)) { Write-Warning "missing $exe"; continue }
    & $signtool sign /sha1 $cert.Thumbprint /fd SHA256 /d "TTSkyAnnot" $exe | Out-Null
    if ($LASTEXITCODE -ne 0) {
        if ($Require) { throw "signing $name failed (is it still running?)" }
        Write-Warning "signing $name failed — close TTSkyAnnot and run sign.ps1 again"
        continue
    }
    & $signtool verify /pa $exe | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "the signature on $name does not verify" }
    Write-Host "signed $name"
    $signed++
}
if ($signed -eq 0 -and $Require) { throw "nothing was signed" }
