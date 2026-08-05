<#
.SYNOPSIS
  Set up this shell for the ad pipeline. Dot-source it -- do not just run it,
  or the aliases vanish with the child scope:

      . .\activate.ps1

  Provides: comfy-py, ffmpeg, ffprobe, $ComfyRoot, $ComfyOutput.
#>

$ErrorActionPreference = 'Stop'

# --- ComfyUI portable install -------------------------------------------------
$ComfyRoot = 'C:\Users\User\Downloads\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable'
if (-not (Test-Path $ComfyRoot)) {
  # Moved? Look for it rather than failing outright.
  $found = Get-ChildItem -Path 'C:\Users\User' -Directory -Filter 'ComfyUI_windows_portable' `
             -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($found) { $ComfyRoot = $found.FullName }
}

$script:pyPath = Join-Path $ComfyRoot 'python_embeded\python.exe'
$ComfyOutput   = Join-Path $ComfyRoot 'ComfyUI\output'

# --- ffmpeg (winget installs it outside the PATH of already-open shells) ------
$ffmpegExe = $null
$candidates = @(
  "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe"
  "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin\ffmpeg.exe"
)
foreach ($c in $candidates) { if (Test-Path $c) { $ffmpegExe = $c; break } }
if (-not $ffmpegExe) {
  $hit = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Filter 'ffmpeg.exe' `
           -Recurse -Depth 4 -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($hit) { $ffmpegExe = $hit.FullName }
}

# --- expose -------------------------------------------------------------------
$ok = $true

if (Test-Path $script:pyPath) {
  # A function, not an alias, so bare script names resolve against pipeline\
  # exactly as py.cmd does.
  $projectRoot = $PSScriptRoot
  $interpreter = $script:pyPath
  $body = @"
param([Parameter(ValueFromRemainingArguments=`$true)]`$Args)
if (-not `$Args) { Get-ChildItem '$projectRoot\pipeline\*.py' | ForEach-Object { `$_.Name }; return }
`$script = `$Args[0]
if (-not (Test-Path `$script)) {
  `$candidate = Join-Path '$projectRoot\pipeline' `$script
  if (Test-Path `$candidate) { `$script = `$candidate }
}
& '$interpreter' `$script @(`$Args | Select-Object -Skip 1)
"@
  Set-Item -Path function:global:comfy-py -Value ([ScriptBlock]::Create($body))
  Write-Host "  comfy-py  -> $script:pyPath" -ForegroundColor DarkGray
  Write-Host "            (bare script names resolve against pipeline\)" -ForegroundColor DarkGray
} else {
  Write-Host "  comfy-py  MISSING (looked in $ComfyRoot)" -ForegroundColor Red
  $ok = $false
}

if ($ffmpegExe) {
  Set-Alias -Name ffmpeg  -Value $ffmpegExe -Scope Global
  $probe = Join-Path (Split-Path $ffmpegExe -Parent) 'ffprobe.exe'
  if (Test-Path $probe) { Set-Alias -Name ffprobe -Value $probe -Scope Global }
  Write-Host "  ffmpeg    -> $ffmpegExe" -ForegroundColor DarkGray
} else {
  Write-Host "  ffmpeg    MISSING (winget install Gyan.FFmpeg)" -ForegroundColor Yellow
}

Set-Variable -Name ComfyRoot   -Value $ComfyRoot   -Scope Global
Set-Variable -Name ComfyOutput -Value $ComfyOutput -Scope Global

if ($ok) {
  Write-Host ""
  Write-Host "ready. try:" -ForegroundColor Green
  Write-Host "  comfy-py scene.py --selftest"          -ForegroundColor DarkGray
  Write-Host "  comfy-py comfy_client.py --stats"      -ForegroundColor DarkGray
  Write-Host "  comfy-py comfy_client.py --show"       -ForegroundColor DarkGray
}
