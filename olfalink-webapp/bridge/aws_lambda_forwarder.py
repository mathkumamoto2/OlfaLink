"""Add this call AFTER the existing AWS Lambda stores the real ESP32 sample.

Requires AWS Lambda env vars OLFALINK_CLOUDRUN_URL and LIVE_INGEST_KEY.
Never include AWS read keys in client-side JS or source control.

Example:
  # Existing AWS persistence has succeeded:
  forward_measurements(original_payload)

If the device does not supply named phases, pass led='ambient' and keep one
stable session ID until the sensor restarts. A continuous ambient stream needs
approximately 90 seconds before the first 30-second prediction.
"""
from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

FIELDS = ("epoch_ms", "led", "bme_gas_ohm", "bme_t", "bme_rh", "tgs2602_adc",
          "tgs2603_adc", "sgp_voc", "sgp_nox")


def forward_measurements(payload: dict) -> None:
    """One real payload per request; for production, batch in AWS if possible."""
    url = os.environ["OLFALINK_CLOUDRUN_URL"].rstrip("/")
    if not url.startswith("https://"):
        raise ValueError("OLFALINK_CLOUDRUN_URL must use HTTPS")
    secret = os.environ["LIVE_INGEST_KEY"]
    sample = {k: payload[k] for k in FIELDS if k in payload}
    if "epoch_ms" not in sample:
        raise ValueError("The AWS payload must include epoch_ms")
    phase = sample.get("led", "ambient")
    if phase not in {"baseline", "exposure", "refresh", "ambient"}:
        phase = "ambient" # Convert only unidentified continuous measurement labels.
    sample["led"] = phase
    required_sensor = ("bme_gas_ohm", "tgs2602_adc", "tgs2603_adc", "sgp_voc", "sgp_nox")
    if not any(sample.get(k) is not None for k in required_sensor):
        raise ValueError("No usable sensor channel was received")
    session = str(payload.get("session") or payload.get("session_id") or "continuous")
    body = json.dumps({"device_id":"esp1", "session_id":session,
                       "samples":[sample]}).encode("utf-8")
    request = Request(url + "/api/v1/live/ingest",data=body,method="POST",
        headers={"Content-Type":"application/json", "x-device-key":secret})
    with urlopen(request, timeout=12) as response:
        if response.status != 202:
            raise RuntimeError(f"Cloud Run ingestion returned HTTP {response.status}")
