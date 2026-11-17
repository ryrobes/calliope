#!/usr/bin/env python3
"""Exercise a self-hosted gateway. --inference makes small paid provider calls."""

import argparse
import io
import json
import math
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid
import wave


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:18090")
    parser.add_argument("--inference", action="store_true")
    parser.add_argument(
        "--audio",
        action="store_true",
        help="Also synthesize and transcribe a short phrase (paid)",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    key = json.loads((args.config / "local-keys.json").read_text())["service_key"]
    result = {
        "url": args.url,
        "checks": {},
        "paid_inference": args.inference or args.audio,
    }

    def request(path, body=None, *, auth=True, content_type="application/json"):
        headers = {"Authorization": "Bearer " + key} if auth else {}
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        if body is not None:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(
            args.url.rstrip("/") + path, data=body, headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)

    def checked(name, action):
        start = time.monotonic()
        try:
            value = action()
            result["checks"][name] = {
                "passed": True,
                "seconds": round(time.monotonic() - start, 3),
                **(value or {}),
            }
        except Exception as error:
            # Do not dump upstream response bodies, request headers, or credentials.
            result["checks"][name] = {
                "passed": False,
                "error": str(error).replace(key, "[redacted]")[:300],
            }
        print(
            name + ": " + ("PASS" if result["checks"][name]["passed"] else "FAIL"),
            flush=True,
        )

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def get_json(path):
        status, body, _ = request(path)
        require(status == 200, path + " HTTP " + str(status))
        return json.loads(body)

    def inventory():
        value = get_json("/v1/capabilities?probe=true")
        require(value["billing"] == "static", "Expected static tenants")
        return {
            "routes": [
                {k: row.get(k) for k in ("id", "state", "model_version")}
                for row in value["data"]
            ]
        }

    checked("inventory", inventory)

    def disabled_route():
        routes = get_json("/v1/capabilities")["data"]
        route = next(
            (
                r
                for r in routes
                if r["kind"] == "specialist" and r["state"] == "disabled"
            ),
            None,
        )
        if route is None:
            return {"skipped": "No disabled specialist configured"}
        status, body, _ = request(
            "/b/" + route["id"] + "/predict", {"inputs": ["smoke"]}
        )
        require(
            status == 503
            and json.loads(body)["error"]["code"] == "capability_unavailable",
            "Disabled route must fail explicitly",
        )
        return {"route": route["id"]}

    checked("disabled_route", disabled_route)
    checked(
        "authentication",
        lambda: require(
            request("/v1/capabilities", auth=False)[0] == 401,
            "Missing key must be rejected",
        ),
    )
    checked(
        "models",
        lambda: {"models": [row["id"] for row in get_json("/v1/models")["data"]]},
    )
    checked(
        "media_profiles",
        lambda: {
            "media": get_json("/v1/media/models"),
            "speech": get_json("/v1/audio/models"),
        },
    )
    if args.inference:

        def embeddings():
            status, body, _ = request(
                "/v1/embeddings",
                {
                    "model": "embed",
                    "input": [
                        "Repair the rooftop HVAC unit.",
                        "A penguin swims in the ocean.",
                    ],
                },
            )
            require(status == 200, "Embedding HTTP " + str(status))
            vectors = [row["embedding"] for row in json.loads(body)["data"]]
            require(
                len(vectors) == 2 and len(vectors[0]) == len(vectors[1]) > 0,
                "Invalid embedding batch",
            )
            require(
                all(math.isfinite(x) for v in vectors for x in v),
                "Non-finite embedding",
            )
            require(
                vectors[0] != vectors[1], "Different texts returned identical vectors"
            )
            return {
                "dimensions": len(vectors[0]),
                "norms": [round(math.sqrt(sum(x * x for x in v)), 5) for v in vectors],
            }

        def chat(stream):
            status, body, _ = request(
                "/v1/chat/completions",
                {
                    "model": "calliope",
                    "messages": [
                        {"role": "user", "content": "Reply with exactly: Clover ready"}
                    ],
                    "max_tokens": 32,
                    "stream": stream,
                },
            )
            require(status == 200, "Chat HTTP " + str(status))
            if stream:
                parts = [
                    json.loads(line[6:])
                    for line in body.decode().splitlines()
                    if line.startswith("data: ") and line[6:] != "[DONE]"
                ]
                content = "".join(
                    choice.get("delta", {}).get("content") or ""
                    for part in parts
                    for choice in part.get("choices", [])
                )
                usage = next(
                    (p["usage"] for p in reversed(parts) if p.get("usage")), None
                )
                require(b"[DONE]" in body, "Stream did not complete")
            else:
                value = json.loads(body)
                content = value["choices"][0]["message"]["content"]
                usage = value.get("usage")
            require("clover" in content.lower(), "Unexpected chat output")
            return {"usage": usage}

        def decisions():
            status, body, _ = request(
                "/b/decisions/predict",
                {
                    "inputs": [
                        {
                            "state": {
                                "customer_message": "Please cancel my appointment for tomorrow."
                            },
                            "questions": {
                                "request": {
                                    "type": "choice",
                                    "instructions": "Classify the customer request.",
                                    "criteria": {
                                        "cancel": "Cancel the appointment",
                                        "book": "Book a new appointment",
                                    },
                                }
                            },
                        }
                    ]
                },
            )
            require(status == 200, "Decision HTTP " + str(status))
            value = json.loads(body)["outputs"][0]
            require(value["answers"]["request"]["choice"] == "cancel", "Wrong decision")
            return {"model": value.get("model"), "usage": value.get("usage")}

        checked("embeddings", embeddings)
        checked("chat", lambda: chat(False))
        checked("chat_stream", lambda: chat(True))
        checked("decisions", decisions)

        def rerank():
            status, body, _ = request("/v1/rerank", {
                "model": "rerank", "query": "Repair an air conditioner",
                "documents": ["Penguins swim in the ocean.", "An HVAC technician fixes the broken AC."],
                "top_n": 2,
            })
            require(status == 200, "Rerank HTTP " + str(status))
            results = json.loads(body)["results"]
            require(len(results) == 2 and results[0]["index"] == 1, "Wrong rerank order")
            require(all(math.isfinite(r["relevance_score"]) for r in results), "Invalid rerank score")
            return {"results": results}

        checked("rerank", rerank)

    if args.audio:

        def audio():
            status, pcm, headers = request(
                "/v1/audio/speech",
                {
                    "model": "calliope-voice",
                    "input": "Clover is ready.",
                    "response_format": "pcm",
                },
            )
            require(status == 200, "Speech HTTP " + str(status))
            require(len(pcm) > 1000 and len(pcm) % 2 == 0, "Invalid PCM")
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(44100)
                wav.writeframes(pcm)
            boundary = "clover-" + uuid.uuid4().hex
            form = (
                (
                    "--"
                    + boundary
                    + '\r\nContent-Disposition: form-data; name="model"\r\n\r\ncalliope-stt\r\n--'
                    + boundary
                    + '\r\nContent-Disposition: form-data; name="file"; filename="smoke.wav"\r\nContent-Type: audio/wav\r\n\r\n'
                ).encode()
                + buffer.getvalue()
                + ("\r\n--" + boundary + "--\r\n").encode()
            )
            status, body, _ = request(
                "/v1/audio/transcriptions",
                form,
                content_type="multipart/form-data; boundary=" + boundary,
            )
            require(status == 200, "Transcription HTTP " + str(status))
            text = json.loads(body).get("text", "")
            require("ready" in text.lower(), "Unexpected transcription")
            return {"pcm_bytes": len(pcm), "transcript": text}

        checked("audio_roundtrip", audio)

    result["passed"] = all(check["passed"] for check in result["checks"].values())
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
