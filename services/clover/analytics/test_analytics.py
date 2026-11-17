import base64
import json
import os
import pickle
import sys
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'capabilities/templates/clover-analytics'))
import main as worker

X = [[1, 1], [1.1, .9], [.9, 1.1], [1.05, 1], [.95, .98], [1.02, 1.03], [1, .97]]


def invoke(client, operation, payload):
    return client.post('/predict', json={'inputs': [{'operation': operation, 'payload': payload}]})


@pytest.mark.parametrize('algorithm', ['isolation_forest', 'lof', 'autoencoder'])
@pytest.mark.parametrize('categorical', [False, True])
def test_persisted_models_survive_worker_restart(algorithm, categorical):
    rows = [r + ['repair' if i % 2 else 'install'] for i, r in enumerate(X)] if categorical else X
    payload = {'X': rows, 'algorithm': algorithm, 'cat_features': [2] if categorical else []}
    with TestClient(worker.create_app()) as client:
        fit = invoke(client, 'anomaly_fit', payload)
        assert fit.status_code == 200, fit.text
        model = fit.json()['outputs'][0]
        points = [[1, 1, 'new-category'], [9, 9, 'new-category']] if categorical else [[1, 1], [9, 9]]
        score_request = {'X': points, 'model_blob_b64': model['blob_b64'], 'model_sha256': model['blob_sha256']}
        before = invoke(client, 'anomaly_score', score_request).json()['outputs'][0]['scores']
    with TestClient(worker.create_app()) as client:
        assert invoke(client, 'anomaly_score', {'X': points, 'model_sha256': model['blob_sha256']}).status_code == 412
        after = invoke(client, 'anomaly_score', score_request)
        assert after.status_code == 200, after.text
        assert np.allclose(after.json()['outputs'][0]['scores'], before)
        assert before[1] > before[0]
        # A cached SHA cannot hide an altered blob.
        score_request['model_blob_b64'] = base64.b64encode(b'bad model').decode()
        assert invoke(client, 'anomaly_score', score_request).status_code == 422


def test_legacy_model_if_supplied():
    path = Path('/fixtures/legacy.json')
    if not path.exists():
        pytest.skip('No live legacy artifact fixture supplied')
    model = json.loads(path.read_text())
    with TestClient(worker.create_app()) as client:
        result = invoke(client, 'anomaly_score', {'X': [[1, 1], [9, 9]], 'model_blob_b64': model['blob_b64']})
        assert result.status_code == 200, result.text
        scores = result.json()['outputs'][0]['scores']
        assert scores[1] > scores[0]


@pytest.mark.parametrize('payload', [
    {'X': []}, {'X': [[1], [2, 3]]}, {'X': [['repair']]}, {'X': [[None]]},
    {'X': [[1]], 'cat_features': [2]}, {'X': [[1]], 'feature_names': ['a', 'b']},
    {'X': X, 'algorithm': 'fabricated'},
])
def test_bad_matrices(payload):
    with TestClient(worker.create_app()) as client:
        assert invoke(client, 'anomaly_fit', payload).status_code == 422


def test_rejects_arbitrary_pickle_global():
    class Bad:
        def __reduce__(self):
            return os.system, ('touch /tmp/should-not-exist-clover',)
    blob = base64.b64encode(pickle.dumps(Bad())).decode()
    with TestClient(worker.create_app()) as client:
        assert invoke(client, 'anomaly_score', {'X': [[1]], 'model_blob_b64': blob}).status_code == 422
    assert not Path('/tmp/should-not-exist-clover').exists()


def embedding_transport(calls, failure=None):
    def respond(request):
        calls.append(request)
        if failure:
            return httpx.Response(503, json={'secret': 'must-not-echo'})
        rows = json.loads(request.content)['inputs']
        vectors = [[0, .01 * i] if x.startswith('repair') else [10, 10 + .01 * i] for i, x in enumerate(rows)]
        return httpx.Response(200, json={'outputs': vectors, 'usage': {'cost': .00001, 'prompt_tokens': len(rows)},
                                        'hutch': {'model_version': 'openrouter/voyageai/voyage-4@1024-float-v1'}})
    return httpx.MockTransport(respond)


@pytest.mark.parametrize('k', ['2', '', '0'])
def test_cluster_shared_embeddings(k):
    calls = []
    texts = ['repair compressor', 'repair fan', 'invoice overdue', 'invoice paid', 'repair pump', 'invoice requested']
    with TestClient(worker.create_app(key='service-test', transport=embedding_transport(calls))) as client:
        response = invoke(client, 'cluster', {'values': json.dumps(texts), 'num_clusters': k})
        assert response.status_code == 200, response.text
        result = response.json()['outputs'][0]
        assert len(result['assignments']) == len(texts)
        assert sorted(i for c in result['clusters'] for i in c['members']) == list(range(len(texts)))
        if k == '2':
            assert result['assignments'][0] == result['assignments'][1]
            assert result['assignments'][0] != result['assignments'][2]
        assert result['usage']['already_metered'] and result['usage']['cost'] > 0
        assert result['embedding_profile'].startswith('openrouter/voyageai/voyage-4')
        assert calls[0].headers['authorization'] == 'Bearer service-test'
        assert calls[0].url.path == '/b/embed/predict'


@pytest.mark.parametrize('values', [[], ['repair'], ['repair', 'repair']])
def test_cluster_small_inputs(values):
    calls = []
    with TestClient(worker.create_app(key='service-test', transport=embedding_transport(calls))) as client:
        response = invoke(client, 'cluster', {'values': values})
        assert response.status_code == 200, response.text
        assert len(response.json()['outputs'][0]['assignments']) == len(values)
        assert bool(calls) == bool(values)


def test_provider_failure_is_not_a_fake_result():
    with TestClient(worker.create_app(key='service-test', transport=embedding_transport([], failure=True))) as client:
        result = invoke(client, 'cluster', {'values': ['repair']})
        assert result.status_code == 502 and 'secret' not in result.text


def test_invalid_batch_makes_no_paid_requests():
    calls = []
    with TestClient(worker.create_app(key='service-test', transport=embedding_transport(calls))) as client:
        response = client.post('/specialists/cluster/predict', json={'inputs': [{'values': ['repair']}, {'values': 'bad-json'}]})
        assert response.status_code == 422 and not calls


def test_missing_gateway_key_and_empty_scoring():
    with TestClient(worker.create_app(key='')) as client:
        assert invoke(client, 'cluster', {'values': ['repair']}).status_code == 503
        assert invoke(client, 'anomaly_score', {'X': []}).json()['outputs'][0]['scores'] == []
        assert client.get('/health').json()['embedding_configured'] is False


def test_lof_tree_model_reloads():
    p = worker.prepare('anomaly_fit', {'X': [[i / 100, i / 80] for i in range(100)], 'algorithm': 'lof'})
    fit = worker.Models().fit(p)
    result = worker.Models().score(worker.prepare('anomaly_score', {'X': [[.5, .5], [10, 10]], 'model_blob_b64': fit['blob_b64']}))
    assert result['scores'][1] > result['scores'][0]
