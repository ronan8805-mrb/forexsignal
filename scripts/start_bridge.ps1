# Starts the ATR-EW bridge on this PC and restarts it if it ever exits.
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File scripts\start_bridge.ps1
# Port: 8000 by default; set BRIDGE_PORT=80 in bridge\.env to also allow MT5 WebRequest (ports 80/443 only).

$ErrorActionPreference = "Stop"
$repo   = Split-Path -Parent $PSScriptRoot
$bridge = Join-Path $repo "bridge"
Set-Location $bridge

function Get-DotEnv([string]$key, [string]$default) {
    if (Test-Path ".env") {
        foreach ($line in Get-Content ".env") {
            if ($line -match "^\s*$key\s*=\s*(.+?)\s*$") { return $Matches[1] }
        }
    }
    return $default
}

if (-not (Test-Path "config.yaml")) { throw "bridge\config.yaml not found. Copy config.example.yaml to config.yaml and edit it." }
if (-not (Test-Path ".env"))        { throw "bridge\.env not found. Copy .env.example to .env and fill in your secrets." }

$py = Join-Path $bridge ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
    & $py -m pip install --upgrade pip
    & $py -m pip install -r requirements.txt
}

New-Item -ItemType Directory -Force -Path (Join-Path $bridge "logs") | Out-Null
$log  = Join-Path $bridge "logs\bridge.log"
$port = Get-DotEnv "BRIDGE_PORT" "8000"

# uvicorn logs to stderr; in Windows PowerShell 5.1, "2>&1" + ErrorActionPreference=Stop would turn
# every log line into a terminating error. Relax it for the run loop.
$ErrorActionPreference = "Continue"
while ($true) {
    Write-Host "$(Get-Date -Format s) starting bridge on 127.0.0.1:$port (log: $log)"
    & $py -m uvicorn app.main:app --host 127.0.0.1 --port $port 2>&1 | Tee-Object -FilePath $log -Append
    Write-Warning "$(Get-Date -Format s) bridge exited (code $LASTEXITCODE). Restarting in 5 s..."
    Start-Sleep -Seconds 5
}
