# ADR 011 — Notification System Design (T1–T5)

**Status:** Accepted 2026-05-19
**Author:** Gaurav Gandhi / CC

---

## Context

The gold-rate-tracker needs a push notification layer that:
- Alerts on model-derived directional signals (T1/T2)
- Alerts on large observed price moves regardless of model output (T3)
- Delivers a weekly digest on Sundays (T4)
- Surfaces model degradation silently otherwise invisible in CI (T5)
- Respects IST quiet hours (22:00–07:00) for T1/T2/T3
- Avoids spam via cooldowns and a combined T1+T2+T3 cap of 3/24h

An earlier module (`ml/daily_summary.py`) covered similar triggers but was designed before the Chronos-Bolt pivot, before walk-forward backtest evidence was available, and before the ADR 012 decision (naive as headline, Chronos as directional companion). Its trigger conditions use different thresholds and do not gate on backtest-validated direction accuracy.

The notification system must also be honest: given the backtest evidence (Chronos 10.4% worse than naive on MAE, but direction accuracy 55.8% and last-30-fold accuracy 63.3%), T1/T2 should fire only when the directional signal is statistically supported — not when Chronos merely has a non-flat forecast. This is a direct consequence of ADR 005 (honest-baseline reporting) and ADR 012 (naive as headline).

---

## Decision

### New module: `ml/notifications.py`

Implements five triggers (T1–T5) evaluated each CI cycle after the Chronos probe completes. Uses `data/chronos_probe.json`, `data/forecast.json`, `data/prices.json`, and `data/backtest.json` as inputs. Persists state across CI runs via GitHub Actions cache (`notification-state-{run_id}`).

### T1/T2 design — directional signal, not level forecast

Per ADR 012, Chronos is retained as a directional companion, not as a headline level forecaster. T1/T2 therefore measure:
- **Chronos lean**: whether `median(p50[h=1..5]) - ibja_last_value` is ≥ 0.5% in the signalled direction
- **7-day momentum**: whether the last 7 calendar days of Tanishq 22K prices trend in the same direction
- **Direction accuracy gate**: rolling last-30-fold direction accuracy ≥ 0.55 (currently 0.633 as of 2026-05-19)

Title language is explicit: "Model and momentum both lean [DOWN/UP] over next 5d". Body is explicit: "This is a directional signal, not a price forecast."

This framing avoids the claim that Chronos predicts level moves accurately (it does not, per backtest), while still surfacing the directional information it does provide.

### State persistence via GitHub Actions cache

`data/notification_state.json` is gitignored (per-machine state; bot commits to track cooldowns would clutter history). Persistence across CI runs uses `actions/cache/restore@v4` + `actions/cache/save@v4` with `run_id`-keyed entries and prefix-match restore. Save is gated on `github.ref_name == 'master'` to prevent PR runs from poisoning production cooldown state.

### Quiet hours

T1/T2/T3 triggered during 22:00–07:00 IST are held in `state.queued` and delivered on the first post-07:00 run if the queued time is ≤12h old. T4 bypasses quiet hours (Sunday 18:00 IST is within business hours). T5 does not queue; it fires at most once per IST calendar day.

### `daily_summary.py` deprecated

`ml/daily_summary.py` is marked deprecated in PR G (this PR) and disabled in `daily-summary.yml`. It is deleted in PR H alongside legacy LightGBM cleanup. The new `ml/notifications.py` provides full coverage of T1–T5.

---

## Consequences

**Positive:**
- Notification system is grounded in the actual backtest results — T1/T2 will not fire when Chronos direction accuracy falls below 0.55
- T3 (large actual move) is model-agnostic: it fires regardless of model state, providing an always-on price alert
- T5 ensures Chronos failures surface as a notification within the same CI cycle they occur
- State persistence design prevents spam across CI runs while surviving cache evictions gracefully (worst case: one duplicate per trigger per eviction)
- Weekly digest (T4) provides regular engagement even during periods when no directional signals fire

**Negative:**
- T1/T2 will be silent until the model's directional accuracy is validated above the gate — currently passing (0.633), but the gate may oscillate as more backtest folds accumulate
- ntfy.sh is a free public push service with no SLA; T4/T5 delivery during ntfy outages is silently dropped (acceptable for portfolio project)
- Quiet-hours queue is held in an Actions cache that expires after 7 days of inactivity — a prolonged CI outage could lose queued alerts

## Alternatives Considered

**Alt 1: Fire T1/T2 unconditionally when Chronos has any non-flat lean.** Rejected: violates ADR 005 (honest-baseline). Chronos MAE is 10.4% worse than naive; signalling forecast confidence without noting this would mislead.

**Alt 2: Remove T1/T2 entirely since Chronos trails naive on MAE.** Rejected: direction accuracy (55.8% average, 63.3% last 30 folds) is the one positive signal from the backtest. Retaining T1/T2 with the direction-accuracy gate is the honest minimum — it uses what Chronos does well and nothing more.

**Alt 3: Keep `daily_summary.py` running in parallel.** Rejected: two overlapping notification modules with different thresholds firing on the same events would create duplicate alerts. Clean cutover in PR G.

**Alt 4: Store notification state in a separate committed file (e.g. `data/notification_state.json` with bot commits).** Rejected: bot commits for every cooldown update would pollute git history with non-data changes. GitHub Actions cache achieves the same persistence without history noise.
