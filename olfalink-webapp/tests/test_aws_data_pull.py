"""AWS /data JSON adapter, security, and real model on-demand regression."""
from __future__ import annotations

import csv
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import aws_live_data
from app.services.on_demand_store import MemoryOnDemandStore, MeasurementUnavailable

ROOT = Path(__file__).resolve().parent.parent


def _row(stamp, phase='ambient'):
    return dict(epoch_ms=stamp, led=phase, bme_gas_ohm=10_000, bme_t=24,
                bme_rh=55, tgs2602_adc=1_000, tgs2603_adc=900,
                sgp_voc=15_000, sgp_nox=14_000, device='esp1')


def test_list_and_wrapped_json_parsing():
    now = 1_900_000_000_000
    rows = [_row(now - 80_000), _row(now - 10_000)]
    for data in (rows, {"data": rows}, {"samples": rows}, {"result": {"items": rows}}, {"measurements": rows}, {"records": rows}):
        actual, sid, last = aws_live_data.normalize(data, "esp1", now_ms=now)
        assert len(actual) == 2 and sid == "aws_live" and last == now - 10_000
        assert actual[0]["bme_gas_ohm"] == 10_000


def test_filters_device_and_session_and_duplicates():
    now = 1_900_000_000_000
    rows = [dict(_row(now-4_000), session='old'),
            dict(_row(now-2_000), session='new'),
            dict(_row(now-1_000), session='new'),
            dict(_row(now-500), device='other', session='new'),
            dict(_row(now-1_000), session='new')]
    found, sid, _ = aws_live_data.normalize(rows, 'esp1', now_ms=now)
    assert len(found) == 2 and sid.startswith('aws_')


def test_invalid_or_missing_data_not_fabricated():
    now = 1_900_000_000_000
    with pytest.raises(MeasurementUnavailable):
        aws_live_data.normalize({"error": "not found"}, "esp1", now_ms=now)
    with pytest.raises(MeasurementUnavailable):
        aws_live_data.normalize([], "esp1", now_ms=now)
    with pytest.raises(MeasurementUnavailable):
        aws_live_data.normalize([_row(now-200_000)], "esp1", now_ms=now)
    phase_ignored, _, _ = aws_live_data.normalize([_row(now, 'refresh')], "esp1", now_ms=now)
    assert phase_ignored[0]["led"] == "ambient"


def test_aliases_and_missing_phase():
    now = 1_900_000_000_000
    raw = dict(epoch_ms=now, bme_gas_kohm=12, bme_temp_c=26, bme_rh_pct=56,
               tgs2602_adc=1200, tgs2603_adc=900, sgp_voc_raw=1100, sgp_nox_raw=1200)
    result, _, _ = aws_live_data.normalize([raw], 'esp1', now_ms=now)
    assert result[0]['led'] == 'ambient'
    assert result[0]['bme_gas_ohm'] == 12_000


