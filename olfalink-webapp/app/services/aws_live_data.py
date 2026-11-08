"""Read AWS /data only on a user-triggered inference request.

The AWS endpoint is a fixed trusted origin; its secret never reaches the browser.
The live AWS response has a {device, count, rows} wrapper containing nested
BME688 / SGP41 / TGS2602 / TGS2603 objects. Older flat CSV-style
payloads remain supported for compatibility.
"""
from __future__ import annotations

import hashlib
import json
import math
import logging
import time
from collections.abc import Mapping
from typing import Any

import httpx

from app.services.on_demand_store import LiveStore, MeasurementUnavailable

DATA_URL = "https://jk8dcwjr0i.execute-api.ap-southeast-2.amazonaws.com/data"
LOOKBACK_MS = 120_000  # Needs ~60s physical reference + 30s exposed window.
FRESH_MS = 120_000
MAX_REPLY_BYTES = 2_000_000
MAX_ROWS = 4_000
LOG = logging.getLogger(__name__)
# Prefer the live AWS sensor objects; accept historical flat aliases too.
CHANNEL_COLUMNS = {
    "bme_gas_ohm": ("bme.gas_ohm", "bme_gas_ohm"),
    "bme_t": ("bme.t", "bme_t", "bme_temp_c"),
    "bme_rh": ("bme.rh", "bme_rh", "bme_rh_pct"),
    "tgs2602_adc": ("tgs2602.adc", "tgs2602_adc"),
    "tgs2603_adc": ("tgs2603.adc", "tgs2603_adc"),
    "sgp_voc": ("sgp.voc", "sgp_voc", "sgp_voc_raw"),
    "sgp_nox": ("sgp.nox", "sgp_nox", "sgp_nox_raw"),
}


def _rows_from_json(payload: Any) -> list[dict]:
    """Fail closed rather than treating an API error object as a measurement."""
    node = payload
    for _ in range(3):
        if isinstance(node, list):
            if len(node) > MAX_ROWS or any(not isinstance(x, dict) for x in node):
                raise MeasurementUnavailable("AWSの測定データ件数または形式が不正です。")
            return node
        if not isinstance(node, dict):
            break
        for key in ("data", "rows", "items", "samples", "readings", "results", "result", "measurements", "records", "points"):
            if isinstance(node.get(key), (list, dict)):
                node = node[key]
                break
        else:
            break
    raise MeasurementUnavailable("AWS /data の応答形式を解釈できません。JSONのキー構成を確認してください。")


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if math.isfinite(n) else None


def _stamp(raw: Mapping[str, Any]) -> int | None:
    value = _number(raw.get("epoch_ms"))
    if value is None or value < 1 or not value.is_integer():
        return None
    return int(value)


def _get_sensor_value(raw: Mapping[str, Any], paths: tuple[str, ...]) -> float | None:
    """Read the first valid finite value without interpreting absent channels.

    Sensor names are fixed by code, not supplied by a client. A missing/invalid
    channel remains None and is rejected later by CNN input validation.
    """
    for path in paths:
        node: Any = raw
        for part in path.split("."):
            if not isinstance(node, Mapping):
                node = None
                break
            node = node.get(part)
        number = _number(node)
        if number is not None:
            return number
    return None


def _read_one(raw: dict) -> dict | None:
    epoch_ms = _stamp(raw)
    if epoch_ms is None:
        return None
    # `led` can be a numeric indicator, physical LED state, or an arbitrary
    # acquisition marker. It is NOT a reliable phase label. In AWS pull mode
    # inference uses only timestamps and sensor values; never inspect it.
    # The synthetic `ambient` flag is an internal compatibility field for
    # v0.7 preprocessing, not a ground-truth acquisition phase.
    sample = {"epoch_ms": epoch_ms, "led": "ambient"}
    clipped: list[str] = []
    for dest, alternatives in CHANNEL_COLUMNS.items():
        value = _get_sensor_value(raw, alternatives)
        # Presentation-only compatibility: signed ADS raw counts near zero can
        # be negative. Do not rewrite the original AWS measurements, and do not
        # clamp missing/NaN values. Keep provenance for the UI and audit trail.
        if dest in ("tgs2602_adc", "tgs2603_adc") and value is not None and value < 0:
            value = 0.0
            clipped.append(dest)
        sample[dest] = value
    if clipped:
        sample["_negative_adc_clipped"] = clipped
    if sample["bme_gas_ohm"] is None:
        # The model's 40-column schema expects ohms, NOT kilo-ohms.
        kohm = _get_sensor_value(raw, ("bme.gas_kohm", "bme_gas_kohm"))
        sample["bme_gas_ohm"] = kohm * 1_000 if kohm is not None else None
    return sample


def _safe_session_id(raw: Any) -> str:
    if raw in (None, ""):
        return "aws_live"
    # Firestore document IDs should not contain arbitrary user-provided path syntax.
    return "aws_" + hashlib.sha256(str(raw).encode("utf-8")).hexdigest()[:24]


