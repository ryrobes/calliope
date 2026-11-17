#!/usr/bin/env python3
"""Paid smoke checks for the hosted SQL text judgments (no user data).

--psql-command optionally runs the seven operators' existing registry tests.
Supply a psql command, e.g. 'docker exec -i postgres psql -XAt -U postgres -d rvbbit'.
The command is executed directly, never through a shell. This script does not
alter definitions, clear receipts, or install routes.
"""

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time
import urllib.error
import urllib.request

OPERATORS = ["clover_sentiment", "clover_sentiment_score", "clover_classify",
             "clover_classify_scores", "clover_entails", "clover_toxic", "clover_moderate"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:18090")
    parser.add_argument("--psql-command")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    key = json.loads((args.config / "local-keys.json").read_text())["service_key"]
    report = {"checks": {}, "paid_inference": True}

    def check(name, function):
        start = time.monotonic()
        try:
            result = function()
            report["checks"][name] = {"passed": True, "seconds": round(time.monotonic() - start, 3), "result": result}
        except Exception as error:
            report["checks"][name] = {"passed": False, "error": str(error).replace(key, "[redacted]")[:400]}
        print(name, "PASS" if report["checks"][name]["passed"] else "FAIL", flush=True)

    def predict(route, inputs, expected_status=200):
        request = urllib.request.Request(args.url.rstrip("/") + "/b/" + route + "/predict",
            data=json.dumps({"inputs": inputs}).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                status, body = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, body = error.code, error.read()
        assert status == expected_status, f"{route}: HTTP {status}, expected {expected_status}"
        value = json.loads(body)
        if expected_status != 200:
            assert "outputs" not in value
            return {"status": status}
        outputs = value["outputs"]
        assert len(outputs) == len(inputs), "Missing batch outputs"
        for row in outputs:
            assert row["contract"] == "clover-text-judgments-v1"
            assert row["model"] and row["usage"]["cost"] >= 0
            assert row["usage"]["input_tokens"] > 0
        return outputs

    def sentiment():
        outputs = predict("sentiment", ["I absolutely love this", "Utterly disappointing", "The invoice was sent on Tuesday"])
        assert [o["label"] for o in outputs] == ["positive", "negative", "neutral"]
        assert outputs[0]["score"] > 0.2 and outputs[1]["score"] < -0.2 and abs(outputs[2]["score"]) < 0.4
        return outputs

    def entailment():
        outputs = predict("nli", [{"premise": "Several employees received a bonus", "hypothesis": "Some employees received a bonus"},
                                  {"premise": "The cat sat on the mat", "hypothesis": "The stock market rose today"}])
        assert [o["label"] for o in outputs] == ["entailment", "not_entailment"]
        assert all(set(o["scores"]) == {"entailment", "not_entailment"} for o in outputs)
        return outputs

    def classify():
        labels = ["HVAC repair", "billing", "shipping", "returns", "sales", "account access", "gardening", "plumbing", "painting", "roofing", "food delivery", "insurance", "booking", "cancellation", "networking", "auto repair", "payroll"]
        outputs = predict("classify", [{"text": "The air conditioner is leaking refrigerant and needs repair", "labels": labels},
                                       {"text": "I was charged twice this month", "labels": "billing, shipping, returns, technical support"}])
        assert outputs[0]["label"] == "HVAC repair" and set(outputs[0]["scores"]) == set(labels)
        assert outputs[1]["label"] == "billing"
        assert all(abs(sum(row["scores"].values()) - 1) < 0.02 for row in outputs)
        return outputs

    def moderation():
        outputs = predict("toxicity", ["Have a wonderful day", "You are a worthless idiot.", "I will hurt you if you come here.", "Terrible product. The app crashes every day."])
        assert [o["toxic"] for o in outputs] == [False, True, True, False]
        assert outputs[1]["scores"]["insult"] >= 0.5 and outputs[1]["scores"]["threat"] < 0.5
        assert outputs[2]["scores"]["threat"] >= 0.5
        return outputs

    def batch():
        outputs = predict("sentiment", ["Excellent service" if i % 2 == 0 else "Terrible service" for i in range(32)])
        assert [o["label"] for o in outputs] == ["positive" if i % 2 == 0 else "negative" for i in range(32)]
        return {"rows": 32, "cost_usd": sum(o["usage"]["cost"] for o in outputs)}

    for name, function in [("sentiment", sentiment), ("entailment", entailment), ("classification", classify), ("moderation", moderation), ("ordered_32_row_batch", batch)]:
        check(name, function)
    # Hutch translates an upstream validation failure to its established 502.
    check("invalid_batch_is_error", lambda: predict("sentiment", ["valid", {"text": ""}], 502))

    if args.psql_command:
        command = shlex.split(args.psql_command) + ["-q", "-v", "ON_ERROR_STOP=1"]

        def sql(statement):
            proc = subprocess.run(command, input=statement, text=True, capture_output=True, timeout=180)
            if proc.returncode:
                raise RuntimeError("SQL smoke command failed; inspect database logs")
            return proc.stdout.strip()

        names = ",".join("'" + n + "'" for n in OPERATORS)
        rows = json.loads(sql(f"SELECT jsonb_agg(jsonb_build_object('name',name,'tests',tests)) FROM rvbbit.operators WHERE name IN ({names});"))
        assert len(rows) == len(OPERATORS), "Expected all seven SQL operators"
        for row in rows:
            for test in row["tests"]:
                def run_test(test=test):
                    # Exercise the provider again, not yesterday's receipt.
                    actual = sql("SET rvbbit.cache_bypass='on';\n" + test["sql"])
                    normalized = {"t": "true", "f": "false"}.get(actual, actual)
                    value, kind = str(test["expect"]["value"]), test["expect"]["type"]
                    passed = {"exact": lambda: normalized == value,
                              "contains": lambda: value in actual,
                              "min": lambda: float(actual) >= float(value),
                              "max": lambda: float(actual) <= float(value)}[kind]()
                    assert passed, f"Expected {kind} {value}; got {actual[:200]}"
                    return {"actual": actual, "expected": test["expect"]}
                check(row["name"] + "/" + test["name"], run_test)

    args.output.write_text(json.dumps(report, indent=2) + "\n")
    raise SystemExit(0 if all(c["passed"] for c in report["checks"].values()) else 1)


if __name__ == "__main__":
    main()
