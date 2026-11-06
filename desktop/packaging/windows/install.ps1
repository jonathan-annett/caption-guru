<#
  Caption Guru - Windows installer (from source, any branch or tag)

  Assumes nothing is installed. Downloads into %LOCALAPPDATA%\CaptionGuru:
    - uv (Python manager) -> Python 3.12, private to this install
    - Node.js LTS (portable zip) + pnpm via corepack, only to build the frontend
    - the Caption Guru source for the branch/tag you choose
  then builds the frontend, installs the desktop app into a venv, and adds
  Start Menu + Desktop shortcuts. No admin rights needed, except the optional
  Microsoft VC++ runtime / WebView2 installers if your PC is missing them.

  Usage:
    Download CaptionGuru-Setup.cmd from
      https://github.com/jonathan-annett/caption-guru/releases/tag/installer
    and double-click it (or double-click install.cmd next to this file).
    Non-interactive:
      .\install.ps1 -Ref feat/sherpa-onnx-streaming -Yes
    Environment overrides: CAPTION_GURU_REF, CAPTION_GURU_HOME
#>
param(
    [string]$Ref = $env:CAPTION_GURU_REF,
    [string]$InstallDir = $env:CAPTION_GURU_HOME,
    [switch]$Yes,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is 10x faster without the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Owner = 'jonathan-annett'
$Repo = 'caption-guru'
$PythonVersion = '3.12'
$NodeMajor = '22'

if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA 'CaptionGuru' }
$Tools = Join-Path $InstallDir 'tools'
$AppDir = Join-Path $InstallDir 'app'
$Venv = Join-Path $InstallDir 'venv'
$Downloads = Join-Path $InstallDir 'downloads'
$StartMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Caption Guru'
$DesktopDir = [Environment]::GetFolderPath('Desktop')
$DesktopFolder = Join-Path $DesktopDir 'Caption Guru'

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Info($msg) { Write-Host "    $msg" }
function Warn($msg) { Write-Host "    ! $msg" -ForegroundColor Yellow }

function Download($url, $out) {
    Info "downloading $url"
    $tmp = "$out.part"
    for ($i = 1; $i -le 3; $i++) {
        try {
            Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -Headers @{ 'User-Agent' = 'caption-guru-installer' }
            Move-Item -Force $tmp $out
            return
        } catch {
            if ($i -eq 3) { throw "Download failed: $url`n$($_.Exception.Message)" }
            Warn "retrying ($i)..."
            Start-Sleep -Seconds (2 * $i)
        }
    }
}

function GitHubApi($path) {
    $h = @{ 'User-Agent' = 'caption-guru-installer'; 'Accept' = 'application/vnd.github+json' }
    if ($env:GITHUB_TOKEN) { $h['Authorization'] = "Bearer $env:GITHUB_TOKEN" }
    Invoke-RestMethod -Uri "https://api.github.com/repos/$Owner/$Repo/$path" -Headers $h -UseBasicParsing
}

function Run($exe, [string[]]$argList, $cwd) {
    Info ("> " + (Split-Path $exe -Leaf) + " " + ($argList -join ' '))
    Push-Location $cwd
    try {
        & $exe @argList
        if ($LASTEXITCODE -ne 0) { throw "$(Split-Path $exe -Leaf) exited with code $LASTEXITCODE" }
    } finally { Pop-Location }
}