def normalize(payload: Any, device_id: str, *, now_ms: int) -> tuple[list[dict], str, int]:
    originals = _rows_from_json(payload)
    device_rows = []
    for row in originals:
        row_device = row.get("device", row.get("device_id"))
        if row_device is not None and str(row_device) != device_id:
            continue
        stamp = _stamp(row)
        if stamp is None or stamp > now_ms + 10_000 or stamp < now_ms - LOOKBACK_MS - 5_000:
            continue
        device_rows.append(row)
    if not device_rows:
        raise MeasurementUnavailable("AWS /data から対象デバイスの直近の測定値を取得できませんでした。")

    # Do not mix sensor sessions: select only the newest session when supplied.
    device_rows.sort(key=lambda r: int(float(r["epoch_ms"])))
    final = device_rows[-1]
    session_key = final.get("session_id", final.get("session"))
    if session_key not in (None, ""):
        device_rows = [r for r in device_rows if str(r.get("session_id", r.get("session"))) == str(session_key)]
    session_id = _safe_session_id(session_key)
    dedup = {}
    for row in device_rows:
        s = _read_one(row)
        if s:
            dedup[s["epoch_ms"]] = s
    ordered = [dedup[t] for t in sorted(dedup)]
    if not ordered:
        raise MeasurementUnavailable("有効な測定値がありません。epoch_msを確認してください。")
    last_ms = ordered[-1]["epoch_ms"]
    if last_ms < now_ms - FRESH_MS:
        raise MeasurementUnavailable("最新の測定データが古いため推論できません。ESP32の通信を確認してください。")
    # Empty/missing sensor fields fail during real 30s trace validation;
    # never substitute synthetic values for real device measurements.
    return ordered, session_id, last_ms


def fetch_window(read_key: str | None, device_id: str, *, now_ms: int | None = None) -> tuple[list[dict], str, int]:
    if not read_key:
        raise MeasurementUnavailable("AWSの読み取りキーがCloud Runに設定されていません。")
    now = int(time.time() * 1_000) if now_ms is None else now_ms
    try:
        with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False) as client:
            with client.stream("GET", DATA_URL,
                params={"device": device_id, "since": now - LOOKBACK_MS, "until": now},
                headers={"x-api-key": read_key, "Accept": "application/json"}) as response:
                response.raise_for_status()
                raw = bytearray()
                for part in response.iter_bytes():
                    raw.extend(part)
                    if len(raw) > MAX_REPLY_BYTES:
                        raise MeasurementUnavailable("AWSの応答サイズが上限を超えました。")
        payload = json.loads(raw)
    except (httpx.HTTPError, ValueError, UnicodeDecodeError) as exc:
        # Do not echo response bodies, API key or network credentials to public clients.
        raise MeasurementUnavailable("AWS /data に接続できません。APIキー、権限、通信状況を確認してください。") from exc
    return normalize(payload, device_id, now_ms=now)


class AWSDataOnDemandStore:
    """A pull-through adapter: AWS is queried only from window() on button POST.

    Reuse Firestore *only* for the latest inference result, not every raw sample.
    """
    def __init__(self, key: str | None, result_store: LiveStore, *,
                 demo_fallback: bool = False, force_demo: bool = False):
        self._key = key
        self._results = result_store
        self._demo_fallback = demo_fallback
        self._force_demo = force_demo

    def window(self, device_id: str) -> tuple[list[dict], str, int]:
        if self._force_demo:
            from app.services.synthetic_demo import synthetic_window
            return synthetic_window(device_id)
        try:
            rows, session_id, end = fetch_window(self._key, device_id)
            if self._demo_fallback:
                # Only validate the real input when demo substitution is enabled.
                # A response with too few rows or missing sensor channels cannot
                # be sent to the CNN, so it also qualifies as unavailable data.
                from app.services.live_stream import select_time_split_90s
                try:
                    select_time_split_90s(rows)
                except (ValueError, IndexError, TypeError) as exc:
                    raise MeasurementUnavailable("実測データがCNNの入力条件を満たしていません。") from exc
            return rows, session_id, end
        except MeasurementUnavailable as exc:
            if not self._demo_fallback:
                raise
            # Never expose credentials or private HTTP error text to users.
            LOG.warning("AWS sensor data unavailable; using explicit synthetic demo: %s", type(exc).__name__)
            from app.services.synthetic_demo import synthetic_window
            return synthetic_window(device_id)

    def latest_result(self, device_id: str) -> dict | None:
        return self._results.latest_result(device_id)

    def save_result(self, device_id: str, session_id: str, end_ms: int, dashboard: dict) -> None:
        self._results.save_result(device_id, session_id, end_ms, dashboard)

    def status(self, device_id: str) -> dict:
        # Do not invoke AWS on GET; connectivity is unverified until button is pressed.
        return {"connected": None, "last_ms": None, "phase": None, "session_id": None}

    def ingest(self, batch) -> int:
        raise ValueError("Live push is disabled in AWS pull mode")
