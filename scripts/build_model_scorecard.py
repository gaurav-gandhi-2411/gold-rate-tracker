"""scripts/build_model_scorecard.py -- ONE forward-scored scorecard of every model (item 2g).

Writes two files from the committed data/*.json track records:
  * data/model_scorecard_weekly.json   machine-readable, every number with provenance + marking;
  * docs/MODEL_SCORECARD.md            one page for people, rendered ONLY from that JSON.

Why data/ and not reports/ for the JSON: the weekly job commits through bot-pr-sync, whose
allow-list is data-only (see .github/actions/bot-pr-sync/action.yml and
scripts/check_bot_pr_sync_allowlist.py). The JSON is machine data, so it lives in data/ and no
allow-list is widened. The markdown is refreshed by docs-refresh.yml (its own PR path), which only
needs the stdlib: `--render-only` imports nothing heavy.

FORWARD means: only predictions issued AFTER the model's registration / promotion date, stated per
model in FORWARD_STARTS with the basis. Retrospective numbers (walk-forward over history, written
before the date) go in a separate column and are never added to the forward numbers. Below
MIN_FORWARD_N forward observations the verdict is "too early (n=...)", never a pass or a fail.

Statistics follow scripts/analysis_scorecard.py (#2015): paired one-sided Diebold-Mariano with a
HAC variance and an effective n (ml.direction.evaluate_reframed.diebold_mariano_test, lag 1),
Newey-West intervals for mean differences, Wilson intervals for coverage, exact binomial for
coverage-vs-target, Benjamini-Hochberg over every model-vs-baseline test that reached
MIN_FORWARD_N. Coverage days are treated as independent in the binomial test (approximate); the
effective n shown next to it uses an AR(1) correction.

Marking: VERIFIED = computed in this run from the raw per-prediction records. INFERRED = copied
from a number the producing job computed earlier (not re-measured here), or a stated approximation.

Live next-fix model (ADR 069): P3 (data/nextfix_p3_oos.json; folds flagged `retro` are re-runs on
past days and are shown only in the "earlier history" column, never as live calls). The old
ridge + neural-net ensemble (data/nextfix_oos.json) is then scored as a shadow on the same days.
While data/nextfix_p3_oos.json does not exist, the P3 row says so and the ensemble row behaves as
before (live, from data/nextfix_oos.json). Two ADR 071 shadow variants of P3 are compared with P3
and get no verdict before VARIANT_MIN_FORWARD_N forward days (ADR 071's frozen rule).

Demotion (ADR 068): ml/demotion.py is on master but nothing here imports it: the rules run in the
check-price job, which writes data/model_demotion_state.json (sticky state) and
forecast.json next_fix.demotion. `demotion_status(model_id, data_dir)` is the single place that
reads the state file; the "demotion monitor" row reads both files. A missing, unreadable or
malformed state file is "no data", never "not demoted".

Usage:
    python scripts/build_model_scorecard.py                 # compute -> JSON + markdown
    python scripts/build_model_scorecard.py --json-only     # weekly job: JSON only
    python scripts/build_model_scorecard.py --render-only   # markdown from the committed JSON
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT_JSON = DATA / "model_scorecard_weekly.json"
OUT_MD = ROOT / "docs" / "MODEL_SCORECARD.md"
SCRIPT_REL = "scripts/build_model_scorecard.py"

# Fewer forward observations than this and no verdict is shown (20 = ml.nextfix.MIN_CONFORMAL, the
# repo's own minimum for calling a range record "ready").
MIN_FORWARD_N = 20
# ADR 071 (frozen): no verdict on a P3 variant before 40 forward decision days (about 8 weeks).
VARIANT_MIN_FORWARD_N = 40
VARIANT_ALPHA = 0.025  # ADR 071: 0.05 / 2 variants, one-sided
VARIANT_MIN_GAIN = 0.02  # ADR 071: at least 2% lower error (champion-challenger margin)
P3_FILE = "nextfix_p3_oos.json"
P3_VARIANTS_FILE = "nextfix_p3_variants_oos.json"
DEMOTION_STATE_FILE = "model_demotion_state.json"
NOMINAL = 0.8
ALPHA = 0.05
# A coverage row is amber (not green) while its Wilson interval is wider than +/- this.
COVERAGE_IMPRECISE_HALF_WIDTH = 0.15

# Registration / promotion dates (forward = issued on or after, see `rule`). Each carries the basis
# so a reader can check it. "data" = derived from the committed file at run time.
FORWARD_STARTS: dict[str, dict[str, str]] = {
    "nextfix": {
        "date": "2026-10-01",
        "basis": "promoted 2026-10-02 (ADR 064/065); forward from decision day d0 >= 2026-10-01",
    },
    "p3": {
        "date": "2026-10-07",
        "basis": "ADR 069: P3 live from decision day d0 >= 2026-10-07 (ml.nextfix.P3_FORWARD_FROM; "
        "folds with retro false)",
    },
    "p3_variants": {
        "date": "2026-10-07",
        "basis": "ADR 071: forward folds only (retro false), d0 >= 2026-10-07 "
        "(ml.nextfix.P3_FORWARD_FROM), compared with P3",
    },
    "intraday": {
        "date": "2026-10-02",
        "basis": "ADR 066 shadow, first entry logged 2026-10-02",
    },
    "weekly_range": {
        "date": "2026-09-24",
        "basis": "ADR 043 shadow_after in data/weekly_range_shadow_log.json; as_of > this date",
    },
    "next_day_range": {
        "date": "2026-09-24",
        "basis": "ADR 047 frozen_at in data/next_day_range_shadow.json; decision_date > this date",
    },
    "wait_or_buy": {
        "date": "2026-09-24",
        "basis": "ADR 049 confirmatory_after in data/wait_or_buy_shadow.json; as_of > this date",
    },
    "nowcast": {
        "date": "2026-09-24",
        "basis": "ADR 061/063 shadow_after in data/nowcast_shadow_log.json; date > this date",
    },
    "h2": {
        "date": "2026-09-24",
        "basis": "ADR 038/042 confirmatory_after_as_of in the h2 shadow results; as_of > this date",
    },
    "chronos": {
        "date": "2026-10-05",
        "basis": "no promotion date exists; counted from this scorecard's first run (2026-10-05)",
    },
    "direction": {
        "date": "2026-10-05",
        "basis": "no forward ledger exists for h1/h2; counted from this scorecard's first run",
    },
    "calibration_band": {
        "date": "2026-10-05",
        "basis": "no per-day ledger exists; counted from this scorecard's first run",
    },
    "fusion": {
        "date": "data",
        "basis": "first as_of_date in data/fusion_snapshots.parquet (every snapshot is forward)",
    },
}

LIGHTS = {"green": "GREEN", "amber": "AMBER", "red": "RED", "grey": "GREY"}


LIVE_MODEL_ID = "nextfix_p3"  # the only model ADR 068's demotion rules watch today


P3_MODEL_VERSION = "nextfix_p3_v1"  # P3 keeps the original state file name (ml.nextfix)


# Mirrors ml.nextfix.CHAMPION_REGISTRY (id -> model version); a test keeps them equal. The version
# is derived from the champion id because forecast.json's next_fix.model_version is
# ``hold_latest_fix_<kind>`` in HOLD windows, not a model version.
CHAMPION_VERSIONS = {
    "p3": P3_MODEL_VERSION,
    "p3_roll60": "nextfix_p3_roll60_v1",
    "p3_monday": "nextfix_p3_monday_v1",
    "ensemble": "nextfix_ridge_mlp_v1",
}


def demotion_state_file(model_version: str) -> str:
    """Same naming as ml.nextfix.demotion_state_file (a test keeps them equal; this script does
    not import ml.*): P3 the original file, any other model its own ``__<version>`` file."""
    if model_version == P3_MODEL_VERSION:
        return DEMOTION_STATE_FILE
    return f"model_demotion_state__{model_version}.json"


def live_champion(data_dir: Path) -> tuple[str | None, str | None]:
    """(model id, model version) the live forecast was built by, from forecast.json (ADR 072).

    The id is ``effective_id`` when the run fell back to P3, else the champion id; the version is
    derived from the id (CHAMPION_VERSIONS), None for an unknown id. (None, None)
    when forecast.json names no champion (older file): callers then read P3 as before.
    """
    nf = (_read_json("forecast.json", data_dir) or {}).get("next_fix")
    nf = nf if isinstance(nf, dict) else {}
    ch = nf.get("champion")
    if not isinstance(ch, dict) or not isinstance(ch.get("id"), str):
        return None, None
    eff = ch.get("effective_id")
    cid = eff if isinstance(eff, str) else ch["id"]
    return cid, CHAMPION_VERSIONS.get(cid)


def not_p3_reason(data_dir: Path) -> str | None:
    """ "the live model is <id>, not P3" when another model is live, else None."""
    cid, _ = live_champion(data_dir)
    return f"the live model is {cid}, not P3" if cid not in (None, "p3") else None


def read_demotion_state(data_dir: Path, name: str = DEMOTION_STATE_FILE) -> dict[str, Any] | None:
    """A sticky state file (ADR 068; ``name`` per model, ADR 072) or None when it is missing,
    unreadable or malformed.

    Fail closed: a state that cannot be validated is None ("unknown"), never "not demoted".
    """
    s = _read_json(name, data_dir)
    if not isinstance(s, dict) or not isinstance(s.get("demoted"), bool):
        return None
    if not isinstance(s.get("reasons", []), list):
        return None
    return s


def demotion_status(model_id: str, data_dir: Path = DATA) -> dict[str, Any] | None:
    """Demotion state for a model: {"state", "reason", "source"} or None when not watched/unknown."""
    if model_id != LIVE_MODEL_ID:
        return None
    other = not_p3_reason(data_dir)
    if other:  # P3's own file is stale while another model is live: do not show it as current
        return {"state": "unavailable", "reason": other, "source": f"data/{DEMOTION_STATE_FILE}"}
    s = read_demotion_state(data_dir)
    if s is None:
        return None
    reasons = ", ".join(str(r) for r in s.get("reasons", [])) or "none"
    return {
        "state": "demoted" if s["demoted"] else "not demoted",
        "reason": reasons,
        "source": f"data/{DEMOTION_STATE_FILE}",
    }


# --- small stats (pure python; numpy/scipy only inside the helpers that need ml.*) --------------


def wilson(k: int, n: int, z: float = 1.959964) -> list[float] | None:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(c - h, 4), round(c + h, 4)]


def p_below(k: int, n: int, nominal: float = NOMINAL) -> float | None:
    """Exact one-sided P(X <= k | n, nominal): small = coverage reliably below target."""
    if n <= 0:
        return None
    return float(sum(math.comb(n, i) * nominal**i * (1 - nominal) ** (n - i) for i in range(k + 1)))


def mcnemar_one_sided(only_a: int, only_b: int) -> float | None:
    """Exact one-sided p that A hits more often than B (discordant pairs only_a vs only_b)."""
    m = only_a + only_b
    if m == 0:
        return None
    return float(sum(math.comb(m, i) for i in range(only_a, m + 1)) / 2**m)


def effective_n_ar1(x: list[float]) -> float | None:
    """n (1 - r) / (1 + r) with r the lag-1 autocorrelation (floored at 0); None below 5 points."""
    n = len(x)
    if n < 5:
        return None
    m = sum(x) / n
    d = [v - m for v in x]
    den = sum(v * v for v in d)
    if den == 0:
        return float(n)
    r = max(0.0, sum(d[i] * d[i - 1] for i in range(1, n)) / den)
    return round(n * (1 - r) / (1 + r), 1)


def hac_mean_ci(x: list[float], lag: int = 1) -> dict[str, Any]:
    """Mean with a Newey-West (Bartlett, `lag`) 95% interval; ci None below 3 points."""
    n = len(x)
    if n == 0:
        return {"n": 0, "mean": None, "ci95": None}
    m = sum(x) / n
    if n < 3:
        return {"n": n, "mean": m, "ci95": None}
    d = [v - m for v in x]
    lrv = sum(v * v for v in d) / n
    for k in range(1, lag + 1):
        lrv += 2 * (1 - k / (lag + 1)) * sum(d[i] * d[i - k] for i in range(k, n)) / n
    se = math.sqrt(max(lrv, 0.0) / n)
    return {"n": n, "mean": m, "ci95": [m - 1.96 * se, m + 1.96 * se]}


def dm_less(loss_model: list[float], loss_base: list[float]) -> dict[str, Any]:
    """One-sided paired HAC DM (H1: model loss lower). Needs >= 5 pairs; else all None."""
    if len(loss_model) < 5:
        return {"p_one_sided": None, "effective_n": None, "dm_stat": None}
    sys.path.insert(0, str(ROOT))
    try:
        from ml.direction.evaluate_reframed import diebold_mariano_test

        r = diebold_mariano_test(list(loss_model), list(loss_base), 2, alternative="less")
    except Exception as exc:  # report, never fake a pass
        return {"p_one_sided": None, "effective_n": None, "dm_stat": None, "error": repr(exc)}
    return {"p_one_sided": r["p_value"], "effective_n": r["effective_n"], "dm_stat": r["dm_stat"]}


def bh_flags(pvals: list[float | None]) -> list[bool | None]:
    """Benjamini-Hochberg at ALPHA over the non-None p-values; None stays None."""
    idx = [i for i, p in enumerate(pvals) if p is not None]
    out: list[bool | None] = [None] * len(pvals)
    if not idx:
        return out
    order = sorted(idx, key=lambda i: pvals[i])  # type: ignore[arg-type,return-value]
    m = len(order)
    cut = -1
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= ALPHA * rank / m:  # type: ignore[operator]
            cut = rank
    for rank, i in enumerate(order, start=1):
        out[i] = rank <= cut
    return out


# --- row construction ----------------------------------------------------------------------------


def _read_json(name: str, data_dir: Path) -> Any:
    """Parsed JSON or None for missing / empty / malformed (callers turn None into 'no data')."""
    try:
        text = (data_dir / name).read_text(encoding="utf-8-sig")  # a BOM is not corruption
        return json.loads(text) if text.strip() else None
    except (OSError, ValueError):
        return None


def _fmt(v: float | None, nd: int = 1) -> str:
    return "n/a" if v is None else f"{v:,.{nd}f}"


def base_row(
    row_id: str, name: str, status: str, predicts: str, start_key: str, sources: list[str]
) -> dict[str, Any]:
    fs = FORWARD_STARTS[start_key]
    return {
        "id": row_id,
        "name": name,
        "status": status,
        "status_note": "",
        "predicts": predicts,
        "forward_start": fs["date"],
        "forward_start_basis": fs["basis"],
        "forward": {
            "n": 0,
            "effective_n": None,
            "metric": None,
            "unit": None,
            "model": None,
            "baseline": None,
            "baseline_name": None,
            "diff": None,
            "ci95": None,
            "p_one_sided": None,
            "p_kind": None,
            "bh_significant": None,
            "first_date": None,
            "last_date": None,
            "marking": "VERIFIED",
            "extras": [],
        },
        "retrospective": {
            "label": "walk-forward over earlier history, NOT forward",
            "text": "none",
            "marking": "INFERRED",
        },
        "gate": {"text": "no gate recorded", "passes": None, "marking": "INFERRED"},
        "verdict": "grey",
        "verdict_kind": "no_data",
        "sentence": "",
        "sources": sources,
        "demotion": None,  # filled by build_rows from the state file (needs the data dir)
    }


def no_data(row: dict[str, Any], why: str) -> dict[str, Any]:
    row["verdict"], row["verdict_kind"] = "grey", "no_data"
    row["sentence"] = f"No forward data: {why}."
    row["forward"]["extras"].append(f"no forward data: {why}")
    return row


def _finish_point(row: dict[str, Any], tag: str) -> None:
    """Verdict for a model-vs-baseline loss row (forward fields already filled)."""
    f = row["forward"]
    n = f["n"]
    if n == 0:
        no_data(row, f"no resolved forecast on or after {row['forward_start']} yet")
        return
    if n < MIN_FORWARD_N:
        row["verdict"], row["verdict_kind"] = "grey", "too_early"
        row["sentence"] = (
            f"Too early (n={n}, need {MIN_FORWARD_N}) to say whether {tag} beats "
            f"{f['baseline_name']}."
        )
        return
    ci = f["ci95"]
    p = f["p_one_sided"]
    row["verdict_kind"] = "scored"
    if ci and ci[1] < 0 and p is not None and p < ALPHA and f["bh_significant"]:
        row["verdict"] = "green"
        row["sentence"] = f"{tag} is reliably more accurate than {f['baseline_name']}."
    elif ci and ci[0] > 0:
        row["verdict"] = "red"
        row["sentence"] = f"{tag} is reliably less accurate than {f['baseline_name']}."
    else:
        row["verdict"] = "amber"
        row["sentence"] = (
            f"No reliable difference from {f['baseline_name']} yet; the evidence is mixed."
        )


def _finish_coverage(row: dict[str, Any], tag: str) -> None:
    f = row["forward"]
    n = f["n"]
    if n == 0:
        no_data(row, f"no scored range on or after {row['forward_start']} yet")
        return
    if n < MIN_FORWARD_N:
        row["verdict"], row["verdict_kind"] = "grey", "too_early"
        row["sentence"] = f"Too early (n={n}, need {MIN_FORWARD_N}) to judge the {tag}."
        return
    row["verdict_kind"] = "scored"
    p = f["p_one_sided"]
    ci = f["ci95"] or [0.0, 1.0]
    if p is not None and p < ALPHA:
        row["verdict"] = "red"
        row["sentence"] = (
            f"The {tag} lands inside the range reliably less often than the 80% promised."
        )
    elif (ci[1] - ci[0]) / 2 > COVERAGE_IMPRECISE_HALF_WIDTH or (p is not None and p < 0.2):
        row["verdict"] = "amber"
        row["sentence"] = f"The {tag} is near the 80% target but the evidence is still loose."
    else:
        row["verdict"] = "green"
        row["sentence"] = f"The {tag} is hitting its 80% target within the margin of error."


def _loss_block(row: dict[str, Any], e_model: list[float], e_base: list[float], unit: str) -> None:
    """Forward fields for a paired absolute-loss comparison (model vs baseline), lower = better."""
    f = row["forward"]
    n = len(e_model)
    f.update(
        n=n,
        unit=unit,
        model=round(sum(e_model) / n, 2) if n else None,
        baseline=round(sum(e_base) / n, 2) if n else None,
    )
    if n:
        d = [a - b for a, b in zip(e_model, e_base, strict=True)]
        ci = hac_mean_ci(d)
        f["diff"] = round(ci["mean"], 2)
        f["ci95"] = [round(v, 2) for v in ci["ci95"]] if ci["ci95"] else None
        dm = dm_less(e_model, e_base)
        f["p_one_sided"] = None if dm["p_one_sided"] is None else round(dm["p_one_sided"], 4)
        f["effective_n"] = (
            dm["effective_n"] if dm["effective_n"] is None else round(dm["effective_n"], 1)
        )
        f["p_kind"] = "one-sided HAC Diebold-Mariano (lag 1)"


def _coverage_block(
    row: dict[str, Any], hits: list[bool], widths: list[float] | None = None
) -> None:
    f = row["forward"]
    n = len(hits)
    k = sum(hits)
    f.update(
        n=n,
        metric="coverage of the 80% range",
        unit="share of days inside",
        model=round(k / n, 3) if n else None,
        baseline=NOMINAL,
        baseline_name="the 80% it promises",
        ci95=wilson(k, n),
        p_one_sided=None if n == 0 else round(p_below(k, n) or 0.0, 4),
        p_kind="exact one-sided binomial, coverage below 80% (days treated as independent)",
        effective_n=effective_n_ar1([1.0 if h else 0.0 for h in hits]),
    )
    f["diff"] = None if n == 0 else round(k / n - NOMINAL, 3)
    if widths:
        f["extras"].append(f"mean range width Rs {sum(widths) / len(widths):,.0f}")


# --- the models ----------------------------------------------------------------------------------


def _fold_err(f: dict[str, Any]) -> float:
    """Absolute error (Rs/g) of a next-fix fold's forecast against the fix that was published."""
    return abs(f["pm1"] - f["pm0"] * math.exp(f["ret"]))


