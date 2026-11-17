"""Contract edges; run in either CPU worker image (torch, numpy, FastAPI)."""
import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient
import pytest


def module(name):
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(name, root / f'capabilities/templates/clover-{name}/main.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gliner, tspulse = module('gliner'), module('tspulse')


def test_gliner_batch_keeps_per_input_labels_and_unicode_offsets(monkeypatch):
    class Model:
        def predict_entities(self, text, labels, threshold, flat_ner):
            assert labels in [['person'], ['location']]
            assert threshold == .3 and flat_ner
            return [dict(text=text[2:], start=2, end=len(text), label=labels[0], score=.8)]
    monkeypatch.setattr(gliner, 'model', Model())
    # No lifespan: this unit check deliberately supplies the model.
    client = TestClient(gliner.app)
    result = client.post('/predict', json={'inputs': [
        {'text': '☀ Alice', 'labels': 'person'}, {'text': '☀ Paris', 'labels': '["location"]'},
        {'text': 'blank labels', 'labels': []}]})
    assert result.status_code == 200
    assert result.json()['usage']['cost'] == 0
    rows = result.json()['outputs']
    assert rows[0][0]['label'] == 'person' and rows[1][0]['label'] == 'location' and rows[2] == []


@pytest.mark.parametrize('item', [{'text': {}}, {'labels': {}}, {'labels': [1]}, {'threshold': 2}, {'threshold': 'NaN'}])
def test_gliner_rejects_invalid_inputs_before_inference(item):
    assert TestClient(gliner.app).post('/predict', json={'inputs': [item]}).status_code == 422


def test_health_does_not_claim_unloaded_models(monkeypatch):
    monkeypatch.setattr(gliner, 'model', None)
    monkeypatch.setattr(tspulse, 'tspulse_reconstruct_model', None)
    assert TestClient(gliner.app).get('/health').status_code == 503
    assert TestClient(tspulse.app).get('/health').status_code == 503


@pytest.mark.parametrize('operation,payload', [
    ('unknown', {}), ('timeseries_embed', {'values': [1]*511}),
    ('timeseries_anomalies', {'values': [1]*1535}),
    ('timeseries_impute', {'values': [None]*512, 'stride': 0}),
    ('timeseries_similarity', {'left': [1]*512, 'right': 'bad JSON'}),
])
def test_invalid_series_is_rejected_without_model_calls(operation, payload):
    r = TestClient(tspulse.app).post('/specialists/' + operation + '/predict', json={'inputs': [payload]})
    assert r.status_code == 422
    assert len(r.text) < 1000


def test_imputation_windows_cover_non_multiple_edges():
    starts = tspulse._window_starts(1025, 512, 256)
    assert starts == [0, 256, 512, 513]
    assert set(range(1025)) == {i for start in starts for i in range(start,start+512)}
    with pytest.raises(ValueError):
        tspulse._tspulse_values([None]*512, field_name='values', min_length=512, allow_missing=True)
    with pytest.raises(ValueError):
        tspulse._tspulse_values([1]*511+[None], field_name='values', min_length=512, allow_missing=False)
