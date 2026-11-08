"""Regression: 3 × 30s time slots, actual checkpoint, separate CNN/LLM status."""
from __future__ import annotations

import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import aws_live_data
from app.services.live_stream import select_time_split_90s, time_split_metadata
from app.services.on_demand_store import MemoryOnDemandStore


def readings(*, step_ms=2000, count=None, missing=None):
    now = int(time.time() * 1000)
    n = count if count is not None else 90_000 // step_ms + 1
    result=[]
    for i in range(n):
        when = now - 90_000 + i * step_ms
        signal = 1.0 + (0.08 if 30_000 <= i*step_ms < 60_000 else 0)
        row={"epoch_ms":when,"device":"esp1","led":(i%2),"stage":"unexpected",
          "bme_gas_ohm":12000 * signal,"bme_t":24.2,"bme_rh":50.2,
          "tgs2602_adc":1024*signal,"tgs2603_adc":950*signal,
          "sgp_voc":15200*signal,"sgp_nox":14400*signal}
        if missing:
            row.pop(missing, None)
        result.append(row)
    return result


@pytest.mark.parametrize("step_ms", [500, 1000, 2000, 3000, 5000])
def test_three_part_windows_handle_sampling_frequency(step_ms):
    r=readings(step_ms=step_ms)
    trace, end = select_time_split_90s(r)
    assert trace.shape == (128, 10)
    assert trace.dtype == np.float32
    assert end == r[-1]["epoch_ms"]
    assert np.all(trace[:24,7] == 1)
    assert np.all(trace[24:88,8] == 1)
    assert np.all(trace[88:,9] == 1)
    assert np.isfinite(trace).all()


def test_short_window_gives_actionable_error():
    r=readings(step_ms=1000)[-45:]
    with pytest.raises(ValueError, match="約90秒"):
        select_time_split_90s(r)


def test_required_channel_never_fabricated():
    with pytest.raises(ValueError, match="tgs2603_adc"):
        select_time_split_90s(readings(missing='tgs2603_adc'))


def test_ignores_odor_label_and_led_stage():
    a=readings(step_ms=2000)
    b=[dict(x, led='exposure', stage='baseline', odor='vinegar') for x in a]
    trace1,_=select_time_split_90s(a)
    trace2,_=select_time_split_90s(b)
    np.testing.assert_array_equal(trace1, trace2)


def test_real_checkpoint_cnn_without_llm(monkeypatch):
    sample=readings(step_ms=2000)
    def mocked_fetch(key, device_id, **kwargs):
        return aws_live_data.normalize({"data": sample}, device_id, now_ms=int(time.time()*1000))
    monkeypatch.setattr(aws_live_data, 'fetch_window', mocked_fetch)
    store=aws_live_data.AWSDataOnDemandStore('test', MemoryOnDemandStore())
    # Even if an LLM URL exists, the default setting MUST prevent requests
    # when the GPU service is deliberately disabled (manual 0 instances).
    settings=Settings(app_mode='aws_pull',aws_read_key='test',llm_service_url='https://example.com',live_device_id='esp1')
    client=TestClient(create_app(settings, live_store=store))
    response=client.post('/api/v1/inference/on-demand')
    assert response.status_code == 200, response.text
    data=response.json()
    assert data['analysis']['status']=='ready'
    assert data['analysis']['predicted_odor_ja']
    assert data['analysis']['generated_by_llm'] is False
    assert data['latest']['intensity'] in range(6)
    assert '30秒×3分割' in data['analysis']['evidence']['uncertainty']
    assert data['analysis']['phase_assignment']['is_ground_truth'] is False
    assert data['analysis']['phase_assignment']['used_led_signal'] is False
    assert [x['label'] for x in data['analysis']['phase_assignment']['windows']] == ['baseline','exposure','refresh']
    assert all(x['sample_count'] >= 4 for x in data['analysis']['phase_assignment']['windows'])
    assert client.get('/api/v1/integration/status').json()['llm_enabled'] is False
    assert client.get('/api/v1/dashboard').json()['analysis']['predicted_odor_ja']


def test_nonmatching_api_schema_gives_409(monkeypatch):
    def bad_fetch(*args,**kwargs):
        return aws_live_data.normalize({'unknown':'shape'}, 'esp1', now_ms=int(time.time()*1000))
    monkeypatch.setattr(aws_live_data,'fetch_window',bad_fetch)
    store=aws_live_data.AWSDataOnDemandStore('test',MemoryOnDemandStore())
    client=TestClient(create_app(Settings(app_mode='aws_pull'), live_store=store))
    response=client.post('/api/v1/inference/on-demand')
    assert response.status_code==409
    assert 'AWS /data' in response.json()['detail']

@pytest.mark.parametrize('enabled,expected_url', [
    (False, None), (True, 'https://example.com')
])
def test_gpu_url_is_only_passed_when_explicitly_enabled(monkeypatch,enabled,expected_url):
    from app.services import on_demand_inference
    sample=readings(step_ms=2000)
    monkeypatch.setattr(aws_live_data, 'fetch_window',
        lambda key, device_id, **kwargs: aws_live_data.normalize({'data':sample},device_id,now_ms=int(time.time()*1000)))
    called=[]
    def fake_describe(label,intensity,score,url=None):
        called.append(url)
        return 'テスト用の説明', bool(url)
    monkeypatch.setattr(on_demand_inference,'describe',fake_describe)
    store=aws_live_data.AWSDataOnDemandStore('test',MemoryOnDemandStore())
    config=Settings(app_mode='aws_pull',aws_read_key='test',
       llm_service_url='https://example.com',llm_enabled=enabled,live_device_id='esp1')
    client=TestClient(create_app(config,live_store=store))
    response=client.post('/api/v1/inference/on-demand')
    assert response.status_code==200
    assert called==[expected_url]
    assert response.json()['analysis']['generated_by_llm'] == enabled


def test_phase_assignment_timestamps_and_counts_ignore_led():
    a=readings(step_ms=1000)
    trace, end = select_time_split_90s(a)
    meta=time_split_metadata(a, end)
    assert len(meta['windows']) == 3
    assert sum(w['sample_count'] for w in meta['windows']) == len(a)
    assert [w['end_ms'] - w['start_ms'] for w in meta['windows']] == [30_000] * 3
    assert meta['is_ground_truth'] is False
    assert meta['used_led_signal'] is False


def test_time_split_does_not_infer_odor_exposure_from_constant_air():
    r=readings(step_ms=1000)
    for row in r:
        row['bme_gas_ohm']=12000
        row['tgs2602_adc']=1024
        row['tgs2603_adc']=950
    _, end=select_time_split_90s(r)
    meta=time_split_metadata(r,end)
    assert meta['method']=='time_split_3x30s'
    assert meta['is_ground_truth'] is False
