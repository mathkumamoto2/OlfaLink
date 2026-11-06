"""Cross-firmware full-session time-series encoder for the OlfaLink research CNN.

One complete CSV is *one* supervised example. Explicitly reject absent exposure:
never invent a phase for a session that did not actually contain it.
Both 40-column and 23-column acquisition schemas map to the same 7 sensors.
"""
from __future__ import annotations
import csv
import io
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime

import numpy as np

CHANNELS = ("bme_gas_kohm", "tgs2602_adc", "tgs2603_adc", "sgp_voc_raw",
            "sgp_nox_raw", "bme_temp_c", "bme_rh_pct")
PHASE_ORDER = ("baseline", "exposure", "refresh")
PHASE_BINS = {"baseline": 24, "exposure": 64, "refresh": 40}
MIN_PHASE_ROWS = {"baseline": 16, "exposure": 20, "refresh": 16}
SOURCE_COLUMNS = {
    "40col": {
        "bme_gas_kohm": "bme_gas_ohm", "tgs2602_adc": "tgs2602_adc",
        "tgs2603_adc": "tgs2603_adc", "sgp_voc_raw": "sgp_voc",
        "sgp_nox_raw": "sgp_nox", "bme_temp_c": "bme_t", "bme_rh_pct": "bme_rh",
    },
    "23col": {
        "bme_gas_kohm": "bme_gas_kohm", "tgs2602_adc": "tgs2602_adc",
        "tgs2603_adc": "tgs2603_adc", "sgp_voc_raw": "sgp_voc_raw",
        "sgp_nox_raw": "sgp_nox_raw", "bme_temp_c": "bme_temp_c",
        "bme_rh_pct": "bme_rh_pct",
    },
}


def parse_csv(text: str, label: str = "session") -> list[dict[str, str]]:
    if len(text) > 5_000_000:
        raise ValueError(f"{label}: excessive CSV size")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    cols = set(reader.fieldnames or [])
    if not cols:
        raise ValueError(f"{label}: empty header")
    if "led" in cols and set(SOURCE_COLUMNS["40col"].values()).issubset(cols):
        kind = "40col"
    elif "stage" in cols and set(SOURCE_COLUMNS["23col"].values()).issubset(cols):
        kind = "23col"
    else:
        raise ValueError(f"{label}: expected complete 40-column or 23-column sensor schema")
    rows = list(reader)
    if len(rows) < 60 or len(rows) > 5000:
        raise ValueError(f"{label}: unreasonable session length: {len(rows)}")
    return rows


def _schema(rows: Sequence[Mapping[str, object]]) -> str:
    first = rows[0]
    if "led" in first and "bme_gas_ohm" in first:
        return "40col"
    if "stage" in first and "bme_gas_kohm" in first:
        return "23col"
    raise ValueError("Unrecognized source schema")


def _numeric(row: Mapping[str, object], key: str, kind: str) -> float:
    raw = row.get(SOURCE_COLUMNS[kind][key])
    try:
        x = float(raw)
    except (TypeError, ValueError, OverflowError):
        return np.nan
    if not np.isfinite(x):
        return np.nan
    if kind == "40col" and key == "bme_gas_kohm":
        x /= 1000.0
    if key == "bme_gas_kohm" and not (0 < x < 100000):
        return np.nan
    if key in ("tgs2602_adc", "tgs2603_adc") and not (0 <= x <= 32767):
        return np.nan
    if key.startswith("sgp_") and not (0 < x < 60000):
        return np.nan
    if key == "bme_temp_c" and not (-40 < x < 100):
        return np.nan
    if key == "bme_rh_pct" and not (0 <= x <= 100):
        return np.nan
    return x


def _clean_phase(rows: Sequence[Mapping[str, object]], key: str, kind: str) -> np.ndarray:
    values = np.array([_numeric(row, key, kind) for row in rows], dtype=np.float64)
    valid = np.isfinite(values)
    if valid.sum() < max(8, int(np.ceil(len(values) * 0.80))):
        raise ValueError(f"Missing or invalid readings for {key}: {valid.sum()}/{len(values)}")
    t = np.arange(len(values))
    return np.interp(t, t[valid], values[valid])


