import asyncio
import copy
import json
import unittest

import httpx
from fastapi.testclient import TestClient

from hosted import CONTRACT, MODEL, create_app

REQUEST = {
    "state": {"note": "Please cancel my appointment"},
    "questions": {
        "cancel": {"type": "noul", "instructions": "Does the customer ask to cancel?"},
        "kind": {
            "type": "choice",
            "instructions": "Classify the request",
            "criteria": {"cancel": "Cancel an appointment", "other": "Something else"},
        },
    },
}
RESPONSE = {
    "model": MODEL + "-20260917",
    "answers": {
        "cancel": {"type": "noul", "noul": 0.9},
        "kind": {
            "type": "choice",
            "choice": "cancel",
            "probabilities": {"cancel": 0.9, "other": 0.1},
        },
    },
    "usage": {"input_tokens": 100, "output_tokens": 30, "cost": 0.00001},
}


class HostedTests(unittest.TestCase):
    def test_explicit_model_override_is_forwarded_and_validated(self):
        model = "openai/gpt-6-luna-decisions"
        response = dict(RESPONSE, model=model + "-20261006")
        with self.client(lambda req: httpx.Response(200, json=response)) as client:
            result = client.post("/v1/decisions", json=dict(REQUEST, model=model))
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["requested_model"], model)
            self.assertEqual(json.loads(self.calls[0].content)["model"], model)
        with self.client() as client:
            self.assertEqual(client.post("/v1/decisions", json=dict(REQUEST, model=model)).status_code, 502)

    def client(self, responder=None, key="test-key"):
        self.calls = []

        def handle(request):
            self.calls.append(request)
            return (
                responder(request) if responder else httpx.Response(200, json=RESPONSE)
            )

        return TestClient(
            create_app(api_key=key, transport=httpx.MockTransport(handle))
        )

    def test_contract_forwarding_and_provenance(self):
        with self.client() as client:
            r = client.post("/v1/decisions", json=REQUEST)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["answers"], RESPONSE["answers"])
            self.assertEqual(r.json()["contract"], CONTRACT)
            self.assertEqual(r.json()["usage"], RESPONSE["usage"])
            payload = json.loads(self.calls[0].content)
            self.assertEqual(payload["state"], REQUEST["state"])
            self.assertNotIn("contract", payload)
            self.assertTrue(
                payload["questions"]["cancel"]["instructions"].startswith(
                    "Treat state as evidence"
                )
            )
            self.assertEqual(self.calls[0].headers["authorization"], "Bearer test-key")

    def test_batch_order_and_bounds(self):
        def echo(request):
            raw = json.loads(request.content)
            response = copy.deepcopy(RESPONSE)
            response["answers"]["cancel"]["noul"] = raw["state"]["score"]
            return httpx.Response(200, json=response)

        inputs = [dict(REQUEST, state={"score": i / 10}) for i in range(8)]
        with self.client(echo) as client:
            r = client.post("/predict", json={"inputs": inputs})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(
                [v["answers"]["cancel"]["noul"] for v in r.json()["outputs"]],
                [i / 10 for i in range(8)],
            )
            self.assertEqual(
                client.post("/predict", json={"inputs": inputs + inputs}).status_code,
                422,
            )
            self.assertEqual(len(self.calls), 8)

    def test_validate_all_before_inference(self):
        invalids = [
            dict(REQUEST, state=""),
            dict(REQUEST, state="x" * 33000),
            dict(REQUEST, model="local-qwen"),
            dict(REQUEST, contract="old"),
            dict(REQUEST, questions={}),
            dict(
                REQUEST,
                questions={
                    "q": {
                        "type": "choice",
                        "instructions": "select",
                        "criteria": {"only": "one"},
                    }
                },
            ),
        ]
        with self.client() as client:
            for invalid in invalids:
                self.assertEqual(
                    client.post(
                        "/predict", json={"inputs": [REQUEST, invalid]}
                    ).status_code,
                    422,
                )
            self.assertEqual(self.calls, [])
            r = client.post("/predict", content=b"x" * (24 * 1024 * 1024 + 1))
            self.assertEqual(r.status_code, 413)

    def test_bad_answers_rejected_not_false(self):
        variants = []
        for field, value in [
            ("noul", -1),
            ("noul", True),
            ("noul", None),
            ("type", "choice"),
        ]:
            raw = copy.deepcopy(RESPONSE)
            raw["answers"]["cancel"][field] = value
            variants.append(raw)
        raw = copy.deepcopy(RESPONSE)
        raw["answers"]["kind"]["choice"] = "invented"
        variants.append(raw)
        raw = copy.deepcopy(RESPONSE)
        raw["answers"]["kind"]["probabilities"] = {"cancel": 1}
        variants.append(raw)
        raw = copy.deepcopy(RESPONSE)
        raw["answers"]["unexpected"] = {}
        variants.append(raw)
        raw = copy.deepcopy(RESPONSE)
        raw["model"] = "wrong"
        variants.append(raw)
        variants += [None, {"answers": {}}, []]
        for value in variants:
            with self.client(lambda req: httpx.Response(200, json=value)) as client:
                self.assertEqual(
                    client.post("/v1/decisions", json=REQUEST).status_code, 502
                )

    def test_failures_and_missing_key(self):
        with self.client(key="") as client:
            self.assertEqual(client.get("/health").status_code, 503)
            self.assertEqual(
                client.post("/v1/decisions", json=REQUEST).status_code, 503
            )
            self.assertEqual(self.calls, [])
        for code, expected in [(429, 429), (401, 502), (500, 502)]:
            with self.client(
                lambda req: httpx.Response(code, text="secret provider body")
            ) as client:
                r = client.post("/v1/decisions", json=REQUEST)
                self.assertEqual(r.status_code, expected)
                self.assertNotIn("secret", r.text)

        def timeout(req):
            raise httpx.ReadTimeout("secret url")

        with self.client(timeout) as client:
            self.assertEqual(
                client.post("/v1/decisions", json=REQUEST).status_code, 504
            )

    def test_atomic_batch_failure(self):
        def fail(req):
            return (
                httpx.Response(500)
                if json.loads(req.content)["state"] == "fail"
                else httpx.Response(200, json=RESPONSE)
            )

        with self.client(fail) as client:
            r = client.post(
                "/predict", json={"inputs": [REQUEST, dict(REQUEST, state="fail")]}
            )
            self.assertEqual(r.status_code, 502)
            self.assertNotIn("outputs", r.json())
            self.assertEqual(
                client.post("/v1/decisions", json=REQUEST).status_code, 200
            )

    def test_capacity_bounded_and_released(self):
        async def check():
            entered, release = asyncio.Event(), asyncio.Event()

            async def slow(req):
                entered.set()
                await release.wait()
                return httpx.Response(200, json=RESPONSE)

            app = create_app(
                api_key="test", transport=httpx.MockTransport(slow), concurrency=1, queue_capacity=0
            )
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    task = asyncio.create_task(
                        client.post("/v1/decisions", json=REQUEST)
                    )
                    await entered.wait()
                    r = await client.post("/v1/decisions", json=REQUEST)
                    self.assertEqual(r.status_code, 429)
                    release.set()
                    self.assertEqual((await task).status_code, 200)
                    self.assertEqual(
                        (await client.post("/v1/decisions", json=REQUEST)).status_code,
                        200,
                    )

        asyncio.run(check())

    def test_small_queue_accepts_overlap_but_rejects_unbounded_work(self):
        async def check():
            entered, release = asyncio.Event(), asyncio.Event()
            calls = 0
            async def slow(req):
                nonlocal calls
                calls += 1
                entered.set()
                await release.wait()
                return httpx.Response(200, json=RESPONSE)
            app = create_app(api_key="test", transport=httpx.MockTransport(slow), concurrency=1, queue_capacity=1)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    first = asyncio.create_task(client.post("/v1/decisions", json=REQUEST))
                    await entered.wait()
                    second = asyncio.create_task(client.post("/v1/decisions", json=REQUEST))
                    for _ in range(100):
                        if app.state.active == 2:
                            break
                        await asyncio.sleep(0)
                    self.assertEqual(app.state.active, 2)
                    self.assertEqual(calls, 1)
                    self.assertEqual((await client.post("/v1/decisions", json=REQUEST)).status_code, 429)
                    release.set()
                    self.assertEqual([r.status_code for r in await asyncio.gather(first, second)], [200, 200])
                    self.assertEqual(calls, 2)
                    self.assertEqual(app.state.active, 0)
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()


