# Metrics Infrastructure Design

Phase 3A design document. Records all decisions that shape ml/metrics.py,
data/metrics_history.json, and the UI accuracy card. All decisions are
finalised pending user approval before Phase 3B implementation begins.

---

## Decision 1 — Decision rule: what counts as "the model said wait"?

### Candidates considered

**Rule A: `delta ≤ -100`**
Fire "wait" when the model's point estimate predicts a drop of ≥₹100 from
today's price. `delta = predicted_22k − current_22k`.

**Rule C: `delta < -(current_22k × 0.007)`**
Percentage-based threshold (~0.7% at ₹14,845 = ₹104). Scales with price
level rather than using a fixed nominal.

**Rule B_conservative: `upper_bound < current_22k`**
Fire "wait" only when the p90 upper bound is itself below today's price —
meaning even the model's optimistic tail predicts a drop.

### Edge case analysis

| Scenario | Rule A | Rule C | Rule B_cons |
|---|---|---|---|
| Saturday carry-forward (delta = 0) | neutral ✓ | neutral ✓ | neutral ✓ |
| Small noise drop (delta = −40) | neutral ✓ | neutral ✓ | neutral ✓ |
| Modest drop (delta = −150) | wait ✓ | wait ✓ | probably neutral* |
| Big drop (delta = −600) | wait ✓ | wait ✓ | wait ✓ |
| Wide interval, negative mean (delta=−110, p90=+400) | wait | wait | neutral** |

*At current prices upper_bound ≈ 15,236 >> 14,845; Rule B_cons almost never fires.
**Rule B_cons ignores the model's directional signal when uncertainty is high.

### Analysis

**Rule B_conservative** is unusable in the current regime: our LightGBM CI
spans roughly ±₹300–500, so the p90 bound will nearly always exceed today's
price. Sample size of "wait" decisions approaches zero.

**Rule C** adds complexity for marginal benefit at current price levels.
At ₹14,845 the threshold is ₹104 vs ₹100 — indistinguishable. Reevaluate
if prices double or halve.

**Rule A (`delta ≤ -100`)** maps directly onto the evaluation criterion
("drops ≥₹100 in 5 days"), uses the model's core output, produces enough
signals at typical volatility to build a sample over weeks, and is the most
explainable: "model predicted a drop of at least ₹100."

### Decision: Rule A — `delta ≤ -100` (₹100 fixed threshold)

Neutral band: `−100 < delta < +100`. Predictions in this band are excluded
from decision accuracy but counted for MAE and directional accuracy.

"Buy now" (`delta > +100`) is tracked symmetrically in the schema for future
use, but the primary metric only evaluates "wait" accuracy per the roadmap.

### Deferred revisit note

The ₹100 fixed threshold is appropriate at current price levels (~₹14,000–
16,000 per 10g). **Revisit when gold reaches ₹20,000+ per 10g, or in 5 years
from 2026-05-14, whichever comes first.** At that price level, ₹100 = <0.5%,
which falls below typical noise, and Rule C (percentage-based, ~0.7%) may be
more appropriate. This note exists so future maintainers don't need to
rediscover the threshold selection rationale.

---

## Decision 2 — "Did it pan out?" definition

### Parameters

**N = 5 trading days.** Roadmap-specified. Matches a realistic "I can wait a
week before buying gold" planning horizon. N=1 is too harsh for a next-day
model with ±₹300 CI. N=7 gives weekends for free.

**"Any day in window" minimum.** For a buyer, if the price was ≥₹100 lower
at ANY point in the next 5 trading days, waiting was correct. "Must be lower
at exactly day 5" tests a weaker claim and artificially penalises correct
directional calls that temporarily recover.

**Trading days only (Mon–Fri).** Tanishq explicitly carries forward Friday's
rate through Saturday and Sunday (Phase 1C confirmed). Counting a carry-forward
Saturday as one of N days grants free days of unchanged price — effectively
making N=3 for Mon–Wed decisions. Exclude carry-forward entries.

Implementation: from the decision date, advance through `prices.json`
skipping any entry where the day is Saturday or Sunday AND the price equals
the previous day's price, until N distinct non-carry-forward prices have
been collected.

### Formal definition

