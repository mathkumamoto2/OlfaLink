"""End-to-end contract checks: real (recorded) format, authenticated push, 30s CNN."""
import csv
import time
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.services.live_stream import LiveBuffer, LiveBatch, select_30s

ROOT = Path(__file__).resolve().parent.parent


def stream_samples():
    with (ROOT / "tests/fixtures/SHOE_intensity_3_SYN_001.csv").open(encoding="utf-8-sig") as fp:
        rows = list(csv.DictReader(fp))
    exposure = [r for r in rows if r["led"] == "exposure"]
    start = int(exposure[0]["epoch_ms"])
    selected = [r for r in rows if r["led"] == "baseline" and start - 60_000 <= int(r["epoch_ms"]) < start]
    selected += [r for r in exposure if int(r["epoch_ms"]) <= start + 39_000]
    # The synthetic fixture's timestamps are arbitrary; rebase to *current* time.
    delta = int(time.time() * 1000) - int(selected[-1]["epoch_ms"])
    keys = ("epoch_ms", "led", "bme_gas_ohm", "bme_t", "bme_rh",
            "tgs2602_adc", "tgs2603_adc", "sgp_voc", "sgp_nox")
    return [{k: (int(r[k]) + delta if k == "epoch_ms" else r[k]) for k in keys} for r in selected]


def test_live_push_auth_and_actual_cnn():
    c = TestClient(create_app(Settings(app_mode="aws", live_ingest_key="test-write-only", live_device_id="esp1")))
    data = {"device_id":"esp1", "session_id":"s1", "samples":stream_samples()}
    assert c.post("/api/v1/live/ingest",json=data).status_code == 401
    assert c.post("/api/v1/live/ingest",json=data,headers={"x-device-key":"wrong"}).status_code == 401
    assert c.post("/api/v1/live/ingest",json=data,headers={"x-device-key":"test-write-only"}).status_code == 202
    status = c.get("/api/v1/live/status").json()
    assert status["live_feed_connected"] is True
    assert status["window_seconds"] == 30
    from app.services import combined_dashboard
    # API must not fall through to the historical AWS subscriber while stream is live.
    result = c.get("/api/v1/dashboard")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["mode"] == "live"
    assert body["analysis"]["status"] == "ready"
    assert body["latest"]["intensity"] in range(6)
    assert body["analysis"]["generated_by_llm"] is False
    assert "test-write-only" not in result.text


def test_no_fake_baseline_or_premature_prediction():
    rows = stream_samples()
    only_exposure = [x for x in rows if x["led"] == "exposure"]
    try:
        select_30s(only_exposure)
    except ValueError as e:
        assert "baseline" in str(e)
    else:
        raise AssertionError("Model must reject missing measured baseline")
    rows_short = [r for r in rows if r["led"] == "baseline"] + only_exposure[:4]
    try:
        select_30s(rows_short)
    except ValueError as e:
        assert "30 seconds" in str(e)
    else:
        raise AssertionError("Premature prediction must be rejected")


def test_malformed_stale_and_out_of_order_rejected():
    buf = LiveBuffer()
    samples = stream_samples()
    d = LiveBatch(device_id="esp1", session_id="s1", samples=samples)
    assert buf.append(d) == len(samples)
    bad = LiveBatch(device_id="esp1", session_id="s1", samples=[samples[0]])
    try:
        buf.append(bad)
    except ValueError as e:
        assert "newer" in str(e)
    else:
        raise AssertionError("old samples accepted")
    assert buf.snapshot()[1]
    assert not buf.snapshot(now_ms=samples[-1]["epoch_ms"] + 130_000)[1]


def test_recovery_is_not_shown_as_current_exposure():
    c = TestClient(create_app(Settings(app_mode="aws", live_ingest_key="test-only")))
    data = {"device_id":"esp1", "session_id":"s1", "samples":stream_samples()}
    assert c.post("/api/v1/live/ingest", json=data, headers={"x-device-key":"test-only"}).status_code == 202
    # Recovery is a distinct phase. Continuing to display exposure as *current*
    # during recovery would be misleading.
    after = data["samples"][-1].copy()
    after["epoch_ms"] += 1000
    after["led"] = "refresh"
    assert c.post("/api/v1/live/ingest", json={"device_id":"esp1", "session_id":"s1", "samples":[after]},headers={"x-device-key":"test-only"}).status_code == 202
    result = c.get("/api/v1/dashboard").json()
    assert result["mode"] == "live"
    assert result["latest"] is None
    assert result["analysis"]["status"] == "not_available"
