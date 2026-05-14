# Feature Inventory — 2026-05-15

Generated from production models in `models/production/`.
**Method:** Split counts parsed directly from LightGBM text-format model files (all three models).
Gain importance available for mean model only (best_epoch=1, so only 2 features have gain > 0).
p10 and p90 gain extraction blocked by a native LightGBM crash on this platform when loading large model files; split counts from the model text headers are authoritative for those two.

**Threshold:** `ACTIVE` = split ≥ 1 in any model. `dead_weight` = split = 0 in all models.

---

## Summary

| Category | Total | ACTIVE | dead_weight |
|----------|-------|--------|-------------|
| price-derived | 13 | 13 | 0 |
| macro | 24 | 24 | 0 |
| calendar | 4 | 3 | 1 (hour) |
| festival | 2 | 0 | 2 (both) |
| regime | 1 | 0 | 1 |
| **Total** | **44** | **40** | **4** (hour, akshaya_tritiya, dhanteras, regime) |

---

## Full Table

Sorted by source group, then by total split count (mean + p10 + p90) descending.

| Feature | Source | mean_split | p10_split | p90_split | total_split | category |
|---------|--------|-----------|----------|----------|-------------|----------|
| **PRICE-DERIVED** | | | | | | |
| roll_7d_std | price | 0 | 47 | 11 | 58 | ACTIVE |
| prev_delta | price | 0 | 15 | 5 | 20 | ACTIVE |
| lag_1 | price | 0 | 20 | 7 | 27 | ACTIVE |
| lag_2 | price | 0 | 19 | 6 | 25 | ACTIVE |
| since_last_drop | price | 0 | 12 | 6 | 18 | ACTIVE |
| lag_4 | price | 0 | 6 | 11 | 17 | ACTIVE |
| lag_3 | price | 0 | 6 | 9 | 15 | ACTIVE |
| roll_7d_max | price | 0 | 2 | 7 | 9 | ACTIVE |
| lag_7d | price | 0 | 9 | 2 | 11 | ACTIVE |
| lag_30d | price | 0 | 6 | 1 | 7 | ACTIVE |
| roll_7d_mean | price | 0 | 4 | 1 | 5 | ACTIVE |
| roll_7d_min | price | 0 | 3 | 2 | 5 | ACTIVE |
| hours_since_prev | price | 0 | 1 | 0 | 1 | ACTIVE |
| **MACRO** | | | | | | |
| usd_inr_change_1d | macro | 0 | 63 | 6 | 69 | ACTIVE |
| gold_usd_5d_vol | macro | 0 | 41 | 13 | 54 | ACTIVE |
| vix_level | macro | 0 | 37 | 3 | 40 | ACTIVE |
| gold_usd_change_1d | macro | 0 | 28 | 2 | 30 | ACTIVE |
| sensex | macro | 0 | 22 | 12 | 34 | ACTIVE |
| dxy | macro | 0 | 18 | 3 | 21 | ACTIVE |
| us_10y_yield | macro | 0 | 17 | 4 | 21 | ACTIVE |
| usd_inr | macro | 0 | 9 | 20 | 29 | ACTIVE |
| usd_inr_lag_7 | macro | 0 | 5 | 18 | 23 | ACTIVE |
| usd_inr_lag_1 | macro | 0 | 11 | 2 | 13 | ACTIVE |
| gold_usd | macro | 0 | 13 | 3 | 16 | ACTIVE |
| gold_usd_lag_7 | macro | 1 | 3 | 2 | 6 | ACTIVE |
| usd_inr_lag_2 | macro | 0 | 7 | 6 | 13 | ACTIVE |
| gold_usd_lag_6 | macro | 0 | 1 | 9 | 10 | ACTIVE |
| gold_usd_lag_5 | macro | 0 | 5 | 3 | 8 | ACTIVE |
| usd_inr_lag_6 | macro | 0 | 5 | 3 | 8 | ACTIVE |
| usd_inr_lag_3 | macro | 0 | 5 | 1 | 6 | ACTIVE |
| gold_usd_lag_3 | macro | 0 | 3 | 2 | 5 | ACTIVE |
| usd_inr_lag_5 | macro | 0 | 3 | 1 | 4 | ACTIVE |
| gold_usd_lag_1 | macro | 0 | 9 | 0 | 9 | ACTIVE |
| sensex_5d_return | macro | 3 | 24 | 0 | 27 | ACTIVE |
| usd_inr_lag_4 | macro | 0 | 1 | 2 | 3 | ACTIVE |
| gold_usd_lag_4 | macro | 0 | 0 | 4 | 4 | ACTIVE |
| gold_usd_lag_2 | macro | 0 | 1 | 0 | 1 | ACTIVE |
| **CALENDAR** | | | | | | |
| dom | calendar | 0 | 26 | 0 | 26 | ACTIVE |
| dow | calendar | 0 | 20 | 13 | 33 | ACTIVE |
| month | calendar | 0 | 11 | 3 | 14 | ACTIVE |
| hour | calendar | 0 | 0 | 0 | 0 | dead_weight |
| **FESTIVAL** | | | | | | |
| akshaya_tritiya | festival | 0 | 0 | 0 | 0 | dead_weight |
| dhanteras | festival | 0 | 0 | 0 | 0 | dead_weight |
| **REGIME** | | | | | | |
| regime | regime | 0 | 0 | 0 | 0 | dead_weight |

---

## Findings

### Dead weight (4 features — never split in any model)

| Feature | Source | Diagnosis |
|---------|--------|-----------|
| `hour` | calendar | Training data has one reading per day; no intra-day variation. Can be dropped. |
| `akshaya_tritiya` | festival | Only 1–2 instances in 367 rows; too sparse to pass `min_data_in_leaf=40`. Can be dropped. |
| `dhanteras` | festival | Same as akshaya_tritiya. Can be dropped. |
| `regime` | regime | REGIME_FEATURE_COLS only appended when macro data present, but the macro df is available — investigate why regime is never used. |

### Underperformers worth watching

- `hours_since_prev` (total_split=1, p10 only): marginal signal, may drop with more data.
- `gold_usd_lag_2`, `gold_usd_lag_4`: single model each; questionable added value vs. lag_1–lag_7.

### Mean model note

`best_epoch=1` means the mean model used only **2 features** (sensex_5d_return, gold_usd_lag_7) across 1 tree. This is a data-bound issue (~367 training rows, high noise delta target), not a feature set problem. The quantile models (p10, p90) ran far more iterations and used 39–34 features respectively.

### Re-audit trigger

Run this inventory again when `real_readings_count ≥ 200` (estimated ~2026-07-15 at 4 readings/day).

---

## Action Items

| Priority | Item | Blocking? |
|----------|------|-----------|
| Low | Drop `hour` from FEATURE_COLS | No — wait for re-audit to confirm gain stays at 0 |
| Low | Drop `akshaya_tritiya`, `dhanteras` from FEATURE_COLS and configs | No — same gate |
| Medium | Investigate why `regime` is dead weight | No — but worth a look |
| Deferred | Add Diwali/Gudi Padwa festival flags | After 200-reading gate |
| Deferred | 14d volatility feature | After 200-reading gate |
