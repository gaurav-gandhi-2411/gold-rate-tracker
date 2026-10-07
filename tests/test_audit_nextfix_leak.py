"""scripts/audit_nextfix_leak.py audits the LIVE P3 record as well as the shadow ensemble."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "audit_nextfix_leak", ROOT / "scripts" / "audit_nextfix_leak.py"
)
assert _spec is not None and _spec.loader is not None
aud = importlib.util.module_from_spec(_spec)
sys.modules["audit_nextfix_leak"] = aud
_spec.loader.exec_module(aud)


def _pairs(n: int = 8) -> pd.DataFrame:
    days = pd.bdate_range("2026-09-01", periods=n)
    d1 = days.to_series().shift(-1).to_numpy()
    return pd.DataFrame({"d0": days, "d1": d1, "pmprev": 13000.0})


def test_report_audits_p3_and_ensemble_separately_and_keeps_the_negative_control() -> None:
    pairs = _pairs()
    ens = [{"d0": "2026-09-04"}, {"d0": "2026-09-05"}, {"d0": "2026-09-08"}]
    p3 = [{"d0": "2026-09-08"}]
    out = aud.build_report(pairs, ens, p3)
    assert out["negative_control_flagged"] is True
    assert out["ensemble_shadow"]["n_folds"] == 3 and out["n_folds"] == 3  # old keys: ensemble
    assert out["p3_live"]["n_folds"] == 1  # reported apart, not merged into the ensemble count
    for rec in (out["ensemble_shadow"], out["p3_live"]):
        assert {"repo_conventions", "legacy_conservative"} <= rec.keys()


def test_main_reads_the_p3_record_path_not_just_the_default() -> None:
    src = (ROOT / "scripts" / "audit_nextfix_leak.py").read_text(encoding="utf-8")
    assert "nf.load_oos(nf.P3_OOS_PATH)" in src
