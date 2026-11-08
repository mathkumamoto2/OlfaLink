"""Regression: real-world negative TGS2602 codes must not become missing values."""
from __future__ import annotations

import time
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import aws_live_data
from app.services.live_stream import select_time_split_90s
from app.services.on_demand_store import MemoryOnDemandStore


def aws_negative_row(ms: int, *, v2=-19, v3=4100):
    return {
        "device": "esp1", "epoch_ms": ms, "led": "unlabeled",
        "bme": {"gas_ohm": 324976, "t": 22.3, "rh": 36.89},
        "sgp": {"voc": 14000, "nox": 13000},
        "tgs2602": {"adc": v2, "vout": -0.0035, "rs_kohm": None},
        "tgs2603": {"adc": v3, "vout": 1.0},
    }


def test_negative_tgs_adc_to_zero_keeps_raw_aws_payload_unchanged():
    now = 1900000000000
    raw = aws_negative_row(now, v2=-162, v3=-7)
    rows, _, _ = aws_live_data.normalize({"rows": [raw]}, "esp1", now_ms=now)
    assert rows[0]["tgs2602_adc"] == 0
    assert rows[0]["tgs2603_adc"] == 0
    assert rows[0]["_negative_adc_clipped"] == ["tgs2602_adc", "tgs2603_adc"]
    assert raw["tgs2602"]["adc"] == -162
    assert raw["tgs2603"]["adc"] == -7


def test_missing_tgs_adc_remains_missing_not_invented():
    now = 1900000000000
    raw = aws_negative_row(now, v2=None, v3=4100)
    rows, _, _ = aws_live_data.normalize({"rows": [raw]}, "esp1", now_ms=now)
    assert rows[0]["tgs2602_adc"] is None
    assert "_negative_adc_clipped" not in rows[0]


def test_negative_real_tgs_end_to_end_actual_cnn(monkeypatch):
    now = int(time.time() * 1000)
    payload = {"device": "esp1", "count": 58, "rows": [
        aws_negative_row(now - 114000 + i * 2000, v2=-162 + i % 20, v3=3456 + i * 13)
        for i in range(58)
    ]}
    rows, _, _ = aws_live_data.normalize(payload, "esp1", now_ms=now)
    trace, _ = select_time_split_90s(rows)
    assert trace.shape == (128, 10)
    assert np.isfinite(trace).all()
    # Training column order is fixed: TGS2602 occupies index 1 and is
    # zero throughout because its reference ADC is zero after clipping.
    assert np.all(trace[:, 1] == 0)

    monkeypatch.setattr(aws_live_data, "fetch_window", lambda *_args, **_kwargs: (rows, "aws_live", rows[-1]["epoch_ms"]))
    store = aws_live_data.AWSDataOnDemandStore("test-key", MemoryOnDemandStore(), demo_fallback=False)
    client = TestClient(create_app(Settings(app_mode="aws_pull", aws_read_key="test-key", llm_enabled=False),live_store=store))
    response = client.post("/api/v1/inference/on-demand")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["latest"]["source"] == "device"
    assert result["analysis"]["input_origin"] == "device"
    assert result["features"]["sensor_correction_applied"] is True
    assert "暫定補正" in result["analysis"]["evidence"]["uncertainty"]
    assert "tgs2602_adc: 58/58件" in result["analysis"]["evidence"]["uncertainty"]
    assert result["analysis"]["predicted_odor_label"]
    assert result["analysis"]["description_source"] in ("dictionary", "no_signal")
    assert result["analysis"]["generated_by_llm"] is False
    assert client.get("/api/v1/dashboard").json()["features"]["sensor_correction_applied"] is True


def test_negative_clipping_does_not_hide_all_missing_channel():
    now = int(time.time() * 1000)
    payload = {"rows": [aws_negative_row(now-114000+2000*i, v2=None) for i in range(58)]}
    rows, _, _ = aws_live_data.normalize(payload, "esp1", now_ms=now)
    with pytest.raises(ValueError, match="tgs2602_adc"):
        select_time_split_90s(rows)


def test_frontend_shows_adc_correction_disclosure():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "web/index.html").read_text(encoding="utf-8")
    js = (root / "web/app.js").read_text(encoding="utf-8")
    assert 'id="correction-warning"' in html
    assert "センサー値を補正した暫定結果" in html
    assert "sensor_correction_applied" in js
    assert "byId('correction-warning').hidden = !adcCorrected" in js
