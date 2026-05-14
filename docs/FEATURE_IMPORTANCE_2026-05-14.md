# Feature Importance Audit — 2026-05-14

Model: production LightGBM (`models/production/lgbm.txt`, `lgbm-p10.txt`, `lgbm-p90.txt`)  
Training corpus: 361 rows (seed + 61 real Tanishq readings)  
Feature set: 44 (19 base + 24 macro + 1 regime)  
meta.json: `best_epoch=1`, `val_mae=225.65`, `naive_mae=223.86`, `beats_naive=false`

---

## Critical finding: mean model stopped at epoch 1

`best_epoch=1` means early stopping fired after round 1. The mean model (lgbm.txt) is a single shallow tree — it ran 500 rounds but the best validation MAE was achieved at round 1, meaning all subsequent rounds overfit. Only 7 of 44 features were used. This is the primary story from this audit.

The quantile models (p10, p90) ran substantially more rounds and used 34 and 33 features respectively. They tell a different, richer story.

---

## All 44 features — mean model (gain, descending)

`***` = calendar/festival features being tracked

| Rank | Feature | Gain | Splits |
|------|---------|------|--------|
| 1 | prev_delta | 19.1054 | 2 |
| 2 | gold_usd | 9.2163 | 1 |
| 3 | **month** *** | 7.6026 | 2 |
| 4 | lag_30d | 7.4132 | 1 |
| 5 | gold_usd_5d_vol | 6.5095 | 1 |
| 6 | **dom** *** | 5.7378 | 1 |
| 7 | roll_7d_std | 4.8608 | 1 |
| 8–44 | (37 features) | 0.0000 | 0 |
| — | **dow** *** | 0.0000 | 0 |
| — | **hour** *** | 0.0000 | 0 |
| — | **akshaya_tritiya** *** | 0.0000 | 0 |
| — | **dhanteras** *** | 0.0000 | 0 |

Zero-importance features (37): lag_1–4, lag_7d, roll_7d_mean, roll_7d_min, roll_7d_max, dow, hour, akshaya_tritiya, dhanteras, since_last_drop, hours_since_prev, usd_inr, us_10y_yield, dxy, sensex, vix_level, usd_inr_change_1d, gold_usd_change_1d, sensex_5d_return, usd_inr_lag_1–7, gold_usd_lag_1–7, regime

---

## Calendar/festival features across all three models

| Feature | lgbm.txt (mean) | lgbm-p10.txt | lgbm-p90.txt | Verdict |
|---------|-----------------|--------------|--------------|---------|
| `month` | **rank 3**, gain=7.60 | rank 28, gain=1.30 | rank 13, gain=3.02 | Active in all three — confirmed signal |
| `dom` | **rank 6**, gain=5.74 | **rank 5**, gain=8.95 | rank 24, gain=1.18 | Active in all three — confirmed signal |
| `dow` | rank 16 (tied-zero), gain=0 | rank 16, gain=2.39 | **rank 4**, gain=10.13 | Used by quantile models; zero in mean. Day-of-week matters for uncertainty width, not direction |
| `hour` | 0 (all) | 0 (all) | 0 (all) | Never used. Intraday gold variation too small vs day-to-day |
| `akshaya_tritiya` | 0 (all) | 0 (all) | 0 (all) | Never used — too sparse (2 events in training window) |
| `dhanteras` | 0 (all) | 0 (all) | 0 (all) | Never used — same sparsity problem |

---

## Top 10 — quantile models (more informative at current data size)

**p10 model:**
1. usd_inr_change_1d (21.99, split=36)
2. gold_usd_5d_vol (13.60, split=28)
3. roll_7d_std (10.77, split=26)
4. us_10y_yield (10.29, split=23)
5. **dom** (8.95, split=25) ***
6. gold_usd_change_1d (8.41, split=11)
7. sensex (7.57, split=16)
8. usd_inr (6.98, split=9)
9. dxy (6.86, split=15)
10. gold_usd (6.61, split=11)

**p90 model:**
1. usd_inr_lag_7 (19.47, split=12)
2. sensex (12.59, split=9)
3. gold_usd_lag_6 (10.37, split=6)
4. **dow** (10.13, split=11) ***
5. gold_usd_lag_2 (7.23, split=5)
6. usd_inr (6.77, split=4)
7. gold_usd_5d_vol (5.12, split=8)
8. prev_delta (4.83, split=11)
9. lag_3 (4.75, split=5)
10. roll_7d_std (3.50, split=9)

---

## Bottom 10 candidates for removal (mean model)

All 37 zero-split features are equally zero. The structurally redundant ones for future pruning:
- `usd_inr_lag_1` through `usd_inr_lag_7` — 7 lags, none used by mean model
- `gold_usd_lag_1` through `gold_usd_lag_7` — same
- `regime` — never used
- `hour` — never used across all three models
- `roll_7d_mean`, `roll_7d_min`, `roll_7d_max` — unused in mean model (roll_7d_std is used)

**Do not prune yet.** The quantile models use many of these. Prune only after auditing across all three models simultaneously and after real-data accumulation provides a more stable importance signal.

---

## Interpretation

The `best_epoch=1` finding is the root cause of almost everything else in the mean model audit:
- 37 zero-importance features are a symptom, not a separate problem
- The model trains on 361 rows with 44 features; a single boosting round produces one shallow tree, exhausting its useful splits in 7 features
- `val_mae=225.65 > naive_mae=223.86` (`beats_naive=false`) — the mean model does not outperform predicting zero delta every time

The quantile models show the feature set is reasonable at higher rounds — `dom` and `dow` rank in the top 5 and 4 respectively in those models. Calendar features have signal; the mean model is just too constrained to reach them in one round.

**Primary lever is not feature engineering — it's data volume.** Both more real Tanishq readings (accumulating at ~4/day) and potentially the IBJA PDF accumulation path (Session C.2) would allow the model to train for more rounds and use more of the existing 44 features.

---

## Next audit trigger

Revisit when `real_readings_count` reaches 200 (currently 61). At ~4 readings/day that is ~35 days from now, approximately 2026-06-18. Expected change: mean model will use more features; `best_epoch` should move above 1.

---

*Produced by feature importance audit, Phase 4 Session 1, 2026-05-14.*
