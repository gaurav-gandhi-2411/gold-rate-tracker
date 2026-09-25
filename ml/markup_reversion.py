"""ml/markup_reversion.py -- ADR 057: does an unusually high Tanishq markup revert, and is that
worth anything to a buyer who waits?

Builds on F1's markup definition (ml/markup.py, PR #2022 -- copied onto this branch byte-identical,
so the two PRs merge cleanly in either order):

    markup_pct(d) = (tanishq_22k(d) / ibja_916_in_force(d) - 1) * 100
    markup_rs(d)  =  tanishq_22k(d) - ibja_916_in_force(d)            (Rs/gram)

where tanishq_22k(d) is the LAST Tanishq reading of IST calendar day d and "in force" is F1's
IST wall-clock rule (reading >= 17:00 IST -> that day's PM fix; 12:00-17:00 -> that day's AM fix;
< 12:00 -> the previous day's PM fix; a missing wanted fix falls BACK to the most recent published
fix and is flagged stale).

Timestamp conventions (every series used here):
  * data/prices.json ``timestamp``: UTC ISO-8601 of the scrape moment (converted to IST for the
    calendar day). 25 rows tagged "(history backfill)" (2026-04-14..2026-05-08) carry a SYNTHETIC
    06:30 UTC (= 12:00 IST) stamp -- scraper/backfill-history.js -- not a real observation time.
  * data/ibja_rates.parquet ``date``: the IST publish date (value date == publish date, weekdays
    only); am_916/pm_916 are Rs/10g for the ~12:00 IST and ~17:00 IST fixes. No per-fix publish
    timestamp exists; ``fetched_at`` is our scraper's write time, not IBJA's publish time.

Three row flags drive the step-1 diagnostic (see ADR 057):
  * ``stale_ibja``      -- F1's flag: the IBJA fix paired with the reading is not the one wanted
                           on that IST date (weekend/holiday/not-yet-published carry-forward).
  * ``tanishq_repeat``  -- Tanishq's daily value is identical to the previous calendar day's value
                           (a possible stale page / no-update day on the retailer side).
  * ``backfill``        -- the reading's timestamp is synthetic (history backfill).

Walk-forward only: the signal on day d uses rows strictly before d for its reference level
(``robust_trailing_z``); outcomes look forward N IBJA business days.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import norm

from ml.markup import (
    PRICES_JSON,
    build_daily_markup_series,
    load_tanishq_readings,
)
from ml.range_forecast.metrics import wilson_ci

# --- Pre-registered constants (ADR 057 section 3; frozen -- do not tune) -----------------------
TRAILING_WINDOW = 60  # rows of the eligible series used for the reference level
MIN_TRAILING = 20  # fewer strictly-earlier rows -> z undefined (no signal)
MAD_TO_SD = 1.4826  # consistency constant: MAD * 1.4826 estimates sd under normality
Z_THRESHOLDS: tuple[float, ...] = (1.0, 1.5)  # 2.0 fired on 1 of 72 days (step 1)
HORIZONS: tuple[int, ...] = (1, 3)  # IBJA business days
FORWARD_PRIMARY = (1.0, 3)  # (z threshold, N) -- the single confirmatory forward cell
ALPHA = 0.05
MIN_SIGNAL_N_HIST = 15
MIN_SIGNAL_EFF_N_HIST = 8.0
MIN_MEAN_SAVING_RS = 20.0  # practical-significance floor, Rs/g, net of market


def hac_lag(horizon: int) -> int:
    """Newey-West truncation lag: N-1 for the overlapping outcome, +3 for the persistence of the
    signal itself (markup half-life ~2.5 days per F1) -> N + 2 (always >= N - 1)."""
    return horizon + 2


BACKFILL_TAG = "(history backfill)"


# ---------------------------------------------------------------------------
# Series construction
# ---------------------------------------------------------------------------


def backfill_timestamps(path: Path = PRICES_JSON) -> set[str]:
    """ISO timestamps (as written in prices.json) of rows tagged as history backfill."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(r["timestamp"]) for r in raw if BACKFILL_TAG in str(r.get("source", ""))}


