from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.services.storage import DemoStore


def client(key=None):
    return TestClient(create_app(Settings(app_mode='demo', device_ingest_key=key),DemoStore()))


def test_dashboard_has_integer_0_to_5_and_demo_not_live():
    response = client().get('/api/v1/dashboard')
    assert response.status_code == 200
    data = response.json()
    assert (data['level_min'],data['level_max']) == (0,5)
    assert all(type(row['intensity']) is int and 0 <= row['intensity'] <= 5 for row in data['trend'])
    assert data['mode'] == 'demo'
    assert not data['device']['connected']
    assert not data['features']['ai_enabled']
    assert not data['features']['llm_enabled']
    assert data['analysis']['status'] == 'disabled'


def test_ingest_disabled_by_default():
    assert client().post('/api/v1/readings',json={'device_id':'olfalink-01','intensity':3}).status_code == 503


def test_ingest_rejects_missing_or_wrong_secret():
    data={'device_id':'olfalink-01','intensity':2}
    assert client('secret').post('/api/v1/readings',json=data).status_code == 401
    assert client('secret').post('/api/v1/readings',json=data,headers={'X-Device-Key':'other'}).status_code == 401


def test_ingest_accepts_authenticated_reading_and_updates_dashboard():
    with client('secret') as c:
        data={'device_id':'olfalink-01','intensity':5,'channels':{'bme688_raw':12345.0}}
        response=c.post('/api/v1/readings',json=data,headers={'X-Device-Key':'secret'})
        assert response.status_code == 201, response.text
        assert response.json()['intensity'] == 5
        latest=c.get('/api/v1/dashboard').json()
        assert latest['latest']['intensity'] == 5
        assert latest['device']['connected']


def test_validates_intensity_bounds_and_timestamp_timezone():
    with client('secret') as c:
        headers={'X-Device-Key':'secret'}
        for value in [-1,6,2.5,'invalid']:
            r=c.post('/api/v1/readings',headers=headers,json={'device_id':'olfalink-01','intensity':value})
            assert r.status_code == 422, (value,r.text)
        r=c.post('/api/v1/readings',headers=headers,json={'device_id':'olfalink-01','intensity':2,'timestamp':'2026-01-01T10:00:00'})
        assert r.status_code == 422
        r=c.post('/api/v1/readings',headers=headers,json={'device_id':'other','intensity':2})
        assert r.status_code == 403


def test_rate_limit_is_applied_to_history_limit():
    assert client().get('/api/v1/readings?limit=241').status_code == 422
    assert len(client().get('/api/v1/readings?limit=3').json()) == 3


def test_website_and_security_headers():
    r=client().get('/')
    assert r.status_code==200
    assert 'OlfaLink' in r.text
    assert r.headers['x-content-type-options']=='nosniff'
    assert 'script-src' in r.headers['content-security-policy']


def test_ai_extension_pipeline_rejects_empty_evidence():
    import asyncio
    from app.services.inference import AnalysisPipeline
    from app.schemas import DescriptionEvidence

    class EmptyModel:
        async def infer(self, measurements):
            return DescriptionEvidence()

    class Narrator:
        async def describe(self, evidence):
            raise AssertionError('Narrator should not run without evidence')

    result = asyncio.run(AnalysisPipeline(EmptyModel(), Narrator()).analyze(DemoStore().recent(3)))
    assert result.status == 'not_available'
    assert not result.generated_by_llm


def test_ai_extension_pipeline_works_with_injected_components():
    import asyncio
    from app.services.inference import AnalysisPipeline
    from app.schemas import DescriptionEvidence

    class ModelStub:
        async def infer(self, measurements):
            return DescriptionEvidence(descriptor_scores={'sweet':0.4},
                                       uncertainty='experimental',
                                       model_version='test-only',
                                       source_reading_ids=[measurements[0].id])

    class NarratorStub:
        async def describe(self, evidence):
            assert evidence.model_version == 'test-only'
            return 'テスト用の説明'

    result=asyncio.run(AnalysisPipeline(ModelStub(), NarratorStub()).analyze(DemoStore().recent(2)))
    assert result.status == 'ready'
    assert result.message == 'テスト用の説明'
    assert result.generated_by_llm is True


def test_invalid_future_provider_fails_closed(monkeypatch):
    import pytest
    monkeypatch.setenv('AI_PROVIDER','unvalidated-model')
    with pytest.raises(ValueError, match='not been implemented'):
        Settings.from_env()


def test_visual_ui_focuses_on_intensity_and_natural_language_not_history():
    with client() as c:
        page = c.get('/')
        assert page.status_code == 200
        html = page.text
        assert 'においの強さ' in html
        assert 'どんなにおい？' in html
        assert '測定結果を待っています。' in html
        assert '架空の例' not in html
        assert '表示サンプル' not in html
        assert '0〜5' in html
        assert 'role="meter"' in html
        assert 'class="sidebar"' not in html
        assert 'trend-chart' not in html
        for asset in ['app.js','styles.css','botanical.svg','landscape.svg']:
            assert c.get('/assets/'+asset).status_code == 200


def test_web_app_keeps_ai_and_llm_extension_points():
    js = client().get('/assets/app.js').text
    assert 'descriptor_scores' in js
    assert 'getDashboard' in js
    assert 'description-title' in js


def test_production_ui_does_not_present_sample_as_live():
    with client() as c:
        html = c.get('/').text
        js = c.get('/assets/app.js').text
        assert 'ほんのり甘く' not in html
        assert 'sampleText' not in js
        assert "payload?.mode !== 'demo'" in js
        assert "latest?.source === 'device'" in js
        assert 'においの強さ' in html
        assert 'どんなにおい？' in html