def _hold_err(f: dict[str, Any]) -> float:
    return abs(f["pm1"] - f["pm0"])


def _pair_stats(e_model: list[float], e_base: list[float]) -> dict[str, Any]:
    """Paired absolute-error comparison on the same days (lower = better): mean difference,
    Newey-West interval, one-sided HAC Diebold-Mariano p and its effective n."""
    n = len(e_model)
    out: dict[str, Any] = {
        "n": n,
        "model": None,
        "baseline": None,
        "diff": None,
        "ci95": None,
        "p_one_sided": None,
        "effective_n": None,
        "p_kind": "one-sided HAC Diebold-Mariano (lag 1)",
    }
    if not n:
        return out
    ci = hac_mean_ci([a - b for a, b in zip(e_model, e_base, strict=True)])
    dm = dm_less(e_model, e_base)
    out.update(
        model=round(sum(e_model) / n, 2),
        baseline=round(sum(e_base) / n, 2),
        diff=round(ci["mean"], 2),
        ci95=[round(v, 2) for v in ci["ci95"]] if ci["ci95"] else None,
        p_one_sided=None if dm["p_one_sided"] is None else round(dm["p_one_sided"], 4),
        effective_n=None if dm["effective_n"] is None else round(dm["effective_n"], 1),
    )
    return out


