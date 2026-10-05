"""Analysis-only classifier for the 2026-10 cleanup map. Read-only: it moves/deletes nothing.

Usage: python classify.py <facts.json from gather_facts.py> <repo_root> <out_dir>
Reads git metadata itself. Writes cleanup_map.csv, summary.json, untracked.csv into out_dir.
Classification is rule-based with explicit per-path decisions below; every row carries evidence.
See gather_facts.py docstring for the scope (and non-scope) of the reference sweeps (rule 85b).
"""

from __future__ import annotations

import contextlib
import csv
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

FACTS = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
ROOT = Path(sys.argv[2])
OUT = Path(sys.argv[3])
OUT.mkdir(parents=True, exist_ok=True)

FILES: list[str] = FACTS["files"]
REFS = FACTS["refs"]
PROD = set(FACTS["prod_reach"])
TEST = set(FACTS["test_reach"])
ROOTS_WF = FACTS["roots_wf"]
IMPORTS = FACTS["imports"]
FETCHED = {x[1] for x in FACTS["fetches"]}

# ---- git metadata (one pass) ----
log = subprocess.run(
    ["git", "log", "--format=@@%cs", "--name-only", "--no-renames"],
    cwd=ROOT,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
).stdout
LAST: dict[str, str] = {}
COMMITS: Counter[str] = Counter()
cur = ""
for line in log.splitlines():
    if line.startswith("@@"):
        cur = line[2:]
    elif line:
        LAST.setdefault(line, cur)
        COMMITS[line] += 1


def size_lines(f: str) -> tuple[int, int]:
    p = ROOT / f
    try:
        sz = p.stat().st_size
    except OSError:
        return 0, 0
    n = 0
    if p.suffix in {
        ".py",
        ".js",
        ".mjs",
        ".md",
        ".yml",
        ".json",
        ".jsonl",
        ".ps1",
        ".html",
        ".css",
        ".toml",
        ".csv",
        ".txt",
        ".lock",
        ".ipynb",
        ".svg",
        ".webmanifest",
    }:
        with contextlib.suppress(OSError), Path(p).open("rb") as fh:
            n = sum(1 for _ in fh)
    return sz, n


def adr_nums(f: str) -> list[int]:
    out = []
    for g in REFS[f].get("adr", []):
        m = re.match(r"docs/adr/(\d+)-", g)
        if m:
            out.append(int(m.group(1)))
    return sorted(set(out))


def cnt(f: str) -> dict[str, int]:
    return REFS[f]["_counts"]


def short_refs(f: str) -> str:
    c = cnt(f)
    parts = [f"{k}={v}" for k, v in sorted(c.items()) if v]
    return "inbound[" + ",".join(parts) + "]" if parts else "inbound[none]"


