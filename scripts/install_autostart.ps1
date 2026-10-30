# Registers two Windows scheduled tasks that start the bridge and the tunnel when you log in,
# hidden, with automatic restart. Run once from an ELEVATED PowerShell (Run as administrator):
#   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
# Remove later with:  Unregister-ScheduledTask -TaskName "ATR-EW bridge","ATR-EW tunnel" -Confirm:$false

$ErrorActionPreference = "Stop"
$ps = (Get-Command powershell.exe).Source

foreach ($name in @("bridge", "tunnel")) {
    $script   = Join-Path $PSScriptRoot "start_$name.ps1"
    $action   = New-ScheduledTaskAction -Execute $ps `
                  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
    $trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                  -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName "ATR-EW $name" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Host "Registered scheduled task: ATR-EW $name"
}

Write-Host ""
Write-Host "Start them now without logging out:"
Write-Host '  Start-ScheduledTask "ATR-EW bridge"; Start-ScheduledTask "ATR-EW tunnel"'
Write-Host "Keep the PC awake while trading:  powercfg /change standby-timeout-ac 0"