def _median_bins(values: np.ndarray, num_bins: int) -> np.ndarray:
    # Uses all original points; repeated interpolation only when bins > rows.
    if len(values) >= num_bins:
        edges = np.linspace(0, len(values), num_bins + 1).astype(int)
        return np.array([np.median(values[edges[k]:edges[k + 1]]) for k in range(num_bins)])
    return np.interp(np.linspace(0, len(values) - 1, num_bins),
                     np.arange(len(values)), values)


def session_trace(rows: Sequence[Mapping[str, object]]) -> tuple[np.ndarray, dict[str, int]]:
    if not rows:
        raise ValueError("No readings")
    kind = _schema(rows)
    phase_key = "led" if kind == "40col" else "stage"
    groups: dict[str, list[Mapping[str, object]]] = {p: [] for p in PHASE_ORDER}
    last_time: float | None = None
    exposure_started = False
    post_exposure_started = False
    # Some devices temporarily call pre-exposure clean air "refresh". Use
    # chronological context to distinguish this from an actual recovery.
    if str(rows[0].get(phase_key, "")).strip().lower() != "baseline":
        raise ValueError("A session must start with baseline")
    for row in rows:
        phase = str(row.get(phase_key, "")).strip().lower()
        if phase not in groups:
            raise ValueError(f"Unexpected measurement stage: {phase!r}")
        if kind == "40col":
            try:
                stamp = float(row["epoch_ms"])
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError("Invalid time value") from exc
        else:
            try:
                stamp = datetime.fromisoformat(str(row["captured_at_pc"])).timestamp() * 1000
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError("Invalid time value") from exc
        if not np.isfinite(stamp) or (last_time is not None and stamp <= last_time):
            raise ValueError("Timestamps must be strictly increasing")
        last_time = stamp
        if phase == "baseline":
            if exposure_started:
                raise ValueError("Baseline after exposure is not a valid session")
            groups["baseline"].append(row)
        elif phase == "exposure":
            if post_exposure_started:
                raise ValueError("Exposure cannot restart after post-exposure recovery")
            exposure_started = True
            groups["exposure"].append(row)
        else:  # 'refresh'
            if exposure_started:
                post_exposure_started = True
                groups["refresh"].append(row)
            else:
                # Pre-exposure refresh is still background; do not mistake it
                # for a recovery to an exposure that has not yet happened.
                groups["baseline"].append(row)
    if not exposure_started:
        raise ValueError("Exposure segment is required, with a baseline before it")
    for phase, minimum in MIN_PHASE_ROWS.items():
        if phase == "refresh" and not groups[phase]:
            continue  # explicitly absent recovery in this acquisition variant
        if len(groups[phase]) < minimum:
            raise ValueError(f"Not enough {phase} samples: {len(groups[phase])}")

    parts = []
    for key in CHANNELS:
        series = {p: (_clean_phase(groups[p], key, kind) if groups[p] else np.empty(0))
                  for p in PHASE_ORDER}
        baseline = series["baseline"]
        reference = float(np.median(baseline[-min(len(baseline), 60):]))
        if key not in ("bme_temp_c", "bme_rh_pct") and reference <= 1e-5:
            raise ValueError(f"Invalid baseline for {key}")
        phases = []
        for phase in PHASE_ORDER:
            v = series[phase]
            if not len(v):
                phases.append(np.zeros(PHASE_BINS[phase], dtype=np.float64))
                continue
            if key in ("bme_temp_c", "bme_rh_pct"):
                d = np.clip(v-reference, -50, 50)
            else:
                d = np.clip((v-reference)/reference, -12, 12)
            phases.append(_median_bins(d, PHASE_BINS[phase]))
        parts.append(np.concatenate(phases))
    readings = np.stack(parts, axis=1).astype(np.float32)
    # Phase indicators are zero in the missing refresh segment; zero-filled
    # sensor channels and a missing mask together prevent fabricated recovery.
    markers = np.zeros((128, 3), dtype=np.float32)
    offset = 0
    for idx, phase in enumerate(PHASE_ORDER):
        n = PHASE_BINS[phase]
        if groups[phase]:
            markers[offset:offset+n, idx] = 1.0
        offset += n
    trace = np.concatenate((readings, markers), axis=1)
    if trace.shape != (128, 10) or not np.isfinite(trace).all():
        raise ValueError("Invalid normalized time-series")
    return trace, {p: len(groups[p]) for p in PHASE_ORDER}
