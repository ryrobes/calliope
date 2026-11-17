#!/usr/bin/env python3
"""Real inference checks for GLiNER and TSPulse, directly or through Clover.

Can save a legacy gateway baseline before changing routes, then compare vectors
and reconstruction after migration. Uses synthetic data; does not mutate routes.
"""
import argparse
import json
import math
from pathlib import Path
import statistics
import shlex
import subprocess
import time
import urllib.request


def fixtures():
    wave = [math.sin(i / 10) for i in range(2048)]
    spikes = wave.copy()
    for i in range(1100, 1110):
        spikes[i] = 10
    gaps = wave[:1025]
    missing = [0, 1, 511, 512, 700, 1023, 1024]
    for i in missing:
        gaps[i] = None
    return wave, spikes, gaps, missing


def check(invoke, legacy=False):
    report = {"checks": {}, "results": {}, "seconds": {}}

    def call(name, payload, label=None):
        start = time.monotonic()
        result = invoke(name, payload)
        report["seconds"][label or name] = round(time.monotonic() - start, 3)
        report["results"][label or name] = result
        return result

    text = "☀️ Alice Johnson works at Acme Corporation in Boston. Email alice@example.com."
    entities = call("extract", {"text": text, "labels": "person,organization,location,email"})
    assert isinstance(entities, list) and len(entities) >= 3, entities
    assert any(e["label"] == "person" and "Alice" in e["text"] for e in entities), entities
    assert any(e["label"] == "email" and e["text"] == "alice@example.com" for e in entities), entities
    for e in entities:
        assert text[e["start"]:e["end"]] == e["text"], e
        assert 0 <= e["score"] <= 1
    assert [e["score"] for e in entities] == sorted((e["score"] for e in entities), reverse=True)
    report["checks"]["entity_labels_unicode_spans_scores"] = True
    assert call("extract", {"text": text, "labels": []}, "no_labels") == []
    threshold_result = call("extract", {"text": text, "labels": ["person"], "threshold": 1}, "threshold")
    if not legacy:
        assert threshold_result == []
    report["checks"]["empty_labels"] = True
    report["checks"]["threshold_forwarded"] = threshold_result == []

    wave, spikes, gaps, missing = fixtures()
    embed = call("timeseries_embed", {"values": json.dumps(wave)})
    vector = embed["embedding"]
    assert embed["n_points"] == len(wave) and embed["window"] == 512
    assert embed["window_start"] == len(wave) - 512
    assert embed["dimension"] == len(vector) > 0 and all(math.isfinite(x) for x in vector)
    assert sum(x*x for x in vector) > 0
    tail = call("timeseries_embed", {"values": wave[-512:]}, "embed_last_window")
    assert max(abs(a-b) for a,b in zip(vector,tail["embedding"])) < 1e-5
    report["checks"]["finite_embedding_and_last_window"] = True
    same = call("timeseries_similarity", {"left": json.dumps(wave), "right": json.dumps(wave)})
    assert abs(same["similarity"] - 1) < 1e-5, same
    report["checks"]["self_similarity"] = True
    imputed = call("timeseries_impute", {"values": json.dumps(gaps)})
    assert imputed["imputed_indices"] == missing and imputed["n_points"] == len(gaps)
    assert all(math.isfinite(x) for x in imputed["values"])
    assert all(abs(imputed["values"][i] - wave[i]) < 1e-6 for i in range(len(gaps)) if i not in missing)
    report["checks"]["impute_edges_overlapping_windows_preserves_observed"] = True
    anomalies = call("timeseries_anomalies", {"values": json.dumps(spikes)})
    scores = anomalies["scores"]
    assert len(scores) == len(spikes) == len(anomalies["anomalies"])
    assert anomalies["anomaly_indices"] == [i for i,v in enumerate(anomalies["anomalies"]) if v]
    near = max(s for s in scores[1080:1180] if s is not None)
    background = statistics.median(s for s in scores[100:900] if s is not None)
    assert near > background and near >= anomalies["threshold"], (near, background)
    report["checks"]["injected_anomaly_detected"] = {"peak": near, "background_median": background}
    return report


