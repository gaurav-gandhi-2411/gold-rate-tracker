"""Unit tests for ml/drivers.py — log-decomposition driver attribution.

Norm #11: mocked data only, no live API calls.

Tests cover the honesty-critical guarantees:
  (A) log terms sum identically to total IBJA log change
  (B) premium flag fires when premium moves beyond threshold
  (C) attribution degrades when macro is stale
  (D) full pipeline: clean data → valid attribution
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml.drivers import (
    _CONV_10G_916,
    MACRO_STALE_THRESHOLD_DAYS,
    PREMIUM_THRESHOLD_PCT,
    WINDOWS_DAYS,
    _decompose_window,
    compute_driver_attribution,
)

# ---------------------------------------------------------------------------
# Test fixtures / helpers
# ---------------------------------------------------------------------------


def _build_merged(
    dates: list[str],
    gold_usd: list[float],
    usd_inr: list[float],
    ibja_10g: list[float],
) -> pd.DataFrame:
    """Construct the fully-annotated merged DataFrame that _decompose_window expects."""
    ln_conv = math.log(_CONV_10G_916)
    df = pd.DataFrame(
        {"ibja_10g": ibja_10g, "gold_usd": gold_usd, "usd_inr": usd_inr},
        index=pd.to_datetime(dates),
    )
    df["ln_ibja"] = np.log(df["ibja_10g"])
    df["ln_gold_usd"] = np.log(df["gold_usd"])
    df["ln_usdinr"] = np.log(df["usd_inr"])
    df["ln_premium"] = df["ln_ibja"] - df["ln_gold_usd"] - df["ln_usdinr"] - ln_conv
    return df


def _write_ibja_parquet(path: Path, dates: list[str], ibja_10g_vals: list[float]) -> None:
    """Write ibja_rates.parquet with pm_916 = ibja_10g (stored raw in INR/10g)."""
    df = pd.DataFrame({"date": dates, "am_916": ibja_10g_vals, "pm_916": ibja_10g_vals})
    df.to_parquet(path, index=False)


def _write_macro_parquet(
    path: Path, dates: list[str], gold_usd: list[float], usd_inr: list[float]
) -> None:
    """Write macro_cache.parquet with UTC-aware index (mirrors the real macro.py output)."""
    idx = pd.to_datetime(dates, utc=True)
    df = pd.DataFrame({"gold_usd": gold_usd, "usd_inr": usd_inr}, index=idx)
    df.to_parquet(path)


def _write_intraday_parquet(
    path: Path,
    dates: list[str],
    gold_at_fix: list[float],
    usd_inr: list[float],
    gold_after_fix: list[float] | None = None,
) -> None:
    """Write macro_intraday.parquet: 24 hourly bars per date (UTC index = bar start).

    Bars ending by 11:00 UTC (before the 11:30 UTC PM fix) hold gold_at_fix; later bars hold
    gold_after_fix (default: unchanged). usd_inr is flat within each date.
    """
    after = gold_after_fix if gold_after_fix is not None else gold_at_fix
    rows = []
    for d, g0, g1, r in zip(dates, gold_at_fix, after, usd_inr, strict=True):
        for h in range(24):
            ts = pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=h)
            rows.append((ts, g0 if h < 11 else g1, r))
    df = pd.DataFrame(rows, columns=["ts", "gold_usd", "usd_inr"]).set_index("ts")
    df.to_parquet(path)


def _write_prices_json(path: Path, dates: list[str], prices_per_g: list[float]) -> None:
    """Write prices.json in INR/gram (matches prices.json schema)."""
    readings = [
        {
            "timestamp": f"{d}T12:00:00.000Z",
            "22k": p,
            "24k": round(p * 1.09, 1),
            "18k": round(p * 0.82, 1),
        }
        for d, p in zip(dates, prices_per_g, strict=True)
    ]
    path.write_text(json.dumps(readings))


def _write_macro_status(path: Path, age_days: float) -> None:
    path.write_text(
        json.dumps(
            {
                "cache_age_days": age_days,
                "cache_exists": True,
                "warn_threshold_days": 7,
                "fail_threshold_days": int(MACRO_STALE_THRESHOLD_DAYS),
            }
        )
    )


def _stable_ibja(dates: list[str], gold_usd: list[float], usd_inr: list[float]) -> list[float]:
    """Compute IBJA_10g values for a flat premium of 1.12."""
    premium = 1.12
    return [g * r * _CONV_10G_916 * premium for g, r in zip(gold_usd, usd_inr, strict=True)]


# ---------------------------------------------------------------------------
# (A) Log decomposition identity: terms sum to total
# ---------------------------------------------------------------------------


def test_log_terms_sum_to_total_ibja_change_gold_only():
    """Δln(gold_usd) + Δln(usd_inr) + Δln(premium) = Δln(IBJA) exactly.

    Scenario: only gold_usd moves; usd_inr and premium held flat.
    """
    dates = ["2026-05-01", "2026-05-08"]
    gold_usd = [4000.0, 4400.0]  # +10%
    usd_inr = [95.0, 95.0]  # flat
    ibja_10g = _stable_ibja(dates, gold_usd, usd_inr)

    merged = _build_merged(dates, gold_usd, usd_inr, ibja_10g)
    w = _decompose_window(merged, window_days=7, tanishq_df=None)

    total = w["delta_pct_ibja"]
    parts = w["delta_pct_gold_usd"] + w["delta_pct_usdinr"] + w["delta_pct_premium"]

    assert total is not None
    assert abs(parts - total) < 1e-4, (
        f"Log terms do not sum: {w['delta_pct_gold_usd']} + "
        f"{w['delta_pct_usdinr']} + {w['delta_pct_premium']} = {parts} ≠ {total}"
    )


def test_log_terms_sum_when_both_drivers_move():
    """Identity holds when gold_usd AND usd_inr both move (cross-term handled by log)."""
    dates = ["2026-05-01", "2026-05-08"]
    gold_usd = [4000.0, 4200.0]  # +5%
    usd_inr = [90.0, 96.0]  # +6.67%
    ibja_10g = _stable_ibja(dates, gold_usd, usd_inr)

    merged = _build_merged(dates, gold_usd, usd_inr, ibja_10g)
    w = _decompose_window(merged, window_days=7, tanishq_df=None)

    total = w["delta_pct_ibja"]
    parts = w["delta_pct_gold_usd"] + w["delta_pct_usdinr"] + w["delta_pct_premium"]
    assert abs(parts - total) < 1e-4, f"{parts=} ≠ {total=}"


# ---------------------------------------------------------------------------
# (B) Premium-residual flag
# ---------------------------------------------------------------------------


def test_premium_flag_fires_when_premium_dominates():
    """attribution_valid=False when premium share > PREMIUM_THRESHOLD_PCT.

    Scenario: gold_usd and usd_inr flat; premium jumps 5% → premium share = 100%.
    """
    dates = ["2026-05-01", "2026-05-08"]
    gold_usd = [4500.0, 4500.0]
    usd_inr = [95.0, 95.0]
    prem_0, prem_1 = 1.10, 1.155  # +5% premium jump
    ibja_10g = [
        gold_usd[0] * usd_inr[0] * _CONV_10G_916 * prem_0,
        gold_usd[1] * usd_inr[1] * _CONV_10G_916 * prem_1,
    ]

    merged = _build_merged(dates, gold_usd, usd_inr, ibja_10g)
    w = _decompose_window(merged, window_days=7, tanishq_df=None)

    assert w["attribution_valid"] is False
    assert w["premium_share_pct"] == pytest.approx(100.0, abs=0.5)


def test_attribution_valid_when_premium_within_threshold():
    """attribution_valid=True when gold_usd drives the move and premium stays flat."""
    dates = ["2026-05-01", "2026-05-08"]
    gold_usd = [4000.0, 4400.0]  # +10%
    usd_inr = [95.0, 95.0]
    ibja_10g = _stable_ibja(dates, gold_usd, usd_inr)  # premium=1.12, flat

    merged = _build_merged(dates, gold_usd, usd_inr, ibja_10g)
    w = _decompose_window(merged, window_days=7, tanishq_df=None)

    assert w["attribution_valid"] is True
    assert w["premium_share_pct"] < PREMIUM_THRESHOLD_PCT


def test_premium_flag_threshold_is_strict_greater_than():
    """Spec says 'exceeds' (strict >) — at exactly PREMIUM_THRESHOLD_PCT the result is valid.

    Constructs a scenario where premium contributes exactly PREMIUM_THRESHOLD_PCT of the
    total log move and verifies attribution_valid=True (not exceeding the threshold).
    The test_premium_flag_fires_when_premium_dominates test covers the clearly-over case.
    """
    # Total IBJA +10% (log space); premium is exactly PREMIUM_THRESHOLD_PCT of that
    total_ln = 0.10
    prem_share = PREMIUM_THRESHOLD_PCT / 100.0  # 0.15
    prem_ln = prem_share * total_ln  # 0.015
    gold_ln = (1.0 - prem_share) * total_ln  # 0.085 (usd_inr flat → all remainder to gold)

    dates = ["2026-05-01", "2026-05-08"]
    gold_usd = [4000.0, 4000.0 * math.exp(gold_ln)]
    usd_inr = [95.0, 95.0]
    base_prem = 1.12
    ibja_10g = [
        gold_usd[0] * usd_inr[0] * _CONV_10G_916 * base_prem,
        gold_usd[1] * usd_inr[1] * _CONV_10G_916 * base_prem * math.exp(prem_ln),
    ]

    merged = _build_merged(dates, gold_usd, usd_inr, ibja_10g)
    w = _decompose_window(merged, window_days=7, tanishq_df=None)

    assert abs(w["premium_share_pct"] - PREMIUM_THRESHOLD_PCT) < 0.1
    # Exactly at threshold (not exceeding) → valid
    assert w["attribution_valid"] is True


# ---------------------------------------------------------------------------
# (C) Macro-staleness degrade
# ---------------------------------------------------------------------------


def test_attribution_degrades_when_macro_stale(tmp_path):
    """attribution_valid=False for all windows when macro_status reports stale cache."""
    age = MACRO_STALE_THRESHOLD_DAYS + 1.0
    _write_macro_status(tmp_path / "macro_status.json", age_days=age)

    # Even with valid IBJA and macro files present, staleness must override
    dates = ["2026-05-01", "2026-05-08"]
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", dates, [130000.0, 132000.0])
    _write_macro_parquet(tmp_path / "macro_cache.parquet", dates, [4500.0, 4500.0], [95.0, 95.0])
    _write_prices_json(tmp_path / "prices.json", dates, [13000.0, 13200.0])

    result = compute_driver_attribution(data_dir=tmp_path)

    assert result["macro_fresh"] is False
    for wd in WINDOWS_DAYS:
        w = result["windows"][f"{wd}d"]
        assert w["attribution_valid"] is False
        assert "stale" in w["attribution_valid_reason"].lower()


def test_attribution_degrades_when_macro_missing(tmp_path):
    """attribution_valid=False for all windows when macro_cache.parquet is absent."""
    _write_macro_status(tmp_path / "macro_status.json", age_days=None)
    # Override: write a status with no cache_age_days
    (tmp_path / "macro_status.json").write_text(
        json.dumps({"cache_age_days": None, "cache_exists": False})
    )

    result = compute_driver_attribution(data_dir=tmp_path)
    assert result["macro_fresh"] is False
    for wd in WINDOWS_DAYS:
        assert result["windows"][f"{wd}d"]["attribution_valid"] is False


# ---------------------------------------------------------------------------
# (D) Full-pipeline integration (clean mocked data)
# ---------------------------------------------------------------------------


def _make_clean_dates(n: int, start: str = "2026-04-01") -> list[str]:
    """n weekday dates starting from start."""
    dates = []
    ts = pd.Timestamp(start)
    while len(dates) < n:
        if ts.weekday() < 5:
            dates.append(ts.strftime("%Y-%m-%d"))
        ts += pd.Timedelta(days=1)
    return dates


def test_full_pipeline_valid_attribution_7d(tmp_path):
    """Full pipeline: stable premium + rising gold_usd → 7d attribution_valid=True."""
    dates = _make_clean_dates(40)  # 40 trading days (~8 weeks)
    # Gold rises steadily; premium flat at 1.12; usd_inr flat
    gold_usd = [4000.0 + i * 4 for i in range(len(dates))]  # +0.1% per day
    usd_inr = [95.0] * len(dates)
    ibja_10g = _stable_ibja(dates, gold_usd, usd_inr)  # premium = 1.12, flat
    tanishq = [v / 10 for v in ibja_10g]  # ~IBJA/10 in INR/g

    _write_macro_status(tmp_path / "macro_status.json", age_days=0.5)
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", dates, ibja_10g)
    _write_macro_parquet(tmp_path / "macro_cache.parquet", dates, gold_usd, usd_inr)
    _write_intraday_parquet(tmp_path / "macro_intraday.parquet", dates, gold_usd, usd_inr)
    _write_prices_json(tmp_path / "prices.json", dates, tanishq)

    result = compute_driver_attribution(data_dir=tmp_path)

    assert result["macro_fresh"] is True
    assert "7d" in result["windows"]
    assert "30d" in result["windows"]

    w7 = result["windows"]["7d"]
    assert w7["attribution_valid"] is True
    assert w7["premium_share_pct"] < PREMIUM_THRESHOLD_PCT
    assert w7["n_obs"] >= 2
    # Gold drove the move upward
    assert w7["total_move_rs_per_g"] is not None
    assert w7["total_move_rs_per_g"] > 0
    assert w7["gold_usd_contrib_rs_per_g"] > 0


def test_full_pipeline_contributions_sum_to_total(tmp_path):
    """gold_usd_contrib + usdinr_contrib + premium_contrib ≈ total_move (within rounding)."""
    dates = _make_clean_dates(40)
    gold_usd = [4000.0 + i * 4 for i in range(len(dates))]
    usd_inr = [95.0] * len(dates)
    ibja_10g = _stable_ibja(dates, gold_usd, usd_inr)
    tanishq = [v / 10 for v in ibja_10g]

    _write_macro_status(tmp_path / "macro_status.json", age_days=0.5)
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", dates, ibja_10g)
    _write_macro_parquet(tmp_path / "macro_cache.parquet", dates, gold_usd, usd_inr)
    _write_intraday_parquet(tmp_path / "macro_intraday.parquet", dates, gold_usd, usd_inr)
    _write_prices_json(tmp_path / "prices.json", dates, tanishq)

    result = compute_driver_attribution(data_dir=tmp_path)

    for key in ["7d", "30d"]:
        w = result["windows"][key]
        if w["attribution_valid"] and w["total_move_rs_per_g"] is not None:
            total = w["total_move_rs_per_g"]
            parts = (
                w["gold_usd_contrib_rs_per_g"]
                + w["usdinr_contrib_rs_per_g"]
                + w["premium_contrib_rs_per_g"]
            )
            # Allow up to Rs 2/g rounding tolerance (shares are from log, total from Tanishq prices)
            assert abs(parts - total) <= 2.0, (
                f"Window {key}: contributions {parts} do not sum to total {total}"
            )


def test_full_pipeline_driver_state_present(tmp_path):
    """driver_state block is populated with 30d percentage changes."""
    dates = _make_clean_dates(40)
    gold_usd = [4500.0] * len(dates)
    usd_inr = [95.0 + i * 0.01 for i in range(len(dates))]  # small INR weakening
    ibja_10g = _stable_ibja(dates, gold_usd, usd_inr)

    _write_macro_status(tmp_path / "macro_status.json", age_days=0.5)
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", dates, ibja_10g)
    _write_macro_parquet(tmp_path / "macro_cache.parquet", dates, gold_usd, usd_inr)
    _write_intraday_parquet(tmp_path / "macro_intraday.parquet", dates, gold_usd, usd_inr)
    _write_prices_json(tmp_path / "prices.json", dates, [v / 10 for v in ibja_10g])

    result = compute_driver_attribution(data_dir=tmp_path)

    ds = result["driver_state"]
    assert ds is not None
    assert "usd_inr_now" in ds
    assert "gold_usd_now" in ds
    assert "usd_inr_30d_pct_change" in ds
    assert "gold_usd_30d_pct_change" in ds


def test_missing_ibja_parquet_degrades_gracefully(tmp_path):
    """Missing ibja_rates.parquet → all windows attribution_valid=False."""
    _write_macro_status(tmp_path / "macro_status.json", age_days=0.5)
    # Do NOT write ibja parquet
    _write_macro_parquet(
        tmp_path / "macro_cache.parquet",
        ["2026-06-01", "2026-06-02"],
        [4500.0, 4500.0],
        [95.0, 95.0],
    )

    result = compute_driver_attribution(data_dir=tmp_path)
    for wd in WINDOWS_DAYS:
        assert result["windows"][f"{wd}d"]["attribution_valid"] is False


def test_ibja_unchanged_window_degrades_gracefully():
    """IBJA flat over window → attribution_valid=False (no move to attribute)."""
    dates = ["2026-05-01", "2026-05-08"]
    ibja_10g = [130000.0, 130000.0]  # flat
    gold_usd = [4500.0, 4500.0]
    usd_inr = [95.0, 95.0]

    merged = _build_merged(dates, gold_usd, usd_inr, ibja_10g)
    w = _decompose_window(merged, window_days=7, tanishq_df=None)

    assert w["attribution_valid"] is False
    assert "unchanged" in w["attribution_valid_reason"]


# ---------------------------------------------------------------------------
# (E) Timing alignment (ADR 058 A16): a fix is paired only with global moves public at the fix
# ---------------------------------------------------------------------------


def _post_fix_jump_fixture(tmp_path: Path) -> list[str]:
    """Gold rises 0.5% per day, and on the LAST date falls 4% at 14:00 UTC -- after IBJA's
    11:30 UTC PM fix and before that day's COMEX settle (13:30 ET = 17:30 UTC). IBJA prices
    each fix off gold at the fix with a flat 1.12 premium, so the true premium move is exactly
    zero and the fall is not in any IBJA value yet. The daily settle of each date is the
    post-fix level (what Yahoo's GC=F daily Close records)."""
    dates = _make_clean_dates(15, start="2026-06-01")
    at_fix = [4000.0 * (1.005**i) for i in range(len(dates))]
    after_fix = list(at_fix)
    after_fix[-1] = at_fix[-1] * 0.96
    usd_inr = [95.0] * len(dates)
    ibja_10g = [g * r * _CONV_10G_916 * 1.12 for g, r in zip(at_fix, usd_inr, strict=True)]
    _write_macro_status(tmp_path / "macro_status.json", age_days=0.5)
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", dates, ibja_10g)
    _write_macro_parquet(tmp_path / "macro_cache.parquet", dates, after_fix, usd_inr)
    _write_intraday_parquet(
        tmp_path / "macro_intraday.parquet", dates, at_fix, usd_inr, gold_after_fix=after_fix
    )
    _write_prices_json(tmp_path / "prices.json", dates, [v / 10 for v in ibja_10g])
    return dates


def test_post_fix_gold_move_is_not_credited_to_local_factors(tmp_path):
    """Old same-date join: the last settle (-4%, after the fix) is paired with the fix, so the
    IBJA rise reads as "gold fell, premium / local factors rose" (~180% of the move) and the
    headline is suppressed. Aligned: premium share ~0, gold explains the whole move."""
    _post_fix_jump_fixture(tmp_path)
    result = compute_driver_attribution(data_dir=tmp_path)
    w7 = result["windows"]["7d"]
    assert w7["attribution_valid"] is True, w7["attribution_valid_reason"]
    assert w7["premium_share_pct"] < 1.0
    assert w7["delta_pct_premium"] == pytest.approx(0.0, abs=0.01)
    assert w7["delta_pct_gold_usd"] > 0  # the pre-fix rise, not the post-fix fall
    assert w7["gold_usd_contrib_rs_per_g"] == pytest.approx(w7["total_move_rs_per_g"], abs=1.0)
    # 30d driver state reports gold as priced at the latest fix: the post-fix fall is excluded
    assert result["driver_state"]["gold_usd_30d_pct_change"] > 0
    assert result["alignment"] == "intraday_at_fix"


def test_no_intraday_bars_suppresses_split_but_keeps_lagged_driver_state(tmp_path):
    """Without intraday bars the split is not shown (lagged daily bars sit 17-33 h before the
    fix and explain IBJA worse than the old join did); driver_state uses the lagged settle."""
    _post_fix_jump_fixture(tmp_path)
    (tmp_path / "macro_intraday.parquet").unlink()
    result = compute_driver_attribution(data_dir=tmp_path)
    assert result["alignment"] == "daily_lagged_state_only"
    for wd in WINDOWS_DAYS:
        w = result["windows"][f"{wd}d"]
        assert w["attribution_valid"] is False
        assert w["delta_pct_premium"] is None
        assert "intraday" in w["attribution_valid_reason"]
    # lagged daily: the last fix gets the previous settle, so the post-fix fall is excluded
    assert result["driver_state"]["gold_usd_30d_pct_change"] > 0


def test_intraday_not_covering_latest_fix_suppresses_split(tmp_path):
    """Intraday bars that stop > MAX_INTRADAY_GAP_HOURS before the latest fix must not
    silently shift the window to an older fix."""
    dates = _post_fix_jump_fixture(tmp_path)
    bars = pd.read_parquet(tmp_path / "macro_intraday.parquet")
    bars[bars.index < pd.Timestamp(dates[-4], tz="UTC")].to_parquet(
        tmp_path / "macro_intraday.parquet"
    )
    result = compute_driver_attribution(data_dir=tmp_path)
    assert result["alignment"] == "daily_lagged_state_only"
    assert result["windows"]["7d"]["attribution_valid"] is False


def test_intraday_reads_last_bar_ended_by_fix():
    """The 10:00-11:00 UTC bar is the last one ended by the 11:30 UTC PM fix; the 11:00-12:00
    bar (in progress at the fix) must not be used. AM-only rows use 06:30 UTC."""
    from ml.drivers import _align_intraday_to_fixes

    ibja = pd.DataFrame(
        {"ibja_10g": [1.0, 2.0], "fix": ["pm", "am"]},
        index=pd.to_datetime(["2026-09-22", "2026-09-23"]),
    )
    idx = pd.date_range("2026-09-22 00:00", "2026-09-23 23:00", freq="h", tz="UTC")
    bars = pd.DataFrame({"gold_usd": range(len(idx)), "usd_inr": range(len(idx))}, index=idx)
    out = _align_intraday_to_fixes(ibja, bars.astype(float))
    assert out.loc["2026-09-22", "gold_usd"] == 10.0  # bar starting 10:00 UTC
    assert out.loc["2026-09-23", "gold_usd"] == 24.0 + 5.0  # AM fix: bar starting 05:00 UTC


def test_align_pairs_each_fix_with_previous_settle_never_same_date():
    """PM of D -> settle of the previous NY trading day (Monday -> Friday); AM-only rows too."""
    from ml.drivers import _align_macro_to_fixes

    ibja = pd.DataFrame(
        {"ibja_10g": [1.0, 2.0, 3.0], "fix": ["pm", "am", "pm"]},
        index=pd.to_datetime(["2026-09-18", "2026-09-21", "2026-09-22"]),  # Fri, Mon, Tue
    )
    macro = pd.DataFrame(
        {"gold_usd": [10.0, 11.0, 12.0, 13.0], "usd_inr": [90.0, 91.0, 92.0, 93.0]},
        index=pd.to_datetime(["2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22"]),
    )
    out = _align_macro_to_fixes(ibja, macro)
    assert out["gold_usd"].tolist() == [10.0, 11.0, 12.0]  # Thu, Fri, Mon settles
    assert out["usd_inr"].tolist() == [90.0, 91.0, 92.0]
    assert [d.strftime("%Y-%m-%d") for d in out["gold_usd_bar_date"]] == [
        "2026-09-17",
        "2026-09-18",
        "2026-09-21",
    ]


def test_align_uses_dst_aware_settle_clock():
    """The 13:30 ET settle is 17:30 UTC in summer, 18:30 UTC in winter -- both after the
    11:30 UTC PM fix of the same date, so both pair with the previous day."""
    from ml.drivers import _COMEX_SETTLE_CLOCK, _known_at_utc

    summer, winter = _known_at_utc(
        pd.to_datetime(["2026-07-15", "2026-01-15"]), _COMEX_SETTLE_CLOCK
    )
    assert (summer.hour, summer.minute) == (17, 30)
    assert (winter.hour, winter.minute) == (18, 30)


def test_align_drops_fix_whose_latest_known_bar_is_stale():
    """A fix with no macro bar within MAX_BAR_AGE_DAYS is dropped, never paired with old data."""
    from ml.drivers import _align_macro_to_fixes

    ibja = pd.DataFrame(
        {"ibja_10g": [1.0, 2.0], "fix": ["pm", "pm"]},
        index=pd.to_datetime(["2026-09-02", "2026-09-22"]),
    )
    macro = pd.DataFrame(
        {"gold_usd": [10.0], "usd_inr": [90.0]}, index=pd.to_datetime(["2026-09-01"])
    )
    out = _align_macro_to_fixes(ibja, macro)
    assert [d.strftime("%Y-%m-%d") for d in out.index] == ["2026-09-02"]
