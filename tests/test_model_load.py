"""Regression test for KI-001: LightGBM CRLF corruption on Windows.

See docs/KNOWN_ISSUES.md for full root cause analysis. The .gitattributes
binary attribute prevents git from converting LF→CRLF in model text files,
but this test guards against the corruption being reintroduced.
"""

from __future__ import annotations

import pytest
from pathlib import Path

MODELS_DIR = Path(__file__).parent.parent / "models" / "production"
MODEL_FILES = ["lgbm.txt", "lgbm-p10.txt", "lgbm-p90.txt"]


@pytest.mark.parametrize("model_file", MODEL_FILES)
def test_model_file_has_no_crlf(model_file: str):
    """Model files must use LF-only line endings (CRLF breaks lgb.Booster parser)."""
    path = MODELS_DIR / model_file
    if not path.exists():
        pytest.skip(f"{model_file} not present in this checkout")
    content = path.read_bytes()
    crlf_count = content.count(b"\r\n")
    assert crlf_count == 0, (
        f"{model_file} contains {crlf_count} CRLF sequences. "
        "This indicates git CRLF conversion (core.autocrlf=true). "
        "See docs/KNOWN_ISSUES.md KI-001 for the fix."
    )


@pytest.mark.parametrize("model_file", MODEL_FILES)
def test_model_loads_nonzero_trees(model_file: str):
    """Each production LightGBM model must load and contain at least 1 tree."""
    lgb = pytest.importorskip("lightgbm")
    path = MODELS_DIR / model_file
    if not path.exists():
        pytest.skip(f"{model_file} not present in this checkout")
    # Skip the load test if CRLF is already present — test_model_file_has_no_crlf
    # covers that failure. Loading a CRLF-corrupted model crashes the process (KI-001).
    if path.read_bytes().count(b"\r\n") > 0:
        pytest.skip(f"{model_file} has CRLF — skipping load test to avoid crash (KI-001)")
    booster = lgb.Booster(model_file=str(path))
    assert booster.num_trees() > 0, (
        f"{model_file} loaded 0 trees — likely CRLF corruption (KI-001)"
    )
