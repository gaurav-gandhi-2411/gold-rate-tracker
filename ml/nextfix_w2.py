"""ADR 074 W2 ``p3_bandbucket``: a shadow-only range for P3, split by forecast size.

Pre-registered in docs/adr/074-weakness-shadows-preregistration.md (frozen, hash f6a3fe6c...). The
point forecast is P3's. The 80% conformal quantile of ``abs(error) / (vol x pm0)`` is taken separately
over the folds whose forecast size ``abs(ret) / vol`` is above, and at or below, its median over the
most recent CONFORMAL_WINDOW (60) folds; a bucket with fewer than MIN_BUCKET (15) folds in that window
uses the pooled quantile, which is exactly P3's live one. So the range widens only where the model
forecasts large moves.

Shadow only. Nothing here reaches the live forecast or the page: the record is written to its own
encrypted file (ADR 060 registry) and read only by scorers. Forward days count from the merge of the
PR that added this module; the record dates that itself, see ``update``.

Scoring (frozen, not done here): coverage of the nominal 80% with an exact 95% Wilson interval
(adequate if it contains 0.80) and mean width at most 10% over P3's on the same days, with at least
40 forward decision days before any statement, each bucket also reported on its own.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from ml.nextfix import CONFORMAL_WINDOW, DATA_DIR, NOMINAL, conformal_q

logger = logging.getLogger(__name__)

W2_PATH = DATA_DIR / "nextfix_w2_bandbucket_oos.json"
MODEL_VERSION = "nextfix_w2_bandbucket_v1"
MIN_BUCKET = 15  # ADR 074 frozen spec: min_bucket_folds


def _score(f: dict) -> float:
    """The live conformal score: abs(error) in units of the day's volatility x base."""
    return abs(f["pm1"] - f["pm0"] * math.exp(f["ret"])) / (f["vol"] * f["pm0"])


def _size(f: dict) -> float:
    return abs(f["ret"]) / f["vol"]


def band_q(prev: list[dict], fold: dict) -> dict | None:
    """W2's quantile for ``fold`` from the folds before it (``prev``, sorted by d0), or None while
    P3 itself has no range. Returns the bucket, W2's quantile and P3's pooled quantile."""
    pooled = conformal_q(prev)
    if pooled is None:
        return None
    recent = [f for f in prev[-CONFORMAL_WINDOW:] if f["vol"] > 0]
    if fold["vol"] <= 0 or not recent:
        return {"bucket": "pooled_no_size", "q_w2": pooled, "q_p3": pooled}
    median = float(np.median([_size(f) for f in recent]))
    high = _size(fold) > median
    members = [f for f in recent if (_size(f) > median) == high]
    bucket = "high" if high else "low"
    if len(members) < MIN_BUCKET:
        return {"bucket": f"{bucket}_pooled", "q_w2": pooled, "q_p3": pooled}
    q = float(np.quantile([_score(f) for f in members], NOMINAL))
    return {"bucket": bucket, "q_w2": q, "q_p3": pooled}


def _fold(prev: list[dict], f: dict) -> dict | None:
    b = band_q(prev, f)
    if b is None:
        return None
    s = _score(f) if f["vol"] > 0 else math.inf
    scale = f["vol"] * f["pm0"]
    return {
        "d0": f["d0"],
        "d1": f["d1"],
        "pm0": f["pm0"],
        "pm1": f["pm1"],
        "ret": f["ret"],
        "vol": f["vol"],
        "bucket": b["bucket"],
        "q_w2": b["q_w2"],
        "q_p3": b["q_p3"],
        "width_w2": round(2 * b["q_w2"] * scale, 2),
        "width_p3": round(2 * b["q_p3"] * scale, 2),
        "covered_w2": s <= b["q_w2"],
        "covered_p3": s <= b["q_p3"],
    }


def update(p3_folds: list[dict], old: dict | None, now: datetime) -> dict:
    """Append a W2 fold for every live P3 fold not yet recorded whose decision day is after the
    record's ``forward_from``.

    ``forward_from`` is set once, on the first run that finds no record: the day AFTER that run's
    date. The first run is necessarily after the merge, so every recorded day is a forward day
    (ADR 074: "forward days count from the merge of that PR"). Folds P3 marks ``retro`` (a re-run,
    or a forecast never published) are skipped, so W2 is scored only on days P3 was live.
    """
    forward_from = (old or {}).get("forward_from")
    if not isinstance(forward_from, str):
        forward_from = (now.date() + timedelta(days=1)).isoformat()
    date.fromisoformat(forward_from)  # a corrupt date raises here, before anything is written
    have = list((old or {}).get("folds", []))
    done = {f["d0"] for f in have}
    p3 = sorted(p3_folds, key=lambda f: f["d0"])
    for i, f in enumerate(p3):
        if f["d0"] in done or f["d0"] < forward_from or f.get("retro") is not False:
            continue
        w = _fold(p3[:i], f)
        if w is not None:
            have.append(w)
    return {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "forward_from": forward_from,
        "note": "ADR 074 W2 shadow: P3's point forecast with a range split by forecast size.",
        "folds": sorted(have, key=lambda f: f["d0"]),
    }


def run(p3_folds: list[dict], data_dir: Path, now: datetime) -> int:
    """Update the W2 record in ``data_dir``. Returns the number of folds; never touches P3."""
    path = data_dir / W2_PATH.name
    old: Any = None
    if path.exists():
        # An unreadable record raises: overwriting it would reset forward_from and lose the days.
        old = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(old, dict):
            raise ValueError(f"{path.name} is not a JSON object")
    rec = update(p3_folds, old, now)
    from ml.demotion import atomic_write_text

    atomic_write_text(path, json.dumps(rec, indent=1) + "\n")
    n = len(rec["folds"])
    # The fold count only: no prices, and no coverage, which ADR 074 reads only after 40 days.
    logger.info("W2 band shadow record: %d forward folds since %s", n, rec["forward_from"])
    return n