```
outcome(decision_date) = "correct" if:
    min(prices on next 5 weekday-non-carry-forward dates) ≤ price(decision_date) − 100
outcome(decision_date) = "incorrect" if:
    all 5 prices > price(decision_date) − 100
outcome(decision_date) = "pending" if:
    fewer than 5 qualifying future dates exist in prices.json yet
```

Only "correct" and "incorrect" outcomes contribute to decision_accuracy.
"Pending" entries are stored but excluded from the denominator until resolved.

---

## Decision 3 — Real-data evaluation track

### Why no bootstrap / retrospective simulation

The original design considered a "retrospective simulation" track: run the
current frozen model on historical real-data dates and evaluate those
predictions. This is rejected for the following reason:

**LightGBM retrains from scratch on every inference run.** It is not a frozen
model loaded from a fixed checkpoint — `ml/inference.py` calls `train.py` (or
equivalent) using all available data before making each day's prediction. This
means the model has already trained on the 31 Phase-1C backfilled dates and
all 58 real readings. Retrospectively "predicting" those same dates measures
memorisation, not generalisation. The look-ahead contamination is total. A
valid bootstrap would require N separate full retrains (one per held-out date),
which is computationally expensive and architecturally out of scope.

### Decision: Live-only track, collecting forward from deploy

Starting from the first inference run after Phase 3B deploys, each daily
prediction is stored in `data/metrics_history.json` as a "pending" entry.
Five trading days later, the weekly backtest resolves the outcome.

The real-data track accumulates one entry per day. The first decision-accuracy
result requires both a "wait" decision AND 5 subsequent trading days — roughly
1–2 weeks from first deploy for the first resolved "wait" outcome (longer if
the model rarely fires "wait").

**UI behaviour until first resolved entry:**
```
Model accuracy
Collecting metrics — first decision-accuracy result in ~5 trading days.
```
MAE and directional accuracy can show earlier (they don't require a "wait"
decision) once at least 5 predictions have been resolved.

**No synthetic-track records in Phase 3.** The existing weekly-backtest.yml
output already serves as a sanity check. Synthetic-track metrics_history.json
entries are reserved for Phase 4+ if a formal backtest database is warranted.

---

## Decision 4 — Schema and UI placement

### metrics_history.json schema