def test_http_query_auth_and_no_redirect(monkeypatch):
    now = 1_900_000_000_000
    seen=[]
    def handler(request):
        seen.append(request)
        assert request.url.path == '/data'
        assert request.url.params['device'] == 'esp1'
        assert request.url.params['since'] == str(now-120_000)
        assert request.url.params['until'] == str(now)
        assert request.headers['x-api-key'] == 'dummy-key'
        return httpx.Response(200, json={"data": [_row(now-30_000), _row(now-1_000)]})
    original = httpx.Client
    monkeypatch.setattr(aws_live_data.httpx, 'Client',
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    rows, _, _ = aws_live_data.fetch_window('dummy-key', 'esp1', now_ms=now)
    assert len(rows) == 2 and len(seen) == 1


def test_network_errors_dont_leak_api_key(monkeypatch):
    def handler(request):
        return httpx.Response(403, text='secret diagnostic')
    original = httpx.Client
    monkeypatch.setattr(aws_live_data.httpx, 'Client',
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(MeasurementUnavailable) as e:
        aws_live_data.fetch_window('sensitive-key', 'esp1')
    assert 'sensitive-key' not in str(e.value)
    assert 'secret diagnostic' not in str(e.value)


def test_button_only_aws_pull_real_model(monkeypatch):
    with (ROOT / 'tests/fixtures/SHOE_intensity_3_SYN_001.csv').open(encoding='utf-8-sig') as f:
        records = list(csv.DictReader(f))
    exposure = [x for x in records if x['led'] == 'exposure']
    start = int(exposure[0]['epoch_ms'])
    originals = [x for x in records if x['led'] == 'baseline' and
                 start-60_000 <= int(x['epoch_ms']) < start]
    originals += [x for x in exposure if int(x['epoch_ms']) <= start+39_000]
    delta = int(time.time()*1000) - int(originals[-1]['epoch_ms'])
    data = [dict(x, epoch_ms=int(x['epoch_ms'])+delta, device='esp1',
                 led=(1 if i%2 else 0), stage=('UNKNOWN' if i%2 else 'recovery'))
            for i, x in enumerate(originals)]

    calls=[]
    def fake_fetch(key, device_id, **kwargs):
        calls.append((key,device_id))
        return aws_live_data.normalize({'data':data}, device_id, now_ms=int(time.time()*1000))
    monkeypatch.setattr(aws_live_data, 'fetch_window', fake_fetch)
    result_store = MemoryOnDemandStore()
    store = aws_live_data.AWSDataOnDemandStore('secret-not-in-client', result_store)
    config=Settings(app_mode='aws_pull',aws_read_key='secret-not-in-client',live_device_id='esp1')
    client=TestClient(create_app(config, live_store=store))
    assert client.get('/api/v1/dashboard').status_code == 200
    assert client.get('/api/v1/live/status').json()['live_feed_connected'] is None
    assert calls == []  # GET never reaches AWS or CNN
    response = client.post('/api/v1/inference/on-demand')
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert response.json()['latest']['source'] == 'device'
    assert response.json()['analysis']['generated_by_llm'] is False  # GPU not required for CNN
    assert response.json()['analysis']['predicted_odor_ja']
    assert response.json()['analysis']['status'] == 'ready' 
    client.get('/api/v1/dashboard')
    assert len(calls) == 1
    assert client.post('/api/v1/live/ingest', json={'device_id':'esp1','session_id':'x','samples':[{'epoch_ms':int(time.time()*1000),'led':'ambient'}]}).status_code == 503


def test_ambient_needs_actual_preceding_60_seconds():
    from app.services.live_stream import select_ambient_30s
    now=1_900_000_000_000
    too_short = [_row(now-(60-i)*1000) for i in range(61)]
    with pytest.raises(ValueError, match="約90秒"):
        select_ambient_30s(too_short)
    sufficient = [_row(now-(90-i)*1000) for i in range(91)]
    trace, end = select_ambient_30s(sufficient)
    assert trace.shape == (128, 10) and end == now


@pytest.mark.parametrize("led,stage", [(0, "baseline"), (1, "exposure"), ("on", "refresh"),
                                       ("garbled", "??"), (None, "unexpected"), (True, 5)])
def test_led_and_stage_not_used_for_inference_input(led, stage):
    now=1_900_000_000_000
    data = [dict(_row(now - i * 1_000), led=led, stage=stage)
            for i in range(91)]
    normalized, _, _ = aws_live_data.normalize(data, "esp1", now_ms=now)
    assert len(normalized) == 91
    assert all(row["led"] == "ambient" for row in normalized)
    from app.services.live_stream import select_ambient_30s
    trace, end_ms = select_ambient_30s(normalized)
    assert trace.shape == (128, 10) and end_ms == now


def test_no_led_or_stage_accepted():
    now=1_900_000_000_000
    data=[]
    for i in range(91):
        record = _row(now - 90_000 + i*1_000)
        record.pop("led")
        data.append(record)
    rows, _, _ = aws_live_data.normalize(data, "esp1", now_ms=now)
    assert len(rows) == 91 and all(r["led"] == "ambient" for r in rows)