def _iso_z(ts: pd.Timestamp | Any) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_pair_frame(
    readings: list[tuple[Any, float]],
    ibja_df: pd.DataFrame,
    backfill_ts: set[str] | None = None,
) -> pd.DataFrame:
    """One row per IST day (F1's daily series) with the flags described in the module docstring.

    Columns: date, tanishq, ibja, ibja_fix_date, ibja_fix_type, stale_ibja, tanishq_repeat,
    backfill, weekend, markup_pct, markup_rs.
    """
    daily = build_daily_markup_series(readings, ibja_df)
    backfill_ts = backfill_ts or set()
    rows = []
    for r in daily:
        rows.append(
            {
                "date": r.ist_date,
                "tanishq": r.retailer_rate_per_gram,
                "ibja": r.ibja_rate_per_gram,
                "ibja_fix_date": r.ibja_fix_date,
                "ibja_fix_type": r.ibja_fix_type,
                "stale_ibja": bool(r.stale_ibja),
                "backfill": _iso_z(r.reading_at_utc) in backfill_ts,
                "weekend": r.ist_date.weekday() >= 5,
                "markup_pct": r.markup_pct,
                "markup_rs": r.retailer_rate_per_gram - r.ibja_rate_per_gram,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    by_date = dict(zip(df["date"], df["tanishq"], strict=True))
    df["tanishq_repeat"] = [
        (d - timedelta(days=1)) in by_date and by_date[d - timedelta(days=1)] == t
        for d, t in zip(df["date"], df["tanishq"], strict=True)
    ]
    return df.reset_index(drop=True)


def ibja_business_days(ibja_df: pd.DataFrame) -> list[date]:
    """IST dates on which IBJA published at least one 916 fix (the business-day calendar)."""
    has_fix = ibja_df["am_916"].notna() | ibja_df["pm_916"].notna()
    return sorted(date.fromisoformat(str(d)[:10]) for d in ibja_df.loc[has_fix, "date"])


def load_default_frame() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(pair frame, ibja_df) from the committed repo data files -- no network."""
    from ml.ibja import load_ibja_parquet

    ibja_df = load_ibja_parquet()
    frame = build_pair_frame(load_tanishq_readings(), ibja_df, backfill_timestamps())
    return frame, ibja_df


VARIANTS: dict[str, str] = {
    "a_all_pairs_f1": "every daily row, adjacent rows (F1's construction)",
    "b_same_day_fresh": "rows whose IBJA fix is the one wanted that IST day (stale_ibja False)",
    "c_no_carry_forward": "b minus Tanishq repeat days and synthetic-timestamp backfill rows",
}


def select_variant(frame: pd.DataFrame, variant: str) -> pd.DataFrame:
    if variant == "a_all_pairs_f1":
        return frame
    if variant == "b_same_day_fresh":
        return frame[~frame["stale_ibja"]]
    if variant == "c_no_carry_forward":
        return frame[~frame["stale_ibja"] & ~frame["tanishq_repeat"] & ~frame["backfill"]]
    raise ValueError(f"unknown variant {variant!r}")


# ---------------------------------------------------------------------------
# Step 1 -- persistence diagnostic
# ---------------------------------------------------------------------------


def _pearson(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def ar1_stats(x_prev: np.ndarray, x_next: np.ndarray, seed: int = 42) -> dict[str, Any]:
    """Lag-1 correlation over explicit (prev, next) pairs, with a Fisher-z 95% CI, a moving-block
    bootstrap 95% CI (block 5 pairs, 2000 draws, seed 42), a Spearman lag-1 (outlier-robust
    check), the implied half-life in steps, and the AR(1) effective n for a mean,
    n * (1 - r) / (1 + r)."""
    n = len(x_prev)
    r = _pearson(x_prev, x_next)
    out: dict[str, Any] = {"n_pairs": n, "ar1": r}
    if r is None or n < 10:
        out["ar1"] = None
        return out
    zr = math.atanh(max(min(r, 0.999999), -0.999999))
    se = 1 / math.sqrt(n - 3)
    out["fisher_ci95"] = [math.tanh(zr - 1.959964 * se), math.tanh(zr + 1.959964 * se)]
    rng = np.random.default_rng(seed)
    block = 5
    starts_max = max(n - block, 0)
    boots = []
    for _ in range(2000):
        starts = rng.integers(0, starts_max + 1, size=math.ceil(n / block))
        idx = np.concatenate([np.arange(s, min(s + block, n)) for s in starts])[:n]
        rb = _pearson(x_prev[idx], x_next[idx])
        if rb is not None:
            boots.append(rb)
    out["block_bootstrap_ci95"] = [
        float(np.percentile(boots, 2.5)),
        float(np.percentile(boots, 97.5)),
    ]
    rank_a = pd.Series(x_prev).rank().to_numpy()
    rank_b = pd.Series(x_next).rank().to_numpy()
    out["spearman_lag1"] = _pearson(rank_a, rank_b)
    out["half_life_steps"] = math.log(0.5) / math.log(r) if 0 < r < 1 else None
    out["effective_n_for_mean"] = n * (1 - r) / (1 + r) if r > -1 else None
    return out


def lag1_pairs(
    sub: pd.DataFrame, column: str, max_gap_days: int | None = None
) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Adjacent-row pairs in ``sub`` (sorted by date). ``max_gap_days`` keeps only pairs whose
    calendar gap is <= that many days. Returns (prev, next, gaps)."""
    s = sub.sort_values("date")
    vals = s[column].to_numpy(dtype=float)
    dates = list(s["date"])
    prev, nxt, gaps = [], [], []
    for i in range(1, len(vals)):
        gap = (dates[i] - dates[i - 1]).days
        if max_gap_days is not None and gap > max_gap_days:
            continue
        prev.append(vals[i - 1])
        nxt.append(vals[i])
        gaps.append(gap)
    return np.asarray(prev), np.asarray(nxt), gaps


def next_business_day_pairs(
    sub: pd.DataFrame, column: str, bdays: list[date]
) -> tuple[np.ndarray, np.ndarray]:
    """Pairs (d, next IBJA business day after d), both present in ``sub``."""
    s = sub.set_index("date")[column]
    pos: dict[Any, int] = {d: i for i, d in enumerate(bdays)}
    prev, nxt = [], []
    for d, v in s.items():
        i = pos.get(d)
        if i is None or i + 1 >= len(bdays):
            continue
        d2 = bdays[i + 1]
        if d2 in s.index:
            prev.append(float(v))
            nxt.append(float(s[d2]))
    return np.asarray(prev), np.asarray(nxt)


def persistence_diagnostic(frame: pd.DataFrame, bdays: list[date]) -> dict[str, Any]:
    """Step-1 table: AR(1) of markup_pct (and markup_rs) per variant and pairing rule."""
    out: dict[str, Any] = {}
    for variant, desc in VARIANTS.items():
        sub = select_variant(frame, variant)
        entry: dict[str, Any] = {
            "definition": desc,
            "n_rows": len(sub),
            "n_weekend_rows": int(sub["weekend"].sum()),
        }
        for column in ("markup_pct", "markup_rs"):
            a, b, gaps = lag1_pairs(sub, column)
            entry[f"{column}__adjacent_rows"] = {
                **ar1_stats(a, b),
                "pair_gap_days_counts": pd.Series(gaps).value_counts().sort_index().to_dict(),
            }
            a1, b1, _ = lag1_pairs(sub, column, max_gap_days=1)
            entry[f"{column}__one_calendar_day"] = ar1_stats(a1, b1)
            a2, b2 = next_business_day_pairs(sub, column, bdays)
            entry[f"{column}__next_ibja_business_day"] = ar1_stats(a2, b2)
        out[variant] = entry
    return out


# ---------------------------------------------------------------------------
# Step 2 -- walk-forward signal, outcomes, tests
# ---------------------------------------------------------------------------


def robust_trailing_z(
    values: np.ndarray, window: int = TRAILING_WINDOW, min_obs: int = MIN_TRAILING
) -> np.ndarray:
    """z[i] = (x[i] - median(prev)) / (1.4826 * MAD(prev)), prev = up to ``window`` values
    STRICTLY before i. NaN when fewer than ``min_obs`` earlier values or MAD == 0."""
    x = np.asarray(values, dtype=float)
    z = np.full(len(x), np.nan)
    for i in range(len(x)):
        prev = x[max(0, i - window) : i]
        if len(prev) < min_obs:
            continue
        med = float(np.median(prev))
        mad = float(np.median(np.abs(prev - med))) * MAD_TO_SD
        if mad > 0:
            z[i] = (x[i] - med) / mad
    return z


def forward_frame(series: pd.DataFrame, bdays: list[date], horizon: int) -> pd.DataFrame:
    """Add the N-business-day-ahead outcome to an eligible series (sorted by date, with a ``z``
    column already computed). Target = the N-th IBJA business day after d; outcome is NaN unless
    the target day is itself in ``series``.

    y            = markup_rs(target) - markup_rs(d)       (Rs/g; < 0 = retail fell vs market)
    gross_saving = tanishq(d) - tanishq(target)           (Rs/g a waiting buyer saves; < 0 = pays
                                                           more)
    market_move  = ibja(target) - ibja(d)
    """
    s = series.sort_values("date").reset_index(drop=True).copy()
    by_date = s.set_index("date")
    pos = {d: i for i, d in enumerate(bdays)}
    y: list[float] = []
    gross: list[float] = []
    mkt: list[float] = []
    tgt: list[date | None] = []
    for d in s["date"]:
        i = pos.get(d)
        target = bdays[i + horizon] if i is not None and i + horizon < len(bdays) else None
        if target is None or target not in by_date.index:
            y.append(np.nan)
            gross.append(np.nan)
            mkt.append(np.nan)
            tgt.append(None)
            continue
        t0, t1 = by_date.loc[d], by_date.loc[target]
        y.append(float(t1["markup_rs"] - t0["markup_rs"]))
        gross.append(float(t0["tanishq"] - t1["tanishq"]))
        mkt.append(float(t1["ibja"] - t0["ibja"]))
        tgt.append(target)
    s["target_date"] = tgt
    s["y"] = y
    s["gross_saving"] = gross
    s["market_move"] = mkt
    return s


def hac_ols(y: np.ndarray, x: np.ndarray, lag: int) -> dict[str, float]:
    """OLS y = a + b*x with Newey-West (Bartlett) SEs. Returns a, b, se_b (HAC), se_b_ols.

    Not reused from scripts/analysis_dow_prereg.hac_wald because that returns only a two-sided
    chi-square p for a joint restriction; the one-sided test on a single slope needs the SE."""
    y = np.asarray(y, dtype=float)
    X = np.column_stack([np.ones(len(y)), np.asarray(x, dtype=float)])
    n, k = X.shape
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    u = y - X @ beta
    xu = X * u[:, None]
    s = xu.T @ xu
    for j in range(1, min(lag, n - 1) + 1):
        g = xu[j:].T @ xu[:-j]
        s += (1 - j / (lag + 1)) * (g + g.T)
    cov = xtx_inv @ s @ xtx_inv * n / (n - k)
    cov_ols = xtx_inv * float(u @ u) / (n - k)
    return {
        "a": float(beta[0]),
        "b": float(beta[1]),
        "se_b": float(math.sqrt(max(cov[1, 1], 0.0))),
        "se_b_ols": float(math.sqrt(max(cov_ols[1, 1], 0.0))),
    }


def hac_mean(x: np.ndarray, lag: int) -> dict[str, Any]:
    """Mean with a Bartlett long-run-variance SE, 95% CI and effective n = n*gamma0/LRV."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 2:
        return {"n": n, "mean": float(x.mean()) if n else None}
    m = float(x.mean())
    d = x - m
    g0 = float(d @ d / n)
    lrv = g0
    for j in range(1, min(lag, n - 1) + 1):
        lrv += 2 * (1 - j / (lag + 1)) * float(d[j:] @ d[:-j] / n)
    lrv = max(lrv, 1e-12)
    se = math.sqrt(lrv / n)
    return {
        "n": n,
        "mean": m,
        "hac_se": se,
        "ci95": [m - 1.959964 * se, m + 1.959964 * se],
        "effective_n": n * g0 / lrv,
    }


def evaluate_cell(ff: pd.DataFrame, z_thr: float, horizon: int) -> dict[str, Any]:
    """One (threshold, horizon) cell on a forward frame with columns z, y, gross_saving."""
    ok = ff[ff["z"].notna() & ff["y"].notna()].sort_values("date")
    lag = hac_lag(horizon)
    signal = (ok["z"] >= z_thr).to_numpy()
    n, n_sig = len(ok), int(signal.sum())
    res: dict[str, Any] = {
        "z_threshold": z_thr,
        "horizon_business_days": horizon,
        "hac_lag": lag,
        "n_eligible_days": n,
        "n_signal_days": n_sig,
        "first_decision_date": ok["date"].min().isoformat() if n else None,
        "last_decision_date": ok["date"].max().isoformat() if n else None,
    }
    if n < 5:
        res["verdict"] = "INCONCLUSIVE"
        return res
    y = ok["y"].to_numpy(dtype=float)
    g = ok["gross_saving"].to_numpy(dtype=float)
    res["baseline_unconditional_y"] = hac_mean(y, lag)
    res["baseline_unconditional_gross_saving"] = hac_mean(g, lag)
    if n_sig < 2 or n_sig == n:
        res["verdict"] = "INCONCLUSIVE"
        return res
    reg = hac_ols(y, signal.astype(float), lag)
    t = reg["b"] / reg["se_b"] if reg["se_b"] > 0 else float("nan")
    res["signal_minus_nonsignal_y"] = {
        **reg,
        "ci95": [reg["b"] - 1.959964 * reg["se_b"], reg["b"] + 1.959964 * reg["se_b"]],
        "t_hac": t,
        "p_one_sided": float(norm.cdf(t)),  # H1: b < 0
    }
    res["signal_effective_n"] = (
        n_sig * (reg["se_b_ols"] / reg["se_b"]) ** 2 if reg["se_b"] else None
    )
    sig_y = y[signal]
    sig_g = g[signal]
    res["signal_days_y"] = hac_mean(sig_y, lag)
    res["signal_days_saving_net_of_market"] = hac_mean(-sig_y, lag)
    res["signal_days_gross_saving"] = hac_mean(sig_g, lag)
    reg_g = hac_ols(g, signal.astype(float), lag)
    res["excess_gross_saving_vs_nonsignal"] = {
        "b": reg_g["b"],
        "ci95": [reg_g["b"] - 1.959964 * reg_g["se_b"], reg_g["b"] + 1.959964 * reg_g["se_b"]],
    }
    pay_more = int((sig_g < 0).sum())
    res["p_pay_more_gross"] = {
        "k": pay_more,
        "n": n_sig,
        "rate": pay_more / n_sig,
        "wilson_ci95": list(wilson_ci(pay_more, n_sig)),
    }
    worse_vs_mkt = int((sig_y > 0).sum())
    res["p_pay_more_net_of_market"] = {
        "k": worse_vs_mkt,
        "n": n_sig,
        "rate": worse_vs_mkt / n_sig,
        "wilson_ci95": list(wilson_ci(worse_vs_mkt, n_sig)),
    }
    # Decomposition of the reversion: which side moved (tanishq fall vs ibja rise).
    res["signal_days_decomposition"] = {
        "mean_tanishq_change": float(-sig_g.mean()),
        "mean_ibja_change": float(ok["market_move"].to_numpy(dtype=float)[signal].mean()),
    }
    return res


def apply_gate(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ADR 057 section 3.6 historical gate: Bonferroni AND BH across the pre-registered family,
    plus practical and sample-size floors. Mutates and returns ``cells``."""
    from ml.direction.stats_corrections import benjamini_hochberg, bonferroni

    ps = [c.get("signal_minus_nonsignal_y", {}).get("p_one_sided") for c in cells]
    ps_clean = [p if p is not None else 1.0 for p in ps]
    bonf = bonferroni(ps_clean, ALPHA)["significant"]
    bh = benjamini_hochberg(ps_clean, ALPHA)["significant"]
    for c, b_ok, bh_ok in zip(cells, bonf, bh, strict=True):
        c["bonferroni_significant"] = bool(b_ok)
        c["bh_significant"] = bool(bh_ok)
        n_sig = c.get("n_signal_days", 0)
        eff = c.get("signal_effective_n") or 0.0
        saving = c.get("signal_days_saving_net_of_market", {}).get("mean")
        if n_sig < MIN_SIGNAL_N_HIST or eff < MIN_SIGNAL_EFF_N_HIST or saving is None:
            c["verdict"] = "INCONCLUSIVE"
        elif b_ok and bh_ok and saving >= MIN_MEAN_SAVING_RS:
            c["verdict"] = "PASS"
        else:
            c["verdict"] = "NEGATIVE"
    return cells


def build_eligible_series(frame: pd.DataFrame, variant: str = "b_same_day_fresh") -> pd.DataFrame:
    """The pre-registered primary series (variant b) with the walk-forward z attached."""
    s = select_variant(frame, variant).sort_values("date").reset_index(drop=True).copy()
    s["z"] = robust_trailing_z(s["markup_pct"].to_numpy(dtype=float))
    return s


@dataclass(frozen=True)
class ShadowEntry:
    """One day's frozen signal (derived numbers only -- no raw retailer prices)."""

    date: str
    markup_pct: float
    markup_rs: float
    z: float | None
    n_trailing: int
    signals: dict[str, bool]
    ibja_fix: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "markup_pct": round(self.markup_pct, 4),
            "markup_rs": round(self.markup_rs, 2),
            "z": None if self.z is None else round(self.z, 4),
            "n_trailing": self.n_trailing,
            "signals": self.signals,
            "ibja_fix": self.ibja_fix,
        }


def shadow_entry_for(series: pd.DataFrame, as_of: date) -> ShadowEntry | None:
    """The frozen signal for ``as_of`` from an eligible series with a ``z`` column, or None if
    ``as_of`` is not an eligible (same-day-fresh) day."""
    rows = series[series["date"] == as_of]
    if rows.empty:
        return None
    i = int(rows.index[0])
    r = rows.iloc[0]
    z = None if pd.isna(r["z"]) else float(r["z"])
    return ShadowEntry(
        date=as_of.isoformat(),
        markup_pct=float(r["markup_pct"]),
        markup_rs=float(r["markup_rs"]),
        z=z,
        n_trailing=min(i, TRAILING_WINDOW),
        signals={f"z_ge_{t:g}": (z is not None and z >= t) for t in Z_THRESHOLDS},
        ibja_fix=f"{r['ibja_fix_date']} {r['ibja_fix_type']}",
    )


def resolve_outcomes(
    entries: list[dict[str, Any]], series: pd.DataFrame, bdays: list[date]
) -> list[dict[str, Any]]:
    """Fill ``outcomes`` (N -> y / gross_saving) for entries whose target day is now in
    ``series``. Never touches the frozen signal fields."""
    by_date = series.set_index("date")
    pos = {d: i for i, d in enumerate(bdays)}
    for e in entries:
        d = date.fromisoformat(e["date"])
        outcomes = e.setdefault("outcomes", {})
        for h in HORIZONS:
            key = f"n{h}"
            if key in outcomes:
                continue
            i = pos.get(d)
            if i is None or i + h >= len(bdays) or d not in by_date.index:
                continue
            tgt = bdays[i + h]
            if tgt not in by_date.index:
                # Target business day exists but is not an eligible (same-day-fresh) row:
                # final once a later eligible day has been observed.
                if len(by_date.index) and max(by_date.index) > tgt:
                    outcomes[key] = {"target_date": tgt.isoformat(), "status": "target_ineligible"}
                continue
            t0: Any = by_date.loc[d]
            t1: Any = by_date.loc[tgt]
            outcomes[key] = {
                "target_date": tgt.isoformat(),
                "y_markup_change_rs": round(float(t1["markup_rs"] - t0["markup_rs"]), 2),
                "gross_saving_rs": round(float(t0["tanishq"] - t1["tanishq"]), 2),
            }
    return entries
