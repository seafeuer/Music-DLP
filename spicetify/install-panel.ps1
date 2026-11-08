# Install the Music DLP panel into Spotify.
#
# Copies the panel into Spicetify's CustomApps, bakes in the bridge port and
# token from Music DLP's config, registers it, and applies.
#
# Run this once after installing. Music DLP rewrites the port and token in
# the installed panel at every start, so a bridge change does not need a
# reinstall - only a restart of Music DLP and a reload of Spotify.
#
# Usage:  .\install-panel.ps1
#         .\install-panel.ps1 -SkipApply   (register only; does not restart
#                                          Spotify, for a dry run or a test)

param(
    [switch]$SkipApply
)

$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$panel = Join-Path $here 'musicdlp-panel'
$spicetifyRoot = Join-Path $env:APPDATA 'spicetify'
$dest = Join-Path $spicetifyRoot 'CustomApps\musicdlp'
# ------------------------------------------------------------- find the CLI
#
# Spicetify has no fixed install location. Look on PATH first, then in the
# folders its installer and package managers use, and finally for an
# unpacked spicetify-cli build under the user's profile. Nothing here names
# a specific computer: a fresh download must work for everyone.

function Find-Spicetify {
    $command = Get-Command spicetify -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'spicetify\spicetify.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\spicetify\spicetify.exe'),
        (Join-Path $env:USERPROFILE 'spicetify-cli\spicetify.exe'),
        (Join-Path $env:USERPROFILE 'spicetify\spicetify.exe')
    )
    if ($env:PROGRAMFILES) {
        $candidates += Join-Path $env:PROGRAMFILES 'Spicetify\spicetify.exe'
    }
    if (${env:PROGRAMFILES(X86)}) {
        $candidates += Join-Path ${env:PROGRAMFILES(X86)} 'Spicetify\spicetify.exe'
    }
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path)) { return $path }
    }

    # An unpacked release (spicetify-cli-<version>\bin\spicetify.exe) or a
    # winget/scoop install that is not on PATH yet.
    $globs = @(
        (Join-Path $env:USERPROFILE 'spicetify-cli*\bin\spicetify.exe'),
        (Join-Path $env:LOCALAPPDATA 'spicetify-cli*\bin\spicetify.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages\Spicetify*\spicetify.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages\Spicetify*\bin\spicetify.exe'),
        (Join-Path $env:USERPROFILE 'scoop\shims\spicetify.exe')
    )
    foreach ($pattern in $globs) {
        $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    return $null
}

$spicetify = Find-Spicetify
if (-not $spicetify) {
    throw "spicetify.exe was not found. Install Spicetify from https://spicetify.app, then run this again. If it is installed somewhere unusual, add its folder to PATH."
}

# ---------------------------------------------------------------- find config

function Find-MusicDlpConfig {
    $candidates = @()
    if ($env:MUSICDLP_HOME) {
        $candidates += Join-Path $env:MUSICDLP_HOME 'data\config.json'
    }
    $candidates += Join-Path $env:APPDATA 'MusicDLP\config.json'
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    return $null
}

$configPath = Find-MusicDlpConfig
if (-not $configPath) {
    throw "Music DLP's config.json was not found. Run the Music DLP app once first, then run this again."
}

Write-Host "Reading settings from $configPath"
$config = Get-Content $configPath -Raw -Encoding utf8 | ConvertFrom-Json

# ------------------------------------------------------------- bridge token

function New-Token {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    return ([Convert]::ToBase64String($bytes) -replace '\+', '-' -replace '/', '_' -replace '=', '')
}

# A config written by an older build, or a minimal one, may not have the
# key yet. Windows PowerShell's PSCustomObject refuses to set a property
# that does not exist, so add it before reading or writing it.
if (-not ($config.PSObject.Properties.Name -contains 'bridge_token')) {
    $config | Add-Member -NotePropertyName bridge_token -NotePropertyValue '' -Force
}
if (-not $config.bridge_token) {
    $config.bridge_token = New-Token
    Write-Host 'Generated a bridge token and saved it to the Music DLP config.'
}