def _no_record(row: dict[str, Any], file: str, extra: str = "") -> dict[str, Any]:
    """The record does not exist (yet): say so, never a verdict."""
    row["verdict"], row["verdict_kind"] = "grey", "no_data"
    row["sentence"] = f"No data: record not present yet (data/{file}){extra}."
    row["forward"]["extras"].append(f"no data: record not present yet (data/{file})")
    return row


def _p3_split(
    data_dir: Path,
) -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]]]:
    """(file_exists, forward folds, retrospective folds) of the P3 record.

    Forward = flagged `retro` false AND decision day >= the P3 start (ADR 069). Everything else is
    a re-run on past days. A fold with no `retro` flag is never counted as forward (fail closed).
    """
    start = FORWARD_STARTS["p3"]["date"]
    exists = (data_dir / P3_FILE).exists()
    doc = _read_json(P3_FILE, data_dir)
    folds = doc.get("folds") if isinstance(doc, dict) else None
    if not isinstance(folds, list):
        return exists, [], []
    good = [f for f in folds if isinstance(f, dict) and {"d0", "pm0", "pm1", "ret"} <= f.keys()]
    fwd = [f for f in good if f.get("retro") is False and f["d0"] >= start]
    old = [f for f in good if not (f.get("retro") is False and f["d0"] >= start)]
    return exists, fwd, old


def _retro_text(old: list[dict[str, Any]], what: str) -> tuple[str, str]:
    """Text for the earlier-history column of a next-fix model: re-runs, not live calls."""
    rm = [_fold_err(f) for f in old]
    rb = [_hold_err(f) for f in old]
    ci = hac_mean_ci([a - b for a, b in zip(rm, rb, strict=True)])
    text = (
        f"{what}: re-run on past days, NOT live calls; n={len(old)} ({old[0]['d0']} to "
        f"{old[-1]['d0']}): error {sum(rm) / len(rm):.1f} vs hold {sum(rb) / len(rb):.1f} Rs/g, "
        f"change {100 * (sum(rm) / sum(rb) - 1):+.1f}%"
        + (f", diff 95% CI [{ci['ci95'][0]:+.1f}, {ci['ci95'][1]:+.1f}]" if ci["ci95"] else "")
    )
    return text, "VERIFIED"


def _direction_extra(fwd: list[dict[str, Any]]) -> str | None:
    moved = [f for f in fwd if f["pm1"] != f["pm0"] and "p_up" in f and "y" in f]
    if not moved:
        return None
    ok = sum((f["p_up"] > 0.5) == (f["y"] > 0) for f in moved)
    ups = sum(f["y"] > 0 for f in moved)
    return f"direction right {ok}/{len(moved)} moves vs always-up {ups}/{len(moved)}"


def _gate_from_forecast(row: dict[str, Any], data_dir: Path) -> None:
    other = not_p3_reason(data_dir)
    if other:  # forecast.json's gates and n_forward describe the live model, not P3 (ADR 072)
        row["gate"] = {"text": f"unavailable: {other}", "passes": None, "marking": "INFERRED"}
        row["forward"]["extras"].append(f"direction and timing gates unavailable: {other}")
        return
    nf = (_read_json("forecast.json", data_dir) or {}).get("next_fix") or {}
    tr = nf.get("track_record") or {}
    if not tr:
        return
    shown = (nf.get("direction") or {}).get("show")
    row["gate"] = {
        "text": (
            f"direction gate {'ship' if tr.get('direction_gate_ship') else 'no ship'}, "
            f"timing gate {'ship' if tr.get('timing_gate_ship') else 'no ship'}; "
            f"direction shown to users: {'yes' if shown else 'no'}"
        ),
        "passes": bool(tr.get("direction_gate_ship")),
        "marking": "INFERRED",
    }
    if "n_forward" in tr:
        row["forward"]["extras"].append(
            f"forecast.json reports n_forward={tr['n_forward']} (copied, not re-measured here)"
        )


def row_p3(data_dir: Path) -> dict[str, Any]:
    """The LIVE next-fix model (ADR 069): P3, forward only from decision day 2026-10-07."""
    cid, _ = live_champion(data_dir)
    row = base_row(
        LIVE_MODEL_ID,
        "ml.nextfix P3 (live next-fix forecast)"
        if cid in (None, "p3")
        else f"ml.nextfix P3 (the live model is now {cid}; P3 is kept as the reference)",
        "LIVE",
        "next IBJA PM fix (22K, Rs/g), forecast after the US close",
        "p3",
        [f"data/{P3_FILE}", "data/forecast.json", f"data/{DEMOTION_STATE_FILE}"],
    )
    row["forward"].update(
        metric="mean absolute error of the next fix", baseline_name="holding the last fix"
    )
    exists, fwd, old = _p3_split(data_dir)
    if not fwd and not old:
        if exists:
            return no_data(row, f"data/{P3_FILE} is unreadable or has no usable folds")
        return _no_record(row, P3_FILE)
    _loss_block(row, [_fold_err(f) for f in fwd], [_hold_err(f) for f in fwd], "Rs/g")
    if fwd:
        row["forward"]["first_date"], row["forward"]["last_date"] = fwd[0]["d0"], fwd[-1]["d0"]
        extra = _direction_extra(fwd)
        if extra:
            row["forward"]["extras"].append(extra)
    if old:
        text, mark = _retro_text(old, "P3 history")
        row["retrospective"].update(text=text, marking=mark)
    _gate_from_forecast(row, data_dir)
    _finish_point(row, "P3")
    return row


def row_nextfix(data_dir: Path) -> list[dict[str, Any]]:
    """P3 row, then the ensemble row. With the P3 record present the ensemble is the SHADOW,
    scored on the same forward days as P3 and compared with it; without it the ensemble row
    behaves exactly as before (live, from data/nextfix_oos.json)."""
    p3_row = row_p3(data_dir)
    p3_exists, p3_fwd, _ = _p3_split(data_dir)
    shadow = p3_exists
    start = FORWARD_STARTS["p3" if shadow else "nextfix"]["date"]
    row = base_row(
        "nextfix_ensemble_shadow" if shadow else "nextfix_model",
        "shadow: ridge+neural-net ensemble" if shadow else "ml.nextfix model window (ridge + nets)",
        "SHADOW" if shadow else "LIVE",
        "next IBJA PM fix (22K, Rs/g), forecast after the US close",
        "p3" if shadow else "nextfix",
        ["data/nextfix_oos.json", "data/forecast.json"] + ([f"data/{P3_FILE}"] if shadow else []),
    )
    row["forward"].update(
        metric="mean absolute error of the next fix", baseline_name="holding the last fix"
    )
    oos = _read_json("nextfix_oos.json", data_dir)
    folds = (oos or {}).get("folds") if isinstance(oos, dict) else None
    if not folds:
        return [p3_row, no_data(row, "data/nextfix_oos.json is missing or empty")]
    fwd = [f for f in folds if f["d0"] >= start]
    old = [f for f in folds if f["d0"] < start]
    _loss_block(row, [_fold_err(f) for f in fwd], [_hold_err(f) for f in fwd], "Rs/g")
    if fwd:
        row["forward"]["first_date"], row["forward"]["last_date"] = fwd[0]["d0"], fwd[-1]["d0"]
        extra = _direction_extra(fwd)
        if extra:
            row["forward"]["extras"].append(extra)
    if old:
        if shadow:
            text, mark = _retro_text(old, "ensemble history")
            row["retrospective"].update(text=text, marking=mark)
        else:
            rm = [_fold_err(f) for f in old]
            rb = [_hold_err(f) for f in old]
            ci = hac_mean_ci([a - b for a, b in zip(rm, rb, strict=True)])
            row["retrospective"].update(
                text=(
                    f"n={len(old)} ({old[0]['d0']} to {old[-1]['d0']}): error "
                    f"{sum(rm) / len(rm):.1f} vs hold {sum(rb) / len(rb):.1f} Rs/g, "
                    f"change {100 * (sum(rm) / sum(rb) - 1):+.1f}%"
                    + (
                        f", diff 95% CI [{ci['ci95'][0]:+.1f}, {ci['ci95'][1]:+.1f}]"
                        if ci["ci95"]
                        else ""
                    )
                ),
                marking="VERIFIED",
            )
    if shadow:
        row["status_note"] = "not shown to users; scored on the same days as P3"
        _add_pair_vs_p3(row, "P3 vs the ensemble", p3_fwd, fwd, min_n=MIN_FORWARD_N)
        row["gate"] = {
            "text": "shadow since ADR 069; nothing shown to users",
            "passes": None,
            "marking": "INFERRED",
        }
    else:
        _gate_from_forecast(row, data_dir)
    _finish_point(row, "the ensemble" if shadow else "the model")
    return [p3_row, row]


