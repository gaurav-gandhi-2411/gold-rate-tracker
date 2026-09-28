"""Replays of the three real timing leaks through the ADR 061 guard (ADR 061 section "Replays").

Each leak is rebuilt in the exact input configuration of the commit/file where it happened and
must be BLOCKED (TimingLeakError); the configuration that fixed it must pass.

  1. Missing embargo -- ml/direction/evaluate.py before a29a98dd (#1930) trained fold i on
     `dataset.iloc[:i]`; ml/direction/config_sweep.run_config_sweep still runs that protocol
     when embargo_label_date_col is None (kept so ADR 034's figures reproduce; ADR 038 A1).
  2. Same-day India VIX -- ml/direction/preregistration.build_proxy_deadzone_dataset before
     b461cf2f (#1949) called `augment_with_m1_drivers(out)`: the VIX close of the same day
     whose move the proxy arm predicts (ADR 038 A2).
  3. Kalman nowcast inputs -- ml/kalman_nowcast.build_observations at 70d5474c (#2050, branch
     feat/kalman-nowcast-shadow) assimilates IBJA AM/PM on their value date and retail readings
     on their capture's UTC date, while the target is that date's LAST Tanishq reading. The
     independent audit (#2070, reports/kalman_leak_audit/inputs_audit.json at ae70692d) found
     GRT/Malabar captures after the target and IBJA PM used before 17:00 IST. Timestamps below
     are copied from that audit and cross-checked against data/ files when they are readable.

Synthetic data where the harness needs a dataset; no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml.direction.config_sweep import augment_with_m1_drivers, run_config_sweep
from ml.direction.leak_checks import check_fold, fold_prediction_moment, training_label_inputs
from ml.direction.preregistration import proxy_feature_inputs, run_proxy_arm
from ml.known_at import capture_known_at, comex_daily_known_at, ibja_known_at
from ml.leak_guard import (
    KnownInput,
    LeakGuard,
    TimingLeakError,
    assert_known_before,
    filter_known_before,
)

ROOT = Path(__file__).resolve().parent.parent


# --- Leak 1: the missing embargo ------------------------------------------------------------


def _h2_dataset(n: int = 40) -> pd.DataFrame:
    """Consecutive business days; the h2 label of day k is IBJA's PM two business days later."""
    days = pd.bdate_range("2026-07-01", periods=n + 2)
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        {
            "as_of_date": days[:n].strftime("%Y-%m-%d"),
            "f1": rng.normal(size=n),
            "label_binary_h2": np.tile([0.0, 1.0], n // 2),
            "label_date_h2": days[2 : n + 2].strftime("%Y-%m-%d"),
        }
    )


