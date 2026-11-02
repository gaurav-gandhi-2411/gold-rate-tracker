"""calibration_band_coverage.json's n counts scored DAYS; the public copy must say days.

ml.calibration.save_calibration_band_coverage scores one row per date (prices.json grouped by
date, last reading wins; it logs "n=0 scored days"). Until 2026-11-01 how-we-know printed that n as
"n=120 weeks measured", overstating the sample about sevenfold.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_producer_counts_days() -> None:
    src = (ROOT / "ml/calibration.py").read_text(encoding="utf-8")
    assert 'groupby("date").last()' in src
    assert "n=0 scored days" in src


def test_the_band_accuracy_line_calls_n_days_not_weeks() -> None:
    strings = (ROOT / "how-we-know-strings.js").read_text(encoding="utf-8")
    line = re.search(r"methBandAccuracyText:.*", strings)
    assert line, "methBandAccuracyText not found"
    assert "n=${n} days measured" in line.group(0)
    assert "weeks measured" not in strings