def _add_pair_vs_p3(
    row: dict[str, Any],
    label: str,
    p3_fwd: list[dict[str, Any]],
    other_fwd: list[dict[str, Any]],
    min_n: int,
    p3_is_model: bool = True,
) -> dict[str, Any]:
    """Paired comparison on the days both records scored. Stored in row['forward']['paired'] and
    summarised in extras. `p3_is_model`: the first-listed model in `label` is P3 (H1: P3's error is
    lower); otherwise the other record is the model (H1: its error is lower than P3's)."""
    other = {f["d0"]: f for f in other_fwd}
    common = [(f, other[f["d0"]]) for f in p3_fwd if f["d0"] in other]
    e_p3 = [_fold_err(a) for a, _ in common]
    e_ot = [_fold_err(b) for _, b in common]
    st = _pair_stats(e_p3, e_ot) if p3_is_model else _pair_stats(e_ot, e_p3)
    st["label"] = label
    st["min_n"] = min_n
    st["marking"] = "VERIFIED"
    row["forward"]["paired"] = st
    n = st["n"]
    if n < min_n:
        row["forward"]["extras"].append(f"{label}, same days: too early (n={n}, need {min_n})")
        return st
    ci = st["ci95"]
    row["forward"]["extras"].append(
        f"{label}, same days: n={n}, eff. n={st['effective_n']}, error {st['model']} vs "
        f"{st['baseline']} Rs/g, diff {st['diff']:+.2f}"
        + (f", 95% [{ci[0]:+.2f}, {ci[1]:+.2f}]" if ci else "")
        + (f", one-sided p={st['p_one_sided']:.3f}" if st["p_one_sided"] is not None else "")
        + " (V; lower error is better, the first-named model's error is listed first)"
    )
    return st


def _variant_verdict(
    row: dict[str, Any],
    v_fwd: list[dict[str, Any]],
    p3_fwd: list[dict[str, Any]],
    name: str,
    is_champion: bool = False,
) -> None:
    """ADR 071's frozen rule. No verdict below VARIANT_MIN_FORWARD_N forward days."""
    st = row["forward"]["paired"]
    n = st["n"]
    row["forward"]["n"] = n
    if n < VARIANT_MIN_FORWARD_N:
        row["verdict"], row["verdict_kind"] = "grey", "too_early"
        tail = (
            f"{name} is the live model and ADR 068's monitor watches it."
            if is_champion
            else "P3 stays."
        )
        row["sentence"] = (
            f"Too early (n={n}, need {VARIANT_MIN_FORWARD_N} by ADR 071's frozen rule); {tail}"
        )
        return
    # direction: exact McNemar on days the fix moved (reported in the frozen rule as a guard)
    pv = {f["d0"]: f for f in p3_fwd}
    only_p3 = only_v = 0
    for f in v_fwd:
        g = pv.get(f["d0"])
        if g is None or f["pm1"] == f["pm0"]:
            continue
        ok_v = (f["p_up"] > 0.5) == (f["y"] > 0)
        ok_p = (g["p_up"] > 0.5) == (g["y"] > 0)
        only_p3 += ok_p and not ok_v
        only_v += ok_v and not ok_p
    p_dir_lower = mcnemar_one_sided(only_p3, only_v)  # small = variant reliably less accurate
    row["forward"]["extras"].append(
        f"direction on days the fix moved, only one right: P3 {only_p3} vs {name} {only_v}"
        + ("" if p_dir_lower is None else f" (exact McNemar one-sided p={p_dir_lower:.3f})")
    )
    row["verdict_kind"] = "scored"
    gain = None if not st["baseline"] else 1 - st["model"] / st["baseline"]
    clearly = (
        gain is not None
        and gain >= VARIANT_MIN_GAIN
        and st["p_one_sided"] is not None
        and st["p_one_sided"] < VARIANT_ALPHA
        and (p_dir_lower is None or p_dir_lower >= VARIANT_ALPHA)
    )
    if clearly:
        row["verdict"] = "green"
        row["sentence"] = (
            f"{name} clearly beats P3 under ADR 071's frozen rule and is already the live model."
            if is_champion
            else f"{name} clearly beats P3 under ADR 071's frozen rule: recommend to GG; nothing "
            "ships automatically."
        )
    elif st["ci95"] and st["ci95"][0] > 0:
        row["verdict"] = "red"
        row["sentence"] = f"{name} is reliably less accurate than P3" + (
            "; it is the live model, so ADR 068's monitor and a person decide."
            if is_champion
            else "; P3 stays."
        )
    else:
        row["verdict"] = "amber"
        row["sentence"] = f"{name} does not clearly beat P3 (ADR 071's rule)" + (
            "; it is the live model anyway and ADR 068's monitor watches it."
            if is_champion
            else "; P3 stays."
        )


def _variant_rows(data_dir: Path) -> list[dict[str, Any]]:
    specs = [
        (
            "p3_roll60",
            "shadow: P3 on the last 60 days (ADR 071 V1)",
            "slope fitted on the 60 most recent resolved days",
        ),
        ("p3_monday", "shadow: P3 Monday slope (ADR 071 V2)", "slope fitted on Monday days only"),
    ]
    _, p3_fwd, p3_old = _p3_split(data_dir)
    live_id, _ = live_champion(data_dir)
    p3_exists = (data_dir / P3_FILE).exists()
    doc = _read_json(P3_VARIANTS_FILE, data_dir)
    variants = doc.get("variants") if isinstance(doc, dict) else None
    rows = []
    for vid, name, what in specs:
        row = base_row(
            vid,
            name,
            "SHADOW",
            f"next IBJA PM fix, like P3 but with the {what}",
            "p3_variants",
            [f"data/{P3_VARIANTS_FILE}", f"data/{P3_FILE}"],
        )
        row["forward"].update(
            metric="mean absolute error of the next fix", baseline_name="P3 (the live model)"
        )
        row["status_note"] = "not shown to users; compared with P3 only"
        row["gate"] = {
            "text": f"ADR 071: needs {VARIANT_MIN_FORWARD_N} forward days, error at least 2% lower, "
            f"one-sided p < {VARIANT_ALPHA}, direction not worse",
            "passes": None,
            "marking": "INFERRED",
        }
        folds = variants.get(vid) if isinstance(variants, dict) else None
        if not isinstance(folds, list) or not folds:
            rows.append(_no_record(row, P3_VARIANTS_FILE))
            continue
        if not p3_exists:
            rows.append(_no_record(row, P3_FILE))
            continue
        good = [f for f in folds if isinstance(f, dict) and {"d0", "pm0", "pm1", "ret"} <= f.keys()]
        start = FORWARD_STARTS["p3_variants"]["date"]
        v_fwd = [f for f in good if f.get("retro") is False and f["d0"] >= start]
        v_old = [f for f in good if not (f.get("retro") is False and f["d0"] >= start)]
        st = _add_pair_vs_p3(row, f"{name} vs P3", p3_fwd, v_fwd, VARIANT_MIN_FORWARD_N, False)
        f = row["forward"]
        f.update(
            unit="Rs/g",
            model=st["model"],
            baseline=st["baseline"],
            diff=st["diff"],
            ci95=st["ci95"],
            p_one_sided=st["p_one_sided"],
            p_kind=st["p_kind"] + f"; frozen alpha {VARIANT_ALPHA} (ADR 071)",
            effective_n=st["effective_n"],
        )
        if st["n"]:
            days = sorted(x["d0"] for x in v_fwd if x["d0"] in {y["d0"] for y in p3_fwd})
            f["first_date"], f["last_date"] = days[0], days[-1]
        if vid == "p3_monday" and v_fwd:
            mon = [
                (x, y)
                for x in v_fwd
                for y in p3_fwd
                if x["d0"] == y["d0"] and date.fromisoformat(x["d0"]).weekday() == 0
            ]
            if mon:
                a = [_fold_err(x) for x, _ in mon]
                b = [_fold_err(y) for _, y in mon]
                f["extras"].append(
                    f"Monday decisions only (reported, not used for the verdict): n={len(mon)}, "
                    f"error {sum(a) / len(a):.1f} vs P3 {sum(b) / len(b):.1f} Rs/g"
                )
        if v_old:
            rm = [_fold_err(x) for x in v_old]
            old_p3 = {x["d0"]: x for x in p3_old}
            pairs = [(x, old_p3[x["d0"]]) for x in v_old if x["d0"] in old_p3]
            txt = f"n={len(v_old)} re-run days ({v_old[0]['d0']} to {v_old[-1]['d0']}): "
            txt += f"variant error {sum(rm) / len(rm):.1f} Rs/g"
            if pairs:
                rp = [_fold_err(y) for _, y in pairs]
                txt += f" vs P3 {sum(rp) / len(rp):.1f} on {len(pairs)} shared days"
            row["retrospective"].update(
                text=f"re-run on past days, NOT live calls; {txt}", marking="VERIFIED"
            )
        _variant_verdict(row, v_fwd, p3_fwd, name, vid == live_id)
        rows.append(row)
    return rows


