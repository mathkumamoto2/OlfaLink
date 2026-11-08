"""v1.6 release checks: graceful fallback, provenance and loading UI."""
from __future__ import annotations

from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services import aws_live_data, odor_narration
from app.services.on_demand_store import MemoryOnDemandStore

from test_time_split_v15 import readings

ROOT = Path(__file__).resolve().parents[1]


def test_real_cnn_and_dictionary_response_on_button_click(monkeypatch):
    rows = readings(step_ms=1000)
    monkeypatch.setattr(aws_live_data, 'fetch_window', lambda key, dev, **kwargs:
        aws_live_data.normalize({'data': rows}, dev, now_ms=int(time.time() * 1000)))
    store = aws_live_data.AWSDataOnDemandStore('test', MemoryOnDemandStore())
    app = create_app(Settings(app_mode='aws_pull', aws_read_key='test', live_device_id='esp1'), live_store=store)
    client = TestClient(app)
    no_inference = client.get('/api/v1/dashboard').json()
    assert no_inference['latest'] is None
    response = client.post('/api/v1/inference/on-demand')
    assert response.status_code == 200
    analysis = response.json()['analysis']
    assert analysis['description_source'] == 'dictionary'
    assert analysis['generated_by_llm'] is False
    assert '説明辞書' in analysis['message']
    assert analysis['predicted_odor_label'] in odor_narration.DESCRIPTIONS
    assert response.json()['latest']['intensity'] in range(1, 6)
    assert client.get('/api/v1/dashboard').json()['analysis']['description_source'] == 'dictionary'


def test_remote_success_returns_llm_with_provenance(monkeypatch):
    monkeypatch.setattr(odor_narration, 'remote_description',
        lambda *args: ('実際のLLM応答という想定のテスト文章です。', True))
    text, generated = odor_narration.describe('vinegar', 4, .95, 'https://example.run.app')
    assert generated and 'テスト文章' in text
    assert odor_narration.description_source('vinegar', 4, generated) == 'llm'


def test_failures_are_dictionary_but_never_falsely_called_llm(monkeypatch):
    def fail(*args):
        raise TimeoutError('sensitive-message-do-not-leak')
    monkeypatch.setattr(odor_narration, 'remote_description', fail)
    text, generated = odor_narration.describe('shoe', 3, .97, 'https://example.run.app')
    assert not generated and odor_narration.description_source('shoe',3,generated)=='dictionary'
    assert '靴' in text and '説明辞書' in text
    assert 'sensitive-message' not in text


def test_low_score_does_not_start_gpu_and_marks_unverified(monkeypatch):
    monkeypatch.setattr(odor_narration, 'remote_description',
        lambda *args: pytest.fail('Low CNN score must not start GPU'))
    text, generated = odor_narration.describe('ammonia', 4, .48, 'https://example.run.app')
    assert not generated
    assert '分類結果が不確か' in text and '説明辞書' in text


def test_zero_signal_and_unknown_labels_are_not_fabricated():
    zero_text, zero_llm = odor_narration.describe('ammonia',0,.99,'https://example.run.app')
    assert not zero_llm and 'アンモニア' not in zero_text
    assert odor_narration.description_source('ammonia',0,zero_llm)=='no_signal'
    txt, llm = odor_narration.describe('unknown-chemical',5,.99,None)
    assert not llm and '見つかりません' in txt
    assert odor_narration.description_source('unknown-chemical',5,llm)=='unavailable'


def test_ui_loading_switches_back_after_success_or_error():
    script = (ROOT / 'web/app.js').read_text(encoding='utf-8')
    template = (ROOT / 'web/index.html').read_text(encoding='utf-8')
    css = (ROOT / 'web/styles.css').read_text(encoding='utf-8')
    assert '判定中です' in template and 'id="analysis-progress"' in template
    assert "byId('analysis-progress').hidden = false;" in script
    assert "byId('result-cards').hidden = true;" in script
    assert '  } finally {' in script
    assert "byId('analysis-progress').hidden = true;" in script
    assert "byId('result-cards').hidden = false;" in script
    assert "action.disabled = true;" in script
    assert "action.disabled = false;" in script
    assert 'setInterval(' not in script
    assert 'animation:olfalink-spin' in css
    assert 'prefers-reduced-motion:reduce' in css


def test_source_markers_never_confuse_dictionary_and_llm():
    from app.schemas import AnalysisResult, AnalysisStatus
    src = AnalysisResult(status=AnalysisStatus.READY,title='ok',message='辞書',generated_by_llm=False,description_source='dictionary')
    assert src.model_dump()['description_source']=='dictionary'
    with pytest.raises(Exception):
        AnalysisResult(status=AnalysisStatus.READY,title='bad',message='x',description_source='untrusted_external')
