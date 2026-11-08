"""v1.9 contract: deliberate button-triggered demo may use the real LLM.

The synthetic input and CNN confidence are never misrepresented as observations.
"""
from __future__ import annotations
import time
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.services import aws_live_data, odor_narration
from app.services.on_demand_store import MeasurementUnavailable, MemoryOnDemandStore

URL = 'https://olfalink-llm.example.run.app'


def app_client(*, llm=True, llm_demo=True):
    store = aws_live_data.AWSDataOnDemandStore(None, MemoryOnDemandStore(), demo_fallback=True)
    return TestClient(create_app(Settings(
        app_mode='aws_pull', aws_read_key=None, live_device_id='esp1',
        llm_service_url=URL, llm_enabled=llm, llm_demo_enabled=llm_demo,
        demo_fallback_enabled=True), live_store=store))


def no_aws(monkeypatch):
    monkeypatch.setattr(aws_live_data, 'fetch_window',
        lambda *args, **kwargs: (_ for _ in ()).throw(MeasurementUnavailable('offline')))


def test_synthetic_llm_real_model_flag_when_enabled(monkeypatch):
    no_aws(monkeypatch)
    calls=[]
    def fake_remote(url, label, level, score, *, demo_mode=False):
        calls.append((url, label, level, score, demo_mode))
        return '低い確信度の分類結果に基づく合成データの参考説明です。', True
    monkeypatch.setattr(odor_narration, 'remote_description', fake_remote)
    c=app_client()
    before=c.get('/api/v1/dashboard').json()
    assert before['latest'] is None
    assert calls == []  # GET never invokes CNN or GPU
    response=c.post('/api/v1/inference/on-demand')
    assert response.status_code == 200, response.text
    out=response.json()
    assert len(calls)==1 and calls[0][0]==URL and calls[0][-1] is True
    assert 0<=calls[0][3]<=1  # original score passed unchanged
    assert out['analysis']['description_source']=='llm'
    assert out['analysis']['generated_by_llm'] is True
    assert out['analysis']['input_origin']=='synthetic_demo'
    assert out['latest']['source']=='demo'
    assert '合成' in out['analysis']['message'] and '実測ではありません' in out['analysis']['title']
    assert out['features']['llm_enabled'] is True
    assert c.get('/api/v1/dashboard').json()['analysis']['generated_by_llm'] is True
    assert len(calls)==1
    # Demo button repeated immediately must not start another paid GPU call.
    again=c.post('/api/v1/inference/on-demand').json()
    assert again['latest']['id']==out['latest']['id']
    assert len(calls)==1


def test_llm_demo_is_opt_in_even_if_gpu_enabled(monkeypatch):
    no_aws(monkeypatch)
    def fail(*args,**kwargs):
        raise AssertionError('GPU should never be called unless opted in')
    monkeypatch.setattr(odor_narration, 'remote_description', fail)
    out=app_client(llm_demo=False).post('/api/v1/inference/on-demand').json()
    assert out['analysis']['input_origin']=='synthetic_demo'
    assert out['analysis']['generated_by_llm'] is False
    assert out['analysis']['description_source'] in ('dictionary','no_signal')


def test_gpu_failure_falls_back_to_dictionary_for_synthetic(monkeypatch):
    no_aws(monkeypatch)
    def fail(*args, **kwargs):
        raise TimeoutError('timeout')
    monkeypatch.setattr(odor_narration, 'remote_description', fail)
    out=app_client().post('/api/v1/inference/on-demand').json()
    assert out['analysis']['generated_by_llm'] is False
    assert out['analysis']['input_origin']=='synthetic_demo'
    assert out['analysis']['description_source'] in ('dictionary','no_signal')
    assert '実測ではありません' in out['analysis']['message']


def test_uncertain_or_zero_level_demo_can_request_llm(monkeypatch):
    calls=[]
    monkeypatch.setattr(odor_narration,'remote_description',
        lambda u,l,i,s,*,demo_mode=False: (calls.append((s,i,demo_mode)) or ('不確かな合成デモで、実際のにおいは特定できません。',True)))
    txt, llm = odor_narration.describe('room',0,0.02,URL,demo_mode=True)
    assert llm is True and calls == [(0.02,0,True)]
    assert '実際のにおい' in txt
    calls.clear()
    txt, llm = odor_narration.describe('room',0,0.02,URL)
    assert llm is False and calls == []


def test_dashboard_integration_exposes_opt_in_without_secrets():
    c=app_client()
    obj=c.get('/api/v1/integration/status').json()
    assert obj['llm_demo_enabled'] is True
    assert obj['inference_mode']=='button_only'
    assert 'OLFALINK_READ_KEY' not in obj


def test_ui_marks_llm_demo_and_spinner():
    from pathlib import Path
    web=Path(__file__).resolve().parents[1]/'web'
    js=(web/'app.js').read_text('utf-8')
    assert '合成データによるLLMデモ' in js
    assert 'isDemo && analysis?.generated_by_llm' in js
    assert "byId('analysis-progress').hidden = false" in js
    assert 'setInterval(' not in js


def test_force_demo_never_contacts_aws(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('AWS must not be called in forced demo')
    monkeypatch.setattr(aws_live_data,'fetch_window',fail)
    store=aws_live_data.AWSDataOnDemandStore('test',MemoryOnDemandStore(),
                                             force_demo=True, demo_fallback=True)
    c=TestClient(create_app(Settings(app_mode='aws_pull', force_demo_enabled=True,
         llm_demo_enabled=False, llm_enabled=False), live_store=store))
    assert c.get('/api/v1/integration/status').json()['force_synthetic_demo'] is True
    out=c.post('/api/v1/inference/on-demand').json()
    assert out['analysis']['input_origin']=='synthetic_demo'
