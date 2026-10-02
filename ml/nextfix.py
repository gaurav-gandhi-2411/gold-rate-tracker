"""ml.nextfix -- forecast of the next official (IBJA PM) rate from the global move after today's fix.

GG 2026-10-02: replace "assume no change" where the data supports it, narrow the range, and turn
the direction signal on. ADR 064 has the evaluation; this module is the production path.

Why this works where earlier models did not: IBJA fixes its PM rate at ~17:00 IST (11:30 UTC).
Global gold (GC=F) and USD/INR keep trading until the US close (~21:00-22:00 UTC). By then part of
tomorrow's fix is already known: the global price has moved since the fix, and IBJA follows it.
Earlier models (Chronos, the ml/direction harness) only saw IBJA's own history or same-day closes
aligned to the wrong side of the fix, so they had nothing to learn from.

Decision time and leakage. A forecast for the fix after IBJA day D uses only:
  * IBJA PM rates up to and including D;
  * the global value (gold_usd x usd_inr) at the US close of D and of the trading day before D;
  * models trained on pairs whose target fix is on or before D.
It is only produced once the US close of D has happened (``US_CLOSE_UTC``) and before IBJA
publishes a newer AM fix (after that, the shop price already reflects the next day's rate).

Global series dating. ``ml.macro``'s daily cache is dated by trading day: the row dated D is the
close of D, forward-filled over weekends. The committed history seed
(data/history_seed_inr22k_proxy.parquet) is dated one day later (its row dated D is the close of
the trading day BEFORE D; Monday's row is Friday's close) -- verified 2026-10-02 against the
feature store: log-ratio spread 0.0037 when shifted by one day vs 0.0145 unshifted. ``global_series``
handles both.

Models. Point forecast of the next fix's log return: the average of a ridge regression and an
ensemble of five small neural networks (one hidden layer, 16 tanh units). P(up): the average of a
logistic regression and the two point models' implied probabilities. Range: split-conformal on the
model's own out-of-sample errors, scaled by recent volatility (80% target).

Track record. ``data/nextfix_oos.json`` holds one out-of-sample forecast per resolved pair,
computed with training data restricted to before that pair's decision time. Each run appends the
pairs that resolved since the last run. ``evaluate`` scores the record (error vs flat-hold,
direction metrics via ml.direction.evaluate, the ADR 019 gates, range coverage).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
IBJA_PATH = DATA_DIR / "ibja_rates.parquet"
PROXY_PATH = DATA_DIR / "history_seed_inr22k_proxy.parquet"
OOS_PATH = DATA_DIR / "nextfix_oos.json"

MODEL_VERSION = "nextfix_ridge_mlp_v1"
FEATURES = ["x_glob", "x_prev", "bdev"]
MAX_GAP_DAYS = 4  # consecutive IBJA days only (weekends/holidays allowed)
BASIS_WINDOW = 20  # pairs in the rolling mean the basis deviation is measured from
MIN_TRAIN = 60  # pairs before the first out-of-sample forecast
NOMINAL = 0.80
CONFORMAL_WINDOW = 60  # most recent out-of-sample errors used for the range
MIN_CONFORMAL = 20
VOL_HALFLIFE = 10
# The US close of D (GC=F settles 17:00 ET = 21:00 UTC in summer, 22:00 UTC in winter), plus margin.
US_CLOSE_UTC = (22, 15)
IBJA_AM_PUBLISH_UTC = (6, 30)
MLP_MODELS = 5
MLP_EPOCHS = 300


# ── data ─────────────────────────────────────────────────────────────────────────────────────────


def load_ibja(path: Path = IBJA_PATH) -> pd.DataFrame:
    """IBJA rates with a PM fix, in Rs./g of 22K (pm_916 is per 10 g), sorted by date."""
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df = df.dropna(subset=["pm_916"]).sort_values("date").reset_index(drop=True)
    df["pm"] = df["pm_916"] / 10.0
    df["am"] = df["am_916"] / 10.0 if "am_916" in df.columns else np.nan
    return df[["date", "pm", "am"]]


def global_series(macro: pd.DataFrame | None = None, proxy_path: Path = PROXY_PATH) -> pd.Series:
    """Daily global value of gold in rupees, dated by the trading day whose US close it is.

    From ``ml.macro``'s cache (gold_usd x usd_inr) where available; the history seed fills earlier
    dates, shifted back one day and rescaled to the cache's level over their overlap.
    """
    parts: list[pd.Series] = []
    if proxy_path.exists():
        px = pd.read_parquet(proxy_path)["raw_pre_duty"].dropna()
        idx = pd.DatetimeIndex(px.index)
        idx = idx.tz_localize(None) if idx.tz is not None else idx
        px.index = idx.normalize() - pd.Timedelta(days=1)
        parts.append(px.astype(float))
    if macro is not None and {"gold_usd", "usd_inr"} <= set(macro.columns):
        m = (macro["gold_usd"] * macro["usd_inr"]).dropna().astype(float)
        idx = pd.DatetimeIndex(m.index)
        idx = idx.tz_localize(None) if idx.tz is not None else idx
        m.index = idx.normalize()
        m = m[~m.index.duplicated(keep="last")]
        if parts:
            px = parts[0]
            overlap = px.index.intersection(m.index)
            if len(overlap) >= 20:
                scale = float(np.median(m.loc[overlap] / px.loc[overlap]))
                px = px[px.index < m.index.min()] * scale
            else:  # no overlap to rescale against: use the cache alone
                px = px.iloc[0:0]
            parts = [px, m]
        else:
            parts = [m]
    if not parts:
        return pd.Series(dtype=float)
    s = pd.concat(parts).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    # Daily calendar, forward-filled: the value "at date t" is the latest close on or before t.
    full = pd.date_range(s.index.min(), s.index.max(), freq="D")
    return s.reindex(full).ffill()


def build_pairs(ibja: pd.DataFrame, glob: pd.Series) -> pd.DataFrame:
    """One row per IBJA day D: features known at D's US close, and the next fix if it is known.

    The last row (the latest IBJA day) has no target yet; it is the one ``forecast`` predicts.
    """
    rows = []
    dates = list(ibja["date"])
    pm = list(ibja["pm"])
    for i, d0 in enumerate(dates):
        c0, cp = d0, d0 - pd.Timedelta(days=1)
        if c0 not in glob.index or cp not in glob.index:
            continue
        nxt = i + 1 < len(dates) and (dates[i + 1] - d0).days <= MAX_GAP_DAYS
        prev_ok = i >= 1 and (d0 - dates[i - 1]).days <= MAX_GAP_DAYS
        rows.append(
            {
                "d0": d0,
                "d1": dates[i + 1] if nxt else pd.NaT,
                "pm0": pm[i],
                "pm1": pm[i + 1] if nxt else np.nan,
                "g0": float(glob.loc[c0]),
                "gprev": float(glob.loc[cp]),
                "pmprev": pm[i - 1] if prev_ok else np.nan,
                "last": i == len(dates) - 1,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # A gap in the IBJA series (a missing day) has no target and is not the forecast row.
    df = df[df["d1"].notna() | df["last"]].reset_index(drop=True)
    df["y"] = np.log(df["pm1"] / df["pm0"])
    df["x_glob"] = np.log(df["g0"] / df["gprev"])
    df["x_prev"] = pd.Series(np.log(df["pm0"] / df["pmprev"]), index=df.index).fillna(0.0)
    df["basis"] = np.log(df["g0"] / df["pm0"])
    df["bdev"] = df["basis"] - df["basis"].rolling(BASIS_WINDOW, min_periods=5).mean().shift(1)
    return df.dropna(subset=["bdev"]).reset_index(drop=True)


# ── models ───────────────────────────────────────────────────────────────────────────────────────


def _mlp_predict(xtr: np.ndarray, ytr: np.ndarray, xte: np.ndarray) -> np.ndarray:
    import torch

    torch.set_num_threads(1)
    mu, sd = xtr.mean(0), xtr.std(0) + 1e-9
    ys = float(ytr.std()) + 1e-9
    xt = torch.tensor((xtr - mu) / sd, dtype=torch.float32)
    yt = torch.tensor(ytr / ys, dtype=torch.float32)[:, None]
    xe = torch.tensor((xte - mu) / sd, dtype=torch.float32)
    preds = []
    for k in range(MLP_MODELS):
        torch.manual_seed(k)
        net = torch.nn.Sequential(
            torch.nn.Linear(xt.shape[1], 16), torch.nn.Tanh(), torch.nn.Linear(16, 1)
        )
        opt = torch.optim.Adam(net.parameters(), lr=0.01, weight_decay=1e-2)
        for _ in range(MLP_EPOCHS):
            opt.zero_grad()
            loss = torch.nn.functional.huber_loss(net(xt), yt)
            loss.backward()
            opt.step()
        with torch.no_grad():
            preds.append(net(xe).numpy()[:, 0] * ys)
    return np.mean(preds, axis=0)


@dataclass(frozen=True)
class Prediction:
    ret: float  # predicted log return of the next fix
    p_up: float  # P(next fix > this fix)
    vol: float  # recent volatility of daily fix returns (EWMA), known at decision time


def predict(train: pd.DataFrame, row: pd.Series, resid_sd: float | None = None) -> Prediction:
    """Fit on ``train`` (targets known by the decision time) and forecast ``row``."""
    from scipy.stats import norm
    from sklearn.linear_model import LogisticRegression, RidgeCV

    x = train[FEATURES].to_numpy(dtype=float)
    y = train["y"].to_numpy(dtype=float)
    xt = row[FEATURES].to_numpy(dtype=float)[None, :]
    ridge = float(RidgeCV(alphas=np.logspace(-6, 0, 13)).fit(x, y).predict(xt)[0])
    mlp = float(_mlp_predict(x, y, xt)[0])
    ret = (ridge + mlp) / 2.0
    mu, sd = x.mean(0), x.std(0) + 1e-12
    up = (y > 0).astype(int)
    if up.min() == up.max():  # one class only: no logistic fit possible
        p_logit = float(up[0])
    else:
        clf = LogisticRegression(C=1.0).fit((x - mu) / sd, up)
        p_logit = float(clf.predict_proba((xt - mu) / sd)[0, 1])
    s = resid_sd if resid_sd and resid_sd > 0 else float(y.std()) or 0.01
    p_up = (p_logit + float(norm.cdf(ridge / s)) + float(norm.cdf(mlp / s))) / 3.0
    vol = float(np.sqrt((train["y"] ** 2).ewm(halflife=VOL_HALFLIFE).mean().iloc[-1]))
    return Prediction(ret=ret, p_up=p_up, vol=vol)


# ── out-of-sample track record ───────────────────────────────────────────────────────────────────


def _day(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%d")


def load_oos(path: Path = OOS_PATH) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return list(data.get("folds", [])) if isinstance(data, dict) else []


def save_oos(folds: list[dict], path: Path = OOS_PATH) -> None:
    payload = {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "note": "One out-of-sample forecast per IBJA day, trained only on fixes known before it.",
        "folds": sorted(folds, key=lambda f: f["d0"]),
    }
    path.write_text(json.dumps(payload, indent=1) + "\n")


def _resid_sd(folds: list[dict]) -> float | None:
    errs = [f["y"] - f["ret"] for f in folds[-CONFORMAL_WINDOW:]]
    return float(np.std(errs)) if len(errs) >= MIN_CONFORMAL else None


def update_oos(pairs: pd.DataFrame, folds: list[dict]) -> list[dict]:
    """Append an out-of-sample forecast for every resolved pair not yet in ``folds``."""
    done = {f["d0"] for f in folds}
    out = list(folds)
    resolved = pairs[pairs["d1"].notna()].reset_index(drop=True)
    for i in range(MIN_TRAIN, len(resolved)):
        row = resolved.iloc[i]
        key = _day(row["d0"])
        if key in done:
            continue
        train = resolved[resolved["d1"] <= row["d0"]]
        if len(train) < MIN_TRAIN:
            continue
        p = predict(train, row, _resid_sd([f for f in out if f["d0"] < key]))
        out.append(
            {
                "d0": key,
                "d1": _day(row["d1"]),
                "pm0": round(float(row["pm0"]), 2),
                "pm1": round(float(row["pm1"]), 2),
                "y": float(row["y"]),
                "ret": p.ret,
                "p_up": p.p_up,
                "vol": p.vol,
            }
        )
        out.sort(key=lambda f: f["d0"])
    return out


# ── evaluation ───────────────────────────────────────────────────────────────────────────────────


def conformal_q(folds: list[dict]) -> float | None:
    """80% quantile of |error| / volatility over the most recent out-of-sample forecasts."""
    recent = folds[-CONFORMAL_WINDOW:]
    if len(recent) < MIN_CONFORMAL:
        return None
    scores = [
        abs(f["pm1"] - f["pm0"] * math.exp(f["ret"])) / (f["vol"] * f["pm0"])
        for f in recent
        if f["vol"] > 0
    ]
    return float(np.quantile(scores, NOMINAL)) if len(scores) >= MIN_CONFORMAL else None


def evaluate(folds: list[dict]) -> dict:
    """Score the track record: error vs flat-hold, direction metrics and gates, range coverage."""
    from scipy.stats import wilcoxon

    from ml.direction.evaluate import compute_direction_metrics
    from ml.direction.gate import decide_direction_signal, decide_timing_signal

    n = len(folds)
    if n < MIN_CONFORMAL:
        return {"n": n, "ready": False}
    err_model = np.array([abs(f["pm1"] - f["pm0"] * math.exp(f["ret"])) for f in folds])
    err_flat = np.array([abs(f["pm1"] - f["pm0"]) for f in folds])
    y_true = [1 if f["y"] > 0 else 0 for f in folds]
    m = compute_direction_metrics(y_true, [f["p_up"] for f in folds], MODEL_VERSION)
    baseline = {"n_test_folds": n, "logistic_metrics": m}
    # Range coverage, walk-forward: each fold's range uses only the errors before it.
    hits, widths = [], []
    for i in range(MIN_CONFORMAL, n):
        q = conformal_q(folds[:i])
        if q is None:
            continue
        f = folds[i]
        half = q * f["vol"] * f["pm0"]
        pred = f["pm0"] * math.exp(f["ret"])
        hits.append(abs(f["pm1"] - pred) <= half)
        widths.append(2 * half)
    return {
        "n": n,
        "ready": True,
        "first_d0": folds[0]["d0"],
        "last_d0": folds[-1]["d0"],
        "mae_model": round(float(err_model.mean()), 1),
        "mae_flat": round(float(err_flat.mean()), 1),
        "mae_change_pct": round(100.0 * (err_model.mean() / err_flat.mean() - 1.0), 1),
        "wilcoxon_p": float(wilcoxon(err_model, err_flat).pvalue),
        "direction": {k: v for k, v in m.items() if k != "reliability"},
        "direction_gate": decide_direction_signal(baseline),
        "timing_gate": decide_timing_signal(baseline),
        "range_coverage": round(float(np.mean(hits)), 3) if hits else None,
        "range_n": len(hits),
        "range_mean_width": round(float(np.mean(widths)), 1) if widths else None,
        "range_nominal": NOMINAL,
    }


# ── forecast ─────────────────────────────────────────────────────────────────────────────────────


def _at(day: pd.Timestamp, hm: tuple[int, int]) -> datetime:
    return datetime(day.year, day.month, day.day, hm[0], hm[1], tzinfo=UTC)


def forecast(
    ibja: pd.DataFrame,
    glob: pd.Series,
    folds: list[dict],
    now: datetime,
    newer_am: bool = False,
) -> dict:
    """Forecast the next fix after the latest IBJA PM day, or say why not.

    Returns {"active": False, "reason": ...} outside the window where the inputs are known and
    still relevant, and {"active": True, ...} with the next fix's prediction otherwise.
    ``newer_am``: IBJA has published an AM fix for a later day (see ``newer_am_available``); the
    shop price then already reflects the next day's rate, so this forecast no longer applies.
    """
    if ibja.empty:
        return {"active": False, "reason": "no_ibja"}
    d0 = ibja["date"].iloc[-1]
    if now < _at(d0, US_CLOSE_UTC):
        return {"active": False, "reason": "waiting_for_us_close", "d0": _day(d0)}
    if newer_am:
        return {"active": False, "reason": "newer_am_fix_published", "d0": _day(d0)}
    if glob.empty or glob.index.max() < d0:
        return {"active": False, "reason": "global_close_missing", "d0": _day(d0)}
    q = conformal_q(folds)
    if q is None:
        return {"active": False, "reason": "track_record_too_short", "d0": _day(d0)}
    pairs = build_pairs(ibja, glob)
    if pairs.empty or not bool(pairs["last"].iloc[-1]) or pairs["d0"].iloc[-1] != d0:
        return {"active": False, "reason": "features_unavailable", "d0": _day(d0)}
    train = pairs[pairs["d1"].notna() & (pairs["d1"] <= d0)]
    if len(train) < MIN_TRAIN:
        return {"active": False, "reason": "training_set_too_small", "d0": _day(d0)}
    row = pairs.iloc[-1]
    p = predict(train, row, _resid_sd(folds))
    pm0 = float(row["pm0"])
    pred = pm0 * math.exp(p.ret)
    half = q * p.vol * pm0
    return {
        "active": True,
        "model_version": MODEL_VERSION,
        "d0": _day(d0),
        "pm0": round(pm0, 2),
        "pred_pm1": round(pred, 2),
        "half_width_pm": round(half, 2),
        "p_up": round(p.p_up, 4),
        "conformal_q": round(q, 4),
        "vol": round(p.vol, 6),
    }


def newer_am_available(raw_ibja_path: Path, d0: pd.Timestamp) -> bool:
    """True when IBJA has published an AM fix for a day after ``d0``."""
    try:
        raw = pd.read_parquet(raw_ibja_path)
    except (OSError, ValueError):
        return False
    raw["date"] = pd.to_datetime(raw["date"]).dt.tz_localize(None).dt.normalize()
    if "am_916" not in raw.columns:
        return False
    return bool(((raw["date"] > d0) & raw["am_916"].notna()).any())


def to_retail(fc: dict, current_22k: float, slope: float) -> dict:
    """Map the next-fix forecast onto the shop price shown on the site.

    The shop price moves with IBJA by the calibration slope (data/calibration.json), so the
    predicted change in the shop price is ``slope x (predicted fix - today's fix)``, anchored on the
    current shop price; the range scales the same way.
    """
    delta = slope * (fc["pred_pm1"] - fc["pm0"])
    half = slope * fc["half_width_pm"]
    predicted = current_22k + delta
    return {
        "predicted_22k": round(predicted),
        "lower": round(predicted - half),
        "upper": round(predicted + half),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────────


def run(
    now: datetime | None = None,
    macro: pd.DataFrame | None = None,
    data_dir: Path = DATA_DIR,
) -> dict:
    """Update the track record and return {"eval": ..., "forecast": ...} (used by ml.inference)."""
    now = now or datetime.now(UTC)
    ibja_path = data_dir / IBJA_PATH.name
    if not ibja_path.exists():
        return {
            "eval": {"n": 0, "ready": False},
            "forecast": {"active": False, "reason": "no_ibja"},
        }
    ibja = load_ibja(ibja_path)
    glob = global_series(macro, data_dir / PROXY_PATH.name)
    pairs = build_pairs(ibja, glob)
    oos_path = data_dir / OOS_PATH.name
    folds = update_oos(pairs, load_oos(oos_path)) if not pairs.empty else load_oos(oos_path)
    save_oos(folds, oos_path)
    ev = evaluate(folds)
    newer_am = not ibja.empty and newer_am_available(ibja_path, ibja["date"].iloc[-1])
    return {"eval": ev, "forecast": forecast(ibja, glob, folds, now, newer_am=newer_am)}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    macro = None
    try:
        from ml.macro import load_macro_features

        macro = load_macro_features()
    except Exception as exc:  # the history seed alone still works
        logger.warning("macro cache unavailable (%s); using the history seed only", exc)
    out = run(macro=macro)
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