class TestLeak1MissingEmbargo:
    def test_pre_1930_training_window_is_blocked(self) -> None:
        """Exactly the pre-#1930 window: every row before i. Row i-1's h2 label is IBJA's PM
        on day i+1 -- published after fold i's prediction moment. (Row i-2's label is dated
        day i itself: published 17:00 IST on i, before the end-of-day moment, so the guard
        allows it; #1930's date embargo is stricter and drops it too.)"""
        ds = _h2_dataset()
        i = 25
        guard = LeakGuard("replay", mode="raise")
        with pytest.raises(TimingLeakError) as exc:
            check_fold(guard, None, ds.iloc[i], ds["label_date_h2"].iloc[:i], "label_binary_h2", [])
        leaked = [v.input.name for v in exc.value.violations]
        assert leaked == [f"label_binary_h2@{ds['label_date_h2'].iloc[i - 1]}"]
        assert exc.value.violations[0].late_by_s == 17 * 3600  # 11:30 UTC i+1 vs 18:30 UTC i

    def test_embargoed_window_passes(self) -> None:
        ds = _h2_dataset()
        i = 25
        keep = ds["label_date_h2"].iloc[:i] < ds["as_of_date"].iloc[i]
        check_fold(
            LeakGuard("replay", mode="raise"),
            None,
            ds.iloc[i],
            ds["label_date_h2"].iloc[:i][keep],
            "label_binary_h2",
            [],
        )

    def test_no_embargo_sweep_is_refused_in_raise_mode(self) -> None:
        """The ADR 034 / pre-A1 pre-registration protocol, run through the real harness."""
        with pytest.raises(TimingLeakError, match="label_binary_h2 training labels"):
            run_config_sweep(
                _h2_dataset(),
                feature_cols=["f1"],
                label_col="label_binary_h2",
                label_guard_mode="raise",
            )

    def test_no_embargo_sweep_reports_every_fold_by_default(self) -> None:
        """Default for the reproduction path: report mode -- the frozen figures still
        reproduce, and the result carries the leak."""
        res = run_config_sweep(_h2_dataset(), feature_cols=["f1"], label_col="label_binary_h2")
        labels = res["leak_guard"]["labels"]
        assert labels["mode"] == "report"
        assert labels["n_checks"] == labels["n_checks_with_violation"] == 20
        assert labels["n_violations"] == 20  # one unpublished label per fold

    def test_embargoed_sweep_passes_in_raise_mode(self) -> None:
        res = run_config_sweep(
            _h2_dataset(),
            feature_cols=["f1"],
            label_col="label_binary_h2",
            embargo_label_date_col="label_date_h2",
        )
        assert res["leak_guard"]["labels"]["mode"] == "raise"
        assert res["leak_guard"]["labels"]["n_violations"] == 0

    def test_real_data_fold_2026_09_18(self) -> None:
        """The last as_of of ADR 038's selection data, on the committed feature store."""
        try:
            from ml.direction.dataset import build_dataset

            ds = build_dataset(require_consecutive=True)
        except Exception as exc:  # data files absent/unreadable (e.g. encrypted checkout)
            pytest.skip(f"committed data not readable: {exc}")
        ds = ds[ds["label_binary_h2"].notna()].reset_index(drop=True)
        hit = ds.index[ds["as_of_date"] == "2026-09-18"]
        if len(hit) == 0:
            pytest.skip("as_of 2026-09-18 not in this data snapshot")
        i = int(hit[0])
        t = fold_prediction_moment("2026-09-18")
        with pytest.raises(TimingLeakError):
            assert_known_before(t, training_label_inputs(ds["label_date_h2"].iloc[:i], "h2"))
        keep = ds["label_date_h2"].iloc[:i] < "2026-09-18"
        assert_known_before(t, training_label_inputs(ds["label_date_h2"].iloc[:i][keep], "h2"))


# --- Leak 2: the proxy arm's same-day India VIX ---------------------------------------------


def _proxy_dataset(prior_day: bool) -> pd.DataFrame:
    days = pd.bdate_range("2026-08-03", periods=40)
    base = pd.DataFrame(
        {
            "as_of_date": days.strftime("%Y-%m-%d"),
            "move_per_gram": np.tile([120.0, -130.0, 150.0, -110.0], 10),
        }
    )
    base["label_binary_deadzone"] = (base["move_per_gram"] > 0).astype(float)
    # Genuine NSE closes on weekdays only, NaN on weekends (as yfinance delivers them).
    cal = pd.date_range("2026-07-27", "2026-10-02", freq="D", tz="UTC")
    vix = pd.Series(np.where(cal.dayofweek < 5, 10.0 + np.arange(len(cal)) % 7, np.nan), cal)
    return augment_with_m1_drivers(base, india_vix=vix, india_vix_prior_day=prior_day)