function Remove-Dir($path) {
    # rd with a \\?\ prefix copes with node_modules paths longer than 260 chars.
    if (Test-Path -LiteralPath $path) {
        cmd /c "rd /s /q `"\\?\$path`" 2>nul" | Out-Null
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
}

function New-Shortcut($path, $target, $arguments, $workdir, $description) {
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($path)
    $s.TargetPath = $target
    $s.Arguments = $arguments
    $s.WorkingDirectory = $workdir
    $s.Description = $description
    $s.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,138"
    $s.Save()
}

function Remove-Shortcuts {
    if (Test-Path $StartMenu) { Remove-Item -Recurse -Force $StartMenu }
    Get-ChildItem $DesktopDir -Filter 'Caption Guru*.lnk' -ErrorAction SilentlyContinue | Remove-Item -Force
    # Only remove our own shortcuts from the Desktop folder, never anything the user put there.
    if (Test-Path $DesktopFolder) {
        Get-ChildItem $DesktopFolder -Include '*.lnk', '*.url' -Recurse -ErrorAction SilentlyContinue | Remove-Item -Force
        if (-not (Get-ChildItem $DesktopFolder -Force -ErrorAction SilentlyContinue)) { Remove-Item -Force $DesktopFolder }
    }
}

# ---------------------------------------------------------------- uninstall
if ($Uninstall) {
    Step "Uninstalling Caption Guru from $InstallDir"
    Remove-Shortcuts
    Remove-Dir $InstallDir
    Info "Removed. Downloaded speech models are kept in:"
    Info "  $env:USERPROFILE\.cache\caption-guru  and  $env:USERPROFILE\.cache\huggingface"
    Info "Delete those folders too if you want the disk space back."
    Info "Your saved caption settings are in $env:APPDATA\CaptionGuru (delete it to start fresh)."
    return
}

Write-Host ""
Write-Host "  Caption Guru - Windows installer" -ForegroundColor Green
Write-Host "  Installs into: $InstallDir"

# ---------------------------------------------------------------- choose branch / tag
if (-not $Ref) {
    $choices = @()
    try {
        $branches = GitHubApi 'branches?per_page=50'
        $tags = GitHubApi 'tags?per_page=6'
        $choices += 'main'
        $choices += ($branches | ForEach-Object { $_.name } | Where-Object { $_ -ne 'main' })
        $choices += ($tags | ForEach-Object { $_.name } | Where-Object { $_ -ne 'installer' })
    } catch {
        Warn "Couldn't list branches from GitHub ($($_.Exception.Message)). You can still type one."
        $choices = @('main')
    }
    Write-Host ""
    Write-Host "  Which version do you want to install?"
    for ($i = 0; $i -lt $choices.Count; $i++) {
        $label = $choices[$i]
        if ($i -eq 0) { $label += '   (stable - default)' }
        Write-Host ("   [{0}] {1}" -f ($i + 1), $label)
    }
    Write-Host ""
    $ans = Read-Host "  Enter a number, or type a branch/tag/commit name [1]"
    if (-not $ans) { $Ref = 'main' }
    elseif ($ans -match '^\d+$' -and [int]$ans -ge 1 -and [int]$ans -le $choices.Count) { $Ref = $choices[[int]$ans - 1] }
    else { $Ref = $ans.Trim() }
}
Info "Version: $Ref"

New-Item -ItemType Directory -Force -Path $InstallDir, $Tools, $Downloads | Out-Null

# ---------------------------------------------------------------- system prerequisites
Step "Checking Windows prerequisites"
if (-not [Environment]::Is64BitOperatingSystem) { throw "Caption Guru needs 64-bit Windows." }

# Microsoft VC++ runtime (needed by the speech engines' native code)
$vcOk = (Test-Path "$env:SystemRoot\System32\msvcp140.dll") -and (Test-Path "$env:SystemRoot\System32\vcruntime140_1.dll")
if ($vcOk) { Info "VC++ runtime: present" }
else {
    Warn "Microsoft Visual C++ runtime is missing - installing it (Windows will ask for permission)."
    $vc = Join-Path $Downloads 'vc_redist.x64.exe'
    Download 'https://aka.ms/vs/17/release/vc_redist.x64.exe' $vc
    $p = Start-Process -FilePath $vc -ArgumentList '/install', '/passive', '/norestart' -Verb RunAs -Wait -PassThru
    if ($p.ExitCode -notin 0, 1638, 3010) { Warn "VC++ installer returned $($p.ExitCode); the app may not start." }
}

# Edge WebView2 runtime (the fullscreen caption window). Built into Windows 11.
$wv2Key = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
$wv2Ok = $false
foreach ($root in 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients', 'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients', 'HKCU:\Software\Microsoft\EdgeUpdate\Clients') {
    $k = Get-ItemProperty -Path "$root\$wv2Key" -ErrorAction SilentlyContinue
    if ($k -and $k.pv -and $k.pv -ne '0.0.0.0') { $wv2Ok = $true }
}
if ($wv2Ok) { Info "WebView2 runtime: present" }
else {
    Warn "Edge WebView2 runtime is missing - installing it."
    $wv = Join-Path $Downloads 'MicrosoftEdgeWebview2Setup.exe'
    Download 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' $wv
    Start-Process -FilePath $wv -ArgumentList '/silent', '/install' -Wait
}

# ---------------------------------------------------------------- uv + Python
Step "Setting up Python $PythonVersion (via uv)"
$UvDir = Join-Path $Tools 'uv'
$Uv = Join-Path $UvDir 'uv.exe'
if (-not (Test-Path $Uv)) {
    $zip = Join-Path $Downloads 'uv.zip'
    Download 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' $zip
    if (Test-Path $UvDir) { Remove-Item -Recurse -Force $UvDir }
    Expand-Archive -Path $zip -DestinationPath $UvDir -Force
}
# Keep uv's Python + cache inside our folder so uninstall is just "delete the folder".
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Tools 'python'
$env:UV_CACHE_DIR = Join-Path $InstallDir 'cache\uv'
$env:UV_NO_PROGRESS = '1'
$env:UV_PYTHON_PREFERENCE = 'only-managed'   # ignore any half-broken system Python
Run $Uv @('python', 'install', $PythonVersion) $InstallDir

# ---------------------------------------------------------------- Node.js
Step "Setting up Node.js (to build the caption display)"
$NodeExe = $null
$sysNode = Get-Command node -ErrorAction SilentlyContinue
if ($sysNode) {
    $v = (& node -v) -replace '^v', ''
    if ([int]($v.Split('.')[0]) -ge 20) { $NodeExe = $sysNode.Source; Info "Using installed Node $v" }
}
if (-not $NodeExe) {
    $NodeDir = Join-Path $Tools 'node'
    $NodeExe = Join-Path $NodeDir 'node.exe'
    if (-not (Test-Path $NodeExe)) {
        $base = "https://nodejs.org/dist/latest-v$NodeMajor.x"
        $sums = (Invoke-WebRequest -Uri "$base/SHASUMS256.txt" -UseBasicParsing).Content
        $m = [regex]::Match($sums, '([0-9a-f]{64})\s+(node-v[\d.]+-win-x64\.zip)')
        if (-not $m.Success) { throw "Couldn't find the Node.js Windows download." }
        $zip = Join-Path $Downloads $m.Groups[2].Value
        Download "$base/$($m.Groups[2].Value)" $zip
        $hash = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
        if ($hash -ne $m.Groups[1].Value) { throw "Node.js download checksum mismatch." }
        $tmp = Join-Path $Tools 'node-tmp'
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        if (Test-Path $NodeDir) { Remove-Item -Recurse -Force $NodeDir }
        Move-Item (Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName $NodeDir
        Remove-Item -Recurse -Force $tmp
    }
    Info "Node $(& $NodeExe -v)"
}
$NodeBin = Split-Path $NodeExe
$env:PATH = "$NodeBin;$env:PATH"
$env:COREPACK_ENABLE_DOWNLOAD_PROMPT = '0'
$env:COREPACK_HOME = Join-Path $InstallDir 'cache\corepack'
$Corepack = Join-Path $NodeBin 'corepack.cmd'
if (-not (Test-Path $Corepack)) { $Corepack = (Get-Command corepack -ErrorAction Stop).Source }

# ---------------------------------------------------------------- source
Step "Downloading Caption Guru '$Ref'"
$commit = $null
try { $commit = (GitHubApi "commits/$Ref").sha } catch { Warn "Couldn't resolve '$Ref' via the GitHub API; trying a direct download." }
$srcZip = Join-Path $Downloads 'source.zip'
$got = $false
$urls = @()
if ($commit) { $urls += "https://github.com/$Owner/$Repo/archive/$commit.zip" }
$urls += "https://github.com/$Owner/$Repo/archive/refs/heads/$Ref.zip"
$urls += "https://github.com/$Owner/$Repo/archive/refs/tags/$Ref.zip"
foreach ($u in $urls) {
    try { Download $u $srcZip; $got = $true; break } catch { }
}
if (-not $got) { throw "No branch, tag or commit called '$Ref' was found in $Owner/$Repo." }

$tmp = Join-Path $InstallDir 'app-new'
if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
Expand-Archive -Path $srcZip -DestinationPath $tmp -Force
$extracted = (Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName
if (-not (Test-Path (Join-Path $extracted 'desktop\pyproject.toml'))) { throw "'$Ref' doesn't contain the desktop app (desktop\pyproject.toml)." }
if (Test-Path $AppDir) { Remove-Dir $AppDir }
Move-Item $extracted $AppDir
Remove-Item -Recurse -Force $tmp

# ---------------------------------------------------------------- frontend
Step "Building the caption display (frontend)"
Run $Corepack @('pnpm', 'install', '--frozen-lockfile') $AppDir
Run $Corepack @('pnpm', '--filter', '@captions/display', 'build') $AppDir
$built = Join-Path $AppDir 'packages\display\dist'
if (-not (Test-Path (Join-Path $built 'index.html'))) { throw "Frontend build didn't produce $built\index.html" }
$WebDir = Join-Path $InstallDir 'web'
Remove-Dir $WebDir
Copy-Item -Recurse $built $WebDir
Info "Cleaning up build tools' node_modules"
@(Join-Path $AppDir 'node_modules') + @(Get-ChildItem (Join-Path $AppDir 'packages') -Directory | ForEach-Object { Join-Path $_.FullName 'node_modules' }) |
    ForEach-Object { Remove-Dir $_ }

# ---------------------------------------------------------------- Python app
Step "Installing the desktop app and speech engines (this is the big one)"
$pyproject = Get-Content (Join-Path $AppDir 'desktop\pyproject.toml') -Raw
$extras = @(@('server', 'audio', 'asr', 'desktop') | Where-Object { $pyproject -match "(?m)^$_\s*=" })
$HasStreaming = $pyproject -match '(?m)^streaming\s*='
if ($HasStreaming) { $extras += 'streaming' }
Info "extras: $($extras -join ', ')"
if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
Run $Uv @('venv', '--python', $PythonVersion, $Venv) $InstallDir
$Py = Join-Path $Venv 'Scripts\python.exe'
# Editable install so the app finds the built frontend in the source tree.
Run $Uv @('pip', 'install', '--python', $Py, '-e', ".\desktop[$($extras -join ',')]") $AppDir
$Captions = Join-Path $Venv 'Scripts\captions.exe'
if (-not (Test-Path $Captions)) { throw "Install finished but $Captions is missing." }

# ---------------------------------------------------------------- launchers + shortcuts
Step "Creating shortcuts"
$short = if ($commit) { $commit.Substring(0, 7) } else { '?' }
@"
ref=$Ref
commit=$commit
installed=$(Get-Date -Format s)
"@ | Set-Content -Encoding ASCII (Join-Path $InstallDir 'version.txt')

$Launch = Join-Path $InstallDir 'Caption Guru.cmd'
@"
@echo off
title Caption Guru [$Ref @ $short]
echo Caption Guru  -  version: $Ref ($short)
echo Close this window to stop captions.
echo.
"$Captions" serve --web "$WebDir" %*
if errorlevel 1 (
  echo.
  echo Caption Guru exited with an error. Copy the text above into your bug report.
  pause
)
"@ | Set-Content -Encoding ASCII $Launch

$Uninst = Join-Path $InstallDir 'Uninstall Caption Guru.cmd'
$SelfCopy = Join-Path $InstallDir 'install.ps1'
if ($PSCommandPath) { Copy-Item -Force $PSCommandPath $SelfCopy }
else { Download "https://github.com/$Owner/$Repo/releases/download/installer/install.ps1" $SelfCopy }
@"
@echo off
cd /d "%TEMP%"
copy /y "$SelfCopy" "%TEMP%\caption-guru-uninstall.ps1" >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\caption-guru-uninstall.ps1" -Uninstall -InstallDir "$InstallDir"
pause
"@ | Set-Content -Encoding ASCII $Uninst

Remove-Shortcuts
New-Item -ItemType Directory -Force -Path $StartMenu | Out-Null
$cmdExe = "$env:SystemRoot\System32\cmd.exe"
function Add-Launcher($name, $extraArgs, $desc) {
    $a = "/c `"`"$Launch`" $extraArgs`""
    New-Shortcut (Join-Path $StartMenu "$name.lnk") $cmdExe $a $InstallDir $desc
}
Add-Launcher 'Caption Guru' '' "Live captions ($Ref)"
Add-Launcher 'Caption Guru (windowed)' '--windowed' 'Live captions in a window'
Add-Launcher 'Caption Guru (demo - no mic)' '--demo --windowed' 'Scripted captions for testing the display'
if ($HasStreaming) {
    Add-Launcher 'Caption Guru (streaming - Nemotron)' '--engine sherpa-onnx' 'True word-by-word streaming engine (first run downloads ~460 MB)'
}
# Settings page (only works while Caption Guru is running; it also opens its own
# settings window on launch). Only for versions that ship the page.
if (Test-Path (Join-Path $WebDir 'control.html')) {
    "[InternetShortcut]`r`nURL=http://127.0.0.1:8765/control.html`r`n" |
        Set-Content -Encoding ASCII (Join-Path $StartMenu 'Caption Guru Settings.url')
}
New-Shortcut (Join-Path $StartMenu 'Uninstall Caption Guru.lnk') $cmdExe "/c `"`"$Uninst`"`"" $env:TEMP 'Remove Caption Guru'

# The same shortcuts in a 'Caption Guru' folder on the Desktop.
New-Item -ItemType Directory -Force -Path $DesktopFolder | Out-Null
Copy-Item -Force (Join-Path $StartMenu '*') $DesktopFolder

# ---------------------------------------------------------------- done
Step "Done!"
Info "Installed $Ref ($short) into $InstallDir"
Info "All the shortcuts are in the 'Caption Guru' folder on your Desktop (and the Start Menu)."
if ($HasStreaming) { Info "This version includes the streaming engine: use 'Caption Guru (streaming - Nemotron)'." }
if (Test-Path (Join-Path $WebDir 'control.html')) { Info "A Settings window opens alongside the captions (or use 'Caption Guru Settings' while it runs)." }
Info "The first run downloads a speech model, so give it a minute."
Info "To try another branch later, just run the installer again."

# Show the shortcuts folder.
try { Start-Process explorer.exe -ArgumentList "`"$DesktopFolder`"" } catch { }
