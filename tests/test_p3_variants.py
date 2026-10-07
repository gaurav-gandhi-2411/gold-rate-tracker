"""ADR 071: slope variants of P3 and their pre-registered comparison. Synthetic data only."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml import nextfix

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import analysis_p3_variants as ap


def _pairs(n: int = 120, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-05", periods=n)
    x = rng.normal(0, 0.01, n)
    # the true slope is 0.4 on Mondays and 0.2 otherwise: the Monday variant should help
    b = np.where(days.dayofweek == 0, 0.4, 0.2)
    y = b * x + rng.normal(0, 0.002, n)
    d1 = days.to_series().shift(-1).to_numpy()
    pm0 = np.full(n, 13000.0)
    return pd.DataFrame(
        {"d0": days, "d1": d1, "x_glob": x, "y": y, "pm0": pm0, "pm1": pm0 * np.exp(y)}
    )


def _row_train(pairs: pd.DataFrame, i: int) -> tuple[pd.DataFrame, pd.Series]:
    row = pairs.iloc[i]
    return pairs[pairs["d1"].notna() & (pairs["d1"] <= row["d0"])], row


def test_roll60_uses_only_the_latest_window():
    pairs = _pairs()
    train, row = _row_train(pairs, 100)
    t = train.sort_values("d0").tail(nextfix.ROLL_WINDOW)
    x, y = t["x_glob"].to_numpy(float), t["y"].to_numpy(float)
    assert nextfix.predict_p3_roll60(train, row).ret == pytest.approx(
        float(x @ y / (x @ x)) * float(row["x_glob"]), rel=1e-12
    )


def test_monday_variant_equals_p3_off_mondays_and_uses_monday_pairs_on_mondays():
    pairs = _pairs()
    monday = pairs.index[pairs["d0"].dt.dayofweek == 0][-1]
    other = pairs.index[pairs["d0"].dt.dayofweek == 2][-1]
    tr_o, r_o = _row_train(pairs, other)
    assert nextfix.predict_p3_monday(tr_o, r_o).ret == nextfix.predict_p3(tr_o, r_o).ret
    tr_m, r_m = _row_train(pairs, monday)
    mon = tr_m[tr_m["d0"].dt.dayofweek == 0]
    assert len(mon) >= nextfix.MONDAY_MIN
    x, y = mon["x_glob"].to_numpy(float), mon["y"].to_numpy(float)
    assert nextfix.predict_p3_monday(tr_m, r_m).ret == pytest.approx(
        float(x @ y / (x @ x)) * float(r_m["x_glob"]), rel=1e-12
    )
    # before MONDAY_MIN Monday pairs exist it is exactly P3
    tr_e, r_e = _row_train(pairs, int(pairs.index[pairs["d0"].dt.dayofweek == 0][4]))
    assert nextfix.predict_p3_monday(tr_e, r_e).ret == nextfix.predict_p3(tr_e, r_e).ret


def test_variants_never_see_their_own_target(monkeypatch):
    pairs = _pairs()
    seen = []

    def spy(train, row, resid_sd=None):
        seen.append((train["d1"].max(), row["d0"]))
        return nextfix.predict_p3_roll60(train, row, resid_sd)

    monkeypatch.setattr(nextfix, "MIN_TRAIN", 40)
    nextfix.update_oos(pairs, [], spy, forward_from="2099-01-01")
    assert seen and all(d1 <= d0 for d1, d0 in seen)


def test_update_variants_writes_a_record_per_variant_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(nextfix, "MIN_TRAIN", 40)
    counts = nextfix.update_variants(_pairs(), tmp_path)
    assert set(counts) == {"p3_roll60", "p3_monday"} and all(v > 0 for v in counts.values())

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(nextfix, "predict_p3_roll60", boom)
    counts2 = nextfix.update_variants(_pairs(), tmp_path)  # one variant fails: the other still runs
    assert counts2["p3_monday"] > 0


def _folds(err_scale: float, n: int = 60, seed: int = 1, retro: bool = False) -> list[dict]:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-10-05", periods=n)
    out = []
    for i in range(n):
        y = rng.normal(0, 0.008)
        ret = y + rng.normal(0, err_scale)
        out.append(
            {
                "d0": days[i].strftime("%Y-%m-%d"),
                "pm0": 13500.0,
                "pm1": 13500.0 * math.exp(y),
                "y": y,
                "ret": ret,
                "p_up": 1 / (1 + math.exp(-ret * 400)),
                "retro": retro,
            }
        )
    return out


def test_a_much_better_variant_clearly_beats_the_live_model_only_with_enough_days():
    live = _folds(0.006)
    var = [{**f, "ret": f["y"] + (f["ret"] - f["y"]) * 0.2} for f in live]  # same days, tiny error
    for f, v in zip(live, var, strict=True):
        v["d0"] = f["d0"]
    res = ap.compare_folds(live, var)
    assert res["n"] == 60 and res["clearly_beats"] is True
    short = ap.compare_folds(live[:30], var[:30])
    assert short["clearly_beats"] is False  # n < 40: not scored


def test_identical_forecasts_do_not_beat_and_p3_stays():
    live = _folds(0.004)
    res = ap.summarise(
        live, {"p3_roll60": [dict(f) for f in live], "p3_monday": [dict(f) for f in live]}
    )
    assert not any(r["clearly_beats"] for r in res["results"].values())
    assert res["decision"].startswith("P3 stays")


def test_one_sided_dm_direction():
    rng = np.random.default_rng(0)
    base = np.abs(rng.normal(100, 10, 60))
    assert ap.one_sided_dm_p_better(base * 0.8, base) < 0.01
    assert ap.one_sided_dm_p_better(base * 1.2, base) > 0.99
