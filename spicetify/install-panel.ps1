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

$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$panel = Join-Path $here 'musicdlp-panel'
$spicetifyRoot = Join-Path $env:APPDATA 'spicetify'
$dest = Join-Path $spicetifyRoot 'CustomApps\musicdlp'
$spicetify = 'C:\Users\seafe\Tools\spicetify-cli-2.46.4\bin\spicetify.exe'
$applyScript = 'C:\Users\seafe\Tools\spicetify-cli-2.46.4\spicetify-apply.ps1'

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

Write-Host 'Applying Spicetify (this closes Spotify)...'
& powershell -NoProfile -ExecutionPolicy Bypass -File $applyScript
if ($LASTEXITCODE -ne 0) { throw "Apply failed with exit code $LASTEXITCODE" }

Write-Host ''
Write-Host 'Done. Start Spotify and open Music DLP in the left nav.'
