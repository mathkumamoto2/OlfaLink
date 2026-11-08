"""Demo-mode must actually call the generator for low confidence and level 0."""
from fastapi.testclient import TestClient
from unittest.mock import patch
from service import app


def test_low_confidence_demo_invokes_generator():
    with patch('service.load', return_value=(object(),object())) as loader, \
         patch('src.inference.describe', return_value='合成入力からの予測は不確かなため、においは特定できません。') as generator:
        with TestClient(app) as c:
            resp=c.post('/generate', json={'label':'vinegar','intensity':0,
                 'uncalibrated_score':0.02,'demo_mode':True})
    assert resp.status_code==200,resp.text
    assert resp.json()['model_used'] is True
    assert generator.call_count==1
    kwargs=generator.call_args.kwargs
    assert kwargs['demo_mode'] is True
    payload=generator.call_args.args[2]
    assert payload['classification_confidence']==0.02
    assert payload['input_origin']=='synthetic_demo'
    assert payload['sensor_reaction_level']==0


def test_default_suppression_still_works():
    with patch('service.load', return_value=(object(),object())), \
         patch('src.inference.describe') as gen:
        with TestClient(app) as c:
            resp=c.post('/generate', json={'label':'vinegar','intensity':0,
               'uncalibrated_score':0.02})
            assert resp.json()['model_used'] is False
            resp=c.post('/generate', json={'label':'vinegar','intensity':3,
               'uncalibrated_score':0.02})
            assert resp.json()['model_used'] is False
            gen.assert_not_called()


def test_unknown_label_is_rejected_with_demo():
    with patch('service.load', return_value=(object(),object())):
        with TestClient(app) as c:
            resp=c.post('/generate',json={'label':'unknown','intensity':3,
             'uncalibrated_score':0.2,'demo_mode':True})
    assert resp.status_code==422
