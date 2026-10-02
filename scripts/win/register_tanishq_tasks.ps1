# Create (or remove) the Windows Task Scheduler tasks for the timed Tanishq visits (E3).
# GG runs this by hand; see docs/TANISHQ_TIMED_VISITS.md for the numbered steps.
#
#   .\scripts\win\register_tanishq_tasks.ps1 -DryRun      # print what would be created
#   .\scripts\win\register_tanishq_tasks.ps1              # create one task per visit time
#   .\scripts\win\register_tanishq_tasks.ps1 -Unregister  # remove them all (undo)
#
# Visit times come from scraper/visit_schedule.json (IST). The laptop clock must be on
# India Standard Time, because Task Scheduler triggers use local time.
param(
    [switch]$DryRun,
    [switch]$Unregister
)
$ErrorActionPreference = "Stop"
$TaskPath = "\GoldRateTracker\"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Dispatch = Join-Path $RepoRoot "scripts\win\tanishq_dispatch.ps1"
$Schedule = Get-Content (Join-Path $RepoRoot "scraper\visit_schedule.json") -Raw | ConvertFrom-Json

if ($Unregister) {
    Get-ScheduledTask -TaskPath $TaskPath -ErrorAction SilentlyContinue |
        Where-Object { $_.TaskName -like "Tanishq-*" } |
        ForEach-Object {
            if ($DryRun) { Write-Output "would remove $($_.TaskName)" }
            else { Unregister-ScheduledTask -TaskName $_.TaskName -TaskPath $TaskPath -Confirm:$false; Write-Output "removed $($_.TaskName)" }
        }
    exit 0
}

if ((Get-TimeZone).Id -ne "India Standard Time") {
    throw "Laptop time zone is $((Get-TimeZone).Id), not India Standard Time. Visit times are IST; fix the time zone first."
}

$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
# Interactive: runs while GG is signed in (a locked screen is fine). gh keeps its token in the
# Windows Credential Manager, which a "run whether logged on or not" task cannot read without
# storing the account password in the task -- deliberately not done.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

foreach ($slot in $Schedule.visits_ist) {
    $name = "Tanishq-" + ($slot -replace ":", "")
    $arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Dispatch`" -Slot $slot"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg
    $trigger = New-ScheduledTaskTrigger -Daily -At $slot
    if ($DryRun) {
        Write-Output "would create $TaskPath$name daily at $slot IST (wake to run, run late if missed)"
        continue
    }
    Register-ScheduledTask -TaskName $name -TaskPath $TaskPath -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    Write-Output "created $TaskPath$name daily at $slot IST"
}
