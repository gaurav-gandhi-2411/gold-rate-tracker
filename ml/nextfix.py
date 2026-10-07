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

Global series. ``ml.macro``'s daily cache (gold_usd x usd_inr), dated by trading day: the row
dated D is the close of D. Before the cache starts, the committed label seed
(data/history_seed_inr22k_label.parquet, ``raw_pre_duty``) fills in; it is dated the same way. The
proxy seed (history_seed_inr22k_proxy.parquet) is NOT used: it is deliberately lagged one day
(ADR 030, a leak control for models that use it as a same-day feature).

All day (v2, GG 2026-10-02). The forecast is for the next IBJA fix, whatever the time:
  * after the US close of D, before IBJA's next AM fix -- the model below, target the next PM fix;
  * after an AM fix, before that day's PM fix -- the AM fix itself (the model added nothing here:
    +2.7% error vs the AM fix, 95% CI [+0.4, +5.3], n=138), target that PM fix;
  * after a PM fix, before the US close -- the PM fix (no edge without hourly prices; that is the
    intraday shadow's job), target the next AM fix.
Each window's range is split-conformal on that window's own walk-forward errors, scaled by recent
volatility (80% target). Direction is shown only in the model window; elsewhere there is no evidence
for it.

Models. LIVE (ADR 069, GG decision D2 2026-10-05): P3, ``pm0 x exp(b x world move)`` with one
slope ``b`` fitted by least squares through the origin on resolved pairs (ADR 067). P(up) =
Phi(forecast / residual spread); range: split-conformal on P3's own out-of-sample errors, scaled by
recent volatility (80% target). SHADOW: the ADR 064 ensemble, the average of a ridge regression and
five small neural networks (one hidden layer, 16 tanh units), P(up) the average of a logistic
regression and the two point models' implied probabilities. It keeps its own record
(``nextfix_oos.json``) and its forecast is returned under ``shadow``; nothing from it is shown.

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
LABEL_PATH = DATA_DIR / "history_seed_inr22k_label.parquet"
OOS_PATH = DATA_DIR / "nextfix_oos.json"  # the ensemble's record: SHADOW since ADR 069
P3_OOS_PATH = DATA_DIR / "nextfix_p3_oos.json"  # the live model's record (ADR 069)
P3_VARIANTS_PATH = DATA_DIR / "nextfix_p3_variants_oos.json"  # ADR 071: shadow slope variants
ROLL_WINDOW = 60  # V1: most recent resolved pairs the slope is fitted on
MONDAY_MIN = 15  # V2: Monday pairs needed before Monday gets its own slope

# Live model (ADR 067/069): one fitted slope on the world move, pm0 x exp(b x move).
MODEL_VERSION = "nextfix_p3_v1"
# The ridge + neural-net ensemble of ADR 064: runs on every cycle in shadow, scored beside P3.
ENSEMBLE_VERSION = "nextfix_ridge_mlp_v1"
# P3 forecasts for decision days from here on were issued live; earlier days in its record are a
# walk-forward re-run on past data (flagged ``retro``) and are never worded as live calls.
# The first decision day whose forecast P3 issues live is the day this merged (2026-10-07), not
# the day GG approved it (2026-10-05, when the ensemble was still live): counting an earlier day
# as forward would present a re-run as a live call.
P3_FORWARD_FROM = "2026-10-07"
STATE_FILE = "model_demotion_state.json"  # ADR 068: sticky demotion state of the live model
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
IBJA_PM_PUBLISH_UTC = (11, 30)
BOOTSTRAP_B = 2000
BOOTSTRAP_BLOCK = 5  # consecutive days resampled together (errors are autocorrelated)
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


def global_series(macro: pd.DataFrame | None = None, label_path: Path = LABEL_PATH) -> pd.Series:
    """Daily global value of gold in rupees, dated by the trading day whose US close it is.

    From ``ml.macro``'s cache (gold_usd x usd_inr) where available; the label seed fills earlier
    dates, rescaled to the cache's level over their overlap so the series has no jump.
    """
    parts: list[pd.Series] = []
    if label_path.exists():
        lb = pd.read_parquet(label_path)["raw_pre_duty"].dropna()
        idx = pd.DatetimeIndex(lb.index)
        idx = idx.tz_localize(None) if idx.tz is not None else idx
        lb.index = idx.normalize()
        parts.append(lb.astype(float))
    if macro is not None and {"gold_usd", "usd_inr"} <= set(macro.columns):
        m = (macro["gold_usd"] * macro["usd_inr"]).dropna().astype(float)
        idx = pd.DatetimeIndex(m.index)
        idx = idx.tz_localize(None) if idx.tz is not None else idx
        m.index = idx.normalize()
        m = m[~m.index.duplicated(keep="last")]
        if parts:
            seed = parts[0]
            overlap = seed.index.intersection(m.index)
            if len(overlap) >= 20:
                scale = float(np.median(m.loc[overlap] / seed.loc[overlap]))
                seed = seed[seed.index < m.index.min()] * scale
            else:  # no overlap to rescale against: use the cache alone
                seed = seed.iloc[0:0]
            parts = [seed, m]
        else:
            parts = [m]
    if not parts:
        return pd.Series(dtype=float)
    s = pd.concat(parts).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    # Daily calendar, forward-filled: the value "at date t" is the latest close on or before t.
    full = pd.date_range(s.index.min(), s.index.max(), freq="D")
    return s.reindex(full).ffill()


def load_ibja_full(path: Path = IBJA_PATH) -> pd.DataFrame:
    """Every IBJA day, AM and PM in Rs./g of 22K (a day may have an AM fix and no PM fix yet)."""
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    df["pm"] = df["pm_916"] / 10.0
    df["am"] = df["am_916"] / 10.0 if "am_916" in df.columns else np.nan
    return df[["date", "am", "pm"]]


def fix_events(full: pd.DataFrame) -> list[tuple[datetime, str, pd.Timestamp, float]]:
    """(publish time UTC, "am"/"pm", date, value) for every fix, oldest first."""
    dates = [pd.Timestamp(d) for d in full["date"]]
    am = full["am"].to_numpy(dtype=float)
    pm = full["pm"].to_numpy(dtype=float)
    out: list[tuple[datetime, str, pd.Timestamp, float]] = []
    for d, a, p in zip(dates, am, pm, strict=True):
        if np.isfinite(a):
            out.append((_at(d, IBJA_AM_PUBLISH_UTC), "am", d, float(a)))
        if np.isfinite(p):
            out.append((_at(d, IBJA_PM_PUBLISH_UTC), "pm", d, float(p)))
    return sorted(out, key=lambda e: e[0])


def ref_fix(full: pd.DataFrame, at: datetime) -> float | None:
    """The latest fix published at or before ``at``: the one a shop price read then reflects."""
    past = [e for e in fix_events(full) if e[0] <= at]
    return past[-1][3] if past else None


def flat_pairs(full: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Walk-forward pairs for the windows that hold the latest fix (no model).

    ``am_to_pm``: an AM fix and the same day's PM fix. ``pm_to_am``: a PM fix and the next IBJA
    day's AM fix (gap <= MAX_GAP_DAYS).
    """
    rows = []
    dates = [pd.Timestamp(d) for d in full["date"]]
    am = full["am"].to_numpy(dtype=float)
    pm = full["pm"].to_numpy(dtype=float)
    for i, d in enumerate(dates):
        if kind == "am_to_pm" and np.isfinite(am[i]) and np.isfinite(pm[i]):
            rows.append({"d0": d, "base": float(am[i]), "target": float(pm[i])})
        elif (
            kind == "pm_to_am"
            and np.isfinite(pm[i])
            and i + 1 < len(dates)
            and np.isfinite(am[i + 1])
            and (dates[i + 1] - d).days <= MAX_GAP_DAYS
        ):
            rows.append({"d0": d, "base": float(pm[i]), "target": float(am[i + 1])})
    df = pd.DataFrame(rows, columns=["d0", "base", "target"])
    df["y"] = np.log(df["target"] / df["base"])
    return df.reset_index(drop=True)


def _ewm_vol(y: np.ndarray) -> np.ndarray:
    """vol[i] = EWMA volatility of y[:i] (known before pair i); vol[len(y)] = including all."""
    v = np.sqrt(pd.Series(np.r_[y, 0.0] ** 2).ewm(halflife=VOL_HALFLIFE).mean().shift(1)).to_numpy(
        copy=True
    )
    v[0] = v[1] if len(v) > 1 and np.isfinite(v[1]) else 0.01
    return v


def flat_record(pairs: pd.DataFrame, since: str | None = None) -> dict:
    """Walk-forward coverage of the volatility-scaled 80% band around the held fix, and the band
    for the next pair. ``since``: score only pairs from this date (the model window's test span)."""
    from ml.metrics import wilson_confidence_interval

    n_all = len(pairs)
    if n_all < MIN_CONFORMAL + 1:
        return {"n": 0, "ready": False}
    y = pairs["y"].to_numpy(dtype=float)
    base = pairs["base"].to_numpy(dtype=float)
    err = (pairs["target"] - pairs["base"]).to_numpy(dtype=float)
    vol = _ewm_vol(y)
    score = np.abs(err) / (vol[:n_all] * base)
    hits, widths = [], []
    first = (
        0 if since is None else int(np.searchsorted(pairs["d0"].to_numpy(), np.datetime64(since)))
    )
    for k in range(max(MIN_CONFORMAL, first), n_all):
        q = float(np.quantile(score[max(0, k - CONFORMAL_WINDOW) : k], NOMINAL))
        half = q * vol[k] * base[k]
        hits.append(abs(err[k]) <= half)
        widths.append(2 * half)
    q_now = float(np.quantile(score[-CONFORMAL_WINDOW:], NOMINAL))
    k, n = int(np.sum(hits)), len(hits)
    ci = [round(v, 3) for v in wilson_confidence_interval(k, n)] if n else None
    return {
        "n": n,
        "ready": n >= MIN_CONFORMAL,
        "first_d0": _day(pairs["d0"].iloc[n_all - n]) if n else None,
        "last_d0": _day(pairs["d0"].iloc[-1]),
        "range_coverage": round(k / n, 3) if n else None,
        "range_coverage_ci95": ci,
        "range_mean_width": round(float(np.mean(widths)), 1) if widths else None,
        "conformal_q": q_now,
        "vol_now": float(vol[n_all]),
    }


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


def predict_p3(train: pd.DataFrame, row: pd.Series, resid_sd: float | None = None) -> Prediction:
    """P3 (ADR 067): the next fix's log return is ``b x`` the world move over the decision day,
    ``b`` least squares through the origin on pairs whose target fix is already known.

    P(up) = Phi(ret / s), s the recent out-of-sample residual spread (the same form the ensemble
    uses for its point models), so the direction gate sees a calibrated-by-construction probability.
    """
    from scipy.stats import norm

    x = train["x_glob"].to_numpy(dtype=float)
    y = train["y"].to_numpy(dtype=float)
    denom = float(x @ x)
    b = float(x @ y / denom) if denom > 0 else 0.0
    ret = b * float(row["x_glob"])
    s = resid_sd if resid_sd and resid_sd > 0 else float(y.std()) or 0.01
    vol = float(np.sqrt((train["y"] ** 2).ewm(halflife=VOL_HALFLIFE).mean().iloc[-1]))
    return Prediction(ret=ret, p_up=float(norm.cdf(ret / s)), vol=vol)


def _p3_from_slope(
    train: pd.DataFrame, row: pd.Series, b: float, resid_sd: float | None
) -> Prediction:
    from scipy.stats import norm

    y = train["y"].to_numpy(dtype=float)
    ret = b * float(row["x_glob"])
    s = resid_sd if resid_sd and resid_sd > 0 else float(y.std()) or 0.01
    vol = float(np.sqrt((train["y"] ** 2).ewm(halflife=VOL_HALFLIFE).mean().iloc[-1]))
    return Prediction(ret=ret, p_up=float(norm.cdf(ret / s)), vol=vol)


def _slope(x: np.ndarray, y: np.ndarray) -> float:
    denom = float(x @ x)
    return float(x @ y / denom) if denom > 0 else 0.0


def predict_p3_roll60(
    train: pd.DataFrame, row: pd.Series, resid_sd: float | None = None
) -> Prediction:
    """ADR 071 V1: P3's slope fitted on the ROLL_WINDOW most recent resolved pairs only."""
    t = train.sort_values("d0").tail(ROLL_WINDOW)
    b = _slope(t["x_glob"].to_numpy(dtype=float), t["y"].to_numpy(dtype=float))
    return _p3_from_slope(train, row, b, resid_sd)


def predict_p3_monday(
    train: pd.DataFrame, row: pd.Series, resid_sd: float | None = None
) -> Prediction:
    """ADR 071 V2: on Monday decisions, a slope fitted on resolved MONDAY pairs only (at least
    MONDAY_MIN of them); every other day, and Mondays before that, exactly P3."""
    t = train
    if pd.Timestamp(row["d0"]).dayofweek == 0:
        mon = train[pd.to_datetime(train["d0"]).dt.dayofweek == 0]
        if len(mon) >= MONDAY_MIN:
            t = mon
    b = _slope(t["x_glob"].to_numpy(dtype=float), t["y"].to_numpy(dtype=float))
    return _p3_from_slope(train, row, b, resid_sd)


VARIANT_PREDICTORS = {"p3_roll60": "predict_p3_roll60", "p3_monday": "predict_p3_monday"}


def update_variants(pairs: pd.DataFrame, data_dir: Path) -> dict[str, int]:
    """Append resolved decision days to the ADR 071 shadow records. Never raises (shadow only)."""
    path = data_dir / P3_VARIANTS_PATH.name
    try:
        data = json.loads(path.read_text())
        old = data.get("variants", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        old = {}
    out: dict[str, list[dict]] = {}
    for name, fn_name in VARIANT_PREDICTORS.items():
        try:
            out[name] = update_oos(
                pairs,
                list(old.get(name, [])),
                globals()[fn_name],
                forward_from=P3_FORWARD_FROM,
            )
        except Exception as exc:
            logger.warning("variant %s failed: %s", name, exc)
            out[name] = list(old.get(name, []))
    payload = {
        "schema_version": 1,
        "note": "ADR 071 shadow variants of P3; one out-of-sample fold per decision day.",
        "variants": {k: sorted(v, key=lambda f: f["d0"]) for k, v in out.items()},
    }
    path.write_text(json.dumps(payload, indent=1) + "\n")
    return {k: len(v) for k, v in out.items()}


# ── out-of-sample track record ───────────────────────────────────────────────────────────────────


def _day(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%d")


def load_oos(path: Path = OOS_PATH) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return list(data.get("folds", [])) if isinstance(data, dict) else []


def save_oos(
    folds: list[dict], path: Path = OOS_PATH, model_version: str = ENSEMBLE_VERSION
) -> None:
    payload = {
        "schema_version": 1,
        "model_version": model_version,
        "note": "One out-of-sample forecast per IBJA day, trained only on fixes known before it.",
        "folds": sorted(folds, key=lambda f: f["d0"]),
    }
    path.write_text(json.dumps(payload, indent=1) + "\n")


def _resid_sd(folds: list[dict]) -> float | None:
    errs = [f["y"] - f["ret"] for f in folds[-CONFORMAL_WINDOW:]]
    return float(np.std(errs)) if len(errs) >= MIN_CONFORMAL else None


def update_oos(
    pairs: pd.DataFrame, folds: list[dict], predictor=None, forward_from: str | None = None
) -> list[dict]:
    """Append an out-of-sample forecast for every resolved pair not yet in ``folds``.

    ``predictor``: ``predict`` (the ensemble) when omitted. ``forward_from``: when given, each new
    fold is flagged ``retro`` if its decision day is earlier (a re-run on past data, not a forecast
    issued live), so scorers can keep forward and retrospective results apart.
    """
    predictor = predictor or predict  # looked up at call time so tests can substitute it
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
        p = predictor(train, row, _resid_sd([f for f in out if f["d0"] < key]))
        fold = {
            "d0": key,
            "d1": _day(row["d1"]),
            "pm0": round(float(row["pm0"]), 2),
            "pm1": round(float(row["pm1"]), 2),
            "y": float(row["y"]),
            "ret": p.ret,
            "p_up": p.p_up,
            "vol": p.vol,
        }
        if forward_from is not None:
            fold["retro"] = key < forward_from
        out.append(fold)
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


def block_bootstrap_ci(n: int, stat, b: int | None = None, seed: int = 0) -> list[float]:
    """95% moving-block bootstrap interval of ``stat(indices)`` (blocks of BOOTSTRAP_BLOCK days)."""
    rng = np.random.default_rng(seed)
    b = b or BOOTSTRAP_B
    k = math.ceil(n / BOOTSTRAP_BLOCK)
    vals = []
    for _ in range(b):
        starts = rng.integers(0, n, size=k)
        idx = (starts[:, None] + np.arange(BOOTSTRAP_BLOCK)[None, :]).ravel()[:n] % n
        vals.append(stat(idx))
    lo, hi = np.nanpercentile(vals, [2.5, 97.5])
    return [float(lo), float(hi)]


def diebold_mariano_p(err_a: np.ndarray, err_b: np.ndarray, lags: int = 4) -> float:
    """Two-sided Diebold-Mariano p-value for equal mean absolute error (Newey-West variance)."""
    from scipy.stats import norm

    d = np.abs(err_a) - np.abs(err_b)
    n = len(d)
    dc = d - d.mean()
    var = float(dc @ dc) / n
    for lag in range(1, lags + 1):
        var += 2.0 * (1.0 - lag / (lags + 1)) * float(dc[lag:] @ dc[:-lag]) / n
    if var <= 0:
        return 1.0
    t = d.mean() / math.sqrt(var / n)
    return float(2.0 * (1.0 - norm.cdf(abs(t))))


def evaluate(folds: list[dict], model_version: str = MODEL_VERSION) -> dict:
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
    m = compute_direction_metrics(y_true, [f["p_up"] for f in folds], model_version)
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
    from ml.metrics import wilson_confidence_interval

    up = np.array(y_true, dtype=bool)
    moved = np.array([f["pm1"] != f["pm0"] for f in folds])
    hit = (np.array([f["p_up"] for f in folds]) > 0.5) == up

    def _imp(ix: np.ndarray) -> float:
        return 100.0 * (err_model[ix].mean() / err_flat[ix].mean() - 1.0)

    def _acc(ix: np.ndarray) -> float:
        sel = moved[ix]
        return float(hit[ix][sel].mean()) if sel.any() else float("nan")

    cov_k, cov_n = int(np.sum(hits)), len(hits)
    cov_ci = wilson_confidence_interval(cov_k, cov_n) if cov_n else None
    return {
        "n": n,
        "n_forward": sum(1 for f in folds if f.get("retro") is False),
        "ready": True,
        "first_d0": folds[0]["d0"],
        "last_d0": folds[-1]["d0"],
        "mae_model": round(float(err_model.mean()), 1),
        "mae_flat": round(float(err_flat.mean()), 1),
        "mae_change_pct": round(100.0 * (err_model.mean() / err_flat.mean() - 1.0), 1),
        "mae_change_ci95": [round(v, 1) for v in block_bootstrap_ci(n, _imp)],
        "dm_p": diebold_mariano_p(err_model, err_flat),
        "wilcoxon_p": float(wilcoxon(err_model, err_flat).pvalue),
        "direction_accuracy_ci95": [round(v, 3) for v in block_bootstrap_ci(n, _acc)],
        "range_coverage_ci95": [round(cov_ci[0], 3), round(cov_ci[1], 3)] if cov_ci else None,
        "direction": {k: v for k, v in m.items() if k != "reliability"},
        "direction_gate": decide_direction_signal(baseline),
        "timing_gate": decide_timing_signal(baseline),
        "range_coverage": round(float(np.mean(hits)), 3) if hits else None,
        "range_n": len(hits),
        "range_hits": [bool(h) for h in hits],  # per fold, oldest first (ml.demotion's range rule)
        "range_mean_width": round(float(np.mean(widths)), 1) if widths else None,
        "range_nominal": NOMINAL,
    }


# ── forecast ─────────────────────────────────────────────────────────────────────────────────────


def _at(day: pd.Timestamp, hm: tuple[int, int]) -> datetime:
    return datetime(day.year, day.month, day.day, hm[0], hm[1], tzinfo=UTC)


def _model_forecast(
    full: pd.DataFrame,
    glob: pd.Series,
    folds: list[dict],
    d0: pd.Timestamp,
    predictor=None,
    model_version: str = MODEL_VERSION,
) -> dict | None:
    """The model window: next PM fix after ``d0`` from the global close of ``d0``, or None."""
    predictor = predictor or predict_p3
    if glob.empty or glob.index.max() < d0:
        return None
    q = conformal_q(folds)
    if q is None:
        return None
    pairs = build_pairs(
        full.dropna(subset=["pm"])[["date", "pm", "am"]].reset_index(drop=True), glob
    )
    if pairs.empty or not bool(pairs["last"].iloc[-1]) or pairs["d0"].iloc[-1] != d0:
        return None
    train = pairs[pairs["d1"].notna() & (pairs["d1"] <= d0)]
    if len(train) < MIN_TRAIN:
        return None
    row = pairs.iloc[-1]
    p = predictor(train, row, _resid_sd(folds))
    base = float(row["pm0"])
    return {
        "mode": "after_us_close",
        "model_version": model_version,
        "base_kind": "pm",
        "base_date": _day(d0),
        "base": round(base, 2),
        "target_kind": "pm",
        "pred": round(base * math.exp(p.ret), 2),
        "half_width": round(q * p.vol * base, 2),
        "p_up": round(p.p_up, 4),
    }


def forecast(
    full: pd.DataFrame,
    glob: pd.Series,
    folds: list[dict],
    now: datetime,
    windows: dict | None = None,
    shadow_folds: list[dict] | None = None,
    demoted: bool = False,
) -> dict:
    """Forecast the next IBJA fix at ``now``, whichever part of the day it is (see module doc).

    ``demoted``: ml.demotion has switched the live model off (ADR 068); the model window then holds
    the latest fix like every other window, so the headline falls back to "hold" and no direction
    is shown.

    ``folds``: the LIVE model's (P3) out-of-sample record. ``shadow_folds``: the ensemble's; when
    given and the model window is open, the ensemble's forecast for the same day is returned under
    ``shadow`` (never headline, never shown). ``windows``: ``flat_record`` results for "am_to_pm"
    and "pm_to_am" (computed if omitted). Returns {"active": False, "reason": ...} only when there
    is no usable fix or track record.
    """
    pm_days = full.dropna(subset=["pm"])
    if pm_days.empty:
        return {"active": False, "reason": "no_ibja"}
    windows = windows or {k: flat_record(flat_pairs(full, k)) for k in ("am_to_pm", "pm_to_am")}
    d_pm = pm_days["date"].iloc[-1]
    am_after = full[(full["date"] > d_pm) & full["am"].notna()]
    if not am_after.empty:  # an AM fix is out and its PM fix is not: hold the AM fix
        rec, kind = windows.get("am_to_pm", {}), "am_to_pm"
        d, base, base_kind, target_kind, mode = (
            am_after["date"].iloc[-1],
            float(am_after["am"].iloc[-1]),
            "am",
            "pm",
            "after_morning_rate",
        )
    else:
        if now >= _at(d_pm, US_CLOSE_UTC) and not demoted:
            fc = _model_forecast(full, glob, folds, d_pm)
            if fc is not None:
                out = {"active": True, **fc}
                if shadow_folds is not None:
                    try:
                        sh = _model_forecast(
                            full, glob, shadow_folds, d_pm, predict, ENSEMBLE_VERSION
                        )
                    except Exception as exc:  # shadow must never take the live forecast down
                        logger.warning("nextfix shadow ensemble failed: %s", exc)
                        sh = None
                    if sh is not None:
                        out["shadow"] = sh
                return out
        rec, kind = windows.get("pm_to_am", {}), "pm_to_am"
        d, base, base_kind, target_kind, mode = (
            d_pm,
            float(pm_days["pm"].iloc[-1]),
            "pm",
            "am",
            "after_afternoon_rate",
        )
    if not rec.get("ready"):
        return {"active": False, "reason": f"{kind}_record_too_short"}
    out = {
        "active": True,
        "mode": mode,
        "model_version": f"hold_latest_fix_{kind}",
        "base_kind": base_kind,
        "base_date": _day(d),
        "base": round(base, 2),
        "target_kind": target_kind,
        "pred": round(base, 2),
        "half_width": round(rec["conformal_q"] * rec["vol_now"] * base, 2),
        "p_up": None,
    }
    if demoted:
        out["demoted"] = True
    return out


def to_retail(fc: dict, current_22k: float, slope: float, ref: float | None = None) -> dict:
    """Map the next-fix forecast onto the shop price shown on the site.

    The shop price moves with IBJA by the calibration slope (data/calibration.json). ``ref`` is the
    fix the current shop price reflects (``ref_fix`` at its read time; the forecast's base fix when
    unknown), so the predicted shop price is ``current + slope x (predicted fix - ref)`` -- a move
    already in the shop price is not counted twice. The range scales by the same slope.
    """
    anchor = fc["base"] if ref is None else ref
    predicted = current_22k + slope * (fc["pred"] - anchor)
    half = slope * fc["half_width"]
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
    """Update the track record; return {"eval", "windows", "forecast", "ibja"} (ml.inference)."""
    now = now or datetime.now(UTC)
    ibja_path = data_dir / IBJA_PATH.name
    if not ibja_path.exists():
        return {
            "eval": {"n": 0, "ready": False},
            "windows": {},
            "forecast": {"active": False, "reason": "no_ibja"},
            "ibja": None,
        }
    full = load_ibja_full(ibja_path)
    glob = global_series(macro, data_dir / LABEL_PATH.name)
    pairs = build_pairs(full.dropna(subset=["pm"]).reset_index(drop=True), glob)
    # Live model: P3 (ADR 069). Its record is cheap (no networks) and rebuilt from the pairs if absent.
    p3_path = data_dir / P3_OOS_PATH.name
    p3_old = load_oos(p3_path)
    folds = (
        update_oos(pairs, p3_old, predict_p3, forward_from=P3_FORWARD_FROM)
        if not pairs.empty
        else p3_old
    )
    save_oos(folds, p3_path, MODEL_VERSION)
    ev = evaluate(folds, MODEL_VERSION)
    if not pairs.empty:
        update_variants(pairs, data_dir)
    # Shadow: the ridge + neural-net ensemble keeps its own record and is scored beside P3.
    oos_path = data_dir / OOS_PATH.name
    try:  # the shadow must never take the live forecast down (ADR 069)
        shadow_folds = (
            update_oos(pairs, load_oos(oos_path)) if not pairs.empty else load_oos(oos_path)
        )
        save_oos(shadow_folds, oos_path, ENSEMBLE_VERSION)
        shadow_ev = evaluate(shadow_folds, ENSEMBLE_VERSION)
    except Exception as exc:
        logger.warning("nextfix shadow ensemble record failed (%s); live P3 unaffected", exc)
        shadow_folds, shadow_ev = load_oos(oos_path), {"n": 0, "ready": False}
    since = ev.get("first_d0") if ev.get("ready") else None
    windows = {k: flat_record(flat_pairs(full, k), since) for k in ("am_to_pm", "pm_to_am")}
    demotion = _demotion(data_dir, folds, ev, now)
    fc = forecast(full, glob, folds, now, windows, shadow_folds, demoted=demotion["demoted"])
    return {
        "eval": ev,
        "shadow_eval": shadow_ev,
        "windows": windows,
        "forecast": fc,
        "demotion": demotion,
        "ibja": full,
    }


def _demotion(data_dir: Path, folds: list[dict], ev: dict, now: datetime) -> dict:
    """Run ADR 068's rules on the live model's record and keep the sticky state file.

    Returns {"demoted", "since", "reasons", "newly_demoted", "checked"}. Never raises: if the rules
    themselves fail the model stays as it is and the failure is logged (a broken monitor must not
    silently switch the live model off, and cannot silently hide a degraded one for more than a run
    because the rules re-run on every cycle).
    """
    from ml import demotion as dm

    path = data_dir / STATE_FILE
    state = dm.load_state(path, MODEL_VERSION)
    try:
        status = dm.demotion_status(folds, ev.get("range_hits") if ev.get("ready") else None)
        state, newly = dm.apply_status(state, status, now.astimezone(UTC).isoformat())
        dm.save_state(path, state)
    except Exception as exc:
        logger.warning("demotion check failed (%s); live model unchanged", exc)
        return {
            "demoted": bool(state.get("demoted")),
            "since": state.get("since"),
            "reasons": state.get("reasons", []),
            "newly_demoted": False,
            "checked": False,
        }
    return {
        "demoted": bool(state["demoted"]),
        "since": state["since"],
        "reasons": state["reasons"],
        "newly_demoted": newly,
        "checked": True,
        "params": status["params"],
        "rules": {k: status[k] for k in ("error", "direction", "range") if k in status},
        "rules_breaching_now": [k for k, v in status["demote"].items() if v],
    }


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