def row_demotion_monitor(data_dir: Path) -> dict[str, Any]:
    """ADR 068 monitor: the sticky state file plus the rules breaching now (from forecast.json)."""
    row = base_row(
        "demotion_monitor",
        "demotion monitor (ADR 068, watches P3)",
        "LIVE",
        "whether the live next-fix model has been switched back to holding the last fix",
        "p3",
        [f"data/{DEMOTION_STATE_FILE}", "data/forecast.json"],
    )
    row["forward"]["marking"] = "INFERRED"  # copied from files the check-price job wrote
    cid, ver = live_champion(data_dir)
    name = DEMOTION_STATE_FILE
    if cid not in (None, "p3"):  # ADR 072: the live champion's own per-model state file
        if ver is None:
            return no_data(row, f"the live model is {cid} but forecast.json names no version")
        name = demotion_state_file(ver)
        row["sources"] = [f"data/{name}", "data/forecast.json"]
    s = read_demotion_state(data_dir, name)
    who = f"the live model {cid}" if cid not in (None, "p3") else "P3"
    if s is None:
        exists = (data_dir / name).exists()
        why = (
            f"data/{name} ({who}) is unreadable or malformed"
            if exists
            else f"data/{name} ({who}) is not present yet"
        )
        return no_data(row, why + "; this is not the same as 'not demoted'")
    if cid not in (None, "p3"):
        row["forward"]["extras"].append(f"state of the live model {cid} ({ver})")
    dm = ((_read_json("forecast.json", data_dir) or {}).get("next_fix") or {}).get("demotion")
    breaching: list[str] | None = None
    if isinstance(dm, dict) and isinstance(dm.get("rules_breaching_now"), list):
        breaching = [str(x) for x in dm["rules_breaching_now"]]
    ex = row["forward"]["extras"]
    ex.append(f"demoted: {'yes' if s['demoted'] else 'no'}")
    ex.append(f"since: {s.get('since') or 'n/a'}")
    ex.append(f"reasons recorded: {', '.join(str(r) for r in s['reasons']) or 'none'}")
    ex.append(f"state file last checked: {s.get('last_checked') or 'n/a'}")
    ex.append(
        "rules breaching now (forecast.json): "
        + ("not available yet" if breaching is None else (", ".join(breaching) or "none"))
    )
    ex.append(
        f"live record {s.get('model_version', 'n/a')}; re-promotion is never automatic "
        "(a person decides)"
    )
    row["verdict_kind"] = "monitor"
    row["gate"] = {
        "text": "ADR 068 rules: error, direction, range; a breach must repeat on 3 daily checks",
        "passes": None,
        "marking": "INFERRED",
    }
    # the page can be held without the state file saying so (a monitor that could not run fails
    # closed and writes nothing): the forecast's own readout is authoritative for what users see
    held = isinstance(dm, dict) and dm.get("demoted") is True
    if s["demoted"] or held:
        row["verdict"] = "red"
        row["sentence"] = "The live next-fix model is switched off; the page holds the last fix" + (
            " (the monitor itself could not run)." if held and dm.get("checked") is False else "."
        )
    elif breaching:
        row["verdict"] = "amber"
        row["sentence"] = "Not demoted, but a monitoring rule is breaching right now."
    elif breaching is None:
        row["verdict"] = "amber"
        row["sentence"] = (
            "Not demoted according to the state file; the live rule readout is not available yet."
        )
    else:
        row["verdict"] = "green"
        row["sentence"] = "Not demoted, and no monitoring rule is breaching."
    return row


def _hold_rows(data_dir: Path) -> list[dict[str, Any]]:
    start = FORWARD_STARTS["nextfix"]["date"]
    specs = [
        ("hold_am_to_pm", "nextfix hold: after the morning fix", "am_to_pm", "that day's PM fix"),
        ("hold_pm_to_am", "nextfix hold: after the afternoon fix", "pm_to_am", "the next AM fix"),
    ]
    rows = []
    for rid, name, kind, target in specs:
        row = base_row(
            rid,
            name,
            "LIVE",
            f"{target}, by holding the latest fix with a volatility-scaled range",
            "nextfix",
            ["data/ibja_rates.parquet"],
        )
        try:
            sys.path.insert(0, str(ROOT))
            from ml import nextfix

            pairs = nextfix.flat_pairs(
                nextfix.load_ibja_full(data_dir / "ibja_rates.parquet"), kind
            )
            rec = nextfix.flat_record(pairs, since=start)
            old = nextfix.flat_record(pairs[pairs["d0"] < start].reset_index(drop=True))
        except Exception as exc:  # report, never fake a pass
            rows.append(no_data(row, f"could not read IBJA history ({type(exc).__name__})"))
            continue
        n = int(rec.get("n", 0))
        k = round((rec.get("range_coverage") or 0.0) * n)
        f = row["forward"]
        _coverage_block(row, [True] * k + [False] * (n - k))
        # flat_record's own walk-forward hit list is not returned, so the hit sequence for the
        # AR(1) effective n is unavailable: say so rather than show a number built from a fake order.
        f["effective_n"] = None
        f["extras"].append(
            "effective n unavailable (hit order not exposed by ml.nextfix.flat_record)"
        )
        if rec.get("range_mean_width") is not None:
            f["extras"].append(f"mean range width Rs {rec['range_mean_width']:,.0f}")
        f["first_date"] = rec.get("first_d0") if n else None
        f["last_date"] = rec.get("last_d0") if n else None
        if old.get("n"):
            row["retrospective"].update(
                text=(
                    f"n={old['n']}: coverage {old['range_coverage']:.3f} "
                    f"(95% {old['range_coverage_ci95'][0]:.3f}-{old['range_coverage_ci95'][1]:.3f})"
                ),
                marking="VERIFIED",
            )
        row["gate"] = {
            "text": "range ready when n >= 20 (ml.nextfix)",
            "passes": None,
            "marking": "INFERRED",
        }
        _finish_coverage(row, "hold range")
        rows.append(row)
    return rows