$port = 8317
if ($config.bridge_port) { $port = [int]$config.bridge_port }
$dlpFolder = 'Music DLP'
if ($config.spotify_dlp_folder) { $dlpFolder = [string]$config.spotify_dlp_folder }

# Write the token back so the running app and the panel agree.
$config | ConvertTo-Json -Depth 8 | Set-Content -Path $configPath -Encoding utf8

# -------------------------------------------------------------- install files

if (-not (Test-Path $panel)) { throw "Panel sources not found at $panel" }
New-Item -ItemType Directory -Path $dest -Force | Out-Null

$configJson = @{ port = $port; token = $config.bridge_token; dlpFolder = $dlpFolder } | ConvertTo-Json -Compress
$index = Get-Content (Join-Path $panel 'index.js') -Raw -Encoding utf8
$index = $index.Replace('__MUSICDLP_CONFIG__', $configJson)
Set-Content -Path (Join-Path $dest 'index.js') -Value $index -Encoding utf8 -NoNewline

Copy-Item (Join-Path $panel 'style.css') (Join-Path $dest 'style.css') -Force
Copy-Item (Join-Path $panel 'manifest.json') (Join-Path $dest 'manifest.json') -Force

Write-Host "Installed the panel to $dest"

# ------------------------------------------------------- register custom app

$iniPath = Join-Path $spicetifyRoot 'config-xpui.ini'
if (-not (Test-Path $iniPath)) { throw "Spicetify config not found at $iniPath" }

$ini = Get-Content $iniPath -Raw
$match = [regex]::Match($ini, '(?m)^custom_apps\s*=\s*(.*)$')
$current = if ($match.Success) { $match.Groups[1].Value.Trim() } else { '' }
# Spicetify stores these as a "|"-separated list.
$apps = @($current -split '[\s,|]+' | ForEach-Object { $_.Trim() } | Where-Object { $_ })

if ($apps -notcontains 'musicdlp') { $apps += 'musicdlp' }

$joined = $apps -join '|'
$wanted = "custom_apps           = $joined"
if (-not $match.Success -or $match.Value -ne $wanted) {
    if ($match.Success) {
        $ini = $ini.Remove($match.Index, $match.Length).Insert($match.Index, $wanted)
    } else {
        $ini = $ini.TrimEnd() + "`r`n$wanted`r`n"
    }
    Set-Content -Path $iniPath -Value $ini -NoNewline -Encoding utf8
    Write-Host "Registered custom apps: $joined"
} else {
    Write-Host 'Custom apps already registered.'
}

# --------------------------------------------------------------- apply it all

if ($SkipApply) {
    Write-Host ''
    Write-Host 'Skipped applying Spicetify (-SkipApply). Start Spotify after running this again without the switch.'
    exit 0
}

Write-Host 'Applying Spicetify (this closes Spotify)...'
Get-Process Spotify -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

# A "spicetify-apply.ps1" near the CLI applies and then re-runs a local
# wrapper patch some Spotify/Spicetify pairs need. When it is absent (a
# stock install), run the CLI's own apply. Both the CLI's own folder and
# its parent are checked, because an unpacked release keeps the binary in
# bin\ while a custom wrapper sits in the release root.
$cli_dir = Split-Path -Parent $spicetify
$applyScript = $null
foreach ($candidate in @(
    (Join-Path $cli_dir 'spicetify-apply.ps1'),
    (Join-Path (Split-Path -Parent $cli_dir) 'spicetify-apply.ps1')
)) {
    if ($candidate -and (Test-Path $candidate)) { $applyScript = $candidate; break }
}
if ($applyScript) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Resolve-Path $applyScript)
} else {
    & $spicetify apply -a
}
if ($LASTEXITCODE -ne 0) { throw "Apply failed with exit code $LASTEXITCODE" }

Write-Host ''
Write-Host 'Done. Start Spotify and open Music DLP in the left nav.'