# ---- explicit decisions -------------------------------------------------------------
DISPATCH = {
    "comex_direction",
    "chronos_m4",
    "prereg_reference",
    "direction_diagnosis",
    "range_forecast",
    "nowcast",
    "buyer_policy",
    "selective_direction",
    "pipeline_sensitivity",
    "calibrated_floor",
    "macro_horizons",
}
PHI_ARCHIVE = {
    "ml/experiments/__init__.py",
    "ml/experiments/driver_decomp.py",
    "ml/experiments/festival_seasonality.py",
    "ml/experiments/horizon_sweep.py",
    "ml/experiments/premium_carry.py",
    "scripts/run_phi7a.py",
    "scripts/run_phi7b.py",
    "scripts/run_phi7c.py",
    "scripts/run_phi7d.py",
    "scripts/run_phi10a.py",
    "scripts/phi10a_flag_and_stop.py",
    "data/experiments/phi10a_driver_decomp.json",
    "data/experiments/phi7_results.json",
}
DOCS_ARCHIVE = {
    "docs/DAILY_SUMMARY_DESIGN.md": "zero inbound refs; 2026-07-18 design for a Groq summary the PWA no longer uses (app.js comment: commentary replaced client-side)",
    "docs/METRICS_DESIGN.md": "zero inbound refs; last touched 2026-05-14, design predates the Chronos/DARK-gate pipeline",
    "docs/FEATURE_IMPORTANCE_2026-05-14.md": "zero inbound refs; LightGBM-era feature importance (model retired, ADR 009/024)",
    "docs/DESIGN.md": "1 root ref only; last touched 2026-05-14; pre-Chronos design",
    "docs/FEATURE_INVENTORY.md": "refs only from other stale docs; last touched 2026-05-15 (LightGBM-era features)",
    "docs/PHASE_3_RETROSPECTIVE.md": "1 root ref; 2026-05-22 retrospective; mentions legacy test files for deleted LightGBM/TFT/N-BEATS code",
    "docs/UI_AUDIT.md": "1 root ref; 2026-05-14 UI audit, superseded by later UI waves",
    "docs/UI_PLAN.md": "1 root ref; 2026-05-14 plan; only consumer of scripts/capture-*.js and docs/screenshots/",
    "docs/CLEAN_IP_FETCH.md": "design for the retired clean-IP Worker fetch (retired 2026-07-16, see archive/README.md)",
    "docs/proposals/p5-product-proposals.md": "zero inbound refs; 2026-09-23 proposals",
    "docs/MODELLING_ASSESSMENT_2026-05-31.md": "dated assessment snapshot; cited by 1 ADR, so link must be rewritten",
}
SCRAPER_DELETE = {
    "scraper/desktop-nav-check.mjs": "zero references anywhere (workflows, docs, tests, package.json); one-off Playwright check; knip: unused file",
    "scraper/norm14-check.mjs": "zero references anywhere; one-off check; knip: unused file",
    "scraper/screenshot-psi3c1.mjs": "zero references anywhere; the ~40 sibling screenshot-psi3* scripts and PNGs next to it are already gitignored and untracked, this one slipped in",
}

rows: list[dict] = []


def add(path, cls, conf, batch, evidence, question="", kind="file"):
    sz, ln = size_lines(path) if kind == "file" else (0, 0)
    rows.append(
        dict(
            path=path,
            kind=kind,
            area=path.split("/")[0] if "/" in path else "(root)",
            cls=cls,
            confidence=conf,
            batch=batch,
            evidence=evidence,
            question=question,
            size_bytes=sz,
            lines=ln,
            last_commit=LAST.get(path, ""),
            commits=COMMITS.get(path, 0),
        )
    )


def wf_evidence(f: str) -> str:
    w = ROOTS_WF.get(f)
    return "named in " + ",".join(sorted(x.split("/")[-1] for x in w)) if w else ""


decision: dict[str, tuple[str, str, str, str, str]] = {}  # path -> cls,conf,batch,evidence,question

