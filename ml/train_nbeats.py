"""
train_nbeats.py — Train N-BEATS on combined gold price history and export ONNX.

Usage (from repo root, requires torch):
    python ml/train_nbeats.py            # train + export
    python ml/train_nbeats.py --epochs N # override max epochs

Reads:  data/history_seed.json + data/prices.json (via load_combined_history)
Writes: models/production/nbeats.onnx
        models/local/nbeats_meta.json   (training metadata, gitignored)

Normalisation is baked into the ONNX model as a constant buffer (the global
std of training deltas), so the inference wrapper (ml/nbeats_infer.py) passes
raw price deltas directly without any external scaling.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset

from ml.forecast import load_combined_history
from ml.nbeats import (
    BATCH_SIZE,
    DROPOUT,
    HIDDEN,
    LOOKBACK,
    LR,
    MAX_EPOCHS,
    N_BLOCKS,
    N_STACKS,
    PATIENCE,
    THETA,
    WEIGHT_DECAY,
    NBeatsNet,
    build_sequences,
)

PROD_DIR = Path(__file__).parent.parent / "models" / "production"
LOCAL_DIR = Path(__file__).parent.parent / "models" / "local"
ONNX_PATH = PROD_DIR / "nbeats.onnx"
META_PATH = LOCAL_DIR / "nbeats_meta.json"

VAL_FRAC = 0.15


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------


def _split(X: np.ndarray, y: np.ndarray, device):
    n_val = max(4, int(len(X) * VAL_FRAC))
    n_train = len(X) - n_val
    X_t = torch.from_numpy(X[:n_train]).to(device)
    y_t = torch.from_numpy(y[:n_train]).to(device)
    X_v = torch.from_numpy(X[n_train:]).to(device)
    y_v = torch.from_numpy(y[n_train:]).to(device)
    return (
        DataLoader(TensorDataset(X_t, y_t), batch_size=BATCH_SIZE, shuffle=True),
        X_v,
        y_v,
        n_train,
        n_val,
    )


def train(max_epochs: int = MAX_EPOCHS) -> tuple:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    if device.type == "cuda":
        print(f"  GPU: {torch.cuda.get_device_name(0)}")

    df = load_combined_history()
    X, y = build_sequences(df, lookback=LOOKBACK)
    global_std = max(float(np.std(y)), 1.0)  # Rs. scale — baked into model

    print(
        f"Sequences : {len(X)} total  "
        f"({int(len(X)*(1-VAL_FRAC))} train / {int(len(X)*VAL_FRAC)} val)  "
        f"lookback={LOOKBACK}"
    )
    print(f"Delta std : Rs.{global_std:.1f}  (normalization scale)")

    train_dl, X_v, y_v, n_train, n_val = _split(X, y, device)

    model = NBeatsNet(
        lookback=LOOKBACK,
        n_stacks=N_STACKS,
        n_blocks=N_BLOCKS,
        hidden=HIDDEN,
        theta=THETA,
        dropout=DROPOUT,
        scale=global_std,
    ).to(device)
    n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Parameters: {n_params:,}")

    optimizer = torch.optim.Adam(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer,
        patience=15,
        factor=0.5,
        min_lr=5e-6,
    )

    best_val_mae = float("inf")
    best_state = None
    best_epoch = 0
    no_improve = 0
    t0 = time.time()
    scale_val = model.scale.item()

    for epoch in range(1, max_epochs + 1):
        model.train()
        epoch_loss = 0.0
        for xb, yb in train_dl:
            # model normalises internally; loss is in normalised space for stability
            pred = model(xb).squeeze(1)
            loss = F.mse_loss(pred / scale_val, yb / scale_val)
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            epoch_loss += loss.item()

        model.eval()
        with torch.no_grad():
            pred_v = model(X_v).squeeze(1)
            val_mae = float((pred_v - y_v).abs().mean())

        scheduler.step(val_mae)

        if val_mae < best_val_mae:
            best_val_mae = val_mae
            best_epoch = epoch
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1

        if epoch % 25 == 0 or epoch == 1:
            lr_now = optimizer.param_groups[0]["lr"]
            print(
                f"  Epoch {epoch:4d}/{max_epochs}  "
                f"train_loss={epoch_loss/len(train_dl):.5f}  "
                f"val_MAE=Rs.{val_mae:.1f}  lr={lr_now:.2e}"
            )

        if no_improve >= PATIENCE:
            print(f"  Early stop at epoch {epoch} " f"(no improvement for {PATIENCE} epochs)")
            break

    elapsed = time.time() - t0
    print(f"\nBest epoch: {best_epoch}  val_MAE=Rs.{best_val_mae:.1f}  " f"({elapsed:.1f}s)")

    model.load_state_dict(best_state)
    meta = {
        "trained_at": datetime.now(UTC).isoformat(),
        "n_train": n_train,
        "n_val": n_val,
        "best_epoch": best_epoch,
        "val_mae": round(best_val_mae, 2),
        "global_std": round(global_std, 2),
        "n_params": n_params,
        "hyperparams": {
            "lookback": LOOKBACK,
            "n_stacks": N_STACKS,
            "n_blocks": N_BLOCKS,
            "hidden": HIDDEN,
            "theta": THETA,
            "dropout": DROPOUT,
            "lr": LR,
            "weight_decay": WEIGHT_DECAY,
            "batch_size": BATCH_SIZE,
        },
    }
    return model.cpu(), meta


# ---------------------------------------------------------------------------
# ONNX export
# ---------------------------------------------------------------------------


def export_onnx(model: NBeatsNet, out_path: Path) -> int:
    import onnx

    out_path.parent.mkdir(parents=True, exist_ok=True)
    model.eval()
    dummy = torch.zeros(1, LOOKBACK, dtype=torch.float32)
    torch.onnx.export(
        model,
        dummy,
        str(out_path),
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
        opset_version=17,
    )
    onnx.checker.check_model(str(out_path))
    return out_path.stat().st_size


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


def smoke_test(model: NBeatsNet) -> float:
    from ml.forecast import load_combined_history

    df = load_combined_history()
    prices = df["22k"].astype(float).values
    deltas = prices[1:] - prices[:-1]
    if len(deltas) < LOOKBACK:
        return float("nan")
    seq = torch.from_numpy(deltas[-LOOKBACK:].astype("float32")).unsqueeze(0)
    model.eval()
    with torch.no_grad():
        return float(model(seq).item())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    max_ep = MAX_EPOCHS
    if "--epochs" in sys.argv:
        idx = sys.argv.index("--epochs")
        max_ep = int(sys.argv[idx + 1])

    print("=" * 60)
    print("N-BEATS training")
    print("=" * 60)

    model, meta = train(max_epochs=max_ep)

    print(f"\nExporting ONNX to {ONNX_PATH}")
    size = export_onnx(model, ONNX_PATH)
    kb = size / 1024
    print(f"Model size: {kb:.1f} KB")
    meta["onnx_path"] = str(ONNX_PATH)
    meta["onnx_bytes"] = size

    delta = smoke_test(model)
    meta["smoke_test_delta"] = round(delta, 1) if not __import__("math").isnan(delta) else None
    print(f"Smoke-test delta (torch): Rs.{delta:+.1f}")

    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"Metadata: {META_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    main()
