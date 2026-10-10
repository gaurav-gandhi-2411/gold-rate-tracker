"""ml.runtime_leak_guard -- the ADR 061 leak guard, run INSIDE the live next-fix path (ADR 073).

ml.nextfix used to rest on structural date filters (``d1 <= d0``, the row at ``d0``) plus an offline
audit (scripts/audit_nextfix_leak.py). This module checks, every time a forecast is built, that
every input used was KNOWN (ml.known_at clocks, the measured USD/INR clock included) before the
moment the forecast is issued. On a violation the caller fails closed to the hold figure; nothing
here raises out of the live path, and a bug in the guard itself counts as a violation (rule 98a:
"couldn't verify" is a deny, never a pass).

Inputs checked, per forecast: the base IBJA fix; the decision-day world move's two closes (gold in
USD and USD/INR, each at the date of the macro row that actually supplied the value, which
ml.nextfix.global_series records in ``source_date``); and every training pair's features and its
target fix. Row inputs are compared with the issue time ``now``; training pairs with the earlier of
``now`` and the decision day's US close (what the offline audit uses).

Not checked (structural only): the rolling basis history behind ``bdev`` and ``x_prev`` (earlier
rows of the same series, known earlier than the checked ones), the label seed's own provenance, and
whether the macro cache's dates are honest -- see ADR 073.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from ml import known_at as ka
from ml.leak_guard import KnownInput, find_leaks, to_utc

logger = logging.getLogger(__name__)

# Published violation entries are aggregated per input name and capped, so a systematic leak (every
# training pair late) cannot bloat forecast.json.
MAX_PUBLISHED = 10
_DAY = pd.Timedelta(days=1)


class KnownCache:
    """Per-call cache of known-at instants (int ns UTC), so a 250-pair training set costs a few
    dozen clock evaluations, not thousands. The USD/INR clock is read when the cache is built, so a
    clock changed before the call (the negative control in the tests) is honoured."""

    def __init__(self) -> None:
        self.usdinr = ka.MACRO_DAILY_CLOCKS["usd_inr"]
        self._cache: dict[tuple[str, int], int] = {}

    def ns(self, kind: str, day_ns: int) -> int:
        key = (kind, day_ns)
        hit = self._cache.get(key)
        if hit is None:
            day = pd.Timestamp(day_ns)
            if kind == "ibja_pm":
                ts = ka.ibja_known_at(day, "pm")
            elif kind == "ibja_am":
                ts = ka.ibja_known_at(day, "am")
            elif kind == "gold_usd":
                ts = ka.comex_daily_known_at(day)
            elif kind == "usd_inr":
                ts = ka.at_utc(day, self.usdinr)
            else:
                raise KeyError(f"no known_at rule for input kind {kind!r}")
            hit = self._cache[key] = int(ts.value)
        return hit


def _day_ns(values: Any, what: str) -> np.ndarray:
    """Dates as int64 ns; a missing date is an error, never an 'early' timestamp."""
    s = pd.Series(pd.to_datetime(values)).reset_index(drop=True)
    if s.isna().any():
        raise ValueError(f"{what} has a missing date; cannot certify when its input was known")
    return s.dt.tz_localize(None).dt.normalize().astype("datetime64[ns]").to_numpy().astype("int64")


class PairClocks:
    """When each input of each row of a ``build_pairs`` frame became known (int64 ns UTC), computed
    once so a record rebuild checks every fold with array slices, not a clock call per input.

    Features: the base IBJA PM of ``d0``, and gold (USD) and USD/INR at the source date of the
    ``d0`` and ``d0 - 1`` world values. Label: the target PM fix (``d1``); rows without one hold
    -1 (never late) and must not be used as training pairs."""

    def __init__(self, pairs: pd.DataFrame, known: KnownCache) -> None:
        d0 = _day_ns(pairs["d0"], "d0")
        g0 = _day_ns(pairs["g0_src"], "g0_src") if "g0_src" in pairs else d0
        gp = _day_ns(pairs["gprev_src"], "gprev_src") if "gprev_src" in pairs else d0 - _DAY.value

        def clock(kind: str, days: np.ndarray) -> np.ndarray:
            uniq, inv = np.unique(days, return_inverse=True)
            return np.array([known.ns(kind, int(u)) for u in uniq], dtype="int64")[inv]

        self._features: list[tuple[str, str, np.ndarray]] = [
            ("ibja_pm(D)", "ibja_pm", clock("ibja_pm", d0)),
            ("gold_usd(D)", "gold_usd", clock("gold_usd", g0)),
            ("usd_inr(D)", "usd_inr", clock("usd_inr", g0)),
            ("gold_usd(D-1)", "gold_usd", clock("gold_usd", gp)),
            ("usd_inr(D-1)", "usd_inr", clock("usd_inr", gp)),
        ]
        has_label = (
            pairs["d1"].notna().to_numpy() if "d1" in pairs else np.zeros(len(pairs), dtype=bool)
        )
        label = np.full(len(pairs), -1, dtype="int64")
        if has_label.any():
            label[has_label] = clock("ibja_pm", _day_ns(pairs["d1"][has_label], "d1"))
        self._label = ("label ibja_pm(D1)", "ibja_pm", label)

    def cols(self, label: bool) -> list[tuple[str, str, np.ndarray]]:
        return [*self._features, self._label] if label else list(self._features)


class LeakCheck:
    """Accumulates the checks of one run; ``info()`` is the JSON-safe block that gets published."""

    def __init__(self) -> None:
        self.n_inputs = 0
        self.n_violations = 0
        self._by_name: dict[str, dict[str, Any]] = {}

    @property
    def violations(self) -> list[dict[str, Any]]:
        return list(self._by_name.values())

    def info(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "checked": True,
            "n_inputs": self.n_inputs,
            "violations": self.violations[:MAX_PUBLISHED],
        }
        if len(self._by_name) > MAX_PUBLISHED:
            out["n_violations"] = self.n_violations
        return out

    def absorb_violations(self, other: LeakCheck) -> None:
        """Take over ``other``'s violations but not its input count: the count published with a
        forecast is that forecast's own inputs, the same whether or not the track record was
        rebuilt this run (a rebuild checks thousands of historical pairs)."""
        self.n_violations += other.n_violations
        for name, entry in other._by_name.items():
            if name in self._by_name:
                self._by_name[name]["count"] += entry["count"]
            else:
                self._by_name[name] = entry

    # -- recording --------------------------------------------------------------------------

    def _record(self, name: str, kind: str, known_ns: int, moment: pd.Timestamp, ctx: str) -> None:
        self.n_violations += 1
        if name in self._by_name:
            self._by_name[name]["count"] += 1
            return
        inp = KnownInput(name, kind, pd.Timestamp(known_ns, tz="UTC"))
        viol = find_leaks(moment, [inp])[0]
        entry = {
            "input": name,
            "source": kind,
            "known_at": inp.known_at.isoformat(),
            "issue_time": moment.isoformat(),
            "late_by_s": int(viol.late_by_s),
            "context": ctx,
            "count": 1,
        }
        self._by_name[name] = entry
        logger.error(
            "leak_guard: input %s [%s] known_at %s is not before the issue time %s (+%ds, %s); "
            "failing closed to the hold figure",
            name,
            kind,
            entry["known_at"],
            entry["issue_time"],
            entry["late_by_s"],
            ctx,
        )

    def guard_error(self, exc: Exception, moment: Any) -> None:
        """The guard itself failed: that is a deny (rule 98a), recorded as a violation."""
        self.n_violations += 1
        logger.error("leak_guard: the guard could not certify the inputs (%r); failing closed", exc)
        self._by_name.setdefault(
            "guard_error",
            {
                "input": "guard_error",
                "source": "guard",
                "known_at": None,
                "issue_time": str(moment),
                "late_by_s": None,
                "context": type(exc).__name__,
                "count": 1,
            },
        )

    # -- checks -----------------------------------------------------------------------------

    def check_base(self, now: datetime, kind: str, day: Any) -> bool:
        """The IBJA fix a hold forecast is anchored on (kind "am" or "pm"). True when clean."""
        try:
            moment = to_utc(now, what="issue time")
            known = KnownCache()
            d = _day_ns([day], "base fix date")
            late = known.ns(f"ibja_{kind}", int(d[0]))
            self.n_inputs += 1
            if late >= moment.value:
                self._record(f"ibja_{kind}(base)", f"ibja_{kind}", late, moment, "base fix")
                return False
        except Exception as exc:  # fail closed: an unverifiable input is a violation
            self.guard_error(exc, now)
            return False
        return True

    def check_pairs(
        self,
        moment: pd.Timestamp,
        pairs: pd.DataFrame,
        known: KnownCache,
        *,
        label: bool,
        context: str,
    ) -> bool:
        """Every input of ``pairs`` (features, and the target fix when ``label``) is known
        strictly before ``moment``. Vectorised over the pairs; True when clean."""
        if pairs.empty:
            return True
        clocks = PairClocks(pairs, known)
        return self._check_cols(moment, clocks.cols(label), None, context)

    def _check_cols(
        self,
        moment: pd.Timestamp,
        cols: list[tuple[str, str, np.ndarray]],
        pos: np.ndarray | None,
        context: str,
    ) -> bool:
        """Compare each input column's known-at (rows ``pos``, all when None) with ``moment``."""
        clean = True
        for name, kind, known_ns in cols:
            vals = known_ns if pos is None else known_ns[pos]
            if name.startswith("label") and (vals < 0).any():
                raise ValueError("a training pair has no target fix; cannot certify it")
            self.n_inputs += len(vals)
            bad = np.flatnonzero(vals >= moment.value)
            if bad.size:
                clean = False
                self.n_violations += int(bad.size) - 1  # _record counts the first itself
                self._record(name, kind, int(vals[bad[0]]), moment, context)
                self._by_name[name]["count"] += int(bad.size) - 1
        return clean

    def check_bar_ends(self, moment: datetime, ends: list[Any], context: str) -> bool:
        """Hourly bars (ADR 066 predictor): a bar is known when it ends, so every bar end must be
        strictly before ``moment``. A missing end (NaT) is a deny, never a pass. True when clean;
        never raises."""
        try:
            t = to_utc(moment, what="decision moment")
            for k, e in enumerate(ends):
                self.n_inputs += 1
                if e is None or pd.isna(e):
                    raise ValueError(f"hourly bar {k} has no end time; cannot certify it")
                known = to_utc(e, what="hourly bar end")
                if known.value >= t.value:
                    self._record(f"hourly_bar_{k}", "hourly_bar", known.value, t, context)
                    return False
        except Exception as exc:  # fail closed: an unverifiable input is a violation
            self.guard_error(exc, moment)
            return False
        return True

    def check_fold(
        self,
        moment: datetime,
        clocks: PairClocks,
        train_pos: np.ndarray,
        row_pos: int,
    ) -> bool:
        """One out-of-sample fold (``update_oos``): the row at ``row_pos`` and the training pairs
        at ``train_pos`` of the pairs ``clocks`` was built from, all against ``moment`` (the
        decision day's US close). True when clean; never raises."""
        try:
            t = to_utc(moment, what="decision moment")
            ok_row = self._check_cols(t, clocks.cols(False), np.array([row_pos]), "fold row")
            ok_train = self._check_cols(t, clocks.cols(True), train_pos, "fold training pairs")
        except Exception as exc:  # fail closed: a guard bug is a deny, never a pass
            self.guard_error(exc, moment)
            return False
        return ok_row and ok_train

    def check_forecast(
        self,
        now: datetime,
        train: pd.DataFrame,
        row: pd.Series,
        d0: Any,
        close: datetime | None = None,
        known: KnownCache | None = None,
    ) -> bool:
        """The live forecast's inputs: the decision-day row against ``now``, the training pairs
        against ``min(now, close)`` (close: the decision day's US close). True when clean; never
        raises."""
        try:
            known = known or KnownCache()
            t_now = to_utc(now, what="issue time")
            t_train = min(t_now, to_utc(close, what="decision moment")) if close else t_now
            one = {c: [row[c]] for c in ("d0", "g0_src", "gprev_src") if c in row.index}
            one.setdefault("d0", [d0])
            ok_row = self.check_pairs(
                t_now, pd.DataFrame(one), known, label=False, context="decision-day row"
            )
            ok_train = self.check_pairs(t_train, train, known, label=True, context="training pairs")
        except Exception as exc:  # fail closed: a guard bug is a deny, never a pass
            self.guard_error(exc, now)
            return False
        return ok_row and ok_train
