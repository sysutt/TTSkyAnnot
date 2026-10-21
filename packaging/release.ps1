# Release a TTSkyAnnot build end to end: bump the version, build, sign, package, upload to ttastro.com and
# verify what the public URL serves. Then install the same build here and point it at the public feed.
#
#   powershell -File packaging\release.ps1                      # 0.2.0 -> 0.2.1, notes from -Notes
#   powershell -File packaging\release.ps1 -Bump minor -Notes "…"
#   powershell -File packaging\release.ps1 -Version 0.2.2 -SkipBuild   # republish an existing build
#
# What it does, in order:
#   1. reads skyannot\__init__.py and works out the next version;
#   2. builds with PyInstaller into dist_new and signs the executables;
#   3. packages (publish.ps1) into the feed folder: TTSkyAnnot-<ver>.zip + version.json with SHA-256;
#   4. uploads the zip first, then version.json, to the server (scp + sudo install) so the feed never points
#      at a package that is not there yet;
#   5. fetches the public version.json and the zip's header back over HTTPS to prove it is live;
#   5b. builds the Windows installer (Inno Setup), signs it and uploads it next to the portable zip;
#   6. installs the same build into dist\TTSkyAnnot, signs it, and sets its update source to the public URL;
#   7. commits the source, pushes it, and tags the release (v<ver>) on GitHub.
#
# Server route: this machine has a working key for the ARM host (see ~\.ssh\ttastro_arm); the deploy notes in
# the site project describe the relay route that is needed when that key is not available.

param(
    [string]$Version = "",
    [ValidateSet("patch", "minor", "major")][string]$Bump = "patch",
    [string]$Notes = "",
    [string]$Feed = "E:\SkyAnnot\updates",
    [string]$BuildDir = "E:\SkyAnnot\dist_new\TTSkyAnnot",
    [string]$InstallDir = "E:\SkyAnnot\dist\TTSkyAnnot",
    [string]$Remote = "ubuntu@168.138.200.230",
    [string]$RemoteKey = (Join-Path $env:USERPROFILE ".ssh\ttastro_arm"),
    [string]$RemoteUpdates = "/var/www/ttastro/ttskyannot/updates",
    [string]$PublicBase = "https://www.ttastro.com/ttskyannot/updates/",
    [switch]$SkipBuild,
    [switch]$SkipInstall,
    [switch]$SkipUpload,
    [switch]$SkipGit,
    [switch]$SkipInstaller,
    [string]$RemoteSetup = "/var/www/ttastro/ttskyannot/setup",
    [string]$SetupBase = "https://www.ttastro.com/ttskyannot/setup/",
    [string]$InnoPath = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$init = Join-Path $root "skyannot\__init__.py"
$sshOpts = @("-i", $RemoteKey, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new")

function Step($text) { Write-Host "== $text" -ForegroundColor Cyan }
function Fail($text) { throw $text }

# ── 1. version ──────────────────────────────────────────────────────────────
$initText = Get-Content $init -Raw
if ($initText -notmatch '__version__\s*=\s*"([^"]+)"') { Fail "cannot read __version__ from $init" }
$source = $Matches[1]
if (-not $Version) {
    $p = $source.Split(".")
    $maj = [int]$p[0]; $min = [int]$p[1]; $pat = [int]$p[2]
    switch ($Bump) {
        "major" { $maj++; $min = 0; $pat = 0 }
        "minor" { $min++; $pat = 0 }
        default { $pat++ }
    }
    $Version = "$maj.$min.$pat"
}
if ($Version -eq $source) {
    Write-Host "version stays $Version (no bump)" -ForegroundColor Yellow
} else {
    Step "version $source -> $Version"
    (Get-Content $init -Raw) -replace '__version__ = "[^"]+"', ('__version__ = "' + $Version + '"') |
        Set-Content -Encoding utf8 $init
}
$zipName = "TTSkyAnnot-$Version.zip"

# ── 2. build and sign ───────────────────────────────────────────────────────
if (-not $SkipBuild) {
    Step "building $Version"
    New-Item -ItemType Directory -Force -Path (Join-Path $root "dist_new"), (Join-Path $root "build_new") | Out-Null
    Push-Location $root
    try {
        & C:\Python314\python.exe -m PyInstaller --noconfirm --distpath dist_new --workpath build_new packaging\skyannot.spec |
            Select-Object -Last 1 | ForEach-Object { Write-Host "   $_" }
        if ($LASTEXITCODE -ne 0) { Fail "PyInstaller failed ($LASTEXITCODE)" }
    } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $BuildDir "TTSkyAnnot.exe"))) { Fail "no build at $BuildDir" }
