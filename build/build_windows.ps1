# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Fred Tombs
#
# Build "AX43 Movie Adder.exe" (single file, ffmpeg bundled) on Windows.
#
#   powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
#
# Everything it downloads (uv, a private Python, PyInstaller, ffmpeg) lives in
# %LOCALAPPDATA%\ax43build - delete that folder to remove it all. Nothing is
# installed system-wide and no admin rights are needed.
param(
    [string]$Repo = (Split-Path -Parent $PSScriptRoot),
    [switch]$Clean
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster

$Root = Join-Path $env:LOCALAPPDATA "ax43build"
if ($Clean -and (Test-Path $Root)) { Remove-Item -Recurse -Force $Root }
New-Item -ItemType Directory -Force $Root | Out-Null
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Root "python"
$env:UV_CACHE_DIR = Join-Path $Root "cache"

function Get-Zip($url, $name) {
    $dir = Join-Path $Root $name
    if (-not (Test-Path $dir)) {
        $zip = Join-Path $Root "$name.zip"
        Write-Host "Downloading $name ..."
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        New-Item -ItemType Directory -Force $dir | Out-Null
        tar.exe -xf $zip -C $dir
        Remove-Item $zip
    }
    return $dir
}

# 1. tools
$uvDir = Get-Zip "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip" "uv"
$uv = (Get-ChildItem $uvDir -Recurse -Filter uv.exe | Select-Object -First 1).FullName
$ffDir = Get-Zip "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip" "ffmpeg"
$ffBin = (Get-ChildItem $ffDir -Recurse -Filter ffmpeg.exe | Select-Object -First 1).DirectoryName

# 2. private Python + PyInstaller
$venv = Join-Path $Root "venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "Setting up Python ..."
    & $uv venv $venv --python 3.12 --python-preference only-managed
    if ($LASTEXITCODE) { throw "uv venv failed" }
}
& $uv pip install --python "$venv\Scripts\python.exe" pyinstaller tkinterdnd2
if ($LASTEXITCODE) { throw "pip install failed" }

# 3. stage sources + ffmpeg (only what we need: ffmpeg, ffprobe and their DLLs)
$src = Join-Path $Root "src"
if (Test-Path $src) { Remove-Item -Recurse -Force $src }
New-Item -ItemType Directory -Force "$src\ffmpeg" | Out-Null
Copy-Item "$Repo\ax43put.py", "$Repo\ax43gui.py", "$Repo\build\ax43.ico" $src
Copy-Item "$ffBin\ffmpeg.exe", "$ffBin\ffprobe.exe" "$src\ffmpeg"
Copy-Item "$ffBin\*.dll" "$src\ffmpeg"

# 4. build
Write-Host "Building exe ..."
Push-Location $src
try {
    & "$venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed `
        --name "AX43 Movie Adder" --icon "$src\ax43.ico" `
        --add-data "$src\ax43.ico;." `
        --add-binary "$src\ffmpeg\*;ffmpeg" `
        --collect-all tkinterdnd2 `
        --distpath "$Root\dist" --workpath "$Root\work" --specpath "$Root\work" `
        "$src\ax43gui.py"
    if ($LASTEXITCODE) { throw "pyinstaller failed" }
} finally { Pop-Location }

$exe = Join-Path $Root "dist\AX43 Movie Adder.exe"
Write-Host ""
Write-Host "Built: $exe  ($([math]::Round((Get-Item $exe).Length / 1MB)) MB)"