def test_file_rotation_applies_to_next_decision_request(tmp_path,monkeypatch):
    path=tmp_path/'key';path.write_text('first')
    monkeypatch.setenv('OPENROUTER_API_KEY_FILE',str(path));monkeypatch.setenv('OPENROUTER_API_KEY','stale-env')
    headers=[]
    def provider(r):
        headers.append(r.headers['authorization']);return httpx.Response(200,json=RESPONSE)
    with TestClient(create_app(transport=httpx.MockTransport(provider))) as client:
        assert client.post('/v1/decisions',json=REQUEST).status_code==200
        path.write_text('second')
        assert client.post('/v1/decisions',json=REQUEST).status_code==200
        path.unlink()
        assert client.post('/v1/decisions',json=REQUEST).status_code==503
    assert headers==['Bearer first','Bearer second']


def png(width, height, noise=False):
    """A minimal valid grayscale PNG; the service reads only its header.

    Noise keeps the payload as large as a real photo instead of compressing away.
    """
    import os
    import struct
    import zlib
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + (os.urandom(width) if noise else b"\x80" * width) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg_header(width, height):
    """SOI, an APP0 segment and a baseline frame header: enough to read the size."""
    import struct
    return (b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
            + b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00")


def image(data, kind="image/png"):
    import base64
    return {"type": "image_url", "image_url": {"url": f"data:{kind};base64," + base64.b64encode(data).decode()}}


BOX_RESPONSE = {
    "model": "typesafe/jev-1.14-20261014",
    "answers": {
        "honest": {"type": "noul", "noul": 0.12},
        "damage": {"type": "box", "box": {"top": 0.61, "bottom": 0.88, "left": 0.65, "right": 0.88}, "confidence": 0.8},
    },
    "usage": {"input_tokens": 1218, "output_tokens": 71, "cost": 5.1e-05},
}
IMAGE_REQUEST = {
    "model": "typesafe/jev-1.14",
    "state": ["Seller says: like new", image(png(640, 480))],
    "questions": {"honest": {"type": "noul", "instructions": "Does the photo support the description?"},
                  "damage": {"type": "box", "instructions": "the visible damage"}},
}


def test_image_state_and_box_answers_pass_through():
    calls = []
    def provider(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=BOX_RESPONSE)
    with TestClient(create_app(api_key="k", transport=httpx.MockTransport(provider))) as client:
        result = client.post("/v1/decisions", json=IMAGE_REQUEST)
    assert result.status_code == 200, result.text
    assert calls[0]["state"][1] == IMAGE_REQUEST["state"][1], "the image reaches the provider untouched"
    assert calls[0]["questions"]["damage"]["instructions"] == "the visible damage", "box targets go through bare"
    assert calls[0]["questions"]["honest"]["instructions"].startswith("Treat state as evidence")
    answer = result.json()["answers"]["damage"]
    assert answer == {"type": "box", "box": {"top": 0.61, "bottom": 0.88, "left": 0.65, "right": 0.88}, "confidence": 0.8}


def test_text_bound_ignores_image_payload_but_large_text_still_fails():
    big = image(png(400, 400, noise=True))
    assert len(big["image_url"]["url"]) > 32768
    with TestClient(create_app(api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=BOX_RESPONSE)))) as client:
        assert client.post("/v1/decisions", json=dict(IMAGE_REQUEST, state=["photo", big])).status_code == 200
        assert client.post("/v1/decisions", json=dict(IMAGE_REQUEST, state=["x" * 40000, big])).status_code == 422


