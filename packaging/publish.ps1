# Publish a TTSkyAnnot build as an update: zip the program folder, hash it, and write version.json.
#
#   powershell -ExecutionPolicy Bypass -File packaging\publish.ps1 -Version 0.2.0 -Notes "批量修正文件头"
#
# The feed folder defaults to E:\SkyAnnot\updates; point the program's "更新源" there, or copy the folder's
# contents to a web server / network share and use that address instead. The user's config.json is left out of
# the package on purpose: the updater keeps the one already installed.

param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Notes = "",
    [string]$Feed = "E:\SkyAnnot\updates",
    [string]$Dist = "E:\SkyAnnot\dist\TTSkyAnnot",
    [int]$Keep = 2
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# The version the program reports must be the version being published, or the updater would offer this build
# again and again.
$init = Get-Content (Join-Path $root "skyannot\__init__.py") -Raw
if ($init -notmatch '__version__\s*=\s*"([^"]+)"') { throw "cannot read __version__ from skyannot\__init__.py" }
$source = $Matches[1]
if ($source -ne $Version) {
    throw "skyannot\__init__.py says $source but you are publishing $Version - bump __version__ first"
}

$exe = Join-Path $Dist "TTSkyAnnot.exe"
if (-not (Test-Path $exe)) { throw "no build at $Dist" }
$sig = Get-AuthenticodeSignature $exe
if ($sig.Status -ne "Valid") { Write-Warning "the build is not signed ($($sig.Status)); publishing anyway" }

New-Item -ItemType Directory -Force -Path $Feed | Out-Null
$stageRoot = Join-Path $env:TEMP ("ttskyannot-publish-" + [guid]::NewGuid().ToString("N"))
$stage = Join-Path $stageRoot "TTSkyAnnot"
try {
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    # copy the build without the things that belong to this machine
    $rc = Start-Process robocopy -ArgumentList @(
        "`"$Dist`"", "`"$stage`"", "/E", "/R:0", "/W:0", "/NFL", "/NDL", "/NJH", "/NJS", "/NP",
        "/XF", "config.json", "*.old", "*.log", "/XD", "*.old"
    ) -Wait -PassThru -NoNewWindow
    if ($rc.ExitCode -ge 8) { throw "robocopy failed with $($rc.ExitCode)" }

    $zip = Join-Path $Feed "TTSkyAnnot-$Version.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $stage, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $false)

    $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    $size = (Get-Item $zip).Length
    $feedData = [ordered]@{
        version = $Version
        url     = (Split-Path $zip -Leaf)
        sha256  = $hash
        size    = $size
        notes   = $Notes
        built   = (Get-Date).ToString("yyyy-MM-dd HH:mm")
    }
    $json = $feedData | ConvertTo-Json -Depth 3
    [System.IO.File]::WriteAllText((Join-Path $Feed "version.json"), $json, (New-Object System.Text.UTF8Encoding($false)))

    "published {0} ({1:N1} MB)" -f $Version, ($size / 1MB)
    "  package: $zip"
    "  feed:    $(Join-Path $Feed 'version.json')"
    "  sha256:  $hash"

    # the packages are ~115 MB each: keep the newest few, drop the rest
    $keep = [Math]::Max(1, $Keep)
    Get-ChildItem $Feed -Filter "TTSkyAnnot-*.zip" | Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $keep | ForEach-Object {
            Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
            "  removed old package: $($_.Name)"
        }
}
finally {
    if (Test-Path $stageRoot) { Remove-Item $stageRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
