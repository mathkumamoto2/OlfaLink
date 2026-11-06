"""Authenticated, low-latency rolling sensor frames for the research 1D-CNN.

This prototype intentionally holds only RAM state. Cloud Run restarts discard it;
use Firestore or a streaming store before reliability-critical production use.
No fake baseline/recovery is synthesized for missing sensor phases.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import RLock
from typing import Literal
import math
import time

from pydantic import BaseModel, ConfigDict, Field, field_validator

WINDOW_MS = 30_000
FRESH_MS = 120_000
MAX_CLOCK_SKEW_MS = 120_000
MIN_EXPOSURE_ROWS = 8
MIN_BASELINE_ROWS = 16

FIELDS = ("bme_gas_ohm", "bme_t", "bme_rh", "tgs2602_adc", "tgs2603_adc", "sgp_voc", "sgp_nox")


class SensorSample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch_ms: int = Field(ge=1)
    led: Literal["baseline", "exposure", "refresh", "ambient"]
    bme_gas_ohm: float | None = None
    bme_t: float | None = None
    bme_rh: float | None = None
    tgs2602_adc: float | None = None
    tgs2603_adc: float | None = None
    sgp_voc: float | None = None
    sgp_nox: float | None = None

    @field_validator(*FIELDS)
    @classmethod
    def finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("non-finite sensor value")
        return v


class LiveBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$", default="esp1")
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$", default="live")
    samples: list[SensorSample] = Field(min_length=1, max_length=100)


class LiveBuffer:
    """Single active device/session in-memory ring buffer (one Cloud Run instance).

    Rejected out-of-order batches cannot change the buffer.
    No readings or identifiers are publicly returned from status API.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._rows = deque(maxlen=3600)
        self._device_id = ""
        self._session_id = ""
        self._last_stamp: int | None = None

    def append(self, batch: LiveBatch, *, now_ms: int | None = None) -> int:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        stamps = [x.epoch_ms for x in batch.samples]
        if any(b <= a for a, b in zip(stamps, stamps[1:])):
            raise ValueError("batch timestamps must be strictly increasing")
        if stamps[-1] > now + MAX_CLOCK_SKEW_MS or stamps[0] < now - 300_000:
            raise ValueError("sensor timestamp is too far from server time")
        with self._lock:
            new_session = (batch.device_id, batch.session_id) != (self._device_id, self._session_id)
            if not new_session and self._last_stamp is not None and stamps[0] <= self._last_stamp:
                raise ValueError("readings must be newer than previous batch")
            if new_session:
                self._rows.clear()
                self._device_id, self._session_id = batch.device_id, batch.session_id
            self._rows.extend(x.model_dump() for x in batch.samples)
            self._last_stamp = stamps[-1]
        return len(batch.samples)

    def snapshot(self, *, now_ms: int | None = None) -> tuple[list[dict], bool, int | None]:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock:
            if self._last_stamp is None:
                return [], False, None
            fresh = abs(now - self._last_stamp) <= FRESH_MS
            if not fresh:
                return [], False, self._last_stamp
            return list(self._rows), True, self._last_stamp


def select_30s(rows: list[dict]) -> tuple[list[dict], int]:
    """Baseline + last 30 seconds of *exposure*, never fake a refresh segment.

    v0.7 CNN expects phase-aware 128-step traces. The exposure segment is taken
    from a real 30-second interval; baseline is measured beforehand and fed as
    reference. Accuracy on partial sessions remains UNVALIDATED.
    """
    import numpy as np
    from ml.session_timeseries_v06 import (
        CHANNELS, PHASE_BINS, _schema, _clean_phase, _median_bins,
    )

    exposure = [r for r in rows if r["led"] == "exposure"]
    if not exposure:
        raise ValueError("waiting for exposure phase")
    final_ms = exposure[-1]["epoch_ms"]
    first_exposure = exposure[0]["epoch_ms"]
    window = [r for r in exposure if final_ms - WINDOW_MS <= r["epoch_ms"] <= final_ms]
    if len(window) < MIN_EXPOSURE_ROWS or final_ms - window[0]["epoch_ms"] < 28_000:
        raise ValueError("waiting for 30 seconds of exposure")
    baseline = [r for r in rows if r["led"] == "baseline" and r["epoch_ms"] < first_exposure]
    # Closest 60 seconds of baseline before first exposure.
    baseline = [r for r in baseline if first_exposure - 60_000 <= r["epoch_ms"]]
    if len(baseline) < MIN_BASELINE_ROWS:
        raise ValueError("waiting for measured pre-exposure baseline")
    # v0.7 training was on full-session traces. Use EXACTLY its normalized
    # seven channels + three phase masks, without inventing a recovery signal.
    kind = _schema(baseline + window)
    parts = []
    for channel in CHANNELS:
        reference_values = _clean_phase(baseline, channel, kind)
        exposure_values = _clean_phase(window, channel, kind)
        reference = float(np.median(reference_values[-min(60, len(reference_values)):]))
        if channel not in ("bme_temp_c", "bme_rh_pct") and reference <= 1e-5:
            raise ValueError("invalid measured baseline")
        if channel in ("bme_temp_c", "bme_rh_pct"):
            a = np.clip(reference_values-reference, -50, 50)
            b = np.clip(exposure_values-reference, -50, 50)
        else:
            a = np.clip((reference_values-reference)/reference, -12, 12)
            b = np.clip((exposure_values-reference)/reference, -12, 12)
        parts.append(np.concatenate((
            _median_bins(a, PHASE_BINS["baseline"]),
            _median_bins(b, PHASE_BINS["exposure"]),
            np.zeros(PHASE_BINS["refresh"], dtype=np.float64),
        )))
    readings = np.stack(parts, axis=1).astype(np.float32)
    markers = np.zeros((128, 3), dtype=np.float32)
    markers[:24, 0] = 1.0
    markers[24:88, 1] = 1.0
    trace = np.concatenate((readings, markers), axis=1)
    if trace.shape != (128, 10) or not np.isfinite(trace).all():
        raise ValueError("invalid 30s trace")
    return trace, final_ms


def select_ambient_30s(rows: list[dict]) -> tuple[list[dict], int]:
    """Continuous unlabeled samples: real preceding baseline, current 30s exposure.

    This creates *phase assignments*, not measurements. The v0.7 model has not
    been trained on continuously ambient data: report output as experimental.
    """
    if not rows:
        raise ValueError("waiting for ambient readings")
    end_ms = int(rows[-1]["epoch_ms"])
    last_30 = [r for r in rows if end_ms - 30_000 <= r["epoch_ms"] <= end_ms]
    # The previous 60 seconds are physically observed reference measurements.
    earlier = [r for r in rows if end_ms - 90_000 <= r["epoch_ms"] < end_ms - 30_000]
    if len(earlier) < MIN_BASELINE_ROWS:
        raise ValueError("直近30秒より前の基準データが不足しています（約90秒の継続測定が必要）。")
    if not last_30 or last_30[-1]["epoch_ms"]-last_30[0]["epoch_ms"] < 28_000:
        raise ValueError("直近30秒間の測定値が不足しています。")
    phase_rows = ([dict(row, led="baseline") for row in earlier] +
                  [dict(row, led="exposure") for row in last_30])
    return select_30s(phase_rows)
