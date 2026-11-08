"""End-to-end guarantees: no invented device observation, no background CNN, no fake LLM."""
from __future__ import annotations
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import aws_live_data, odor_narration, on_demand_inference
from app.services.on_demand_store import MeasurementUnavailable, MemoryOnDemandStore
from app.services.synthetic_demo import synthetic_window
from app.services.live_stream import select_time_split_90s
from test_time_split_v15 import readings


def make_client(key='test', llm=False):
    results = MemoryOnDemandStore()
    store = aws_live_data.AWSDataOnDemandStore(key, results, demo_fallback=True)
    config=Settings(app_mode='aws_pull',aws_read_key=key,
                    llm_service_url='https://private.run.app',llm_enabled=llm,
                    demo_fallback_enabled=True,live_device_id='esp1')
    return TestClient(create_app(config, live_store=store))


def test_random_demo_has_valid_real_cnn_shape_and_new_values_each_click():
    a, sess_a, end_a = synthetic_window('esp1')
    b, sess_b, end_b = synthetic_window('esp1')
    assert sess_a != sess_b and sess_a.startswith('synthetic_')
    assert len(a) == 91 and a != b
    assert all(row['led'] == 'ambient' for row in a)
    assert all(all(row[k] is not None for k in aws_live_data.CHANNEL_COLUMNS) for row in a)
    signal, signal_end = select_time_split_90s(a)
    assert signal.shape == (128, 10) and signal_end == end_a


def test_aws_offline_auto_fallback_is_not_labeled_as_real(monkeypatch):
    attempts = []
    def missing(*args, **kwargs):
        attempts.append(1)
        raise MeasurementUnavailable('Sensor disconnected')
    monkeypatch.setattr(aws_live_data, 'fetch_window', missing)
    client = make_client(key=None)
    before = client.get('/api/v1/dashboard').json()
    assert before['latest'] is None
    assert not attempts
    response = client.post('/api/v1/inference/on-demand')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['latest']['source'] == 'demo'
    assert data['analysis']['input_origin'] == 'synthetic_demo'
    assert data['analysis']['status'] == 'ready'
    assert '合成' in data['analysis']['message']
    assert '実測ではありません' in data['analysis']['title']
    assert data['analysis']['phase_assignment']['is_ground_truth'] is False
    assert data['analysis']['phase_assignment']['method'] == 'synthetic_demo_time_split_3x30s'
    assert data['analysis']['generated_by_llm'] is False
    assert data['features']['synthetic_fallback'] is True
    assert len(attempts) == 1
    cached = client.get('/api/v1/dashboard').json()
    assert len(attempts) == 1
    assert cached['latest']['source'] == 'demo'
    assert cached['analysis']['input_origin'] == 'synthetic_demo'


def test_no_gpu_call_even_if_llm_enabled_during_synthetic_demo(monkeypatch):
    monkeypatch.setattr(aws_live_data,'fetch_window', lambda *args, **kwargs: (_ for _ in ()).throw(MeasurementUnavailable('offline')))
    monkeypatch.setattr(odor_narration,'remote_description', lambda *args: pytest.fail('NEVER call GPU with synthetic values'))
    client = make_client(llm=True)
    response = client.post('/api/v1/inference/on-demand')
    assert response.status_code == 200, response.text
    assert response.json()['analysis']['generated_by_llm'] is False
    assert response.json()['features']['llm_enabled'] is False


def test_real_sensor_data_priority_then_fallback_then_recovery(monkeypatch):
    values = readings(step_ms=1000)
    available = {'value':True}
    def changed_fetch(key,device,**kwargs):
        if not available['value']:
            raise MeasurementUnavailable('no readings')
        return aws_live_data.normalize({'data':values},device,now_ms=int(time.time()*1000))
    monkeypatch.setattr(aws_live_data,'fetch_window',changed_fetch)
    client = make_client()
    first = client.post('/api/v1/inference/on-demand').json()
    assert first['latest']['source'] == 'device'
    assert first['analysis']['input_origin'] == 'device'
    available['value']=False
    second = client.post('/api/v1/inference/on-demand').json()
    assert second['latest']['source'] == 'demo'
    available['value']=True
    third = client.post('/api/v1/inference/on-demand').json()
    assert third['latest']['source'] == 'device'
    assert third['analysis']['input_origin'] == 'device'


def test_incomplete_device_measurements_are_replaced_not_faked_as_real(monkeypatch):
    poor = [{**row, 'tgs2603_adc':None} for row in readings(step_ms=1000)]
    monkeypatch.setattr(aws_live_data,'fetch_window',
        lambda k,d,**kwargs:aws_live_data.normalize(poor,d,now_ms=int(time.time()*1000)))
    response=make_client().post('/api/v1/inference/on-demand')
    assert response.status_code == 200
    assert response.json()['latest']['source'] == 'demo'
    assert response.json()['analysis']['input_origin'] == 'synthetic_demo'


def test_fallback_is_opt_in_and_old_fail_closed_behavior_remains(monkeypatch):
    monkeypatch.setattr(aws_live_data,'fetch_window',
        lambda *args,**kwargs: (_ for _ in ()).throw(MeasurementUnavailable('offline')))
    store = aws_live_data.AWSDataOnDemandStore('key',MemoryOnDemandStore())
    client=TestClient(create_app(Settings(app_mode='aws_pull'),live_store=store))
    assert client.post('/api/v1/inference/on-demand').status_code == 409


def test_ui_displays_explicit_provenance_and_loading():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    html=(root/'web/index.html').read_text(encoding='utf-8')
    js=(root/'web/app.js').read_text(encoding='utf-8')
    assert 'id="demo-warning"' in html
    assert 'ランダムに生成した疑似センサー値' in html
    assert "latest?.source === 'demo'" in js
    assert 'input_origin' in js
    assert "byId('demo-warning').hidden = !isDemo" in js
    assert "byId('analysis-progress').hidden = false" in js
    assert 'setInterval(' not in js
