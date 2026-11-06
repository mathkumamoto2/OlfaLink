"""The real uploaded CNN weights run only for an explicit POST."""
from __future__ import annotations
import csv
import time
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.services.on_demand_store import MemoryOnDemandStore

ROOT = Path(__file__).resolve().parent.parent


def actual_format_samples():
    with (ROOT / 'tests/fixtures/SHOE_intensity_3_SYN_001.csv').open(encoding='utf-8-sig') as file:
        rows = list(csv.DictReader(file))
    exposure = [r for r in rows if r['led'] == 'exposure']
    start = int(exposure[0]['epoch_ms'])
    subset = [r for r in rows if r['led'] == 'baseline' and start-60_000 <= int(r['epoch_ms']) < start]
    subset += [r for r in exposure if int(r['epoch_ms']) <= start+39_000]
    delta = int(time.time()*1000) - int(subset[-1]['epoch_ms'])
    fields = ('epoch_ms','led','bme_gas_ohm','bme_t','bme_rh','tgs2602_adc','tgs2603_adc','sgp_voc','sgp_nox')
    return [{k:int(r[k])+delta if k == 'epoch_ms' else r[k] for k in fields} for r in subset]


def build_client():
    store = MemoryOnDemandStore()
    client = TestClient(create_app(Settings(app_mode='on_demand',live_ingest_key='test-only',live_device_id='esp1'), live_store=store))
    return client,store


def send_samples(c, samples, phase='ignored', session='s1'):
    return c.post('/api/v1/live/ingest', json={
        'device_id':'esp1','session_id':session,'samples':samples
    }, headers={'x-device-key':'test-only'})


def test_on_demand_does_not_infer_from_get_or_push(monkeypatch):
    client,store = build_client()
    called = {'n':0}
    from app.services import on_demand_inference as module
    real = module.get_model
    def record():
        called['n'] += 1
        return real()
    monkeypatch.setattr(module,'get_model',record)
    rows = actual_format_samples()
    response = send_samples(client,rows)
    assert response.status_code == 202,response.text
    assert client.get('/api/v1/dashboard').status_code == 200
    assert client.get('/api/v1/dashboard').json()['latest'] is None
    assert called['n'] == 0
    result = client.post('/api/v1/inference/on-demand')
    assert result.status_code == 200,result.text
    data = result.json()
    assert data['latest']['intensity'] in range(6)
    assert data['mode'] == 'on_demand'
    assert data['analysis']['status'] == 'ready'
    assert data['analysis']['generated_by_llm'] is False
    assert called['n'] == 1
    again = client.post('/api/v1/inference/on-demand')
    assert again.status_code == 200
    assert called['n'] == 1, 'must serve cached result within 30 seconds'
    saved = client.get('/api/v1/dashboard').json()
    assert saved['latest']['id'] == data['latest']['id']
    assert called['n'] == 1


def test_ingest_requires_auth_and_fresh_samples():
    client,_ = build_client()
    rows = actual_format_samples()
    payload = {'device_id':'esp1','session_id':'s1','samples':rows}
    assert client.post('/api/v1/live/ingest',json=payload).status_code == 401
    assert client.post('/api/v1/live/ingest',json=payload,headers={'x-device-key':'invalid'}).status_code == 401
    assert send_samples(client,rows).status_code == 202
    status = client.get('/api/v1/live/status').json()
    assert status['live_feed_connected'] is True
    assert status['state_storage'] == 'firestore_persistent'
    stale = rows[0].copy(); stale['epoch_ms'] = 1
    assert send_samples(client,[stale]).status_code == 422


def test_no_baseline_gives_409_and_no_dummy_prediction():
    client,_ = build_client()
    exposure = [r for r in actual_format_samples() if r['led']=='exposure']
    assert send_samples(client,exposure).status_code == 202
    res = client.post('/api/v1/inference/on-demand')
    assert res.status_code == 409
    assert client.get('/api/v1/dashboard').json()['latest'] is None


def test_ambient_90_second_reference():
    client,_ = build_client()
    rows = actual_format_samples()
    # Extend measured points into a 90-second continuous sequence. Values are
    # from the recorded synthetic fixture; not presented as real sensor data.
    template = rows[:1] + rows[-1:]
    now = int(time.time()*1000)
    continuous=[]
    for i in range(91):
        row = template[i % 2].copy()
        row['epoch_ms'] = now-(90-i)*1000
        row['led']='ambient'
        continuous.append(row)
    assert send_samples(client, continuous,session='ambient1').status_code == 202
    result = client.post('/api/v1/inference/on-demand')
    assert result.status_code == 200,result.text
    assert '直前60秒' in result.json()['analysis']['evidence']['uncertainty']


def test_no_periodic_browser_refresh():
    js=(ROOT/'web/app.js').read_text(encoding='utf-8')
    assert 'setInterval(' not in js
    assert "action.addEventListener('click', analyze)" in js
    assert "from '/assets/api.js'" in js