def _dedupe_intraday(entries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """One scored decision per base fix and window: the latest entry logged BEFORE the target fix's
    publish clock. Stricter than ml.nextfix_intraday.score, which keeps the latest entry even if it
    was logged after the target was published."""
    sys.path.insert(0, str(ROOT))
    am, pm = (6, 30), (11, 30)
    try:
        from ml import nextfix

        am, pm = nextfix.IBJA_AM_PUBLISH_UTC, nextfix.IBJA_PM_PUBLISH_UTC
    except Exception:  # keep the documented defaults
        pass
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for e in entries:
        if e.get("target") is None or not e.get("target_date"):
            continue
        h, m = am if e.get("target_kind") == "am" else pm
        publish = f"{e['target_date']}T{h:02d}:{m:02d}:00Z"
        if e["logged_at"] >= publish:
            continue
        out.setdefault(e["window"], {})
        cur = out[e["window"]].get(e["base_at"])
        if cur is None or e["logged_at"] > cur["logged_at"]:
            out[e["window"]][e["base_at"]] = e
    return {w: list(v.values()) for w, v in out.items()}


def _intraday_rows(data_dir: Path) -> list[dict[str, Any]]:
    start = FORWARD_STARTS["intraday"]["date"]
    doc = _read_json("nextfix_intraday_shadow.json", data_dir)
    entries = (doc or {}).get("entries") if isinstance(doc, dict) else None
    windows = [
        ("after_morning_rate", "after the morning fix"),
        ("after_afternoon_rate", "after the afternoon fix"),
        ("after_us_close", "after the US close"),
    ]
    rows = []
    groups = (
        _dedupe_intraday([e for e in entries if e["logged_at"][:10] >= start]) if entries else {}
    )
    for wkey, wname in windows:
        row = base_row(
            f"intraday_{wkey}",
            f"nextfix_intraday shadow ({wname})",
            "SHADOW",
            "next IBJA fix from the hourly world gold price (pass-through beta 1.0)",
            "intraday",
            ["data/nextfix_intraday_shadow.json"],
        )
        row["forward"].update(
            metric="mean absolute error of the next fix", baseline_name="holding the last fix"
        )
        if not entries:
            rows.append(no_data(row, "data/nextfix_intraday_shadow.json is missing or empty"))
            continue
        rs = groups.get(wkey, [])
        _loss_block(
            row,
            [abs(r["pred"]["beta_1.0"] - r["target"]) for r in rs],
            [abs(r["pred"]["beta_0.0"] - r["target"]) for r in rs],
            "Rs/g",
        )
        if rs:
            half = [abs(r["pred"]["beta_0.5"] - r["target"]) for r in rs]
            row["forward"]["extras"].append(
                f"beta 0.5 error {sum(half) / len(half):.1f} Rs/g (reported, not used for the verdict)"
            )
            row["forward"]["first_date"] = min(r["base_date"] for r in rs)
            row["forward"]["last_date"] = max(r["base_date"] for r in rs)
        row["forward"]["extras"].append(
            "one decision per base fix, logged before the target fix was published"
        )
        summ = ((doc or {}).get("summary") or {}).get(wkey) if isinstance(doc, dict) else None
        late = summ.get("n_excluded_logged_after_target") if isinstance(summ, dict) else None
        row["forward"]["extras"].append(
            "ADR 066 scoring correction: entries logged after their target fix was published are "
            + (
                f"excluded; the shadow job reports {late} excluded so far (I)"
                if late is not None
                else "excluded; the shadow job's count is not in its summary (n/a)"
            )
        )
        row["gate"] = {
            "text": "ADR 066 promotion: >= 14 days of shadow AND an interval that excludes zero",
            "passes": False,
            "marking": "INFERRED",
        }
        _finish_point(row, "the hourly pass-through")
        rows.append(row)
    return rows


def row_calibration_band(data_dir: Path) -> dict[str, Any]:
    row = base_row(
        "calibration_band",
        "IBJA-calibrated displayed band",
        "LIVE",
        "Tanishq 22K range shown on the IBJA-calibrated tier",
        "calibration_band",
        ["data/calibration_band_coverage.json"],
    )
    c = _read_json("calibration_band_coverage.json", data_dir)
    if not isinstance(c, dict) or not c.get("n"):
        return no_data(row, "data/calibration_band_coverage.json is missing or empty")
    row["retrospective"].update(
        text=(
            f"n={c['n']}: coverage {c['coverage']:.3f} (95% {c['wilson_ci_low']:.3f}-"
            f"{c['wilson_ci_high']:.3f}) vs 0.80 promised; stratified shadow band "
            f"{c['stratified_shadow']['pooled_stratified']['coverage']:.3f}"
        ),
        marking="INFERRED",
    )
    row["gate"] = {
        "text": f"resolvable at n: {c.get('resolvable_at_n')}; below 80% (p vs nominal "
        f"{c['stratified_shadow']['pooled_production']['p_vs_nominal']:.3f})",
        "passes": False,
        "marking": "INFERRED",
    }
    no_data(
        row, "the file is a walk-forward re-score of history with no per-day ledger of issued bands"
    )
    row["sentence"] = (
        "No forward data: this band has no per-day ledger of issued ranges, so only the "
        f"retrospective miss is known ({c['coverage']:.0%} inside vs 80% promised, n={c['n']})."
    )
    return row


def _direction_rows(data_dir: Path) -> list[dict[str, Any]]:
    d = _read_json("direction_baseline.json", data_dir)
    rows = []
    for h in ("h1", "h2"):
        row = base_row(
            f"direction_{h}",
            f"ml.direction ({h} up/down)",
            "DARK",
            f"whether the price is higher after {h[1]} IBJA reading(s)",
            "direction",
            ["data/direction_baseline.json"],
        )
        blk = ((d or {}).get("horizons") or {}).get(h) if isinstance(d, dict) else None
        if not blk:
            rows.append(
                no_data(row, "data/direction_baseline.json is missing or has no such horizon")
            )
            continue
        lg = blk.get("logistic_metrics", {})
        lb = blk.get("lightgbm_metrics", {})
        sig = blk.get("significance_vs_always_up", {})
        row["retrospective"].update(
            text=(
                f"n={blk.get('n_test_folds')} folds to {str(blk.get('as_of_date_range', ''))[-10:]}: "
                f"logistic {lg.get('accuracy', float('nan')):.3f}, lightgbm {lb.get('accuracy', float('nan')):.3f} "
                f"vs always-up {blk.get('always_up_baseline_accuracy', float('nan')):.3f}, p={sig.get('p_value', float('nan')):.3f}"
            ),
            marking="INFERRED",
        )
        pg = blk.get("probability_gate", {})
        row["gate"] = {
            "text": f"probability gate {'ship' if pg.get('ship') else 'no ship (stays dark)'}: {pg.get('reason', '')}"[
                :160
            ],
            "passes": bool(pg.get("ship")),
            "marking": "INFERRED",
        }
        row["status_note"] = "flag off; nothing shown to users"
        no_data(
            row,
            "the harness re-scores history each Monday and keeps no forward ledger"
            + ("; the forward arm is the h2 pre-registration row" if h == "h2" else ""),
        )
        rows.append(row)
    return rows


def row_chronos(data_dir: Path) -> dict[str, Any]:
    from ml.metrics import fold_mae_5d  # per-fold MAE; works for scores-only and older files

    start = FORWARD_STARTS["chronos"]["date"]
    row = base_row(
        "chronos",
        "Chronos (chronos-bolt-tiny) walk-forward",
        "DARK",
        "5-day price path",
        "chronos",
        ["data/backtest.json"],
    )
    row["forward"].update(
        metric="mean absolute error over 5 days", baseline_name="holding the last price"
    )
    row["status_note"] = "computed every cycle; only a status string is shown"
    b = _read_json("backtest.json", data_dir)
    folds = (b or {}).get("folds") if isinstance(b, dict) else None
    if not folds:
        return no_data(row, "data/backtest.json is missing or empty")

    def mean(v: list[float]) -> float:
        return sum(v) / len(v)

    fwd = [f for f in folds if f["context_end_date"] >= start]
    old = [f for f in folds if f["context_end_date"] < start]
    _loss_block(
        row,
        [fold_mae_5d(f, "chronos") for f in fwd],
        [fold_mae_5d(f, "naive") for f in fwd],
        "Rs/g",
    )
    row["forward"]["extras"].append("5-day folds overlap; effective n is the HAC figure")
    if old:
        d = [fold_mae_5d(f, "chronos") - fold_mae_5d(f, "naive") for f in old]
        ci = hac_mean_ci(d, lag=4)
        row["retrospective"].update(
            text=(
                f"n={len(old)} folds: Chronos {mean([fold_mae_5d(f, 'chronos') for f in old]):.1f} vs "
                f"hold {mean([fold_mae_5d(f, 'naive') for f in old]):.1f} Rs/g"
                + (
                    f", diff 95% CI [{ci['ci95'][0]:+.0f}, {ci['ci95'][1]:+.0f}] (lag 4)"
                    if ci["ci95"]
                    else ""
                )
            ),
            marking="VERIFIED",
        )
    row["gate"] = {
        "text": "not promoted; base-rate fallback in use",
        "passes": False,
        "marking": "INFERRED",
    }
    _finish_point(row, "Chronos")
    return row


def _weekly_rows(data_dir: Path) -> list[dict[str, Any]]:
    start = FORWARD_STARTS["weekly_range"]["date"]
    doc = _read_json("weekly_range_shadow_log.json", data_dir)
    entries = (doc or {}).get("entries") if isinstance(doc, dict) else None
    rows = []
    for hz, label in (("1d", "1-day range"), ("week", "7-day range")):
        row = base_row(
            f"weekly_range_{hz}",
            f"ml.weekly_range shadow ({label})",
            "SHADOW",
            f"{label} for the price",
            "weekly_range",
            ["data/weekly_range_shadow_log.json"],
        )
        if not entries:
            rows.append(no_data(row, "data/weekly_range_shadow_log.json is missing or empty"))
            continue
        scored = [
            e
            for e in entries
            if e.get("horizon") == hz
            and e.get("as_of", "") > start
            and isinstance(e.get("result"), dict)
            and "inside" in e["result"]
        ]
        scored.sort(key=lambda e: e["as_of"])
        _coverage_block(row, [bool(e["result"]["inside"]) for e in scored])
        if scored:
            row["forward"]["first_date"], row["forward"]["last_date"] = (
                scored[0]["as_of"],
                scored[-1]["as_of"],
            )
        row["gate"] = {
            "text": "needs about 8-10 weeks of forward days (ADR 043)",
            "passes": None,
            "marking": "INFERRED",
        }
        row["status_note"] = "read by the page only under page_v2 (off)"
        _finish_coverage(row, label)
        rows.append(row)
    return rows


def row_next_day_range(data_dir: Path) -> dict[str, Any]:
    start = FORWARD_STARTS["next_day_range"]["date"]
    row = base_row(
        "next_day_range_v2",
        "ml.next_day_range v2 shadow",
        "SHADOW",
        "next-day range (vs the displayed range)",
        "next_day_range",
        ["data/next_day_range_shadow.json"],
    )
    d = _read_json("next_day_range_shadow.json", data_dir)
    rows_ = (d or {}).get("rows") if isinstance(d, dict) else None
    if not rows_:
        return no_data(row, "data/next_day_range_shadow.json is missing or empty")
    fwd = sorted(
        (
            r
            for r in rows_
            if r.get("decision_date", "") > start and "hit_v2" in r and "hit_displayed" in r
        ),
        key=lambda r: r["decision_date"],
    )
    hits = [bool(r["hit_v2"]) for r in fwd]
    _coverage_block(row, hits, [r["width_v2"] for r in fwd if r.get("width_v2") is not None])
    f = row["forward"]
    if fwd:
        kd = sum(bool(r["hit_displayed"]) for r in fwd)
        f["extras"].append(f"displayed range (baseline) coverage {kd}/{len(fwd)}")
        a = sum(bool(r["hit_v2"]) and not r["hit_displayed"] for r in fwd)
        b = sum(bool(r["hit_displayed"]) and not r["hit_v2"] for r in fwd)
        p = mcnemar_one_sided(a, b)
        f["extras"].append(
            f"v2 vs displayed, days only one hit: {a} vs {b}"
            + ("" if p is None else f" (exact McNemar one-sided p={p:.3f})")
        )
        wd = [r["width_displayed"] for r in fwd if r.get("width_displayed")]
        wv = [r["width_v2"] for r in fwd if r.get("width_v2")]
        if wd and wv:
            f["extras"].append(
                f"v2 is {100 * (sum(wv) / sum(wd) - 1):+.0f}% wider than the displayed range"
            )
        f["first_date"], f["last_date"] = fwd[0]["decision_date"], fwd[-1]["decision_date"]
    retro = [r for r in rows_ if r.get("decision_date", "") <= start and "hit_v2" in r]
    if retro:
        k = sum(bool(r["hit_v2"]) for r in retro)
        kd = sum(bool(r["hit_displayed"]) for r in retro)
        row["retrospective"].update(
            text=f"n={len(retro)}: v2 {k / len(retro):.3f} vs displayed {kd / len(retro):.3f}; "
            "failed the width bar (ADR 047)",
            marking="VERIFIED",
        )
    row["gate"] = {
        "text": "ADR 047: coverage not below displayed AND width <= +25%",
        "passes": False,
        "marking": "INFERRED",
    }
    _finish_coverage(row, "v2 range")
    return row


def row_wait_or_buy(data_dir: Path) -> dict[str, Any]:
    start = FORWARD_STARTS["wait_or_buy"]["date"]
    row = base_row(
        "wait_or_buy",
        "ml.wait_or_buy shadow (buy now or wait)",
        "SHADOW",
        "chance the price is lower after waiting N days (N=1 scored here)",
        "wait_or_buy",
        ["data/wait_or_buy_shadow.json"],
    )
    row["forward"].update(
        metric="Brier score of the lower-price chance (N=1)", baseline_name="a flat 50% chance"
    )
    row["status_note"] = "flag off; nothing shown to users"
    d = _read_json("wait_or_buy_shadow.json", data_dir)
    entries = (d or {}).get("entries") if isinstance(d, dict) else None
    if not entries:
        return no_data(row, "data/wait_or_buy_shadow.json is missing or empty")

    def scored(n: int) -> list[dict[str, Any]]:
        out = [
            e
            for e in entries
            if e.get("n") == n
            and e.get("as_of", "") > start
            and isinstance(e.get("result"), dict)
            and "lower" in e["result"]
            and isinstance(e.get("prob"), dict)
            and "p_hat" in e["prob"]
        ]
        return sorted(out, key=lambda e: e["as_of"])

    s1 = scored(1)
    # Brier loss (p - outcome)^2; lower = better. Baseline: 0.5 for every day.
    _loss_block(
        row,
        [(e["prob"]["p_hat"] - float(e["result"]["lower"])) ** 2 for e in s1],
        [(0.5 - float(e["result"]["lower"])) ** 2 for e in s1],
        "Brier",
    )
    for n in (2, 7):
        row["forward"]["extras"].append(f"N={n}: {len(scored(n))} scored so far")
    if s1:
        row["forward"]["first_date"], row["forward"]["last_date"] = s1[0]["as_of"], s1[-1]["as_of"]
    row["gate"] = {
        "text": "needs about 8 weeks forward (ADR 049); flag off",
        "passes": None,
        "marking": "INFERRED",
    }
    _finish_point(row, "the chance estimate")
    return row


def row_nowcast(data_dir: Path) -> dict[str, Any]:
    start = FORWARD_STARTS["nowcast"]["date"]
    row = base_row(
        "nowcast_m3",
        "nowcast shadow: morning-rate estimate (M3)",
        "SHADOW",
        "same-day Tanishq 22K from IBJA AM+PM",
        "nowcast",
        ["data/nowcast_shadow_log.json"],
    )
    row["forward"].update(
        metric="mean absolute error of today's rate", baseline_name="the live estimate (M0)"
    )
    d = _read_json("nowcast_shadow_log.json", data_dir)
    days = (d or {}).get("days") if isinstance(d, dict) else None
    if not days:
        return no_data(row, "data/nowcast_shadow_log.json is missing or empty")
    ok = [
        x
        for x in days
        if x.get("date", "") > start
        and x.get("truth_rs_per_g") is not None
        and x.get("M0_current") is not None
        and x.get("M3_am_pm") is not None
        and not x.get("inputs_known_after_target")
    ]
    ok.sort(key=lambda x: x["date"])
    _loss_block(
        row,
        [abs(x["M3_am_pm"] - x["truth_rs_per_g"]) for x in ok],
        [abs(x["M0_current"] - x["truth_rs_per_g"]) for x in ok],
        "Rs/g",
    )
    excl = sum(1 for x in days if x.get("date", "") > start and x.get("inputs_known_after_target"))
    row["forward"]["extras"].append(
        f"{excl} day(s) excluded: an input was published after the target (ADR 063)"
    )
    if ok:
        row["forward"]["first_date"], row["forward"]["last_date"] = ok[0]["date"], ok[-1]["date"]
    row["gate"] = {
        "text": f"shadow window ends {d.get('window_ends')} (ADR 061/063)",
        "passes": None,
        "marking": "INFERRED",
    }
    _finish_point(row, "the morning-rate estimate")
    return row


def row_h2(data_dir: Path) -> dict[str, Any]:
    row = base_row(
        "direction_h2_prereg",
        "h2 pre-registration shadow (live h2 arm)",
        "SHADOW",
        "up/down after 2 IBJA readings (ADR 038/042)",
        "h2",
        ["data/preregistered_h2_shadow_results.json"],
    )
    row["forward"].update(
        metric="accuracy", baseline_name="always predict up", unit="share correct"
    )
    d = _read_json("preregistered_h2_shadow_results.json", data_dir)
    runs = (d or {}).get("runs") if isinstance(d, dict) else None
    live = [r for r in (runs or []) if r.get("arm") == "live_h2"]
    if not live:
        return no_data(row, "data/preregistered_h2_shadow_results.json has no live_h2 run")
    r = sorted(live, key=lambda r: r.get("scored_at_utc", ""))[-1]
    need = d.get("preregistered_n_for_power")
    n = int(r.get("n") or 0)
    f = row["forward"]
    f.update(
        n=n,
        effective_n=r.get("effective_n"),
        model=r.get("accuracy"),
        baseline=r.get("always_up_accuracy"),
        p_one_sided=r.get("p_value"),
        p_kind="as reported by the job (HAC DM)",
        marking="INFERRED",
    )
    f["extras"].append(
        f"pre-registered n for power: {need}; reached: {bool(r.get('reached_preregistered_n'))}"
    )
    proxy = [x for x in runs if x.get("arm") != "live_h2"]
    if proxy:
        p = sorted(proxy, key=lambda x: x.get("scored_at_utc", ""))[-1]
        row["retrospective"].update(
            text=f"proxy dead-zone arm, n={p.get('n')}: accuracy {p.get('accuracy')} vs always-up {p.get('always_up_accuracy')}",
            marking="INFERRED",
        )
    row["gate"] = {
        "text": "needs the pre-registered n before any claim",
        "passes": None,
        "marking": "INFERRED",
    }
    if n == 0:
        no_data(row, f"no live h2 day scored yet (need n={need})")
    elif n < MIN_FORWARD_N:
        row["verdict"], row["verdict_kind"] = "grey", "too_early"
        row["sentence"] = f"Too early (n={n}, need {MIN_FORWARD_N}; pre-registered target {need})."
    else:
        row["verdict_kind"] = "scored"
        sig = bool(r.get("significant_at_05")) and (f["model"] or 0) > (f["baseline"] or 1)
        row["verdict"] = "green" if sig else "amber"
        row["sentence"] = (
            "Beats always-up reliably." if sig else "No reliable edge over always-up yet."
        )
    return row


def row_fusion(data_dir: Path) -> dict[str, Any]:
    row = base_row(
        "shadow_fusion",
        "ml.shadow_fusion national benchmark",
        "SHADOW",
        "same-day Tanishq 22K from the national retail average",
        "fusion",
        ["data/fusion_snapshots.parquet", "data/prices.json"],
    )
    row["forward"].update(
        metric="mean absolute gap to Tanishq (same day)", baseline_name="yesterday's Tanishq rate"
    )
    try:
        import pandas as pd

        sys.path.insert(0, str(ROOT))
        from ml.fusion import DEFAULT_WEIGHTS

        snaps = pd.read_parquet(data_dir / "fusion_snapshots.parquet")
        prices = _read_json("prices.json", data_dir)
        if snaps.empty or not prices:
            raise ValueError("empty input")
    except Exception as exc:  # report, never fake a pass
        return no_data(row, f"inputs unavailable ({type(exc).__name__})")
    nat = snaps[snaps["city"].isna() & snaps["source"].isin(list(DEFAULT_WEIGHTS))].copy()
    if nat.empty:
        return no_data(row, "no national snapshots persisted")
    first = str(nat["as_of_date"].min())
    row["forward_start"] = first
    nat = nat.sort_values("capture_utc").groupby(["as_of_date", "source"]).last().reset_index()
    nat["w"] = nat["source"].map(DEFAULT_WEIGHTS)
    bench = (nat["rate_22k"] * nat["w"]).groupby(nat["as_of_date"]).sum() / nat["w"].groupby(
        nat["as_of_date"]
    ).sum()
    t22: dict[str, float] = {}
    for p in sorted((p for p in prices if p.get("22k") is not None), key=lambda p: p["timestamp"]):
        t22[p["timestamp"][:10]] = float(p["22k"])
    days = sorted(t22)
    e_model, e_base, dates = [], [], []
    for d in sorted(bench.index):
        d = str(d)
        prev = [x for x in days if x < d]
        if d in t22 and prev and d >= first:
            e_model.append(abs(float(bench[d]) - t22[d]))
            e_base.append(abs(t22[prev[-1]] - t22[d]))
            dates.append(d)
    _loss_block(row, e_model, e_base, "Rs/g")
    if dates:
        row["forward"]["first_date"], row["forward"]["last_date"] = dates[0], dates[-1]
    row["forward"]["extras"].append(
        "benchmark is the last national snapshot of the day; Tanishq is the day's last reading"
    )
    row["gate"] = {
        "text": "Phase D promotion pending a go from GG (ADR 026)",
        "passes": False,
        "marking": "INFERRED",
    }
    _finish_point(row, "the national benchmark")
    return row


# --- assemble --------------------------------------------------------------------------------------


def _git(args: list[str]) -> str:
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, check=False, cwd=ROOT)
        return r.stdout.strip()
    except OSError:
        return ""


