# data/ — JSON schema reference

All files here are written by GitHub Actions and read by the PWA frontend (`app.js`). They are committed to the repo so GitHub Pages can serve them as static JSON.

---

## prices.json

Rolling list of scraped Tanishq gold readings.

```json
[
  {
    "timestamp": "2026-05-09T15:24:10.217Z",
    "22k": 14010,
    "24k": 15284,
    "18k": 11463,
    "source": "https://www.tanishq.co.in/gold-rate.html?lang=en_IN"
  }
]
```

| Field | Type | Description |
|---|---|---|
| `timestamp` | ISO 8601 UTC string | When the scrape ran |
| `22k` | integer | 22K price in ₹/gram |
| `24k` | integer | 24K price in ₹/gram |
| `18k` | integer | 18K price in ₹/gram |
| `source` | string | URL scraped |

Written by: `scraper/update-and-notify.js` (appends each scrape run).

---

## history_seed.json

Bootstrap dataset of ~444 estimated daily readings, generated once by `ml/seed_history.py` from Yahoo Finance (GC=F × INR=X). **Not real Tanishq retail prices** — see calibration note in `README.md`. Same schema as `prices.json`.

Written by: `ml/seed_history.py` (one-time, committed manually).

---

## forecast.json

Latest LightGBM forecast for the next midnight UTC reading.

```json
{
  "predicted_at": "2026-05-09T20:54:52.038125+05:30",
  "target_time":  "2026-05-10T05:30:00+05:30",
  "predicted_22k": 14050,
  "lower": 13943,
  "upper": 14200,
  "model_version": "lgbm-v1-aae7e831",
  "training_rows": 428,
  "real_readings_count": 1,
  "warmup": true
}
```

| Field | Type | Description |
|---|---|---|
| `predicted_at` | ISO 8601 | When the model ran |
| `target_time` | ISO 8601 | Midnight UTC the forecast targets (shown in IST in the PWA) |
| `predicted_22k` | integer | Point estimate for 22K ₹/gram |
| `lower` | integer | 10th-percentile quantile (80% interval lower bound) |
| `upper` | integer | 90th-percentile quantile (80% interval upper bound) |
| `model_version` | string | `lgbm-v1-` + first 8 chars of SHA-1 of the training set |
| `training_rows` | integer | Number of rows the model trained on |
| `real_readings_count` | integer | Number of live (non-seed) readings used |
| `warmup` | boolean | `true` when `real_readings_count < 56` (~14 days × 4/day) |

Written by: `ml/forecast.py` (overwrites on each scrape run).

---

## backtest.json

Walk-forward backtest results from the last weekly run. Contains model vs. naive baseline metrics and per-fold predictions.

```json
{
  "generated_at": "2026-05-09T20:54:16.154653+05:30",
  "backtest_days": 90,
  "folds": 58,
  "model":    { "mae": 283.4, "mape": 1.87, "direction_acc": 0.483 },
  "baseline": { "mae": 204.3, "mape": 1.35, "direction_acc": 0.0 },
  "predictions": [
    { "ts": "2026-02-09T00:00:00Z", "actual": 15403, "predicted": 15754, "baseline": 15507 }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `generated_at` | ISO 8601 | When the backtest ran |
| `backtest_days` | integer | How many trailing days were used as the test window |
| `folds` | integer | Number of walk-forward folds evaluated |
| `model.mae` | float | Mean absolute error (₹/gram) |
| `model.mape` | float | Mean absolute percentage error |
| `model.direction_acc` | float | Fraction of folds where the model predicted the correct direction of change |
| `baseline.*` | same | Same metrics for the naive baseline (predict last value unchanged) |
| `predictions` | array | Per-fold: timestamp, actual price, model prediction, baseline prediction |

Written by: `ml/backtest.py` (overwrites on each weekly Actions run).

---

## commentary.json

Rolling list of LLM-generated market notes (capped at 30 entries).

```json
[
  {
    "ts": "2026-05-09T15:27:57Z",
    "text": "Only 1 real reading collected so far; trend metrics will be available after ~14 days of data. The current 22K gold price is Rs.14010.",
    "model": "llama-3.3-70b-versatile",
    "prompt_hash": "72e4a2352490"
  }
]
```

| Field | Type | Description |
|---|---|---|
| `ts` | ISO 8601 UTC | When the commentary was generated |
| `text` | string | 2–3 sentence plain-English market note |
| `model` | string | Groq model ID used |
| `prompt_hash` | string | First 12 hex chars of SHA-1 of the user-message payload (for deduplication) |

Written by: `ml/commentary.py` (appends on each scrape run; rolls off oldest when > 30 entries).
