# Trigger ONE Tanishq visit at an exact time (GG decision E3; docs/TANISHQ_TIMED_VISITS.md).
#
# Run by Windows Task Scheduler on the laptop that hosts the self-hosted runner. It does not
# scrape anything itself: it asks GitHub to start scrape-tanishq-selfhosted.yml
# (workflow_dispatch), so the existing scrape -> commit -> bot-PR-sync path and all its guards
# (jump guard, health record, outcomes log) are reused unchanged. The reading's timestamp is set
# by scraper/scrape.js at the moment of capture, so a late run is recorded at its real time.
#
# After a shutdown or sleep Task Scheduler starts the task of EVERY missed slot at the same moment
# (StartWhenAvailable). They used to dispatch one run each and the workflow's concurrency group cancelled
# all but one (2026-10-14 and 10-15: four dispatches, two cancelled). Now the processes of one wake take a
# machine-wide lock in turn; the first (the leader) waits $SettleSec seconds when it is late so its siblings
# can register, dispatches ONCE, and records the batch (dispatch_batch.json). Every sibling then logs its own
# line as `DISPATCHED (batch of N, led by slot X; covers a,b,c)`, so the attribution still sees each slot as
# served and the log says which slots the single run covers.
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
    [switch]$SideTasksOnly,  # run only the side tasks below (for testing); triggers no visit
    [int]$SettleSec = 20,  # how long a late leader waits for the other missed slots to register
    [int]$LateAfterSec = 120,  # a slot started this many seconds after its time counts as late
    [int]$BatchWindowMin = 15,  # a batch dispatched this recently, listing my slot, covers me
    [switch]$SkipSideTasks  # for tests: skip the side tasks below
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