Step "signing the build"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "sign.ps1") -Dist $BuildDir | Out-Null

# ── 2b. the Windows installer ───────────────────────────────────────────────
# The portable zip is for people who like a folder; the installer is for everyone else: Start Menu entry,
# uninstaller, per-user install by default (no admin prompt), which keeps the program folder writable for
# config.json and the in-app updater. Built before the feed is written, so version.json can point at it.
$script:setupExe = ""
if (-not $SkipInstaller) {
    Step "building the Windows installer"
    $iscc = $InnoPath
    if (-not $iscc) {
        foreach ($c in @((Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
                         (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
                         (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"))) {
            if ($c -and (Test-Path $c)) { $iscc = $c; break }
        }
    }
    if (-not $iscc) {
        Write-Host "   Inno Setup not found; the installer is skipped (winget install JRSoftware.InnoSetup)" -ForegroundColor Yellow
    } else {
        $setupDir = Join-Path $root "setup"
        New-Item -ItemType Directory -Force -Path $setupDir | Out-Null
        & $iscc ("/DVersion=" + $Version) ("/DBuildDir=" + $BuildDir) (Join-Path $PSScriptRoot "installer.iss") |
            Select-Object -Last 3 | ForEach-Object { Write-Host "   $_" }
        if ($LASTEXITCODE -ne 0) { Fail "Inno Setup failed ($LASTEXITCODE)" }
        $setupExe = Join-Path $setupDir ("TTSkyAnnot-Setup-" + $Version + ".exe")
        if (-not (Test-Path $setupExe)) { Fail "no installer at $setupExe" }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "sign.ps1") `
            -Dist $InstallDir -Extra $setupExe | Select-Object -Last 2 | ForEach-Object { Write-Host "   $_" }
        Write-Host ("   built " + (Split-Path $setupExe -Leaf) + " (" + [math]::Round((Get-Item $setupExe).Length / 1MB, 1) + " MB)") -ForegroundColor Green
        $script:setupExe = $setupExe
    }
}

# ── 3. package ──────────────────────────────────────────────────────────────
Step "packaging into $Feed"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "publish.ps1") `
    -Version $Version -Notes $Notes -Feed $Feed -Dist $BuildDir -Keep 2 -SetupPath $setupExe | Select-Object -Last 4 | ForEach-Object { Write-Host "   $_" }
$zipPath = Join-Path $Feed $zipName
$feedPath = Join-Path $Feed "version.json"
if (-not (Test-Path $zipPath)) { Fail "no package at $zipPath" }
$feedData = Get-Content $feedPath -Raw -Encoding UTF8 | ConvertFrom-Json
$sha = $feedData.sha256
$size = [int64]$feedData.size

# ── 4. upload (zip first, feed last) ────────────────────────────────────────
if (-not $SkipUpload) {
    Step "uploading $([math]::Round($size / 1MB, 1)) MB to $Remote"
    & ssh @sshOpts $Remote "mkdir -p /tmp/ttskyannot-upload && rm -f /tmp/ttskyannot-upload/*" | Out-Null
    & scp @sshOpts $zipPath "${Remote}:/tmp/ttskyannot-upload/" | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "scp of the package failed" }
    & scp @sshOpts $feedPath "${Remote}:/tmp/ttskyannot-upload/" | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "scp of version.json failed" }
    $remoteCmd = "sudo install -d -o www-data -g www-data '$RemoteUpdates' && " +
                 "sudo mv -f /tmp/ttskyannot-upload/$zipName '$RemoteUpdates/' && " +
                 "sudo mv -f /tmp/ttskyannot-upload/version.json '$RemoteUpdates/' && " +
                 "sudo chown www-data:www-data '$RemoteUpdates/$zipName' '$RemoteUpdates/version.json' && " +
                 "ls -l '$RemoteUpdates'"
    & ssh @sshOpts $Remote $remoteCmd | ForEach-Object { Write-Host "   $_" }
    if ($LASTEXITCODE -ne 0) { Fail "installing the files on the server failed" }
    # the feed only ever points at the newest package, so the server keeps the same two the local folder does
    $pruneCmd = "cd '$RemoteUpdates' && ls -1t TTSkyAnnot-*.zip | tail -n +3 | xargs -r sudo rm -f && ls -l"
    & ssh @sshOpts $Remote $pruneCmd | ForEach-Object { Write-Host "   $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "   (could not prune the older packages on the server)" -ForegroundColor Yellow }
}

# ── 4b. upload the installer ────────────────────────────────────────────────
if ($setupExe -and (Test-Path $setupExe) -and -not $SkipUpload) {
    Step "uploading the installer"
    $setupName = Split-Path $setupExe -Leaf
    & scp @sshOpts $setupExe "${Remote}:/tmp/$setupName" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   installer upload failed (skipped)" -ForegroundColor Yellow
    } else {
        $cmd = "sudo install -d -o www-data -g www-data '$RemoteSetup' && " +
               "sudo install -o www-data -g www-data -m 644 '/tmp/$setupName' '$RemoteSetup/$setupName' && " +
               "sudo install -o www-data -g www-data -m 644 '/tmp/$setupName' '$RemoteSetup/TTSkyAnnot-Setup.exe' && " +
               "ls -l '$RemoteSetup' | tail -4"
        & ssh @sshOpts $Remote $cmd | ForEach-Object { Write-Host "   $_" }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "   installing the installer on the server failed" -ForegroundColor Yellow
        } else {
            $live = & curl.exe -s -o NUL -w "%{http_code} %{size_download}" ($SetupBase + "TTSkyAnnot-Setup.exe")
            Write-Host ("   live: " + $live) -ForegroundColor Green
            # keep the two newest installers on the server as well
            $prune = "cd '$RemoteSetup' && ls -1t TTSkyAnnot-Setup-*.exe | tail -n +3 | xargs -r sudo rm -f"
            & ssh @sshOpts $Remote $prune | Out-Null
        }
    }
}
if ($setupExe -and (Test-Path $setupExe)) {
    # keep the two newest installers locally
    Get-ChildItem (Split-Path $setupExe -Parent) -Filter "TTSkyAnnot-Setup-*.exe" |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip 2 |
        ForEach-Object { Remove-Item $_.FullName -Force; Write-Host ("   removed old installer: " + $_.Name) }
}

# ── 5. verify what the public URL serves ────────────────────────────────────
if (-not $SkipUpload) {
    Step "verifying $PublicBase"
    $live = Invoke-RestMethod -Uri ($PublicBase + "version.json") -TimeoutSec 30
    if ($live.version -ne $Version) { Fail "public feed says $($live.version), expected $Version" }
    if ($live.sha256 -ne $sha) { Fail "public feed has a different sha256" }
    # a HEAD through Invoke-WebRequest is buggy on Windows PowerShell for large files; HttpClient is not
    Add-Type -AssemblyName System.Net.Http
    $client = New-Object System.Net.Http.HttpClient
    $req = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Head, ($PublicBase + $zipName))
    $resp = $client.SendAsync($req).Result
    if (-not $resp.IsSuccessStatusCode) { Fail "the public package answered $([int]$resp.StatusCode)" }
    $liveLen = [int64]$resp.Content.Headers.ContentLength
    $client.Dispose()
    if ($liveLen -ne $size) {
        Fail "public package is $liveLen bytes, expected $size"
    }
    Write-Host "   feed live: version $Version, $([math]::Round($size / 1MB, 1)) MB" -ForegroundColor Green
}

# ── 6. install here ─────────────────────────────────────────────────────────
if (-not $SkipInstall) {
    Step "installing into $InstallDir"
    Get-Process -Name TTSkyAnnot -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 1
    Copy-Item (Join-Path $BuildDir "TTSkyAnnot.exe") $InstallDir -Force
    Copy-Item (Join-Path $BuildDir "ttskyannot-cli.exe") $InstallDir -Force
    robocopy $BuildDir $InstallDir /E /R:0 /W:0 /NFL /NDL /NJH /NJS /NP | Out-Null
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "sign.ps1") -Dist $InstallDir | Out-Null
    $cfgPath = Join-Path $InstallDir "config.json"
    if (Test-Path $cfgPath) {
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
        $cfg | Add-Member -NotePropertyName update_url -NotePropertyValue $PublicBase -Force
        [System.IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json -Depth 5),
            (New-Object System.Text.UTF8Encoding($false)))
    }
    $sig = (Get-AuthenticodeSignature (Join-Path $InstallDir "TTSkyAnnot.exe")).Status
    Write-Host "   installed, signature $sig" -ForegroundColor Green
}

