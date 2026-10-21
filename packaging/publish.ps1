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
    [int]$Keep = 2,
    [string]$SetupPath = "",
    [string]$SetupBase = "https://www.ttastro.com/ttskyannot/setup/"
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

    # a note in the zip: the usual reason a fresh copy will not start is that it was never really extracted
    $readme = @"
TTSkyAnnot $Version

1. 把整个压缩包解压到一个普通文件夹（不要在压缩包里直接双击运行，也不要只复制 exe 出来）。
2. 打开解压出来的 TTSkyAnnot.exe。程序是绿色版，不需要安装。

运行时报 "DLL load failed while importing QtCore / 找不到指定的模块"，
基本都是没有完整解压（缺少 _internal 文件夹），或者杀毒软件隔离了里面的文件。
重新完整解压一次即可；仍然不行时，把杀毒软件的隔离区里 TTSkyAnnot 相关的文件恢复出来。

The program is a portable one-folder build: extract the whole zip to a normal folder first,
then run TTSkyAnnot.exe. Running the exe from inside the zip, or copying only the exe,
gives "DLL load failed while importing QtCore".
"@
    [System.IO.File]::WriteAllText((Join-Path $stage "使用说明.txt"), $readme,
        (New-Object System.Text.UTF8Encoding($true)))

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
    # The installer lives under /ttskyannot/setup/ rather than beside the feed, so its address is absolute:
    # an installed copy downloads it and hands over to the wizard, which knows how to replace itself.
    if ($SetupPath -and (Test-Path $SetupPath)) {
        $feedData.setup = $SetupBase.TrimEnd("/") + "/" + (Split-Path $SetupPath -Leaf)
        $feedData.setup_sha256 = (Get-FileHash $SetupPath -Algorithm SHA256).Hash.ToLower()
        $feedData.setup_size = (Get-Item $SetupPath).Length
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