def compare(report, baseline):
    comparisons = {}
    for name, field in [("timeseries_embed", "embedding"), ("timeseries_impute", "values"),
                        ("timeseries_anomalies", "scores")]:
        a, b = report["results"][name][field], baseline["results"][name][field]
        assert len(a) == len(b), name
        assert [x is None for x in a] == [x is None for x in b], name
        delta = max(abs(x-y) for x,y in zip(a,b) if x is not None)
        comparisons[name] = {"max_absolute_difference": delta, "within_1e-4": delta < 1e-4}
    report["legacy_comparison"] = comparisons
    assert all(x["within_1e-4"] for x in comparisons.values()), comparisons


def check_sql(command):
    """Run installed operator fixtures with SQL result-cache bypass enabled."""
    def sql(query):
        process = subprocess.run(shlex.split(command) + ['-q', '-v', 'ON_ERROR_STOP=1'],
                                 input="SET rvbbit.cache_bypass='on';\n" + query,
                                 text=True, capture_output=True, timeout=300)
        if process.returncode:
            raise RuntimeError('SQL check failed: ' + process.stderr[-1000:])
        return process.stdout.strip()

    operators = ['clover_extract', 'clover_pii', 'clover_series_anomalies',
                 'clover_series_embed', 'clover_series_impute', 'clover_series_similarity']
    fixtures = json.loads(sql("SELECT jsonb_agg(jsonb_build_object('name',name,'tests',tests)) FROM rvbbit.operators WHERE name IN ("
                              + ','.join("'"+name+"'" for name in operators) + ");"))
    assert {x['name'] for x in fixtures} == set(operators)
    checks = {}
    for operator in fixtures:
        assert operator['tests'], operator['name']
        for test in operator['tests']:
            actual = sql(test['sql'])
            normalized = {'t': 'true', 'f': 'false'}.get(actual, actual)
            expect = test['expect']
            passed = {'exact': lambda: normalized == str(expect['value']),
                      'contains': lambda: str(expect['value']) in actual,
                      'min': lambda: float(actual) >= float(expect['value']),
                      'not_empty': lambda: bool(actual and actual not in ('NULL', 'null', '[]'))}[expect['type']]()
            assert passed, (operator['name'], test['name'], actual)
            checks[operator['name'] + '/' + test['name']] = True
    # The installed Document Brain alias still resolves through the Clover
    # specialist; installing either pack must not overwrite its definition.
    entities = json.loads(sql("SELECT rvbbit.extract_entities('Alice Johnson works in Boston','person,location');"))
    assert any(e['label'] == 'person' and 'Alice' in e['text'] for e in entities)
    checks['extract_entities_alias'] = True
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:18090")
    parser.add_argument("--gliner-url")
    parser.add_argument("--tspulse-url")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--legacy", action="store_true", help="Record legacy adapter limitations without requiring the new threshold forwarding")
    parser.add_argument("--psql-command", help="Optional psql command, including connection and -XAt flags, to exercise existing SQL functions")
    args = parser.parse_args()
    key = json.loads((args.config / "local-keys.json").read_text())["service_key"] if args.config else None

    def invoke(name, payload):
        origin = args.gliner_url if name == "extract" else args.tspulse_url
        url = origin.rstrip("/") + "/specialists/" + name + "/predict" if origin else args.url.rstrip("/") + "/b/" + name + "/predict"
        headers = {"Content-Type": "application/json"}
        if key and not origin:
            headers["Authorization"] = "Bearer " + key
        request = urllib.request.Request(url, data=json.dumps({"inputs": [payload]}).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.load(response)["outputs"][0]

    report = check(invoke, args.legacy)
    if args.compare:
        compare(report, json.loads(args.compare.read_text()))
    if args.psql_command:
        report['sql_checks'] = check_sql(args.psql_command)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k,v in report.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
