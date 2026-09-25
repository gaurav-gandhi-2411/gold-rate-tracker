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
    [int]$CoolOffMin = 180
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

$recent = gh run list --repo $Repo --workflow $Workflow --limit 5 --json createdAt,status | ConvertFrom-Json
$cutoff = (Get-Date).ToUniversalTime().AddMinutes(-$MinSpacingMin)
foreach ($r in $recent) {
    $created = ([datetime]$r.createdAt).ToUniversalTime()
    if ($r.status -in @("queued", "in_progress", "waiting", "pending") -or $created -gt $cutoff) {
        Write-Log ("SKIP recent run status={0} created={1:o}" -f $r.status, $created)
        exit 0
    }
}

gh workflow run $Workflow --repo $Repo --ref master -f "slot_ist=$Slot"
if ($LASTEXITCODE -ne 0) { Write-Log "FAIL gh workflow run exit=$LASTEXITCODE"; exit 1 }
Write-Log "DISPATCHED"
