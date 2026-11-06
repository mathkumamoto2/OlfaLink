"""Read published named sessions from the ESP32 AWS dataset, never from the browser."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO
from threading import Lock
from time import monotonic
from typing import Any
import os
import httpx
from openpyxl import load_workbook

BASE_URL = "https://jk8dcwjr0i.execute-api.ap-southeast-2.amazonaws.com"
MANIFEST_URL = BASE_URL + "/publish"
DATASET_URL = BASE_URL + "/publish/dataset.xlsx"
MAX_BYTES = 9_500_000
REQUIRED = frozenset({"session", "led", "epoch_ms", "bme_gas_ohm", "bme_t", "bme_rh", "tgs2602_adc", "tgs2603_adc", "sgp_voc", "sgp_nox"})
TTL_SECONDS = 300


class SourceUnavailable(Exception):
    pass


def _key(value: Any) -> str:
    """Normalize integral Excel session IDs consistently between sheets."""
    if value is None:
        return ""
    if isinstance(value, (int, float)) and float(value).is_integer():
        return str(int(value))
    return str(value).strip()


def _download(url: str, secret: str) -> bytes:
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False) as client:
        with client.stream("GET", url, headers={"x-api-key": secret, "Accept": "application/json" if url == MANIFEST_URL else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}) as response:
            response.raise_for_status()
            data = bytearray()
            for piece in response.iter_bytes():
                data.extend(piece)
                if len(data) > MAX_BYTES:
                    raise SourceUnavailable("AWS dataset exceeds supported response size")
            return bytes(data)


def _decode_book(content: bytes) -> list[dict]:
    try:
        book = load_workbook(BytesIO(content), read_only=True, data_only=True)
        try:
            if not {"data", "sessions"}.issubset(book.sheetnames):
                raise SourceUnavailable("AWS dataset does not contain data and sessions sheets")
            sessions = book["sessions"].iter_rows(values_only=True)
            session_fields = [str(c).strip() if c is not None else "" for c in next(sessions)]
            if not {"session", "name"}.issubset(session_fields):
                raise SourceUnavailable("Missing named sessions fields")
            labeled = {}
            for cells in sessions:
                row = dict(zip(session_fields, cells))
                if row.get("name") and str(row["name"]).strip():
                    labeled[_key(row.get("session"))] = str(row["name"]).strip()
            data_rows = book["data"].iter_rows(values_only=True)
            fields = [str(c).strip() if c is not None else "" for c in next(data_rows)]
            if not REQUIRED.issubset(fields):
                raise SourceUnavailable("AWS data fields do not match model requirements")
            result = []
            for number, values in enumerate(data_rows):
                if number >= 100_000:
                    raise SourceUnavailable("Dataset row limit exceeded")
                row = dict(zip(fields, values))
                sid = _key(row.get("session"))
                if sid not in labeled:
                    continue
                try:
                    stamp = float(row["epoch_ms"])
                except (ValueError, TypeError):
                    continue
                if stamp <= 0:
                    continue
                row["epoch_ms"] = stamp
                row["_session_id"] = sid
                # Do not send the private human-written session names to the browser.
                result.append(row)
            return result
        finally:
            book.close()
    except (OSError, ValueError, KeyError, StopIteration, TypeError) as exc:
        raise SourceUnavailable("AWS Excel dataset could not be decoded") from exc


class AWSSubscriber:
    def __init__(self):
        self._lock = Lock()
        self._until = 0.0
        self._version: int | None = None
        self._rows: list[dict] = []
        self._manifest: dict = {}

    def fetch(self, secret: str) -> tuple[list[dict], dict]:
        if not secret:
            raise SourceUnavailable("OLFALINK_READ_KEY is not configured")
        with self._lock:
            if monotonic() < self._until:
                return self._rows, self._manifest
            try:
                import json
                manifest = json.loads(_download(MANIFEST_URL, secret))
                version = int(manifest["version"])
                if self._version != version:
                    rows = _decode_book(_download(DATASET_URL, secret))
                    self._rows = rows
                    self._version = version
                self._manifest = {"version": version, "rows": len(self._rows)}
                self._until = monotonic() + TTL_SECONDS
                return self._rows, self._manifest
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                # Deliberately do not expose response bodies, URLs with credentials, or secrets.
                raise SourceUnavailable("AWS publish request failed") from exc


subscriber = AWSSubscriber()


def ranked_sessions(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    by_id: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_id[row["_session_id"]].append(row)
    result = []
    for sid, observations in by_id.items():
        ordered = sorted(observations, key=lambda row: row["epoch_ms"])
        result.append((sid, ordered))
    return sorted(result, key=lambda pair: pair[1][-1]["epoch_ms"], reverse=True)
