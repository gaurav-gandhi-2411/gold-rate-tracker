"""ml.range_forecast — shadow-only research: "how much could 22K move in the
next N days" (h = 1, 5, 10, 20 trading days), as central 80%/90% intervals on
the log return from day t to day t+h.

SHADOW RESEARCH ONLY. Nothing in this package is imported by, or writes to,
ml/direction/gate.py, app.js, index.html, i18n.js, or any published data
file. See scripts/analysis_range_forecast.py for the .github/workflows/
analysis.yml entry point, and docs/adr (to be added by GG on review) for the
decision record.

Submodules:
    data        -- series loading (proxy/IBJA), driver features, embargo helpers
    metrics     -- Kupiec, Christoffersen, Winkler score, QLIKE, coverage CI
    conformal   -- walk-forward split conformal (CQR) calibration
    baselines   -- historical volatility, historical simulation, EWMA
    garch       -- GARCH(1,1), scipy MLE, normal or Student-t innovations
    har         -- HAR-RV (daily/weekly/monthly realized-variance components), OLS
    quantile_gbm -- LightGBM quantile regression on the M1 driver set
    chronos_model -- Chronos-Bolt-Tiny quantile forecasts on the log-price series
    walkforward -- shared forecast-set dataclass + per-model walk-forward driver
"""

from __future__ import annotations