def build_rows(data_dir: Path = DATA) -> list[dict[str, Any]]:
    producers = [
        lambda: row_nextfix(data_dir),
        lambda: _variant_rows(data_dir),
        lambda: [row_demotion_monitor(data_dir)],
        lambda: _hold_rows(data_dir),
        lambda: _intraday_rows(data_dir),
        lambda: [row_calibration_band(data_dir)],
        lambda: _direction_rows(data_dir),
        lambda: [row_h2(data_dir)],
        lambda: [row_chronos(data_dir)],
        lambda: _weekly_rows(data_dir),
        lambda: [row_next_day_range(data_dir)],
        lambda: [row_wait_or_buy(data_dir)],
        lambda: [row_nowcast(data_dir)],
        lambda: [row_fusion(data_dir)],
    ]
    rows: list[dict[str, Any]] = []
    for fn in producers:
        try:
            rows.extend(fn())
        except Exception as exc:  # one broken model must never hide the others
            rows.append(
                {
                    **base_row("error", "scorecard error", "SHADOW", "n/a", "chronos", []),
                    "sentence": f"No forward data: builder error {type(exc).__name__}: {exc}",
                }
            )
    for r in rows:
        r["demotion"] = demotion_status(r["id"], data_dir)
    # Benjamini-Hochberg over every model-vs-baseline test that reached MIN_FORWARD_N. The ADR 071
    # variants are excluded: their frozen rule has its own alpha (0.025) and its own minimum n.
    fam = [
        r
        for r in rows
        if r["forward"]["n"] >= MIN_FORWARD_N
        and r["verdict_kind"] != "monitor"
        and not r["id"].startswith("p3_")
        and r["forward"]["p_one_sided"] is not None
        and r["forward"]["p_kind"]
        and "HAC" in r["forward"]["p_kind"]
    ]
    for r, flag in zip(fam, bh_flags([r["forward"]["p_one_sided"] for r in fam]), strict=True):
        r["forward"]["bh_significant"] = flag
    # verdicts depend on the BH flag, so finish them now for the rows that were waiting on it
    for r in fam:
        f = r["forward"]
        if r["verdict_kind"] == "scored":
            ci = f["ci95"]
            tag = r["name"]
            if ci and ci[1] < 0 and f["p_one_sided"] < ALPHA and f["bh_significant"]:
                r["verdict"], r["sentence"] = (
                    "green",
                    f"{tag} is reliably more accurate than {f['baseline_name']}.",
                )
            elif ci and ci[0] > 0:
                r["verdict"], r["sentence"] = (
                    "red",
                    f"{tag} is reliably less accurate than {f['baseline_name']}.",
                )
            else:
                r["verdict"] = "amber"
                r["sentence"] = (
                    f"No reliable difference from {f['baseline_name']} yet; the evidence is mixed."
                )
    return rows