# Phase trace in its OWN file (laptop_attribution.py reads every line of $Log as an outcome, so a phase
# line there would be misread as one). A process that is killed (no trap runs) leaves its last phase here:
# on 2026-10-22 all six catch-up processes after a boot ended 0xC000013A and left no line at all.
$Trace = Join-Path $LogDir "dispatch_trace.log"
function Write-Trace([string]$Msg) {
    try { Add-Content -Path $Trace -Value ("{0} slot={1} pid={2} {3}" -f (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz"), $Slot, $PID, $Msg) } catch { }
}
Write-Trace "START"

trap { Write-Log ("FAIL unexpected: {0}" -f $_.Exception.Message); exit 1 }

if (-not $SideTasksOnly) {
# Windows PowerShell 5.1 turns ANY stderr output of a native command into a terminating error
# when $ErrorActionPreference is Stop, even with 2>$null. A failing gh call (no network right
# after a wake) then killed this script with exit 1, no log line and no retry: the 01:40 IST
# slot of 2026-10-12 was lost that way. The native calls below are judged by $LASTEXITCODE
# instead, so Continue is set for them and Stop is restored after the dispatch.
$ErrorActionPreference = "Continue"
# After a wake from sleep the network can take a minute or two to come back.
$online = $false
for ($i = 0; $i -lt 10; $i++) {
    gh api rate_limit --silent 2>$null
    if ($LASTEXITCODE -eq 0) { $online = $true; break }
    Start-Sleep -Seconds 30
}
if (-not $online) { Write-Log "SKIP no network/GitHub after 5 min"; exit 1 }
Write-Trace "ONLINE"

# Cool-off after a rate limit or challenge (ADR 059 / PR #2048's 429 handling). Fail closed: an
# unreadable log means "cannot verify", so the visit is skipped.
$lines = gh api -H "Accept: application/vnd.github.raw" "repos/$Repo/contents/data/tanishq_scrape_outcomes.jsonl" 2>$null
if ($LASTEXITCODE -ne 0 -or -not $lines) { Write-Log "SKIP cannot read outcomes log"; exit 1 }
$last = @($lines | Where-Object { $_.Trim() }) | Select-Object -Last 1
if (-not $last) { Write-Log "SKIP empty outcomes log"; exit 1 }
try { $rec = $last | ConvertFrom-Json } catch { Write-Log "SKIP unparseable last outcome"; exit 1 }
if ($rec.blocked -eq $true) {
    $blockedAt = ([datetime]$rec.timestamp).ToUniversalTime()
    if ($blockedAt -gt (Get-Date).ToUniversalTime().AddMinutes(-$CoolOffMin)) {
        Write-Log ("SKIP cool-off after blocked run at {0:o}" -f $blockedAt)
        exit 0
    }
}

# --- One dispatch per wake. Every process registers its slot, then they take a machine-wide lock in turn.
$StateFile = Join-Path $LogDir "dispatch_batch.json"
$PendingFile = Join-Path $LogDir "dispatch_pending.log"
$startUtc = (Get-Date).ToUniversalTime()
# Several processes append here at the same instant: retry on a sharing violation instead of losing the line.
$pendingLine = ("{0} {1} {2}" -f $startUtc.ToString("o"), $Slot, $PID) + [Environment]::NewLine
for ($i = 0; $i -lt 20; $i++) {
    try { [System.IO.File]::AppendAllText($PendingFile, $pendingLine); break } catch { Start-Sleep -Milliseconds 150 }
}
$mutex = New-Object System.Threading.Mutex($false, "Global\gold-rate-tracker-tanishq-dispatch")
$locked = $false
Write-Trace "WAIT lock"
try { $locked = $mutex.WaitOne([TimeSpan]::FromMinutes(4)) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
Write-Trace ("LOCK acquired={0}" -f $locked)
if (-not $locked) { Write-Log "SKIP dispatch lock busy for 4 min"; exit 1 }
try {
    # How late am I? The slot's most recent IST occurrence, against now. A process Task Scheduler starts a few
    # seconds BEFORE the slot time is on time, not a day late.
    $ist = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date), "India Standard Time")
    $sched = [datetime]::ParseExact($ist.ToString("yyyy-MM-dd") + " " + $Slot, "yyyy-MM-dd HH:mm", $null)
    if ($sched -gt $ist -and ($sched - $ist).TotalMinutes -gt 5) { $sched = $sched.AddDays(-1) }
    $lateSec = [Math]::Max(0, ($ist - $sched).TotalSeconds)
    $schedUtc = (Get-Date).ToUniversalTime().AddSeconds(-$lateSec)  # the slot's time as a UTC instant
    # A sibling that already dispatched (or skipped) for this wake and listed my slot has done my visit. A FAIL
    # is not adopted: a transient failure right after a wake should be retried by each process.
    $batch = $null
    if (Test-Path $StateFile) { try { $batch = Get-Content -Path $StateFile -Raw | ConvertFrom-Json } catch { $batch = $null } }
    $covered = $false
    $joinedLate = ""
    if ($batch -and ("$($batch.result)" -match "^(DISPATCHED|SKIP recent run)")) {
        $batchAt = ([datetime]$batch.at_utc).ToUniversalTime()
        if ((@($batch.slots) -contains $Slot) -and $batchAt -ge $startUtc.AddMinutes(-1)) { $covered = $true }
        elseif ($batch.result -eq "DISPATCHED" -and $lateSec -gt $LateAfterSec -and $batchAt -ge $startUtc.AddMinutes(-$BatchWindowMin) -and $schedUtc -le $batchAt) {
            # a sibling that came online after the leader's settle time: the run just dispatched serves it too
            $covered = $true
            $joinedLate = "; joined late"
        }
    }
    if ($covered) {
        $locked = $false
        $mutex.ReleaseMutex()
        Write-Log ("{0} (batch of {1}, led by slot {2}; covers {3}{4})" -f $batch.result, @($batch.slots).Count, $batch.leader, (@($batch.slots) -join ","), $joinedLate)
        if ($batch.result -ne "DISPATCHED") { exit 0 }
        $skipDispatch = $true
    } else {
        $skipDispatch = $false
    }
    if (-not $skipDispatch) {
        if ($lateSec -gt $LateAfterSec -and $SettleSec -gt 0) { Start-Sleep -Seconds $SettleSec }  # let siblings register
        # The slots of this wake: everything registered since two minutes before I started.
        $covers = @()
        $pendingLines = @()
        for ($i = 0; $i -lt 20; $i++) {
            try { $pendingLines = @(Get-Content -Path $PendingFile -ErrorAction Stop); break } catch { Start-Sleep -Milliseconds 150 }
        }
        foreach ($l in $pendingLines) {
            # one torn line (a power loss mid-append) must never stop a dispatch: skip what does not parse
            try {
                $parts = $l -split " "
                if ($parts.Count -ge 2 -and ([datetime]$parts[0]).ToUniversalTime() -ge $startUtc.AddMinutes(-2)) { $covers += $parts[1] }
            } catch { }
        }
        $covers = @($covers | Select-Object -Unique)
        if ($pendingLines.Count -gt 1000) { try { Set-Content -Path $PendingFile -Value @($pendingLines | Select-Object -Last 200) } catch { } }  # keep it short
        if ($covers.Count -eq 0) { $covers = @($Slot) }
        $batchNote = ""
        if ($covers.Count -gt 1) { $batchNote = " (batch of {0}, led by slot {1}; covers {2})" -f $covers.Count, $Slot, ($covers -join ",") }

        $result = $null
        $exitCode = 0
        Write-Trace "LEADER listing recent runs"
        $recentRaw = gh run list --repo $Repo --workflow $Workflow --limit 5 --json createdAt,status,conclusion 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $recentRaw) { $result = "SKIP cannot list recent runs"; $exitCode = 1 }
        else {
            $recent = $recentRaw | ConvertFrom-Json
            $cutoff = (Get-Date).ToUniversalTime().AddMinutes(-$MinSpacingMin)
            foreach ($r in $recent) {
                # A run the workflow itself skipped (the GitHub cron fires and skips while Task Scheduler
                # owns the visits) is not a visit. Counting it made a catch-up at 00:09 IST on 2026-10-11
                # skip the missed 19:50 slot because a skipped cron run had been created 3 minutes earlier.
                if ($r.conclusion -eq "skipped") { continue }
                $created = ([datetime]$r.createdAt).ToUniversalTime()
                if ($r.status -in @("queued", "in_progress", "waiting", "pending") -or $created -gt $cutoff) {
                    $result = "SKIP recent run status={0} created={1:o}" -f $r.status, $created
                    break
                }
            }
        }
        if (-not $result) {
            gh workflow run $Workflow --repo $Repo --ref master -f "slot_ist=$Slot"
            if ($LASTEXITCODE -ne 0) { $result = "FAIL gh workflow run exit=$LASTEXITCODE"; $exitCode = 1 }
            else { $result = "DISPATCHED" }
        }
        # Record the batch for the siblings still waiting for the lock, then log my own line.
        $state = [ordered]@{ at_utc = (Get-Date).ToUniversalTime().ToString("o"); leader = $Slot; slots = $covers; result = $result }
        try { ($state | ConvertTo-Json -Compress) | Set-Content -Path $StateFile -Encoding ASCII } catch { Write-Log ("NOTE could not write the batch state: {0}" -f $_.Exception.Message) }
        Write-Log ($result + $batchNote)
        Write-Trace ("DONE {0}" -f $result)
        if ($exitCode -ne 0) { $locked = $false; $mutex.ReleaseMutex(); exit $exitCode }
        if ($result -ne "DISPATCHED") { $locked = $false; $mutex.ReleaseMutex(); exit 0 }
    }
} finally {
    if ($locked) { try { $mutex.ReleaseMutex() } catch { } }
}
$ErrorActionPreference = "Stop"
}

# --- Side tasks. They run only AFTER the visit has been triggered, are best effort, and write to a
# separate log (side_tasks.log) so the dispatcher log that scripts/laptop_attribution.py reads keeps
# one kind of line. Nothing below can change this script's exit code.
if ($SkipSideTasks) { exit 0 }
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
