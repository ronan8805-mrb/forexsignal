# Runs the dip-buy engine every 10 minutes while you are logged in (no admin needed):
#   powershell -ExecutionPolicy Bypass -File scripts\install_dipbuy_task.ps1
# Remove it:   schtasks /Delete /TN "DipBuy engine" /F
# Pause it:    schtasks /Change /TN "DipBuy engine" /DISABLE     (resume with /ENABLE)
#
# MT5 must be open and logged in on this PC - the engine talks to that terminal.
# The PC must not sleep:  powercfg /change standby-timeout-ac 0

$ErrorActionPreference = "Stop"
$dip    = Join-Path (Split-Path $PSScriptRoot -Parent) "dipbuy"
$pyw    = Join-Path $dip ".venv\Scripts\pythonw.exe"       # pythonw = no console window popping up
$engine = Join-Path $dip "engine.py"
if (-not (Test-Path $pyw)) { throw "dipbuy\.venv not found - run scripts\setup_dipbuy.ps1 first." }
if (-not (Test-Path (Join-Path $dip "config.toml"))) { throw "dipbuy\config.toml not found - run scripts\setup_dipbuy.ps1 first." }

$tr = "`"$pyw`" `"$engine`" run"
schtasks /Create /TN "DipBuy engine" /SC MINUTE /MO 10 /TR $tr /F | Out-Null
if ($LASTEXITCODE -ne 0) { throw "schtasks failed (exit $LASTEXITCODE)" }
schtasks /Run /TN "DipBuy engine" | Out-Null

Write-Host "Scheduled task 'DipBuy engine' created: runs every 10 minutes, first run started now." -ForegroundColor Green
Write-Host "Activity log:  $dip\engine.log      Trade journal:  $dip\journal.csv"
Write-Host "Status:        cd `"$dip`"; .\.venv\Scripts\python.exe engine.py status"
