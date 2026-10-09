"""scripts/calibrate_challenger_size_round2.py: frozen procedure constants and the held-out/calibration split."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import calibrate_challenger_size_round2 as r2


def test_round2_follows_the_procedure_pre_registered_in_amendment_5() -> None:
    assert r2.CHALLENGERS == ("ensemble", "p3_monday")
    assert r2.CAL_BLOCKS == (10, 20, 30, 40, 60) and r2.CAL_PHIS == (0.2, 0.3, 0.4, 0.5)
    assert r2.HELD_BLOCK == 25 and r2.HELD_BLOCK_SEED == 11
    assert r2.HELD_STAT == 30 and r2.HELD_STAT_SEED == 12
    assert r2.HELD_PHIS == (0.25, 0.35, 0.45) and r2.HELD_PHI_SEED == 13
    assert float(r2.K_GRID[0]) == 1.5 and float(r2.K_GRID[-1]) == 8.0
    assert abs(r2.ALLOWANCE - 0.05 / 3) < 1e-12


def test_held_out_resamplers_are_not_calibration_resamplers() -> None:
    assert r2.HELD_BLOCK not in r2.CAL_BLOCKS and r2.HELD_STAT != r2.CAL_STATIONARY
    assert not set(r2.HELD_PHIS) & set(r2.CAL_PHIS)