Each entry represents one PREDICTION (one day's inference output). Outcome
is resolved later by the weekly backtest.

```json
{
  "decision_date": "2026-05-14",
  "predicted_at": "2026-05-14T12:04:27Z",
  "current_22k": 14845,
  "predicted_22k": 14965,
  "delta": 120.0,
  "lower": 14791,
  "upper": 15236,
  "decision": "neutral",
  "outcome_window_days": 5,
  "outcome_resolved_at": null,
  "outcome": "pending",
  "min_future_price": null,
  "drop_threshold": 100,
  "track": "real",
  "model_version": "lgbm-only",
  "real_readings_count": 58
}
```

`decision`: `"wait"` | `"neutral"` | `"buy_now"`
`outcome`: `"correct"` | `"incorrect"` | `"pending"`
`track`: `"real"` (synthetic track reserved for future)

**Idempotency:** keyed on `decision_date`. If a record for the same date
already exists, skip insertion. If `outcome` is "pending" and enough future
prices now exist, update `outcome` and `min_future_price` in-place.

**No pre-aggregated summary file.** Aggregate metrics (decision_accuracy, MAE,
directional_accuracy) are computed at read time — by the UI client-side from
the full `metrics_history.json`. A second pre-aggregated file creates two
sources of truth and adds a sync failure mode with no meaningful payoff:
50–100KB is fine on mobile (CDN-cached), and client-side aggregation of ≤500
entries is trivial in JavaScript.

### UI placement and content

**Component:** Small accuracy card, positioned BELOW the forecast section,
ABOVE the history table.

**State: collecting (no resolved entries yet)**
```
Model accuracy · real-data track
Collecting metrics — first decision-accuracy result in ~5 trading days.
```

**State: MAE/directional resolved but no wait decisions yet (n_wait_resolved = 0)**
```
Model accuracy · real-data track
No wait signals yet
Mean error (MAE): ₹148  ·  Direction: 58%  (n=12)
```

**State: at least one wait decision resolved (n_wait_resolved ≥ 1)**
```
Model accuracy · real-data track · last 30 days
Decision accuracy    67%    (n=8 wait signals)
Mean error (MAE)     ₹148
Direction correct    58%    (n=26)
```

**n display rule:** Always show n in parentheses. Decision accuracy shows
`n_wait_resolved`. MAE/directional show `n_all_resolved`. Both cover the
rolling 30-day window.

No trend sparkline in Phase 3. Single rolling-30-day value is sufficient.

---

## Decision 5 — Backtest cadence

**Split responsibility:**

- `check-price.yml` (daily, after inference): append a "pending" entry for
  today's decision to `metrics_history.json`. Cheap — no outcome resolution,
  no price lookups.
- `weekly-backtest.yml` (weekly): resolve all pending entries whose outcome
  window has closed (`decision_date` + 5 trading days ≤ today). Emit resolved
  stats to stdout. Commit updated `metrics_history.json`.

**Why not resolve daily:** walk-forward resolution requires scanning
prices.json for 5 future trading-day entries per pending record, which is
cheap but adds latency to the daily CI step. Weekly is sufficient because
metrics don't change meaningfully day-by-day.

**Why not on-demand only:** metrics must accumulate automatically without
human intervention for the signal to be trustworthy.

---

## Implementation Plan (Phase 3B)

### New file: ml/metrics.py

```python
def compute_decision(delta: float, threshold: float = 100.0) -> str:
    """Map model delta to decision: 'wait' | 'neutral' | 'buy_now'."""

def resolve_outcome(entry: dict, prices_df: pd.DataFrame) -> dict:
    """
    Given a pending entry and the full prices DataFrame, fill in:
    outcome, outcome_resolved_at, min_future_price.
    Returns the updated entry. Skips if already resolved.
    """

def aggregate_metrics(entries: list[dict], window_days: int = 30) -> dict:
    """
    Compute decision_accuracy, MAE, directional_accuracy from resolved entries
    within the last window_days. Returns dict with values and n-counts.
    """

def record_prediction(forecast: dict, prices_df: pd.DataFrame, out_path: Path) -> None:
    """
    Append or update today's pending entry in metrics_history.json.
    Idempotent on decision_date key.
    """

def resolve_pending(out_path: Path, prices_df: pd.DataFrame) -> int:
    """
    Resolve all pending entries whose outcome window has closed.
    Returns count of newly resolved entries.
    """
```

### check-price.yml addition (after Run forecast step)

```yaml
- name: Record prediction metrics
  continue-on-error: true
  run: python -m ml.metrics --record
```

### weekly-backtest.yml addition

```yaml
- name: Resolve metrics outcomes
  continue-on-error: true
  run: python -m ml.metrics --resolve
```

`data/metrics_history.json` added to the bot commit step in both workflows.

### UI changes

- `index.html`: new `<section id="accuracy-section" class="accuracy-section" hidden>` between forecast section and history table
- `app.js`: `renderAccuracy(entries)` function that:
  - Fetches `data/metrics_history.json`
  - Filters to resolved entries in last 30 days
  - Computes decision_accuracy, MAE, directional_accuracy client-side
  - Populates the accuracy card
- `style.css`: accuracy card styles matching existing card design language

### Tests: tests/test_metrics.py

- `test_decision_rule_wait`: delta = -150 → "wait"
- `test_decision_rule_neutral`: delta = -50 → "neutral"
- `test_decision_rule_buy_now`: delta = +150 → "buy_now"
- `test_decision_boundary_exact`: delta = -100 → "wait" (inclusive)
- `test_outcome_correct`: 5 future prices where min ≤ current - 100
- `test_outcome_incorrect`: 5 future prices all > current - 100
- `test_outcome_pending`: fewer than 5 future prices available
- `test_weekend_skipped`: Saturday carry-forward excluded from window
- `test_idempotency`: calling record twice doesn't duplicate entry
- `test_aggregate_decision_accuracy`: known series, verify calculation
- `test_aggregate_mae`: known series, verify calculation
- `test_aggregate_directional`: known series, verify calculation
- `test_aggregate_empty`: no resolved entries returns None values cleanly
- `test_aggregate_no_wait_decisions`: n_wait=0 → decision_accuracy=None
```