for f in FILES:
    a = f.split("/")[0] if "/" in f else "(root)"
    adrs = adr_nums(f)
    adr_s = f" ADR{'/'.join(map(str, adrs[:6]))}" if adrs else ""
    base = f.rsplit("/", 1)[-1]
    c = cnt(f)

    # ---------------- ml ----------------
    if a == "ml":
        if f == "ml/logging_setup.py":
            decision[f] = (
                "DELETE",
                "HIGH",
                "B1",
                "not imported by any module/test except the generic import sweep in tests/test_no_dead_imports.py; vulture60: configure_logging unused; only a report mentions the name; no workflow",
                "",
            )
        elif f == "ml/llm_cache_helpers.py":
            decision[f] = (
                "ARCHIVE",
                "UNCERTAIN",
                "B3",
                "forward-looking helpers (ADR 013 'infra shipped, not used'); only tests/test_llm_cache_helpers.py imports it; vulture60 flags 4 unused functions; Groq commentary path retired",
                "ADR 013 names this file as the prepared infra for a future Claude migration: is that trigger still live? If yes KEEP.",
            )
        elif f in PHI_ARCHIVE:
            decision[f] = (
                "ARCHIVE",
                "HIGH",
                "B3",
                f"Phi7/Phi10 closed experiments; no workflow; reached only from tests and scripts/run_phi*.py; results frozen in data/experiments/*.json, ADR 018/019;{short_refs(f)}",
                "",
            )
        elif f == "ml/experiments/drift_naive.py":
            decision[f] = (
                "KEEP",
                "UNCERTAIN",
                "",
                "ADR 018 'evaluated, held pending out-of-regime' has a revisit trigger; no workflow",
                "Is ADR 018's out-of-regime revisit still wanted? If not, ARCHIVE with the Phi7 set (B3).",
            )
        elif f == "ml/experiments/direction_enrichment.py":
            decision[f] = (
                "KEEP",
                "UNCERTAIN",
                "",
                "not importable from any root, but docs/DIRECTION_SIGNAL_STATUS.md step 2 names `python -m ml.experiments.direction_enrichment` as the re-check command at n~250",
                "Is DIRECTION_SIGNAL_STATUS step 2 still planned?",
            )
        elif f in PROD:
            decision[f] = (
                "KEEP",
                "HIGH",
                "",
                (wf_evidence(f) or "reachable from workflow-invoked modules via import graph")
                + f";{short_refs(f)}",
                "",
            )
        elif base == "requirements.txt" or base.endswith(".lock"):
            decision[f] = (
                "KEEP",
                "HIGH",
                "",
                "dependency manifest used by lint.yml / check-price.yml",
                "",
            )
        elif any(n >= 26 for n in adrs):
            decision[f] = (
                "KEEP",
                "HIGH",
                "",
                f"test-only reach but named by frozen/pre-registered/shadow ADR{'/'.join(map(str, adrs))}; moving it would break the ADR's frozen file reference;{short_refs(f)}",
                "",
            )
        else:
            decision[f] = (
                "KEEP",
                "UNCERTAIN",
                "",
                f"no workflow root, not named by any ADR>=26;{short_refs(f)}",
                "Is there a consumer outside the tracked tree?",
            )
        continue

    # ---------------- scripts ----------------
    if a == "scripts":
        m = re.match(r"scripts/analysis_(.+)\.py$", f)
        if f in PHI_ARCHIVE:
            decision[f] = (
                "ARCHIVE",
                "HIGH",
                "B3",
                "Phi7/Phi10 one-off runner; zero inbound references except its own output JSON in data/experiments (frozen); not in analysis.yml options",
                "",
            )
        elif f.startswith("scripts/capture-") and f.endswith(".js"):
            decision[f] = (
                "DELETE",
                "HIGH",
                "B1",
                "one-off U-wave screenshot capture; sole inbound mention is stale docs/UI_PLAN.md; not in any workflow; output (docs/screenshots) is itself a DELETE candidate",
                "",
            )
        elif f in (
            "scripts/win/mlflow-up.ps1",
            "scripts/win/mlflow-down.ps1",
            "scripts/win/train-all.ps1",
        ):
            decision[f] = (
                "DELETE",
                "HIGH",
                "B1",
                "MLflow/training scaffolding: docker-compose.yml and ml/training were removed by ADR 024 (`python -m ml.training` / `docker compose up mlflow` cannot run); no workflow; ADR 024 listed them as removed but the .ps1 files survived",
                "",
            )
        elif f == "scripts/inspect-tanishq.js":
            decision[f] = (
                "ARCHIVE",
                "UNCERTAIN",
                "B1",
                "only mentioned in .claude/settings.local.json and a fixture comment; Tanishq now scraped by scraper/scrape.js",
                "Is this still the manual Tanishq-DOM debugging aid? If yes KEEP.",
            )
        elif f in PROD or f in ("scripts/measure-lighthouse.js", "scripts/screenshot-og.mjs"):
            decision[f] = (
                "KEEP",
                "HIGH",
                "",
                (wf_evidence(f) or "invoked by workflow") + f";{short_refs(f)}",
                "",
            )
        elif m and m.group(1) in DISPATCH:
            decision[f] = (
                "KEEP",
                "HIGH",
                "",
                f"dispatchable via analysis.yml option '{m.group(1)}' (SCRIPT=scripts/analysis_<name>.py is built dynamically, so static grep cannot see it); frozen report in reports/;{short_refs(f)}",
                "",
            )
        elif f == "scripts/check_required_checks_positive.py":
            decision[f] = (
                "KEEP",
                "HIGH",
                "",
                "mandated READY-gate script (.claude/CLAUDE.md reporting standard); tests + RUNBOOK + ADR reference it; not workflow-invoked by design",
                "",
            )
        elif f.startswith("scripts/win/"):
            decision[f] = (
                "KEEP",
                "MED",
                "",
                "Windows operator scripts (Tanishq task registration/dispatch referenced by docs/TANISHQ_TIMED_VISITS.md; lint/test are dev helpers with zero refs)",
                ""
                if "tanishq" in f
                else "lint.ps1/test.ps1 have zero inbound refs: still used by GG locally?",
            )
        elif a == "scripts" and any(n >= 26 for n in adrs):
            decision[f] = (
                "KEEP",
                "HIGH",
                "",
                f"producer of a frozen/pre-registered result named by ADR{'/'.join(map(str, adrs))}; not workflow-scheduled;{short_refs(f)}",
                "",
            )
        elif f in ("scripts/analysis_proxy_subperiods.py", "scripts/analysis_timing_audit.py"):
            decision[f] = (
                "KEEP",
                "UNCERTAIN",
                "",
                "no inbound reference by path, but it produces a committed frozen report (reports/proxy_subperiods_config_j.json / reports/timing_audit/*) cited by ADR 038/058",
                "Confirm the report's provenance header names this script; if the report can be regenerated from elsewhere, ARCHIVE.",
            )
        else:
            decision[f] = (
                "KEEP",
                "UNCERTAIN",
                "",
                f"no workflow root;{short_refs(f)}",
                "Documented ops/research tool or dead? Check docs hit.",
            )
        continue

    # ---------------- data ----------------
    if a == "data":
        if f == "data/commentary.json":
            decision[f] = (
                "DELETE",
                "HIGH",
                "B1",
                "no workflow writes it (last touched 2026-08-10 after 704 commits); app.js:1635 states it is 'now unused by the frontend'; no ml/ module or test references it; only CHANGELOG + that app.js comment mention the name",
                "Confirm service-worker.js does not precache it (grep: not present).",
            )
        elif f in PHI_ARCHIVE:
            decision[f] = (
                "ARCHIVE",
                "HIGH",
                "B3",
                "frozen Phi7/Phi10 experiment result, referenced by ADR 018 / its runner only",
                "",
            )
        elif f in ("data/event_watch_today.json", "data/markup_today.json"):
            decision[f] = (
                "KEEP",
                "UNCERTAIN",
                "",
                f"fetched by app.js (flag-gated F4/F1 cards) but no scheduled producer exists on master; last touched {LAST.get(f)}",
                "Is the producing pipeline (ADR 050 event_watch / unmerged markup-meter branch) going to be scheduled? If not, retire the card and the file together.",
            )
        elif f == "data/stale_day_shadow.json":
            decision[f] = (
                "KEEP",
                "UNCERTAIN",
                "",
                "ADR 048 pre-registration artifact; its runner scripts/run_stale_day_shadow.py is not scheduled; last touched 2026-09-24",
                "Is the stale-day shadow to be scheduled in weekly-backtest?",
            )
        else:
            why = []
            if f in FETCHED:
                why.append("fetched by PWA JS")
            if REFS[f].get("workflow"):
                why.append(
                    "named in " + ",".join(sorted(x.split("/")[-1] for x in REFS[f]["workflow"]))
                )
            if REFS[f].get("ml"):
                why.append(
                    "read/written by ml/"
                    + ",".join(sorted(x.split("/")[-1] for x in REFS[f]["ml"])[:3])
                )
            if REFS[f].get("scripts"):
                why.append(
                    "scripts/" + ",".join(sorted(x.split("/")[-1] for x in REFS[f]["scripts"])[:2])
                )
            decision[f] = (
                "KEEP",
                "HIGH" if why else "UNCERTAIN",
                "",
                "; ".join(why) or short_refs(f),
                "",
            )
        continue

    # ---------------- docs ----------------
    if a == "docs":
        if f.startswith("docs/adr/"):
            decision[f] = (
                "KEEP",
                "HIGH",
                "",
                "ADR (decision record; ADR>=26 are frozen pre-registrations/shadow specs). Numbers 006,033,051,054,060 do not exist in the tree.",
                "",
            )
        elif f.startswith("docs/screenshots/"):
            decision[f] = (
                "DELETE",
                "UNCERTAIN",
                "B4",
                "before/after PNGs from the 2026-05 U-wave; inbound refs: docs/UI_AUDIT.md/UI_PLAN.md only (both ARCHIVE candidates); recoverable from git history",
                "Do any merged PR bodies or external pages hotlink these paths? (rule 15c embeds use raw URLs on the PR branch.)",
            )
        elif f in DOCS_ARCHIVE:
            decision[f] = ("ARCHIVE", "MED", "B2", DOCS_ARCHIVE[f] + f";{short_refs(f)}", "")
        elif f in ("docs/PROGRESS.md", "docs/SESSION_AUDIT_2026-08.md"):
            decision[f] = (
                "KEEP",
                "UNCERTAIN",
                "",
                f"large historical log ({size_lines(f)[1]} lines); referenced by ADR/RUNBOOK/archive README ({short_refs(f)})",
                "Freeze as a history file under docs/archive/ (needs ADR/RUNBOOK link rewrite) or keep as the living log?",
            )
        else:
            decision[f] = (
                "KEEP",
                "HIGH" if sum(c.values()) else "MED",
                "",
                f"current-surface doc (touched {LAST.get(f)});{short_refs(f)}",
                "",
            )
        continue

    # ---------------- reports ----------------
    if a == "reports":
        if f.startswith("reports/screenshots/"):
            if f.startswith("reports/screenshots/readme-overhaul/"):
                decision[f] = ("KEEP", "HIGH", "", "embedded by README.md", "")
            else:
                decision[f] = (
                    "DELETE",
                    "UNCERTAIN",
                    "B4",
                    f"PR-evidence PNG (rule 15c); zero tracked references; history retains it; wave dir {f.split('/')[2]}",
                    "Rule 15c says committed screenshots 'survive the PR/branch lifecycle': does GG want a retention policy (e.g. keep last N waves) instead of deleting?",
                )
        else:
            decision[f] = (
                "KEEP",
                "HIGH" if sum(c.values()) else "MED",
                "",
                f"committed analysis output; frozen-result evidence;{short_refs(f)}",
                ""
                if sum(c.values())
                else "No path-reference found; produced by an analysis.yml-dispatched/regenerated script, so KEEP until provenance header is confirmed.",
            )
        continue

    # ---------------- scraper ----------------
    if f in SCRAPER_DELETE:
        decision[f] = ("DELETE", "HIGH", "B1", SCRAPER_DELETE[f], "")
        continue
    if a == "scraper":
        if f == "scraper/backfill-history.js":
            decision[f] = (
                "KEEP",
                "MED",
                "",
                "knip: unused file, but cited by ADR 057 and ml/markup_reversion.py (frozen pre-registration source of history)",
                "Is the backfill still re-runnable? KEEP while ADR 057 stands.",
            )
        else:
            decision[f] = (
                "KEEP",
                "HIGH",
                "",
                (
                    ", ".join(sorted(x.split("/")[-1] for x in REFS[f].get("workflow", [])))
                    or "package/workflow dependency"
                )
                + ";"
                + short_refs(f),
                "",
            )
        continue

    # ---------------- tests ----------------
    if a == "tests":
        if f.endswith(".py") and "/" not in f[len("tests/") :]:
            tgt = [
                t
                for t in IMPORTS.get(f, [])
                if not t.startswith("tests/") and t != "ml/__init__.py"
            ]
            ph = [t for t in tgt if t in PHI_ARCHIVE or t == "ml/llm_cache_helpers.py"]
            if tgt and len(ph) == len(tgt) and f != "tests/test_no_dead_imports.py":
                decision[f] = (
                    "ARCHIVE",
                    "HIGH",
                    "B3",
                    "imports only ARCHIVE-class modules: " + ",".join(ph),
                    "",
                )
                continue
        decision[f] = (
            "KEEP",
            "HIGH",
            "",
            "test/fixture/helper; run by lint.yml (pytest -m 'not integration' / node --test / headless step) or consumed by check_test_counts/registry gates",
            "",
        )
        continue

    # ---------------- notebooks / config / etc ----------------
    if f.startswith("notebooks/"):
        decision[f] = (
            "ARCHIVE",
            "MED",
            "B2",
            "single LightGBM-era EDA notebook (ADR 001/007/008 era); zero inbound refs; excluded from Jekyll via _config.yml; no workflow; pre-commit nbstripout not required",
            "Needed as an interview artifact? If yes KEEP.",
        )
    elif f == ".claude/settings.local.json":
        decision[f] = (
            "KEEP",
            "UNCERTAIN",
            "",
            "tracked 'local' settings file (88 lines across .claude/); referenced by nothing; normally untracked",
            "Should settings.local.json be gitignored/untracked? (GG/config decision, not cleanup of code.)",
        )
    elif f == ".github/workflows/ci.yml":
        decision[f] = (
            "KEEP",
            "HIGH",
            "FIX",
            "FINDING: triggers on branches [main] but the default branch is master; `gh run list --workflow ci.yml` returns 0 runs ever, so scripts/check_manifest_provenance.py never runs in CI. Needs a trigger fix, not deletion.",
            "",
        )
    elif f == ".github/workflows/analysis.yml":
        decision[f] = (
            "KEEP",
            "MED",
            "FIX",
            "workflow_dispatch only; last run 2026-09-24 (failures at 20:02-20:06Z); options list includes 'chronos_m4' but scripts/analysis_chronos_m4.py does not exist (dangling choice)",
            "",
        )
    elif a == ".github":
        decision[f] = ("KEEP", "HIGH", "", "workflow/action in use; " + short_refs(f), "")
    else:
        decision[f] = ("KEEP", "HIGH", "", "root/app surface: " + short_refs(f), "")

