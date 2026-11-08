"""AWS /data real nested field names, units, provenance, and model regression."""
from __future__ import annotations

import time
from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import create_app
from app.services import aws_live_data
from app.services.live_stream import select_time_split_90s
from app.services.on_demand_store import MemoryOnDemandStore


def live_row(ms: int, *, sgp_nox: int | None = 14000, mac: str = 'test') -> dict:
    return {
        'device': 'esp1', 'mac': mac, 'seq': 1200, 'epoch_ms': ms,
        'led': 'unlabeled',
        'bme': {'t': 22.3, 'rh': 36.89, 'p': 1027.09,
                'gas_kohm': 325, 'gas_ohm': 324976},
        'sgp': {'voc': 15000, 'nox': sgp_nox, 'voc_index': 101, 'nox_index': 21},
        'tgs2602': {'adc': 1200, 'rs_kohm': 48.0, 'vout': 1.3},
        'tgs2603': {'adc': 1100, 'rs_kohm': 50.0, 'vout': 1.2},
        'health': {'bme': True, 'sgp': True},
    }


def test_nested_aws_response_maps_seven_raw_channels():
    now = 1900000000000
    payload = {'device':'esp1','count': 1,'truncated':False,
               'rows': [live_row(now - 2000)]}
    rows, session, end = aws_live_data.normalize(payload,'esp1',now_ms=now)
    assert session == 'aws_live' and end == now - 2000
    assert rows[0] == {
        'epoch_ms': now-2000, 'led':'ambient',
        'bme_gas_ohm':324976.0, 'bme_t':22.3, 'bme_rh':36.89,
        'tgs2602_adc':1200.0, 'tgs2603_adc':1100.0,
        'sgp_voc':15000.0, 'sgp_nox':14000.0,
    }


def test_nested_bme_kohm_unit_conversion_and_invalid_channel_preserved():
    now=1900000000000
    r=live_row(now)
    del r['bme']['gas_ohm']
    r['sgp']['nox']='not-a-number'
    normalized,_,_=aws_live_data.normalize({'rows':[r]},'esp1',now_ms=now)
    assert normalized[0]['bme_gas_ohm']==325000
    assert normalized[0]['sgp_nox'] is None


def test_nested_json_continuous_data_reaches_actual_cnn_without_llm(monkeypatch):
    now=int(time.time()*1000)
    # 58 rows / 120s was observed in AWS; roughly 2-second cadence.
    # Provide 58 samples ending now, covering the full 90s model input.
    payload={'device':'esp1','count':58,'truncated':False,
             'rows':[live_row(now - 114_000 + i*2000) for i in range(58)]}
    actual,_,_=aws_live_data.normalize(payload,'esp1',now_ms=now)
    trace,_=select_time_split_90s(actual)
    assert trace.shape==(128,10)
    def fake_fetch(key, device, **kwargs):
        assert key == 'placeholder-key'
        return aws_live_data.normalize(payload,device,now_ms=now)
    monkeypatch.setattr(aws_live_data,'fetch_window',fake_fetch)
    config=Settings(app_mode='aws_pull',aws_read_key='placeholder-key',
                    live_device_id='esp1', demo_fallback_enabled=True,
                    llm_enabled=False)
    client=TestClient(create_app(config,live_store=aws_live_data.AWSDataOnDemandStore(
        'placeholder-key', MemoryOnDemandStore(), demo_fallback=True)))
    result=client.post('/api/v1/inference/on-demand')
    assert result.status_code==200, result.text
    data=result.json()
    assert data['analysis']['status']=='ready'
    assert data['analysis']['input_origin']=='device'
    assert data['latest']['source']=='device'
    assert data['analysis']['phase_assignment']['used_led_signal'] is False
    assert data['analysis']['generated_by_llm'] is False
    assert data['analysis']['predicted_odor_label']


def test_missing_nested_channels_fail_closed_no_fabricated_measurement():
    now=1900000000000
    payload={'rows':[live_row(now - 114_000 + 2000*i,sgp_nox=None) for i in range(58)]}
    normalized,_,_=aws_live_data.normalize(payload,'esp1',now_ms=now)
    with pytest.raises(ValueError,match='sgp_nox_raw'):
        select_time_split_90s(normalized)
