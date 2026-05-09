"""
nbeats_infer.py — ONNX inference wrapper for the trained N-BEATS model.

Requires only onnxruntime (no torch).  The ONNX model embeds its own
normalisation scale (global delta std baked in as a constant buffer during
export), so this wrapper passes raw price deltas directly and receives a
raw delta back — no external scaling needed.

Usage:
    from ml.nbeats_infer import load_nbeats_session, predict_nbeats_delta

    session = load_nbeats_session()          # returns None if model absent
    if session is not None:
        delta = predict_nbeats_delta(session, df)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ml.nbeats import LOOKBACK

ONNX_PATH = Path(__file__).parent.parent / "models" / "production" / "nbeats.onnx"


def load_nbeats_session(path: Path = ONNX_PATH):
    """
    Load the ONNX model and return an onnxruntime.InferenceSession.

    Returns None if the model file does not exist or onnxruntime is not
    installed — forecast.py uses this to skip N-BEATS gracefully.
    """
    if not path.exists():
        return None
    try:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.log_severity_level = 3  # suppress INFO/WARNING noise
        return ort.InferenceSession(
            str(path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
    except Exception as exc:
        print(f"  N-BEATS: could not load ONNX model ({exc})")
        return None


def predict_nbeats_delta(session, df: pd.DataFrame, lookback: int = LOOKBACK) -> float | None:
    """
    Run N-BEATS inference on the last `lookback` price deltas in df.

    The model handles normalisation internally (scale is baked into the ONNX
    graph), so raw price deltas are passed directly.

    Returns the predicted next-day price delta in Rs., or None if the
    history is too short to form a complete input window.
    """
    prices = df["22k"].astype(float).values
    deltas = prices[1:] - prices[:-1]

    if len(deltas) < lookback:
        return None

    x = deltas[-lookback:].reshape(1, -1).astype(np.float32)
    try:
        result = session.run(["output"], {"input": x})
        return float(result[0][0, 0])
    except Exception as exc:
        print(f"  N-BEATS inference error: {exc}")
        return None
