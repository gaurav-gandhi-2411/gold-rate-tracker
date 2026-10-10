# Trigger ONE Tanishq visit at an exact time (GG decision E3; docs/TANISHQ_TIMED_VISITS.md).
#
# Run by Windows Task Scheduler on the laptop that hosts the self-hosted runner. It does not
# scrape anything itself: it asks GitHub to start scrape-tanishq-selfhosted.yml
# (workflow_dispatch), so the existing scrape -> commit -> bot-PR-sync path and all its guards
# (jump guard, health record, outcomes log) are reused unchanged. The reading's timestamp is set
# by scraper/scrape.js at the moment of capture, so a late run is recorded at its real time.
#
# Safe to run late or twice: it skips if a run of the workflow was created in the last
# $MinSpacingMin minutes or is still queued / in progress. $MinSpacingMin must stay below the
# shortest gap between visits in scraper/visit_schedule.json (15 min under GG decision 4a;
# tests/test_tanishq_timed_visits.py checks this). It also skips every visit for $CoolOffMin
# minutes after a run that Tanishq answered with a rate limit or a challenge (the outcomes log's
# "blocked" flag), so a 429 is never followed by another visit 15-30 min later. If it cannot
# reach GitHub, or cannot read the outcomes log, it logs and exits; the slot is then simply
# missed and the site shows the "not fresh" state.
param(
    [string]$Slot = "",
    [int]$MinSpacingMin = 10,
    [int]$CoolOffMin = 180,
    [switch]$SideTasksOnly  # run only the side tasks below (for testing); triggers no visit
)
$ErrorActionPreference = "Stop"
$Repo = "gaurav-gandhi-2411/gold-rate-tracker"
$Workflow = "scrape-tanishq-selfhosted.yml"
$LogDir = Join-Path $env:LOCALAPPDATA "gold-rate-tracker"
$Log = Join-Path $LogDir "tanishq_dispatch.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log([string]$Msg) {
    $line = "{0} slot={1} {2}" -f (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz"), $Slot, $Msg
    Add-Content -Path $Log -Value $line
}

if (-not $SideTasksOnly) {
# After a wake from sleep the network can take a minute or two to come back.
$online = $false
for ($i = 0; $i -lt 10; $i++) {
    gh api rate_limit --silent 2>$null
    if ($LASTEXITCODE -eq 0) { $online = $true; break }
    Start-Sleep -Seconds 30
}
if (-not $online) { Write-Log "SKIP no network/GitHub after 5 min"; exit 1 }

# Cool-off after a rate limit or challenge (ADR 059 / PR #2048's 429 handling). Fail closed: an
# unreadable log means "cannot verify", so the visit is skipped.
$lines = gh api -H "Accept: application/vnd.github.raw" "repos/$Repo/contents/data/tanishq_scrape_outcomes.jsonl" 2>$null
if ($LASTEXITCODE -ne 0 -or -not $lines) { Write-Log "SKIP cannot read outcomes log"; exit 1 }
$last = @($lines | Where-Object { $_.Trim() }) | Select-Object -Last 1
try { $rec = $last | ConvertFrom-Json } catch { Write-Log "SKIP unparseable last outcome"; exit 1 }
if ($rec.blocked -eq $true) {
    $blockedAt = ([datetime]$rec.timestamp).ToUniversalTime()
    if ($blockedAt -gt (Get-Date).ToUniversalTime().AddMinutes(-$CoolOffMin)) {
        Write-Log ("SKIP cool-off after blocked run at {0:o}" -f $blockedAt)
        exit 0
    }
}

$recent = gh run list --repo $Repo --workflow $Workflow --limit 5 --json createdAt,status,conclusion | ConvertFrom-Json
$cutoff = (Get-Date).ToUniversalTime().AddMinutes(-$MinSpacingMin)
foreach ($r in $recent) {
    # A run the workflow itself skipped (the GitHub cron fires and skips while Task Scheduler
    # owns the visits) is not a visit. Counting it made a catch-up at 00:09 IST on 2026-10-11
    # skip the missed 19:50 slot because a skipped cron run had been created 3 minutes earlier.
    if ($r.conclusion -eq "skipped") { continue }
    $created = ([datetime]$r.createdAt).ToUniversalTime()
    if ($r.status -in @("queued", "in_progress", "waiting", "pending") -or $created -gt $cutoff) {
        Write-Log ("SKIP recent run status={0} created={1:o}" -f $r.status, $created)
        exit 0
    }
}

gh workflow run $Workflow --repo $Repo --ref master -f "slot_ist=$Slot"
if ($LASTEXITCODE -ne 0) { Write-Log "FAIL gh workflow run exit=$LASTEXITCODE"; exit 1 }
Write-Log "DISPATCHED"
}