def build(data_dir: Path = DATA, now: datetime | None = None) -> dict[str, Any]:
    rows = build_rows(data_dir)
    now = now or datetime.now(UTC)
    return {
        "schema_version": 1,
        "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": SCRIPT_REL,
        "git_sha": _git(["rev-parse", "HEAD"]),
        "script_blob_sha": _git(["hash-object", SCRIPT_REL]),
        "min_forward_n": MIN_FORWARD_N,
        "nominal_coverage": NOMINAL,
        "alpha": ALPHA,
        "forward_starts": FORWARD_STARTS,
        "models": rows,
    }


# --- render (stdlib only; a pure function of the JSON) -------------------------------------------


def _cell_forward(r: dict[str, Any]) -> str:
    f = r["forward"]
    start = r["forward_start"]
    head = f"since {start}: n={f['n']}"
    if f["effective_n"] is not None:
        head += f", eff. n={f['effective_n']}"
    if r["verdict_kind"] == "monitor":
        return "state check (I): not a forward score"
    if r["verdict_kind"] in ("no_data", "too_early") or f["model"] is None:
        return head + (" (V)" if f["marking"] == "VERIFIED" else " (I)")
    tag = "V" if f["marking"] == "VERIFIED" else "I"
    nd = 3 if f["unit"] in ("share of days inside", "share correct", "Brier") else 1
    line = f"{f['model']:.{nd}f} vs {f['baseline']:.{nd}f} {f['baseline_name']}"
    parts = [head, f"{line} ({tag})"]
    if f["ci95"] is not None:
        what = (
            "diff"
            if f["diff"] is not None and f["unit"] not in ("share of days inside",)
            else "coverage"
        )
        if what == "diff":
            parts.append(
                f"diff {f['diff']:+.{nd}f}, 95% [{f['ci95'][0]:+.{nd}f}, {f['ci95'][1]:+.{nd}f}]"
            )
        else:
            parts.append(f"95% [{f['ci95'][0]:.3f}, {f['ci95'][1]:.3f}]")
    if f["p_one_sided"] is not None:
        parts.append(f"one-sided p={f['p_one_sided']:.3f}")
    return "<br>".join(parts)


def render(doc: dict[str, Any]) -> str:
    rows = doc["models"]
    lines = [
        "# Model scorecard",
        "",
        "<!-- GENERATED by scripts/build_model_scorecard.py from data/model_scorecard_weekly.json; "
        "do not edit by hand -->",
        "",
        f"Last computed {doc['generated_at_utc']} from script `{doc['script']}` "
        f"(blob `{doc['script_blob_sha'][:10] or 'n/a'}`, repo commit `{doc['git_sha'][:10] or 'n/a'}`). "
        "Refreshed weekly.",
        "",
        f"Every model, scored only on predictions made AFTER its start date. With fewer than "
        f'{doc["min_forward_n"]} forward results a row says "too early" instead of a verdict. '
        "Numbers from earlier history sit in their own column and are never mixed into the forward ones. "
        "(V) = computed in this run from the raw records; (I) = copied from the producing job, "
        "not re-measured here.",
        "",
        "| Model | Status | Predicts | Forward score (since the start date) | Gate | Light | In plain words | "
        "Earlier history only (NOT forward) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        status = r["status"] + (f" ({r['status_note']})" if r["status_note"] else "")
        gate = r["gate"]["text"] + (" (I)" if r["gate"]["marking"] == "INFERRED" else " (V)")
        if r.get("demotion"):
            gate += f"<br>demotion: {r['demotion'].get('state')}"
        extras = "".join(f"<br>{x}" for x in r["forward"]["extras"])
        retro = r["retrospective"]["text"] + (
            " (V)" if r["retrospective"]["marking"] == "VERIFIED" else " (I)"
        )
        lines.append(
            f"| **{r['name']}** | {status} | {r['predicts']} | {_cell_forward(r)}{extras} | {gate} | "
            f"{LIGHTS[r['verdict']]} | {r['sentence']} | {retro} |"
        )
    lines += [
        "",
        "## Start dates",
        "",
        "| Model | Forward start | Basis |",
        "|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['name']} | {r['forward_start']} | {r['forward_start_basis']} |")
    lines += [
        "",
        "## Why you can trust this",
        "",
        "- Computed, never typed: every number above is produced by the script from the committed "
        "`data/*.json` records and carries its source file(s); the machine copy is "
        "`data/model_scorecard_weekly.json`.",
        "- Forward only: a model is judged on forecasts made after its start date, so tuning on "
        "history cannot flatter it. Start dates and their basis are listed above.",
        "- A baseline on the same days for every comparison (hold the last fix, a flat 50% chance, the "
        "live estimate, the 80% a range promises).",
        "- Honest about size: the one-sided p-values use a HAC (autocorrelation-robust) variance and "
        "the table shows the effective n; the green light also requires surviving a "
        "Benjamini-Hochberg correction across all comparisons that have enough data.",
        "",
        "## Limits",
        "",
        f"- Small samples: below {doc['min_forward_n']} forward results nothing is concluded. At 20 "
        "results only large gaps are detectable.",
        "- Coverage rows treat days as independent in the binomial p-value (approximate); the effective "
        "n shows how much that overstates.",
        "- The forward start for models without a promotion date (Chronos, ml.direction, calibration band) "
        "is this scorecard's first run, not the model's birth.",
        "- Traffic light: GREEN = reliably better / on target; AMBER = no reliable difference or loose "
        "evidence; RED = reliably worse; GREY = too early or no forward data. It is a summary of "
        "evidence, not advice to trade.",
        "- (I) numbers are copied from the job that produced them and inherit its errors.",
        "- Demotion (ADR 068): the rules run in the check-price job and write "
        "`data/model_demotion_state.json`; this page only reads that file and the live readout in "
        "`forecast.json`. A missing or unreadable state file shows as no data, never as "
        '"not demoted". `demotion_status()` in the script is the one place that reads it.',
        "- Past tests are never live calls: days a record flags as re-runs on past data (the P3 "
        "record's `retro` days, and any day before a row's start date) sit in the earlier-history "
        "column, labelled as re-runs. Only days from a row's start date on were issued live.",
        f"- The two P3 variants (ADR 071) get no verdict before {VARIANT_MIN_FORWARD_N} forward "
        "days; the rule was fixed before any forward data existed.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--render-only", action="store_true", help="markdown from the committed JSON")
    ap.add_argument(
        "--json-only",
        action="store_true",
        help="write only the JSON (the weekly job; docs-refresh renders the markdown)",
    )
    ap.add_argument("--out-json", type=Path, default=OUT_JSON)
    ap.add_argument("--out-md", type=Path, default=OUT_MD)
    ap.add_argument("--data-dir", type=Path, default=DATA)
    args = ap.parse_args()
    if args.render_only:
        try:
            doc = json.loads(args.out_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"FAIL: cannot read {args.out_json}: {exc}")
            return 1
    else:
        doc = build(args.data_dir)
        args.out_json.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8", newline="\n")
    n = len(doc["models"])
    if args.json_only:
        # The weekly job commits through bot-pr-sync, which rebases: a modified file it did not
        # stage (the markdown) makes that rebase fail ("You have unstaged changes").
        print(f"OK: {n} model rows -> {args.out_json.name}")
        return 0
    args.out_md.write_text(render(doc), encoding="utf-8", newline="\n")
    print(f"OK: {n} model rows -> {args.out_json.name}, {args.out_md.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
