<#
  Registers (or removes) the Windows scheduled task that wakes the PC and runs the daily hub.
  Run from an ELEVATED PowerShell in this folder:

    powershell -ExecutionPolicy Bypass -File .\schedule-task.ps1 -Time 05:00 -Then sleep
    powershell -ExecutionPolicy Bypass -File .\schedule-task.ps1 -TestInMinutes 5 -Then sleep   # one-off dry run, no games
    powershell -ExecutionPolicy Bypass -File .\schedule-task.ps1 -Remove

  The task runs "<base python>\pythonw.exe -I hub.pyw --auto --then <action>" as the current user,
  only while that user is signed in, with highest privileges (so no UAC prompt), and may wake the PC
  from sleep / hibernate.  It does not change power plans, sign-in options or anything else.
#>
param(
    [string]$Time = "05:00",
    [ValidateSet("sleep", "hibernate", "shutdown", "none")] [string]$Then = "sleep",
    [int]$TestInMinutes = 0,
    [switch]$Remove
)
$ErrorActionPreference = "Stop"
$Name = Split-Path -Leaf $PSScriptRoot          # "daily-hub"; a copy in another folder gets its own task
$TestName = "$Name-test"

$me = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$me).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell."
}

if ($Remove) {
    foreach ($n in @($Name, $TestName)) {
        if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $n -Confirm:$false
            "Removed task $n"
        }
    }
    return
}

$root = $PSScriptRoot
$exe = Join-Path $root "DailyHub.exe"
if (Test-Path -LiteralPath $exe) {
    # packaged release: run the exe itself
    $program = $exe
    $argLine = "--auto --then $Then"
} else {
    # source checkout: the base interpreter's pythonw.exe runs hub.pyw (no console window)
    $homeLine = Get-Content -LiteralPath (Join-Path $root ".venv\pyvenv.cfg") |
        Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1
    $program = Join-Path (($homeLine -replace '^\s*home\s*=\s*', '').Trim()) "pythonw.exe"
    if (-not (Test-Path -LiteralPath $program)) { throw "pythonw.exe not found: $program" }
    $hub = Join-Path $root "hub.pyw"
    $argLine = "-I `"$hub`" --auto --then $Then"
}
if ($TestInMinutes -gt 0) {
    $taskName = $TestName
    $argLine += " --dry"
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes($TestInMinutes)
} else {
    $taskName = $Name
    $trigger = New-ScheduledTaskTrigger -Daily -At $Time
}
$action = New-ScheduledTaskAction -Execute $program -Argument $argLine -WorkingDirectory $root
$principal = New-ScheduledTaskPrincipal -UserId $me.Name -LogonType Interactive -RunLevel Highest
# No time limit + parallel instances: a hub window left open since yesterday keeps its task instance
# "running", and today's start must still launch (it hands --auto to that window and exits).
$settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances Parallel
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description "Wake the PC, run the ticked games in the daily hub, then: $Then" -Force | Out-Null

"Registered task '$taskName'"
"  run : $program $argLine"
"  next: " + (Get-ScheduledTaskInfo -TaskName $taskName).NextRunTime
