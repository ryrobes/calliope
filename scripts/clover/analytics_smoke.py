#!/usr/bin/env python3
"""Exercise the CPU analytics SQL-compatible routes; cluster calls use paid embeddings.

Does not install routes or alter SQL definitions. Saves one fitted model for a
subsequent --reuse-model run after a worker restart/reinstall.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import urllib.request


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', type=Path, required=True)
    ap.add_argument('--url', default='http://127.0.0.1:18090')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--saved-model', type=Path, required=True)
    ap.add_argument('--reuse-model', action='store_true')
    ap.add_argument('--psql-command')
    args = ap.parse_args()
    key = json.loads((args.config / 'local-keys.json').read_text())['service_key']
    report = {'checks': {}, 'paid_embeddings': not args.reuse_model}

    def predict(name, payload):
        req = urllib.request.Request(args.url + '/b/' + name + '/predict',
              data=json.dumps({'inputs': [payload]}).encode(),
              headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=300) as response:
            return json.load(response)['outputs'][0]

    if not args.reuse_model:
        texts = ['A compressor is leaking refrigerant', 'The air conditioner compressor needs repair',
                 'A customer requests an invoice copy', 'The invoice balance is overdue']
        c = predict('cluster', {'values': json.dumps(texts), 'num_clusters': '2'})
        ids = c['assignments']
        assert ids[0] == ids[1] and ids[2] == ids[3] and ids[0] != ids[2], ids
        assert c['embedding_profile'] and c['usage']['already_metered']
        report['checks']['sql_shaped_clustering'] = c
        model = predict('anomaly_fit', {'X': '[[1,1],[1.1,0.9],[0.9,1.1],[1.05,1],[0.95,0.98],[1.02,1.03],[1,0.97]]'})
        args.saved_model.write_text(json.dumps(model))
        args.saved_model.chmod(0o600)
    else:
        model = json.loads(args.saved_model.read_text())
    s = predict('anomaly_score', {'X': '[[1,1],[9,9]]', 'model_blob_b64': model['blob_b64'], 'model_sha256': model['blob_sha256']})
    assert s['scores'][1] > s['scores'][0]
    report['checks']['saved_model_score'] = s
    report['model'] = {k: v for k, v in model.items() if k != 'blob_b64'}
    if args.psql_command:
        command = shlex.split(args.psql_command) + ['-q', '-v', 'ON_ERROR_STOP=1']
        def sql(query):
            proc = subprocess.run(command, input="SET rvbbit.cache_bypass='on';\n" + query, text=True, capture_output=True, timeout=300)
            if proc.returncode:
                raise RuntimeError('SQL check failed: ' + proc.stderr[-700:])
            return proc.stdout.strip()
        # Use the operators' authored tests. Cache bypass ensures this worker is
        # exercised rather than a result left by the legacy implementation.
        operators = ['clover_cluster', 'clover_anomaly_fit', 'clover_anomaly_score']
        fixtures = json.loads(sql("SELECT jsonb_agg(jsonb_build_object('name',name,'tests',tests)) FROM rvbbit.operators WHERE name IN (" + ','.join("'" + x + "'" for x in operators) + ");"))
        for op in fixtures:
            for test in op['tests']:
                actual = sql(test['sql'])
                normalized = {'t': 'true', 'f': 'false'}.get(actual, actual)
                value, kind = str(test['expect']['value']), test['expect']['type']
                passed = {'exact': lambda: normalized == value,
                          'contains': lambda: value in actual,
                          'min': lambda: float(actual) >= float(value),
                          'max': lambda: float(actual) <= float(value)}[kind]()
                assert passed, op['name'] + '/' + test['name']
                report['checks'][op['name'] + '/' + test['name']] = {'passed': True, 'expected': test['expect']}
        result = sql("WITH m AS MATERIALIZED (SELECT rvbbit.clover_anomaly_fit('[[1,1],[1.1,0.9],[0.9,1.1],[1.05,1],[0.95,0.98],[1.02,1.03],[1,0.97]]') AS fit) SELECT rvbbit.clover_anomaly_score(fit->>'blob_b64','[[1,1],[9,9]]') FROM m;")
        scored = json.loads(result)
        assert scored['scores'][1] > scored['scores'][0]
        report['checks']['sql_fit_to_score_composition'] = scored
        clustered = json.loads(sql("SELECT rvbbit.clover_cluster('[\"repair compressor\",\"repair refrigerant leak\",\"pay invoice\",\"invoice overdue\"]','2');"))
        assert len(clustered['assignments']) == 4
        report['checks']['sql_cluster'] = clustered
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print('PASS ' + ', '.join(report['checks']))


if __name__ == '__main__':
    main()
