"""ml.hourly_beta05 -- ADR 077: the hourly world-price model with beta 0.5, a forward-only shadow challenger.

Registration only. Nothing here reaches the page, forecast.json, the champion file or any record.
``ml.nextfix_intraday`` already logs the beta 0.5 forecast on every shadow entry (``pred["beta_0.5"]``), so
no new data is produced; this module freezes WHICH entries may ever count and WHAT will be asked of them.

Beta 0.5 was picked after seeing the ADR 066 backtest (morning MAE 23.3 against 29.8 for beta 1.0;
afternoon 79.0 against 78.2). Every decision logged at or before ``REGISTERED_FROM`` is therefore
spent for this challenger and can never count toward it.

No effect (MAE change, interval, p-value, error of any kind) is computed here. The look that will
compute them is a separate code PR that must reproduce ``RULE_SHA256`` and be registered in
tests/test_adr077_beta05.py (the ADR 076 effect guard lists every producer of hourly effects).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from ml import nextfix_intraday

CHALLENGER_ID = "hourly_b05"
BETA = 0.5
PRED_KEY = f"beta_{BETA}"
# Strictly later than the merge of the ADR 077 PR (a test checks it is after the ADR 076 cut-off; the
# merge time is checked after the merge and reported). Decisions logged at or before it never count.
REGISTERED_FROM = "2026-10-18T00:00:00Z"

RULE: dict[str, Any] = {
    "version": 1,
    "id": CHALLENGER_ID,
    "beta": BETA,
    "windows": ["after_morning_rate", "after_afternoon_rate"],
    "counts_only_decisions_logged_after": REGISTERED_FROM,
    "never_counts": "every decision logged at or before the registration instant (ADR 066 backtest and shadow days)",
    "look": {
        "first_monday_on_or_after": "2026-12-14",
        "min_resolved_fixes_per_window": 20,
        "min_calendar_days": 28,
        "requires_adr076_look_taken": True,
        "entries_counted_before": "00:00Z of the look Monday",
        "number_of_looks": 1,
    },
    "conditions": {
        "a": "95% cluster-bootstrap CI (seed 42, 10000) of the change in MAE vs holding the fix lies below 0",
        "b": "one-sided Diebold-Mariano p below alpha_per_window",
        "alpha_per_window": 0.025,
        "not_corrected_for": "the ADR 076 test of beta 1.0 on the same period",
    },
    "promotion": {
        "route": "ADR 072 rule v7 against the live champion, nothing else",
        "family": "outside the v7 family (family_size 3) until a new ADR adds it with alpha/4",
        "forward_days_count_from": "the merge of the code PR that gives it a live predictor",
    },
    "protections": "ADR 068 demotion rules and the ADR 073 runtime leak guard apply unchanged once live",
    "final": "no amendment before the look",
}


def rule_hash(rule: dict[str, Any] | None = None) -> str:
    blob = json.dumps(RULE if rule is None else rule, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


RULE_SHA256 = "f41f5fa34a6d8666a53564cb29eeb127b59d1df4561c72e537202ee451015b72"

assert BETA in nextfix_intraday.BETAS  # the shadow log must already carry this forecast


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)


def eligible(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entries logged strictly after the registration instant. Selects; computes no effect."""
    start = _parse(REGISTERED_FROM)
    return [e for e in entries if _parse(str(e["logged_at"])) > start]
