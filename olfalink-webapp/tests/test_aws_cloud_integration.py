"""Contract checks with fabricated AWS Excel and the user-supplied CNN weights."""
from __future__ import annotations
from io import BytesIO
import csv
import json
from pathlib import Path
from openpyxl import Workbook
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.services import aws_sessions
from app.services.combined_dashboard import aws_dashboard
from app.services.odor_narration import describe

BASE=Path(__file__).resolve().parent.parent


def fixture_rows():
    with (BASE/'tests/fixtures/SHOE_intensity_3_SYN_001.csv').open(encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def build_xlsx():
    rows=fixture_rows()
    wb=Workbook(); ws=wb.active; ws.title='sessions'
    ws.append(['session','name','note']);ws.append([51,'SHOE training',''])
    ws2=wb.create_sheet('data')
    headers=list(rows[0].keys())
    ws2.append(headers)
    for row in rows:
        ws2.append([51 if k=='session' else row[k] for k in rows[0]])
    output=BytesIO();wb.save(output)
    return output.getvalue()


def test_excel_to_true_cnn(monkeypatch):
    source=build_xlsx()
    calls=[]
    def mock_download(url,secret):
        calls.append(url)
        assert secret=='local-test-secret'
        return json.dumps({'version':101},ensure_ascii=False).encode() if url==aws_sessions.MANIFEST_URL else source
    monkeypatch.setattr(aws_sessions,'_download',mock_download)
    monkeypatch.setattr(aws_sessions,'subscriber',aws_sessions.AWSSubscriber())
    # The dashboard module imported subscriber, so patch that binding too.
    import app.services.combined_dashboard as combined
    monkeypatch.setattr(combined,'subscriber',aws_sessions.subscriber)
    result=aws_dashboard('local-test-secret')
    assert result.mode=='aws'
    assert result.latest is not None and result.latest.intensity==3
    assert result.analysis.status.value=='ready'
    assert result.analysis.generated_by_llm is False
    assert result.device.connected is False
    assert result.analysis.evidence.source_reading_ids==[result.latest.id]
    assert result.latest.channels=={}
    assert len(calls)==2
    # A subsequent refresh inside TTL must not re-download the Excel.
    aws_dashboard('local-test-secret')
    assert len(calls)==2


def test_reject_unlabeled_session():
    content=build_xlsx()
    from openpyxl import load_workbook
    wb=load_workbook(BytesIO(content)); wb['sessions']['B2']=''
    file=BytesIO();wb.save(file)
    assert aws_sessions._decode_book(file.getvalue())==[]


def test_aws_mode_api_no_raw_or_mock(monkeypatch):
    import app.main as main
    from app.services.combined_dashboard import make_empty
    monkeypatch.setattr(main,'aws_dashboard',lambda *_: make_empty('AWS未設定'))
    client=TestClient(create_app(Settings(app_mode='aws')))
    assert client.get('/api/v1/readings').json()==[]
    assert client.get('/api/v1/dashboard').json()['analysis']['message']=='AWS未設定'
    assert client.get('/api/v1/healthz').status_code==200
    assert client.post('/api/v1/readings',json={'device_id':'olfalink-01','intensity':2}).status_code==503


def test_description_is_not_llm_imitation():
    s,used=describe('ammonia',5,.92,None)
    assert 'アンモニア' in s and used is False
    s,used=describe('ammonia',0,.92,None)
    assert 'アンモニア' not in s and used is False


def test_diagnostic_endpoint_reports_usable_sessions(monkeypatch):
    from app.services import aws_sessions
    rows = aws_sessions._decode_book(build_xlsx())

    class StandInSubscriber:
        def fetch(self, supplied_key):
            assert supplied_key == 'diagnostic-test-only'
            return rows, {'version': 12345}

    monkeypatch.setattr(aws_sessions, 'subscriber', StandInSubscriber())
    client = TestClient(create_app(Settings(app_mode='aws', aws_read_key='diagnostic-test-only')))
    body = client.get('/api/v1/integration/status').json()
    assert body['aws_reachable'] is True
    assert body['model_compatible_sessions'] == 1
    assert body['named_sessions'] == 1
    assert body['live_stream'] is False
    assert body['dataset_version'] == 12345
    assert 'diagnostic-test-only' not in str(body)


def test_diagnostic_endpoint_missing_key():
    client = TestClient(create_app(Settings(app_mode='aws', aws_read_key=None)))
    body = client.get('/api/v1/integration/status').json()
    assert body['aws_reachable'] is False
    assert 'key' in body['reason'].lower()


def test_llm_fallback_flag_tracks_real_model_use(monkeypatch):
    import app.services.odor_narration as narration
    monkeypatch.setattr(narration, 'remote_description', lambda *_: ('テキスト説明', False))
    message, llm_used = narration.describe('shoe', 3, .92, 'https://example.run.app')
    assert message == 'テキスト説明'
    assert llm_used is False
