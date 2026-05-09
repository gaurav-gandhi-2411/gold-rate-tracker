"""
nbeats.py — N-BEATS neural network for gold price delta forecasting.

Architecture: Generic N-BEATS with doubly-residual stacking.
  Input : (batch, LOOKBACK) — per-sequence Z-score-normalised price deltas
  Output: (batch, 1)        — normalised next-day delta prediction

The caller is responsible for normalisation/denormalisation.
See ml/nbeats_infer.py for the inference wrapper that handles this.

Training:  python ml/train_nbeats.py   (requires torch; uses GPU if available)
Inference: ml/nbeats_infer.py          (requires onnxruntime only — no torch)
"""

from __future__ import annotations

# ── Hyper-parameters ─────────────────────────────────────────────────────────
LOOKBACK  = 28    # input window: 28 calendar days of price deltas
N_STACKS  = 1     # number of N-BEATS stacks
N_BLOCKS  = 3     # blocks per stack
HIDDEN    = 32    # FC layer width inside each block
THETA     = 4     # theta (basis coefficient) dimension
DROPOUT   = 0.20  # dropout rate after every hidden layer
BATCH_SIZE = 32   # training mini-batch size
LR        = 5e-4  # initial Adam learning rate
WEIGHT_DECAY = 1e-3
MAX_EPOCHS   = 500
PATIENCE     = 40   # early-stopping patience (epochs)
MIN_STD      = 1.0  # Rs — kept for inference wrapper compatibility


try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False


def _require_torch():
    if not _TORCH_AVAILABLE:
        raise ImportError("torch is required for N-BEATS training. "
                          "Install with: pip install torch")


# ── Model classes ─────────────────────────────────────────────────────────────

class NBeatsBlock(nn.Module):
    """
    Single N-BEATS block.

    FC stack (4 layers) maps the current backcast residual to a hidden
    representation, which is then projected to:
      - backcast: the portion of the residual this block explains
      - forecast: this block's additive contribution to the final prediction
    """
    def __init__(self, lookback: int, hidden: int, theta: int, dropout: float):
        _require_torch()
        super().__init__()
        self.fc = nn.Sequential(
            nn.Linear(lookback, hidden), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden,   hidden), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden,   hidden), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden,   hidden), nn.ReLU(),
        )
        self.theta_b  = nn.Linear(hidden, theta,   bias=False)
        self.theta_f  = nn.Linear(hidden, theta,   bias=False)
        self.basis_b  = nn.Linear(theta,  lookback, bias=False)
        self.basis_f  = nn.Linear(theta,  1,        bias=False)

    def forward(self, x: "torch.Tensor"):
        h         = self.fc(x)
        backcast  = self.basis_b(self.theta_b(h))
        forecast  = self.basis_f(self.theta_f(h))
        return backcast, forecast


class NBeatsStack(nn.Module):
    """Sequence of N-BEATS blocks with doubly-residual connections."""

    def __init__(self, n_blocks: int, lookback: int, hidden: int, theta: int, dropout: float):
        _require_torch()
        super().__init__()
        self.blocks = nn.ModuleList([
            NBeatsBlock(lookback, hidden, theta, dropout)
            for _ in range(n_blocks)
        ])

    def forward(self, x: "torch.Tensor"):
        forecast = torch.zeros(x.shape[0], 1, device=x.device)
        for block in self.blocks:
            backcast, f = block(x)
            x        = x - backcast   # remove explained portion from residual
            forecast = forecast + f
        return x, forecast


class NBeatsNet(nn.Module):
    """
    Full N-BEATS network.

    The `scale` parameter (global std of training deltas, in Rs.) is baked in
    as a buffer and exported to ONNX.  This makes the ONNX model self-contained:
    it accepts raw price deltas and returns a raw delta — no external
    normalisation needed in the inference wrapper.
    """
    def __init__(
        self,
        lookback:  int   = LOOKBACK,
        n_stacks:  int   = N_STACKS,
        n_blocks:  int   = N_BLOCKS,
        hidden:    int   = HIDDEN,
        theta:     int   = THETA,
        dropout:   float = DROPOUT,
        scale:     float = 1.0,   # global delta std (Rs.) — set from training data
    ):
        _require_torch()
        super().__init__()
        self.register_buffer("scale", torch.tensor([scale], dtype=torch.float32))
        self.stacks = nn.ModuleList([
            NBeatsStack(n_blocks, lookback, hidden, theta, dropout)
            for _ in range(n_stacks)
        ])

    def forward(self, x: "torch.Tensor"):
        """
        x: (batch, lookback) raw price deltas (Rs.)
        returns: (batch, 1) raw predicted next-day delta (Rs.)
        """
        x_in = x / self.scale          # normalise by global std
        forecast = torch.zeros(x_in.shape[0], 1, device=x_in.device)
        for stack in self.stacks:
            x_in, f = stack(x_in)
            forecast = forecast + f
        return forecast * self.scale    # denormalise back to Rs.


# ── Data helpers ──────────────────────────────────────────────────────────────

def build_sequences(df: "pd.DataFrame", lookback: int = LOOKBACK):
    """
    Convert a daily price DataFrame into overlapping (X, y) windows.

    X : float32 array (n_sequences, lookback)  — price deltas
    y : float32 array (n_sequences,)           — next-day delta (target)
    """
    import numpy as np
    prices = df["22k"].astype(float).values
    deltas = prices[1:] - prices[:-1]           # n-1 delta values
    X, y = [], []
    for i in range(len(deltas) - lookback):
        X.append(deltas[i : i + lookback])
        y.append(deltas[i + lookback])
    return (
        __import__("numpy").array(X, dtype="float32"),
        __import__("numpy").array(y, dtype="float32"),
    )


def normalize_batch(x: "torch.Tensor"):
    """
    Per-sequence Z-score normalisation.

    Returns (x_norm, mu, sigma) — mu and sigma are per-row scalars
    (shape: batch).  sigma is clamped to MIN_STD (Rs.) to avoid
    division by near-zero for flat sequences.
    """
    mu    = x.mean(dim=1, keepdim=True)
    sigma = x.std(dim=1,  keepdim=True).clamp(min=MIN_STD)
    return (x - mu) / sigma, mu.squeeze(1), sigma.squeeze(1)
