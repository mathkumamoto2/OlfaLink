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
    if len(earlier) < MIN_BASELINE_ROWS or earlier[-1]["epoch_ms"] - earlier[0]["epoch_ms"] < 58_000:
        raise ValueError("直近30秒より前の基準データが不足しています（約90秒の継続測定が必要）。")
    if not last_30 or last_30[-1]["epoch_ms"]-last_30[0]["epoch_ms"] < 28_000:
        raise ValueError("直近30秒間の測定値が不足しています。")
    phase_rows = ([dict(row, led="baseline") for row in earlier] +
                  [dict(row, led="exposure") for row in last_30])
    return select_30s(phase_rows)


TEMPORAL_WINDOW_MS = 90_000
TEMPORAL_SEGMENT_MS = 30_000
MIN_TEMPORAL_ROWS = 4
MIN_TEMPORAL_SPAN_MS = 20_000


def time_split_metadata(rows: list[dict], end_ms: int) -> dict:
    """Expose the deterministic time slots as *unverified* phase annotations.

    These labels are input compatibility positions, not observed odor events.
    """
    start_ms = end_ms - TEMPORAL_WINDOW_MS
    boundaries = (
        ("baseline", start_ms, start_ms + TEMPORAL_SEGMENT_MS),
        ("exposure", start_ms + TEMPORAL_SEGMENT_MS,
         start_ms + 2 * TEMPORAL_SEGMENT_MS),
        ("refresh", start_ms + 2 * TEMPORAL_SEGMENT_MS, end_ms),
    )
    windows = []
    for index, (label, begin, finish) in enumerate(boundaries):
        count = sum(begin <= int(row["epoch_ms"]) < finish or
                    (index == 2 and int(row["epoch_ms"]) == finish) for row in rows)
        windows.append({"label": label, "start_ms": begin,
                        "end_ms": finish, "sample_count": count})
    return {"method": "time_split_3x30s", "is_ground_truth": False,
            "used_led_signal": False, "windows": windows}


def select_time_split_90s(rows: list[dict]) -> tuple["np.ndarray", int]:
    """Encode 90s of real observations as three *temporal* 30s segments.

    Segment names are compatibility slots required by the existing v0.7
    checkpoint; they are NOT measured baseline/exposure/refresh annotations.
    No LED or odor label is read and no sensor signal is fabricated.

    This enables inference, NOT valid identification without a controlled
    acquisition protocol and new independent real-world validation.
    """
    import numpy as np
    from ml.session_timeseries_v06 import (
        CHANNELS, PHASE_BINS, _schema, _numeric, _median_bins,
    )

    if not rows:
        raise ValueError("AWSから測定値が返っていません。")
    ordered = sorted(rows, key=lambda row: int(row["epoch_ms"]))
    end_ms = int(ordered[-1]["epoch_ms"])
    start_ms = end_ms - TEMPORAL_WINDOW_MS
    # Assign all samples to exactly one interval; the last endpoint is inclusive.
    segments = (
        [r for r in ordered if start_ms <= int(r["epoch_ms"]) < start_ms + 30_000],
        [r for r in ordered if start_ms + 30_000 <= int(r["epoch_ms"]) < start_ms + 60_000],
        [r for r in ordered if start_ms + 60_000 <= int(r["epoch_ms"]) <= end_ms],
    )
    names = ("第1区間", "第2区間", "第3区間")
    for name, segment in zip(names, segments):
        if len(segment) < MIN_TEMPORAL_ROWS:
            raise ValueError(
                f"{name}の測定値が不足しています（{len(segment)}件）。"
                "約90秒の連続測定後に再度解析してください。"
            )
        stamps = [int(row["epoch_ms"]) for row in segment]
        if stamps[-1] - stamps[0] < MIN_TEMPORAL_SPAN_MS:
            raise ValueError(
                f"{name}の測定時間が短すぎます。"
                "約90秒の連続した測定が必要です。"
            )
        if any(b <= a for a, b in zip(stamps, stamps[1:])):
            raise ValueError("測定時刻に重複があります。")

    kind = _schema(ordered)
    # Follow the exact channel ordering, units, relative normalization and
    # phase-bin layout used in the v0.7 checkpoint.
    encoded = []
    for channel in CHANNELS:
        phase_values = []
        for name, segment in zip(names, segments):
            v = np.asarray([_numeric(row, channel, kind) for row in segment], dtype=np.float64)
            good = np.isfinite(v)
            required = max(3, int(np.ceil(len(v) * 0.8)))
            if int(good.sum()) < required:
                raise ValueError(
                    f"{name}の {channel} に有効な測定値が不足しています"
                    f"（{int(good.sum())}/{len(v)}件）。AWS側のセンサー項目を確認してください。"
                )
            # Only fill isolated missing readings by interpolation between
            # actually measured values. No all-missing channel is invented.
            timestamps = np.asarray([int(r["epoch_ms"]) for r in segment], dtype=np.float64)
            cleaned = np.interp(timestamps, timestamps[good], v[good])
            phase_values.append(cleaned)

        first = phase_values[0]
        reference = float(np.median(first[-min(len(first), 60):]))
        if channel not in ("bme_temp_c", "bme_rh_pct") and reference <= 1e-5:
            if channel in ("tgs2602_adc", "tgs2603_adc") and reference == 0.0:
                # Negative ADC codes were clamped to zero by AWS normalization.
                # A relative change from a zero reference is undefined. Encode
                # this unreliable ADC channel as an all-zero *normalized* trace
                # instead of dividing by zero or inventing a positive baseline.
                # The 7-channel model was not trained for a missing ADC channel;
                # treat its final prediction as provisional, not validated.
                transformed = [np.zeros_like(v) for v in phase_values]
            else:
                raise ValueError(f"{channel} の基準値が不正です。")
        elif channel in ("bme_temp_c", "bme_rh_pct"):
            transformed = [np.clip(v - reference, -50, 50) for v in phase_values]
        else:
            transformed = [np.clip((v - reference) / reference, -12, 12) for v in phase_values]
        encoded.append(np.concatenate([
            _median_bins(values, PHASE_BINS[phase])
            for phase, values in zip(("baseline", "exposure", "refresh"), transformed)
        ]))

    readings = np.stack(encoded, axis=1).astype(np.float32)
    masks = np.zeros((128, 3), dtype=np.float32)
    masks[:24, 0] = 1.0
    masks[24:88, 1] = 1.0
    masks[88:, 2] = 1.0
    trace = np.concatenate((readings, masks), axis=1)
    if trace.shape != (128, 10) or not np.isfinite(trace).all():
        raise ValueError("CNNの入力形式を生成できませんでした。")
    return trace, end_ms