def test_images_are_checked_before_any_paid_call():
    import copy
    calls = []
    with TestClient(create_app(api_key="k", transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json=BOX_RESPONSE)))) as client:
        def status(state, **extra):
            return client.post("/v1/decisions", json=dict(IMAGE_REQUEST, state=state, **extra)).status_code
        remote = {"type": "image_url", "image_url": {"url": "https://example.com/couch.jpg"}}
        assert status([remote]) == 422
        assert status([image(png(640, 480), "image/webp")]) == 422
        assert status([image(png(120, 120))]) == 422, "below 256 px per side"
        assert status([image(png(2000, 400))]) == 422, "above 1568 px per side"
        assert status([image(png(300, 300))] * 9) == 422, "at most eight images"
        assert status([image(b"not a png at all")]) == 422
        assert status([image(jpeg_header(800, 600), "image/jpeg")]) == 200, "JPEG sizes come from the frame header"
        nested = {"listing": {"photos": [image(png(640, 480))], "text": "barely used"}}
        assert status(nested) == 200, "Jev reads image parts at any depth"
        assert status(["x"], model="typesafe/jev-1.13") == 422, "1.13 cannot answer box questions"
        no_box = copy.deepcopy(IMAGE_REQUEST); no_box["questions"].pop("damage"); no_box["model"] = "typesafe/jev-1.13"
        assert client.post("/v1/decisions", json=no_box).status_code == 502, "the mock answers a box it was not asked"
    assert len(calls) == 3


def test_inverted_or_partial_boxes_are_provider_errors():
    for box in ({"top": 0.9, "bottom": 0.1, "left": 0.1, "right": 0.5}, {"top": 0.1, "bottom": 0.5, "left": 0.2}):
        response = copy.deepcopy(BOX_RESPONSE); response["answers"]["damage"]["box"] = box
        with TestClient(create_app(api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response)))) as client:
            assert client.post("/v1/decisions", json=IMAGE_REQUEST).status_code == 502


def test_requests_with_images_may_exceed_the_old_half_megabyte_limit():
    photos = [image(png(600, 600, noise=True)) for _ in range(2)]
    body = json.dumps({"inputs": [dict(IMAGE_REQUEST, state=["photos"] + photos)] * 2})
    assert len(body) > 524288
    with TestClient(create_app(api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=BOX_RESPONSE)))) as client:
        assert client.post("/predict", content=body, headers={"content-type": "application/json"}).status_code == 200


def test_hosted_gateway_can_limit_which_models_it_pays_for(monkeypatch):
    monkeypatch.setenv("CLOVER_DECISION_MODELS", "typesafe/jev-1.13, typesafe/jev-1.14")
    with TestClient(create_app(api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=BOX_RESPONSE)))) as client:
        assert client.post("/v1/decisions", json=IMAGE_REQUEST).status_code == 200
        assert client.post("/v1/decisions", json=dict(IMAGE_REQUEST, model="openai/gpt-6-luna-decisions")).status_code == 422