for f in FILES:
    cls, conf, batch, ev, q = decision[f]
    add(f, cls, conf, batch, ev, q)

# ---- directories ----
DIRS = {
    ".claude": ("KEEP", "project instructions", ""),
    ".github": ("KEEP", "workflows+actions", ""),
    ".portfolio": ("KEEP", "metrics manifest checked by ci.yml (see FIX finding)", ""),
    "archive": ("KEEP", "already the archive; ARCHIVE batches move into archive/", ""),
    "config": ("KEEP", "retailers.json read by scraper/retailer-enabled.mjs + ml/retailers.py", ""),
    "data": ("KEEP", "live data surface; 1 DELETE + 2 ARCHIVE files inside", ""),
    "docs": ("KEEP", "11 docs ARCHIVE, 30 screenshots DELETE-uncertain", ""),
    "fonts": ("KEEP", "PWA fonts", ""),
    "icons": ("KEEP", "PWA icons", ""),
    "ml": ("KEEP", "live pipeline; 1 DELETE, 8 ARCHIVE files inside", ""),
    "notebooks": ("ARCHIVE", "single stale EDA notebook", ""),
    "reports": ("KEEP", "frozen-result evidence; screenshots subtree DELETE-uncertain", ""),
    "scraper": ("KEEP", "Tanishq scraper + canary", ""),
    "scripts": ("KEEP", "workflow gates/ops/analysis; see rows", ""),
    "tests": ("KEEP", "301 files", ""),
    "worker-deadman": (
        "KEEP",
        "dead-man Worker incl. parked Telegram channel (PR #2296 merged); never DELETE",
        "",
    ),
}
for d, (cls, ev, q) in DIRS.items():
    add(d + "/", cls, "HIGH", "", ev, q, kind="dir")

