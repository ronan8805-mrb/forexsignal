# One-time setup for the dip-buy engine (no admin needed):
#   powershell -ExecutionPolicy Bypass -File scripts\setup_dipbuy.ps1
# Creates dipbuy\.venv with 64-bit Python 3.13/3.12/3.11 (the versions MetaTrader5 ships for),
# installs the MetaTrader5 package, and creates dipbuy\config.toml from the example.

$ErrorActionPreference = "Continue"      # PowerShell 5.1 treats pip's stderr notes as errors otherwise
$dip  = Join-Path (Split-Path $PSScriptRoot -Parent) "dipbuy"
$venv = Join-Path $dip ".venv"
$py   = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $py)) {
    $made = $false
    foreach ($ver in @("3.13", "3.12", "3.11")) {
        & py "-$ver" -c "import struct,sys; sys.exit(0 if struct.calcsize('P')==8 else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Creating virtual environment with Python $ver ..."
            & py "-$ver" -m venv $venv
            if ($LASTEXITCODE -eq 0) { $made = $true; break }
        }
    }
    if (-not $made) {
        Write-Host "No 64-bit Python 3.11-3.13 found. Install Python 3.13 (64-bit) from python.org and run this again." -ForegroundColor Red
        exit 1
    }
}

Write-Host "Installing MetaTrader5 package ..."
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r (Join-Path $dip "requirements.txt") --quiet
& $py -c "import MetaTrader5 as m; print('MetaTrader5 package', m.__version__)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Import failed - retrying with NumPy 1.x ..."
    & $py -m pip install "numpy<2" --quiet
    & $py -c "import MetaTrader5 as m; print('MetaTrader5 package', m.__version__)"
    if ($LASTEXITCODE -ne 0) { Write-Host "MetaTrader5 still does not import - send me the error above." -ForegroundColor Red; exit 1 }
}

$cfg = Join-Path $dip "config.toml"
if (-not (Test-Path $cfg)) {
    Copy-Item (Join-Path $dip "config.example.toml") $cfg
    Write-Host "Created dipbuy\config.toml (dry_run = true)."
} else {
    Write-Host "dipbuy\config.toml already exists - left unchanged."
}

Write-Host ""
Write-Host "Setup done. Next (with MT5 open and logged in to the demo):" -ForegroundColor Green
Write-Host "  cd $dip"
Write-Host "  .\.venv\Scripts\python.exe engine.py test-telegram"
Write-Host "  .\.venv\Scripts\python.exe engine.py check"
