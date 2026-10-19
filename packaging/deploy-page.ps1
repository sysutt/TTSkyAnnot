# Deploy the TTSkyAnnot intro page (and its images) to ttastro.com.
#
#   powershell -File packaging\deploy-page.ps1
#   powershell -File packaging\deploy-page.ps1 -PageDir "D:\path\to\site\ttskyannot"
#
# The page is a normal page of the ttastro.com site: it uses the shared /assets/site.css and /assets/site.js,
# so only this folder has to be uploaded. The update packages themselves live in <webroot>/ttskyannot/updates
# and are uploaded by release.ps1; this script never touches them.

param(
    [string]$PageDir = "D:\OneDrive\一些其他的东西\TTastro\site\ttskyannot",
    [string]$Remote = "ubuntu@168.138.200.230",
    [string]$RemoteKey = (Join-Path $env:USERPROFILE ".ssh\ttastro_arm"),
    [string]$RemoteDir = "/var/www/ttastro/ttskyannot",
    [string]$PublicUrl = "https://www.ttastro.com/ttskyannot/"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path (Join-Path $PageDir "index.html"))) { throw "no index.html in $PageDir" }
$sshOpts = @("-i", $RemoteKey, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new")

Write-Host "== uploading $PageDir" -ForegroundColor Cyan
& ssh @sshOpts $Remote "rm -rf /tmp/ttskyannot-page && mkdir -p /tmp/ttskyannot-page/img /tmp/ttskyannot-page/en" | Out-Null
& scp @sshOpts (Join-Path $PageDir "index.html") "${Remote}:/tmp/ttskyannot-page/" | Out-Null
if (Test-Path (Join-Path $PageDir "img")) {
    & scp @sshOpts (Join-Path $PageDir "img\*") "${Remote}:/tmp/ttskyannot-page/img/" | Out-Null
}
if (Test-Path (Join-Path $PageDir "en\index.html")) {
    & scp @sshOpts (Join-Path $PageDir "en\index.html") "${Remote}:/tmp/ttskyannot-page/en/" | Out-Null
}
if ($LASTEXITCODE -ne 0) { throw "scp failed" }

$cmd = "sudo install -d -o www-data -g www-data '$RemoteDir' '$RemoteDir/img' '$RemoteDir/en' && " +
       "sudo cp -f /tmp/ttskyannot-page/index.html '$RemoteDir/index.html' && " +
       "sudo cp -f /tmp/ttskyannot-page/img/*.jpg '$RemoteDir/img/' && " +
       "sudo cp -f /tmp/ttskyannot-page/en/index.html '$RemoteDir/en/index.html' && " +
       "sudo chown -R www-data:www-data '$RemoteDir' && " +
       "ls -l '$RemoteDir' '$RemoteDir/en'"
& ssh @sshOpts $Remote $cmd | ForEach-Object { Write-Host "   $_" }
if ($LASTEXITCODE -ne 0) { throw "installing the page failed" }

Write-Host "== verifying $PublicUrl" -ForegroundColor Cyan
# Invoke-WebRequest on Windows PowerShell trips over its own .NET request object here; curl is dependable
$status = & curl.exe -s -o NUL -w "%{http_code} %{size_download}" $PublicUrl
if ($status -notmatch '^200 ') { throw "the page answered $status" }
$statusEn = & curl.exe -s -o NUL -w "%{http_code} %{size_download}" ($PublicUrl.TrimEnd("/") + "/en/")
if ($statusEn -notmatch '^200 ') { throw "the English page answered $statusEn" }
Write-Host "   page live ($status)" -ForegroundColor Green