class TestLeak2SameDayIndiaVix:
    def test_same_day_vix_is_blocked_by_the_proxy_arm(self) -> None:
        """Pre-#1949 build_proxy_deadzone_dataset: augment_with_m1_drivers(out)."""
        with pytest.raises(TimingLeakError, match="india_vix") as exc:
            run_proxy_arm(_proxy_dataset(prior_day=False))
        v = exc.value.violations[0]
        # Day t's close (15:30 IST on t) vs the prediction moment (00:00 IST on t): 15.5 h late.
        assert v.late_by_s == 15.5 * 3600

    def test_prior_day_vix_passes(self) -> None:
        res = run_proxy_arm(_proxy_dataset(prior_day=True))
        feats = res["leak_guard"]["features"]
        assert (feats["mode"], feats["n_violations"]) == ("raise", 0)
        assert feats["n_inputs_checked"] > 0

    def test_monday_uses_fridays_close(self) -> None:
        ds = _proxy_dataset(prior_day=True)
        monday = ds[pd.to_datetime(ds["as_of_date"]).dt.dayofweek == 0].iloc[0]
        friday = (pd.Timestamp(monday["as_of_date"]) - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
        assert monday["india_vix_asof_date"] == friday
        assert proxy_feature_inputs(monday)[0].known_at == pd.Timestamp(f"{friday}T10:00Z")


# --- Leak 3: the Kalman nowcast's same-day inputs -------------------------------------------

# From reports/kalman_leak_audit/inputs_audit.json (#2070 @ ae70692d); cross-checked below.
KALMAN_DAYS = {
    "2026-07-21": {
        "target": "2026-07-21T08:22:07.863Z",
        "ibja_fetched_at": "2026-07-21T13:40:14.666998+00:00",
        "retail_last_capture": {"grt": "2026-07-21T19:48:27Z", "malabar": "2026-07-21T19:48:27Z"},
    },
    "2026-08-28": {"target": "2026-08-28T06:31:13.885Z", "retail_last_capture": {}},
    "2026-09-09": {"target": "2026-09-09T04:32:54.463Z", "retail_last_capture": {}},
}


def _registered_inputs(day: str, *, repo_fetch: bool = False) -> list[KnownInput]:
    """build_observations' day-`day` inputs: IBJA AM/PM of `day` (age 0), each retailer's last
    capture of the UTC day, and COMEX closed the previous day (assimilated on c + 1)."""
    spec = KALMAN_DAYS[day]
    fetched = spec.get("ibja_fetched_at") if repo_fetch else None
    prev = (pd.Timestamp(day) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return [
        KnownInput("ibja_am", "ibja_am", ibja_known_at(day, "am", fetched)),
        KnownInput("ibja_pm", "ibja_pm", ibja_known_at(day, "pm", fetched)),
        KnownInput("comex", "comex", comex_daily_known_at(prev)),
        *(
            KnownInput(src, src, capture_known_at(ts))
            for src, ts in spec["retail_last_capture"].items()
        ),
    ]


class TestLeak3KalmanInputs:
    def test_grt_malabar_after_target_and_pm_before_1700_ist_are_blocked(self) -> None:
        target = KALMAN_DAYS["2026-07-21"]["target"]
        with pytest.raises(TimingLeakError) as exc:
            assert_known_before(target, _registered_inputs("2026-07-21"), context="2026-07-21")
        assert sorted(v.input.source for v in exc.value.violations) == [
            "grt",
            "ibja_pm",
            "malabar",
        ]

    def test_strict_rescore_inputs_pass(self) -> None:
        """#2070's strict re-score keeps only known_at < target -- the same set the filter
        helper returns, which the guard then accepts."""
        target = KALMAN_DAYS["2026-07-21"]["target"]
        kept = filter_known_before(target, _registered_inputs("2026-07-21"))
        assert [x.source for x in kept] == ["ibja_am", "comex"]
        assert_known_before(target, kept)

    def test_repo_fetch_time_also_blocks_the_am_fix(self) -> None:
        """With this repo's own fetched_at (13:40 UTC), even the AM fix was not yet held at
        08:22 UTC -- the audit's descriptive 'used before fetched_at' count."""
        target = KALMAN_DAYS["2026-07-21"]["target"]
        bad = {
            v.input.source
            for v in LeakGuard("r", mode="report").check(
                target, _registered_inputs("2026-07-21", repo_fetch=True)
            )
        }
        assert {"ibja_am", "ibja_pm"} <= bad

    def test_pm_blocked_am_allowed_73_seconds_before(self) -> None:
        target = KALMAN_DAYS["2026-08-28"]["target"]
        bad = LeakGuard("r", mode="report").check(target, _registered_inputs("2026-08-28"))
        assert [v.input.source for v in bad] == ["ibja_pm"]

    def test_am_fix_before_1200_ist_is_blocked(self) -> None:
        target = KALMAN_DAYS["2026-09-09"]["target"]
        bad = LeakGuard("r", mode="report").check(target, _registered_inputs("2026-09-09"))
        assert [v.input.source for v in bad] == ["ibja_am", "ibja_pm"]

    def test_replay_timestamps_match_the_committed_data(self) -> None:
        try:
            raw = json.loads((ROOT / "data" / "prices.json").read_text(encoding="utf-8"))
            fusion = pd.read_parquet(ROOT / "data" / "fusion_snapshots.parquet")
            ibja = pd.read_parquet(ROOT / "data" / "ibja_rates.parquet")
        except Exception as exc:
            pytest.skip(f"committed data not readable: {exc}")
        for day, spec in KALMAN_DAYS.items():
            last = [
                r["timestamp"]
                for r in raw
                if r.get("22k") is not None and r["timestamp"][:10] == day
            ]
            assert last and last[-1] == spec["target"]
            for src, ts in spec["retail_last_capture"].items():
                caps = fusion.loc[fusion["source"] == src, "capture_utc"].astype(str)
                assert caps[caps.str.startswith(day)].max() == ts
        row = ibja.loc[ibja["date"].astype(str) == "2026-07-21"].iloc[0]
        assert str(row["fetched_at"]) == KALMAN_DAYS["2026-07-21"]["ibja_fetched_at"]
