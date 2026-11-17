import pytest
import json
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app


def test_health_requires_weights_gpu_and_credentials(monkeypatch):
    monkeypatch.setenv("CLOVER_COMPUTER_KEY", "test-key")
    monkeypatch.setattr(app, "weights_present", lambda root: True)
    monkeypatch.setattr(app, "cuda_available", lambda: False)
    client = TestClient(app.app)
    assert client.get("/health").status_code == 503
    monkeypatch.setattr(app, "cuda_available", lambda: True)
    assert client.get("/health").status_code == 200
    monkeypatch.setattr(app, "weights_present", lambda root: False)
    assert client.get("/health").status_code == 503


def test_weights_check_detects_missing_shards(tmp_path):
    for path in ["base/config.json", "base/tokenizer_config.json", "base/one.safetensors",
                 "adapters/text/adapter_config.json", "adapters/text/adapter_model.safetensors",
                 "adapters/multimodal/adapter_config.json", "adapters/multimodal/adapter_model.safetensors"]:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("fixture")
    index = tmp_path / "base/model.safetensors.index.json"
    index.write_text(json.dumps({"weight_map":{"a":"one.safetensors","b":"two.safetensors"}}))
    assert not app.weights_present(tmp_path)
    (tmp_path / "base/two.safetensors").write_text("fixture")
    assert app.weights_present(tmp_path)


def decision(**values):
    return app.Decision(
        goal="Open the latest report",
        app="report portal",
        observation="Two report links",
        options=[
            {"id": "latest", "role": "link", "label": "September", "action": "click"},
            {"id": "older", "role": "link", "label": "August", "action": "click"},
        ],
        **values,
    )


def test_missing_visual_input_and_duplicate_ids_are_rejected():
    with pytest.raises(ValidationError):
        decision(modality="multimodal")
    value = decision().model_dump()
    value["options"][1]["id"] = value["options"][0]["id"]
    with pytest.raises(ValidationError):
        app.Decision(**value)


def test_busy_gpu_returns_retry_instead_of_loading_a_second_model():
    runtime = app.Runtime(factory=lambda mode: pytest.fail("Concurrent model load"))
    runtime.lock.acquire()
    try:
        with pytest.raises(app.HTTPException) as error:
            runtime.run(app.Batch(inputs=[decision()]))
        assert error.value.status_code == 429
    finally:
        runtime.lock.release()


def test_repeated_calls_reuse_model_and_report_cold_load_separately():
    loaded = []

    class Model:
        def __init__(self, mode):
            self.modality, self.load_ms = mode, 23
            loaded.append(mode)

        def score(self, value):
            return {"selected_id": "latest", "metadata": {}}

    runtime = app.Runtime(factory=Model)
    batch = app.Batch(inputs=[decision()])
    assert runtime.run(batch)["outputs"][0]["metadata"]["load_ms"] == 23
    assert runtime.run(batch)["outputs"][0]["metadata"]["load_ms"] == 0
    assert loaded == ["text"]


def test_worker_requires_its_own_key_not_the_callers_gateway_key(monkeypatch):
    monkeypatch.setenv("CLOVER_COMPUTER_KEY", "worker-key")
    client = TestClient(app.app)
    body = {"inputs": [decision().model_dump()]}
    assert client.post("/predict", json=body).status_code == 401
    assert (
        client.post(
            "/predict", json=body, headers={"Authorization": "Bearer caller-key"}
        ).status_code
        == 401
    )
    monkeypatch.setattr(
        app.runtime, "run", lambda batch: {"outputs": [{"selected_id": "latest"}]}
    )
    assert (
        client.post(
            "/predict", json=body, headers={"Authorization": "Bearer worker-key"}
        ).json()["outputs"][0]["selected_id"]
        == "latest"
    )
    monkeypatch.delenv("CLOVER_COMPUTER_KEY")
    assert (
        client.post(
            "/predict", json=body, headers={"Authorization": "Bearer worker-key"}
        ).status_code
        == 401
    )
