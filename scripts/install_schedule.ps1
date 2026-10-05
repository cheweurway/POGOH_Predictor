# Register a Windows scheduled task that runs the collector every 5 minutes.
#
# Run once from PowerShell in the project folder:
#     powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
#
# Settings chosen on purpose:
# - Runs only while you are logged in, so no stored password is needed.
# - Keeps running on battery. The default task settings would silently stop
#   collection whenever the laptop is unplugged.
# - StartWhenAvailable: after sleep, run once promptly instead of waiting for
#   the next slot. The collector's 4 minute guard prevents double polls.
# - IgnoreNew: never start a second copy while one is still running.
# - pythonw.exe runs without flashing a console window.
#
# To remove the task later:
#     Unregister-ScheduledTask -TaskName PogohCollector -Confirm:$false

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$Script = Join-Path $ProjectRoot "scripts\run_collector.py"

if (-not (Test-Path $Python)) { throw "Virtual environment not found at $Python" }

$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`"" -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName "PogohCollector" -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Polls the Pogoh bike share API every 5 minutes into data/pogoh.db" -Force | Out-Null

Write-Host "Registered PogohCollector. Check data\collector.log after a few minutes."
