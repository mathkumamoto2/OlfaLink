"""Research-only session-level 1D CNN for intensity 0..5.

One complete baseline -> exposure -> refresh measurement is ONE sample.
Synthetic augmentations share nine real source sessions. Source-origin holdout
is required; random variant splits are not independent validation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import torch
from torch import nn

from ml.session_timeseries_v06 import CHANNELS, PHASE_BINS, parse_csv, session_trace

MODEL_VERSION = "olfalink-intensity-1dcnn-v0.6-research"
SOURCE_REAL_SESSIONS = 9
FLOORS = np.array([0.10, 0.20, 0.20, 0.02, 0.02, 0.30, 1.0], dtype=np.float32)
PHASE_LENGTHS = tuple(PHASE_BINS.values())
TIMESTEPS = sum(PHASE_LENGTHS)
WARNING = (
    "研究用の暫定推定です。合成時系列は実測9セッションから生成されており、"
    "においの種類と強度が交絡しています。未知の実測データへの精度は未検証です。強度1は未学習です。"
    "人が感じる強度や安全性を保証しません。"
)


def fit_channel_scales(train_traces: np.ndarray) -> np.ndarray:
    """Fit using only training sessions (never diagnostic holdout)."""
    if train_traces.ndim != 3 or train_traces.shape[1:] != (TIMESTEPS, len(CHANNELS) + 3):
        raise ValueError("Unexpected trace array shape")
    scales = np.maximum(np.quantile(np.abs(train_traces[:, :, :len(CHANNELS)]), 0.90, axis=(0, 1)), FLOORS)
    if not np.isfinite(scales).all():
        raise ValueError("Training scales are invalid")
    return scales.astype(np.float32)


def prepare_inputs(traces: np.ndarray, scales: np.ndarray) -> np.ndarray:
    """(N,128,10) -> (N,10,128), scale sensors, retain observed phase masks."""
    arr = np.asarray(traces, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[None, :, :]
    if arr.ndim != 3 or arr.shape[1:] != (TIMESTEPS, len(CHANNELS) + 3):
        raise ValueError("Expected (N, 128, 10) traces")
    scale = np.asarray(scales, dtype=np.float32)
    if scale.shape != (len(CHANNELS),) or not np.isfinite(scale).all() or np.any(scale <= 0):
        raise ValueError("Invalid channel scales")
    if not np.isfinite(arr).all():
        raise ValueError("Non-finite sensor trace")
    readings = np.clip(arr[:, :, :len(CHANNELS)] / scale.reshape(1, 1, -1), -8.0, 8.0)
    markers = arr[:, :, len(CHANNELS):]
    if (not np.isin(markers, [0.0, 1.0]).all()):
        raise ValueError("Invalid phase presence masks")
    merged = np.concatenate((readings, markers), axis=2)
    return np.ascontiguousarray(merged.transpose(0, 2, 1), dtype=np.float32)


class IntensityCNN(nn.Module):
    """Small convolutional regressor with phase-specific temporal pooling."""

    def __init__(self) -> None:
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Conv1d(10, 24, kernel_size=5, padding=2),
            nn.GroupNorm(4, 24), nn.GELU(),
            nn.Conv1d(24, 32, kernel_size=5, padding=2),
            nn.GroupNorm(4, 32), nn.GELU(),
            nn.Conv1d(32, 48, kernel_size=5, padding=2),
            nn.GroupNorm(6, 48), nn.GELU(),
            nn.Conv1d(48, 48, kernel_size=3, padding=1),
            nn.GroupNorm(6, 48), nn.GELU(),
        )
        # Each of 3 phases: mean and max of 48 channels -> 288-dim descriptor.
        self.regressor = nn.Sequential(
            nn.Linear(48 * 2 * 3, 48), nn.GELU(), nn.Dropout(0.10),
            nn.Linear(48, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.ndim != 3 or x.shape[1:] != (10, TIMESTEPS):
            raise ValueError("Expected a batch of full (10,128) session traces")
        encoded = self.encoder(x)
        pooled = []
        offset = 0
        for count in PHASE_LENGTHS:
            segment = encoded[:, :, offset:offset+count]
            pooled.extend((segment.mean(dim=-1), segment.amax(dim=-1)))
            offset += count
        return self.regressor(torch.cat(pooled, dim=1)).squeeze(1)


def save_model(path: Path, model: IntensityCNN, scales: np.ndarray, metadata: dict) -> None:
    """Store only tensor weights and simple metadata; never persist raw CSV rows."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "version": MODEL_VERSION,
        "state_dict": {k: v.detach().cpu() for k, v in model.state_dict().items()},
        "channel_scales": torch.from_numpy(np.asarray(scales, dtype=np.float32).copy()),
        "channels": list(CHANNELS),
        "phase_bins": dict(PHASE_BINS),
        "metadata": metadata,
    }, path)


def load_model(path: Path) -> tuple[IntensityCNN, np.ndarray, dict]:
    """Load a locally generated checkpoint with PyTorch safe weights-only loader."""
    # Do not load checkpoints downloaded from unknown sources without reviewing provenance.
    try:
        bundle = torch.load(Path(path), map_location="cpu", weights_only=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"Could not read CNN checkpoint: {exc}") from exc
    if bundle.get("version") != MODEL_VERSION:
        raise ValueError("Unsupported CNN model version")
    if tuple(bundle.get("channels", [])) != CHANNELS or bundle.get("phase_bins") != PHASE_BINS:
        raise ValueError("Incompatible feature encoding")
    net = IntensityCNN()
    net.load_state_dict(bundle["state_dict"], strict=True)
    net.eval()
    scales = bundle["channel_scales"].detach().cpu().numpy().astype(np.float32)
    if scales.shape != (len(CHANNELS),) or not np.isfinite(scales).all() or (scales <= 0).any():
        raise ValueError("Invalid checkpoint scaling")
    return net, scales, bundle["metadata"]


@torch.inference_mode()
def predict_trace(model: IntensityCNN, scales: np.ndarray, trace: np.ndarray) -> dict:
    x = torch.from_numpy(prepare_inputs(trace, scales))
    v = float(model(x).item())
    if not np.isfinite(v):
        raise ValueError("Model produced a non-finite prediction")
    value = float(np.clip(v, 0.0, 5.0))
    return {
        "predicted_intensity_continuous": round(value, 3),
        "predicted_intensity_0_5": int(np.floor(value + 0.5)),
        "raw_regression_output": round(v, 3),
        "intensity_basis": "research_1dcnn_full_session_regression",
        "model_version": MODEL_VERSION,
        "independent_real_validation": False,
        "trained_real_source_sessions": SOURCE_REAL_SESSIONS,
        "observed_intensity_levels": [0, 2, 3, 4, 5],
        "warning_ja": WARNING,
    }


def predict_rows(model: IntensityCNN, scales: np.ndarray,
                 rows: Sequence[Mapping[str, object]]) -> dict:
    trace, lengths = session_trace(rows)
    result = predict_trace(model, scales, trace)
    result["phase_row_counts"] = lengths
    return result


def predict_csv_text(model: IntensityCNN, scales: np.ndarray, csv_text: str) -> dict:
    return predict_rows(model, scales, parse_csv(csv_text, "inference csv"))