# ── 7. commit and push the source ───────────────────────────────────────────
# The release is only half done when it is on the server: the code that produced it belongs on GitHub as well,
# tagged with the same version. Anything the last release left behind (the version bump, notes, new modules)
# is committed here, so the repository never drifts behind the feed. A failed push is a warning, not an error:
# the package is already live and the commit stays in the local repository.
#
# Git is called through cmd.exe: Windows PowerShell turns a native command's stderr into a terminating error
# while $ErrorActionPreference is "Stop", and git writes its progress and its warnings there.
function GitLines([string]$line) {
    return @(& cmd /c ("git -C `"" + $root + "`" " + $line + " 2>&1"))
}
if (-not $SkipGit) {
    Step "committing and pushing the source"
    GitLines "rev-parse --is-inside-work-tree" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   not a git checkout, skipped" -ForegroundColor Yellow
    } else {
        $branch = (@(GitLines "rev-parse --abbrev-ref HEAD") -join "").Trim()
        GitLines "add -A" | Out-Null
        $dirty = @(GitLines "status --porcelain") | Where-Object { $_.Trim() }
        # a 115 MB package in the tree is the one thing GitHub will refuse outright: check before committing
        $big = @()
        foreach ($f in @(GitLines "diff --cached --name-only") | Where-Object { $_.Trim() }) {
            $full = Join-Path $root $f.Trim()
            if ((Test-Path -LiteralPath $full -PathType Leaf) -and ((Get-Item -LiteralPath $full).Length -gt 90MB)) {
                $big += $f.Trim()
            }
        }
        if ($big.Count -gt 0) {
            Write-Host ("   not committing: over 90 MB - " + ($big -join ", ")) -ForegroundColor Yellow
            Write-Host "   (a feed or build folder inside the repository? add it to .gitignore)" -ForegroundColor Yellow
            GitLines "reset -q" | Out-Null
        } elseif ($dirty.Count -gt 0) {
            $msg = "release: v" + $Version
            if ($Notes) { $msg = $msg + "`n`n" + $Notes }
            $msgFile = Join-Path $env:TEMP ("ttskyannot-release-msg-" + $Version + ".txt")
            [System.IO.File]::WriteAllText($msgFile, $msg, (New-Object System.Text.UTF8Encoding($false)))
            GitLines ("commit -q -F `"" + $msgFile + "`"") | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Host "   commit failed" -ForegroundColor Yellow
            } else {
                Write-Host ("   committed " + $dirty.Count + " file(s): " + ((GitLines "log -1 --oneline") -join ""))
            }
            Remove-Item -LiteralPath $msgFile -Force -ErrorAction SilentlyContinue
        } else {
            Write-Host "   working tree clean"
        }
        $push = GitLines ("push origin " + $branch)
        $push | Where-Object { "$_".Trim() } | ForEach-Object { Write-Host "   $_" }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "   push failed (offline or no key); the commit is kept locally" -ForegroundColor Yellow
        }
        $tag = "v" + $Version
        if (-not (@(GitLines ("tag -l " + $tag)) | Where-Object { $_.Trim() })) {
            GitLines ("tag -a " + $tag + " -m `"TTSkyAnnot " + $Version + "`"") | Out-Null
            $tagOut = GitLines ("push origin " + $tag)
            $tagOut | Where-Object { "$_".Trim() } | ForEach-Object { Write-Host "   $_" }
            if ($LASTEXITCODE -ne 0) {
                Write-Host "   tag push failed; the tag is kept locally" -ForegroundColor Yellow
            }
        } else {
            Write-Host "   tag $tag already exists"
        }
    }
}

Step "done: $Version"
Write-Host "   package: $zipPath ($([math]::Round($size / 1MB, 1)) MB)"
Write-Host "   feed:    $PublicBase"
Write-Host "   page:    https://www.ttastro.com/ttskyannot/"
