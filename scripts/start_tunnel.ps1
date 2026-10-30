# Exposes the local bridge to TradingView on your free ngrok static domain (HTTPS, port 443).
# Needs once:  ngrok config add-authtoken <YOUR_NGROK_AUTHTOKEN>
# and in bridge\.env:  NGROK_DOMAIN=your-name.ngrok-free.app
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\start_tunnel.ps1

$ErrorActionPreference = "Stop"
$repo   = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repo "bridge\.env"

function Get-DotEnv([string]$key, [string]$default) {
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match "^\s*$key\s*=\s*(.+?)\s*$") { return $Matches[1] }
        }
    }
    return $default
}

$domain = Get-DotEnv "NGROK_DOMAIN" ""
$port   = Get-DotEnv "BRIDGE_PORT" "8000"
if (-not $domain) { throw "Set NGROK_DOMAIN=<your static domain> in bridge\.env (ngrok dashboard > Domains)." }
if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) { throw "ngrok not found. Install: winget install ngrok.ngrok" }

$ErrorActionPreference = "Continue"
while ($true) {
    Write-Host "$(Get-Date -Format s) tunnel https://$domain -> http://127.0.0.1:$port"
    ngrok http $port --url="https://$domain"
    Write-Warning "$(Get-Date -Format s) ngrok exited (code $LASTEXITCODE). Restarting in 5 s..."
    Start-Sleep -Seconds 5
}