# --- Side tasks. They run only AFTER the visit has been triggered, are best effort, and write to a
# separate log (side_tasks.log) so the dispatcher log that scripts/laptop_attribution.py reads keeps
# one kind of line. Nothing below can change this script's exit code.
$SideLog = Join-Path $LogDir "side_tasks.log"
function Write-Side([string]$Msg) {
    try { Add-Content -Path $SideLog -Value ("{0} slot={1} {2}" -f (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz"), $Slot, $Msg) } catch { }
}

# (1) Keep the CI health monitor running. GitHub's cron skips runs, so a monitor that depends on it
# may not run; this laptop is a second, independent trigger (check-price.yml is a third). Start
# ci-health.yml when its latest run is more than 2 hours old.
try {
    $ci = @(gh run list --repo $Repo --workflow ci-health.yml --limit 1 --json createdAt | ConvertFrom-Json)
    $ageMin = 99999
    if ($LASTEXITCODE -eq 0 -and $ci.Count -gt 0) {
        $ageMin = [int]((Get-Date).ToUniversalTime() - ([datetime]$ci[0].createdAt).ToUniversalTime()).TotalMinutes
    }
    if ($ageMin -gt 120) {
        gh workflow run ci-health.yml --repo $Repo --ref master
        Write-Side ("CIHEALTH started (last run {0} min ago) exit={1}" -f $ageMin, $LASTEXITCODE)
    } else {
        Write-Side ("CIHEALTH not needed (last run {0} min ago)" -f $ageMin)
    }
} catch { Write-Side ("NOTE ci-health trigger failed: {0}" -f $_.Exception.Message) }

# (2) Refresh the slot attribution (scripts/laptop_attribution.py) so a session always has current
# slot-by-slot reasons. The result stays on THIS laptop in $LogDir (never in the repo checkout, so the
# checkout stays clean); the weekly routine reads it from there. It reads the missed-slot list from
# master's data/input_timeliness_weekly.json, fetched fresh, and merges into its own previous file.
try {
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
    if (-not $py) {
        $cand = Join-Path $env:USERPROFILE "anaconda3\python.exe"
        if (Test-Path $cand) { $py = $cand }
    }
    if (-not $py) { throw "no python found" }
    $rep = Join-Path $LogDir "input_timeliness_weekly.json"
    $att = Join-Path $LogDir "laptop_attribution.json"
    $json = gh api -H "Accept: application/vnd.github.raw" "repos/$Repo/contents/data/input_timeliness_weekly.json" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $json) { throw "cannot read the timeliness report" }
    # No BOM: Python reads these files as plain UTF-8 JSON.
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($rep, ($json -join "`n"), $utf8)
    if (-not (Test-Path $att)) {  # first run: start from the copy on master so history is kept
        $seed = gh api -H "Accept: application/vnd.github.raw" "repos/$Repo/contents/data/laptop_attribution.json" 2>$null
        if ($LASTEXITCODE -eq 0 -and $seed) { [System.IO.File]::WriteAllText($att, ($seed -join "`n"), $utf8) }
    }
    $script = Join-Path $PSScriptRoot "..\laptop_attribution.py"
    $p = Start-Process -FilePath $py -ArgumentList @("-I", "`"$script`"", "--timeliness", "`"$rep`"", "--out", "`"$att`"") `
        -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $LogDir "attribution.out") `
        -RedirectStandardError (Join-Path $LogDir "attribution.err")
    $null = $p.Handle  # caches the handle; otherwise ExitCode of a redirected process reads back empty
    if ($p.WaitForExit(280000)) {
        Write-Side ("ATTRIBUTION exit={0}" -f $p.ExitCode)
    } else {
        Stop-Process -Id $p.Id -Force  # by PID: only the process started just above
        Write-Side "NOTE attribution timed out after 280 s"
    }
} catch { Write-Side ("NOTE attribution skipped: {0}" -f $_.Exception.Message) }
exit 0