# ---- untracked / ignored (local, from main checkout) ----
UNTRACKED = [
    ("mlruns/", "empty", "DELETE", "local-only; MLflow retired by ADR 024; 0 files"),
    (
        "mlflow-db/",
        "0.6 MB",
        "DELETE",
        "local-only; MLflow retired by ADR 024; docker-compose.yml gone",
    ),
    (
        ".mypy_cache/",
        "454 MB / 22,394 files",
        "DELETE",
        "regenerable tool cache (gitignored); largest single item on disk",
    ),
    (".pytest_cache/ .ruff_cache/", "0.1 MB", "DELETE", "regenerable tool caches"),
    (
        "ml/models/ ml/training/ ml/tuning/",
        "__pycache__ only",
        "DELETE",
        "ghost directories of modules deleted by PR #29/ADR 024; contain only .pyc",
    ),
    (
        "models/",
        "20.8 MB / 36 files (local/lgbm, nbeats, tft, optuna)",
        "ARCHIVE",
        "UNCERTAIN: pre-Chronos trained artifacts referenced by ADR 001/007/008; not regenerable without retraining; move out of the repo dir, do not delete without GG",
    ),
    (
        "worker/",
        "5 files, 61 KB (gitignored)",
        "ARCHIVE",
        "UNCERTAIN: retired Phi25 clean-IP Worker (ADR/RUNBOOK; retired 2026-07-16); GG must still revoke its PAT manually",
    ),
    (
        "logs/",
        "9 files, 0.2 MB",
        "DELETE",
        "GPU-utilization logs from 2026-05-10/11 N-BEATS/TFT runs (retired models)",
    ),
    (
        "scraper/psi3c*.png, scraper/*screenshot*.mjs, spinner-check.mjs, visual-audit.mjs, verify-*.mjs, debug-standalone.mjs, scripts/screenshot_psi3b.mjs",
        "~45 files",
        "DELETE",
        "gitignored one-off verification artifacts; sibling of the 3 tracked scraper one-offs in batch B1",
    ),
    ("scraper/node_modules/", "16.8 MB", "KEEP", "installed dependencies (playwright/lighthouse)"),
    ("worker-deadman/.wrangler/", "local", "KEEP", "wrangler dev state"),
    ("__pycache__ dirs (ml, ml/*, scripts, tests)", "bytecode", "DELETE", "regenerable"),
    (
        "data/macro_cache.parquet, macro_status.json, notification_state.json, ibja_30day_sample.pdf",
        "runtime caches (gitignored)",
        "KEEP",
        "runtime state read by macro.py / notifications.py",
    ),
    (".env", "secret file (gitignored)", "KEEP", "never touched, never read"),
    (".claude/scheduled_tasks.lock", "lock", "KEEP", "session lock"),
    (
        "reports/model_audit_2026-10/ (untracked, other session)",
        "n/a",
        "KEEP",
        "belongs to another workstream; excluded from this analysis",
    ),
]
with open(OUT / "untracked.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["path", "size", "class", "evidence"])
    w.writerows(UNTRACKED)

fields = [
    "path",
    "kind",
    "area",
    "cls",
    "confidence",
    "batch",
    "size_bytes",
    "lines",
    "last_commit",
    "commits",
    "evidence",
    "question",
]
with open(OUT / "cleanup_map.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader()
    for r in sorted(rows, key=lambda r: (r["kind"] != "dir", r["path"])):
        w.writerow({k: r[k] for k in fields})

# ---- summary ----
tracked = [r for r in rows if r["kind"] == "file"]
summary: dict = {"tracked_files": len(tracked), "by_class": Counter(r["cls"] for r in tracked)}
summary["by_class_confidence"] = Counter(f"{r['cls']}/{r['confidence']}" for r in tracked)
by_area: dict = defaultdict(Counter)
for r in tracked:
    by_area[r["area"]][r["cls"]] += 1
summary["by_area"] = {k: dict(v) for k, v in sorted(by_area.items())}
bt: dict = defaultdict(lambda: dict(files=0, lines=0, bytes=0, uncertain=0))
for r in tracked:
    if r["batch"] and r["cls"] in ("DELETE", "ARCHIVE"):
        b = bt[r["batch"]]
        b["files"] += 1
        b["lines"] += r["lines"]
        b["bytes"] += r["size_bytes"]
        b["uncertain"] += r["confidence"] == "UNCERTAIN"
summary["batches"] = dict(bt)
summary["area_totals"] = {}
for a in sorted({r["area"] for r in tracked}):
    rs = [r for r in tracked if r["area"] == a]
    summary["area_totals"][a] = dict(
        files=len(rs), bytes=sum(r["size_bytes"] for r in rs), lines=sum(r["lines"] for r in rs)
    )
(OUT / "summary.json").write_text(json.dumps(summary, indent=1, default=dict))
print(json.dumps(summary, indent=1, default=dict))
