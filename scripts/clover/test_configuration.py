import importlib.util
from pathlib import Path
import unittest
import json
import os
import subprocess
import tempfile
import sys

spec = importlib.util.spec_from_file_location(
    "clover_configure", Path(__file__).with_name("configure.py")
)
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class ConfigurationTests(unittest.TestCase):
    def test_luna6_sql_helpers_leave_the_answer_budget_for_answers(self):
        routes={r['name']:r for r in config.configuration()['llms']}
        self.assertEqual(routes['clover']['upstream_model'],'openai/gpt-6-luna')
        self.assertEqual(routes['clover']['request_defaults']['reasoning'],{'effort':'none'})
        self.assertNotIn('reasoning',routes['calliope'].get('request_defaults',{}))
        custom=next(r for r in config.configuration(sql_model='custom/model')['llms'] if r['name']=='clover')
        self.assertNotIn('reasoning',custom['request_defaults'])

    def test_agent_model_does_not_change_the_sql_operator_model(self):
        # The bundle gives the agent Sol; SQL operators must stay on the cheap model.
        routes={r['name']:r for r in config.configuration(chat='openai/gpt-6.1-sol')['llms']}
        self.assertEqual(routes['calliope']['upstream_model'],'openai/gpt-6.1-sol')
        self.assertEqual(routes['clover']['upstream_model'],'openai/gpt-6-luna')
        self.assertEqual(routes['clover']['request_defaults']['reasoning'],{'effort':'none'})
        routes={r['name']:r for r in config.configuration(sql_model='anthropic/claude-haiku-5.5')['llms']}
        self.assertEqual(routes['clover']['upstream_model'],'anthropic/claude-haiku-5.5')
        self.assertEqual(routes['calliope']['upstream_model'],'openai/gpt-6-luna')

    def test_research_defaults_to_luna_not_gemma(self):
        route=next(r for r in config.configuration(research=True)['backends'] if r['name']=='web_research')
        self.assertEqual(route['model_version'],'clover-research-v0.3/openai/gpt-6-luna/exa/postgres-v1')

    def test_service_host_replaces_compose_names_and_binds_locally(self):
        cfg=config.configuration(research=True, service_host='127.0.0.1', analytics_worker='http://127.0.0.1:8095')
        self.assertEqual(cfg['bind'],'127.0.0.1:8090')
        urls=[r.get(k,'') for r in cfg['backends'] for k in ('upstream_base','health_url')]
        self.assertFalse([u for u in urls if '://clover-' in u])
        self.assertIn('http://127.0.0.1:8093',urls)
        self.assertIn('http://127.0.0.1:8092/health',urls)
        self.assertEqual(config.configuration()['bind'],'0.0.0.0:8090')
        self.assertEqual(config.configuration(service_host='127.0.0.1',bind='127.0.0.1:9000')['bind'],'127.0.0.1:9000')
        for bad in ('', 'http://x', 'a:1', 'a b'):
            with self.assertRaises(ValueError):
                config.configuration(service_host=bad)

    def test_cli_service_host_reaches_client_env_and_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            env={k:v for k,v in os.environ.items() if k not in ('RVBBIT_CLOVER_KEY','OPENROUTER_API_KEY')}
            subprocess.run([sys.executable, str(Path(__file__).with_name('configure.py')), '--output', directory,
                            '--service-host', '127.0.0.1'], env=env, capture_output=True, check=True)
            client=(Path(directory)/'client.env').read_text()
            self.assertIn('RVBBIT_CLOVER_BASE_URL=http://127.0.0.1:8090\n', client)
            catalog=(Path(directory)/'catalog.sql').read_text()
            self.assertIn("SET max_concurrent=4 WHERE name='decisions'", catalog)
            self.assertEqual(json.loads((Path(directory)/'hutch.json').read_text())['bind'], '127.0.0.1:8090')

    def test_generator_preserves_local_keys_and_records_actual_embedding_model(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {
                k: v
                for k, v in os.environ.items()
                if k not in ("RVBBIT_CLOVER_KEY", "OPENROUTER_API_KEY")
            }
            command = [
                sys.executable,
                str(Path(__file__).with_name("configure.py")),
                "--output",
                directory,
            ]
            subprocess.run(command, env=env, capture_output=True, check=True)
            keys = json.loads((Path(directory) / "local-keys.json").read_text())
            tenants_path = Path(directory) / "tenants.json"
            self.assertEqual(json.loads(tenants_path.read_text())["tenants"][0]["lanes"], 4096)
            shared=Path(directory)/'credentials/openrouter.key'
            shared.write_text('rotated-through-ui')
            cfg_path=Path(directory)/"hutch.json"
            current=json.loads(cfg_path.read_text())
            current["ledger_postgres"]={"instance_id":"existing-installation","dsn_env":"PRIVATE_LEDGER_DSN"}
            cfg_path.write_text(json.dumps(current))
            subprocess.run(
                command
                + [
                    "--force",
                    "--embedding",
                    "openrouter",
                    "--embedding-model",
                    "provider/new-embed",
                    "--lanes", "2048",
                ],
                env=env,
                capture_output=True,
                check=True,
            )
            self.assertEqual(
                keys, json.loads((Path(directory) / "local-keys.json").read_text())
            )
            self.assertEqual(json.loads(cfg_path.read_text())["ledger_postgres"],current["ledger_postgres"])
            self.assertEqual(json.loads(tenants_path.read_text())["tenants"][0]["lanes"], 2048)
            self.assertEqual(shared.read_text(),'rotated-through-ui')
            self.assertIn('OPENROUTER_API_KEY=rotated-through-ui', (Path(directory)/'providers.env').read_text())
            self.assertIn(
                "provider/new-embed", (Path(directory) / "catalog.sql").read_text()
            )
            self.assertEqual(
                (Path(directory) / "providers.env").stat().st_mode & 0o777, 0o600
            )

            routes = Path(directory) / "routes.json"
            routes.write_text(json.dumps({"embed": {
                "adapter": "openai-embeddings", "upstream_base": "https://hf.example",
                "upstream_path": "/v1/embeddings", "model_version": "custom-pinned-model",
                "upstream_params": {"model": "custom/model", "dimensions": 768},
            }}))
            subprocess.run(command + ["--force", "--routes", str(routes)], env=env, capture_output=True, check=True)
            profile = json.loads((Path(directory) / "inference.json").read_text())
            self.assertEqual(profile["embedding_model"], "custom/model")
            self.assertEqual(profile["embedding_dimensions"], 768)

    def test_cpu_arctic_embeddings_get_cpu_sized_backend_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {k: v for k, v in os.environ.items() if k not in ("RVBBIT_CLOVER_KEY", "OPENROUTER_API_KEY")}
            script = str(Path(__file__).with_name("configure.py"))
            subprocess.run([sys.executable, script, "--output", directory, "--embedding", "cpu"],
                           env=env, capture_output=True, check=True)
            catalog = (Path(directory) / "catalog.sql").read_text()
            self.assertIn("SET batch_size=8,max_concurrent=1,timeout_ms=300000 WHERE name='embed'", catalog)
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([sys.executable, script, "--output", directory], env=env, capture_output=True, check=True)
            self.assertNotIn("timeout_ms=300000 WHERE name='embed'", (Path(directory) / "catalog.sql").read_text())

    def test_remote_override_does_not_retain_cpu_health_probe(self):
        value = config.configuration(
            overrides={
                "embed": {
                    "adapter": "openai-embeddings",
                    "upstream_base": "https://hf.example",
                    "upstream_path": "/v1/embeddings",
                    "model_version": "arctic-remote",
                }
            }
        )
        embed = next(x for x in value["backends"] if x["name"] == "embed")
        self.assertNotIn("health_url", embed)

    def test_default_is_hosted_shared_profile_without_billing_provider(self):
        value = config.configuration()
        self.assertNotIn("polar", value)
        embed = next(x for x in value["backends"] if x["name"] == "embed")
        self.assertEqual(embed["model_version"], "openrouter/voyageai/voyage-4@1024-float-v1")
        self.assertEqual(embed["upstream_base"], "https://openrouter.ai/api")
        self.assertFalse(
            next(x for x in value["backends"] if x["name"] == "forecast")["enabled"]
        )

    def test_cloud_embedding_selection_is_explicit(self):
        value = config.configuration(
            embedding="openrouter", embedding_model="provider/embedding"
        )
        embed = next(x for x in value["backends"] if x["name"] == "embed")
        self.assertEqual(embed["upstream_params"]["model"], "provider/embedding")
        self.assertEqual(embed["upstream_bearer_token_env"], "OPENROUTER_API_KEY")
        self.assertNotIn("health_url", embed)

    def test_text_routes_follow_decision_model_and_explicit_override(self):
        value = config.configuration(decision_model="provider/new-decisions")
        for route in value["backends"]:
            if route["name"] in config.HOSTED_TEXT_ROUTES:
                self.assertTrue(route["enabled"])
                self.assertEqual(route["model_version"], "clover-text-judgments-v1/provider/new-decisions")
                self.assertEqual(route["upstream_path"], f"/specialists/{route['name']}/predict")
        self.assertFalse(next(r for r in value["backends"] if r["name"] == "nli3")["enabled"])
        value = config.configuration(overrides={"nli": {"upstream_base": "https://old.example", "upstream_path": "/predict", "model_version": "original-nli"}})
        nli = next(r for r in value["backends"] if r["name"] == "nli")
        self.assertNotIn("health_url", nli)
        self.assertEqual(nli["model_version"], "original-nli")

    def test_hosted_specialists_require_contract_identity(self):
        with self.assertRaises(ValueError):
            config.configuration(
                overrides={"forecast": {"upstream_base": "https://hf.example"}}
            )
        value = config.configuration(
            overrides={
                "forecast": {
                    "upstream_base": "https://hf.example",
                    "upstream_path": "/predict",
                    "model_version": "chronos-2-pinned",
                    "upstream_bearer_token_env": "HF_TOKEN",
                }
            }
        )
        forecast = next(x for x in value["backends"] if x["name"] == "forecast")
        self.assertTrue(forecast["enabled"])
        self.assertEqual(forecast["adapter"], "predict")
        self.assertEqual(forecast["upstream_bearer_token_env"], "HF_TOKEN")


if __name__ == "__main__":
    unittest.main()


def test_optional_cpu_analytics_tracks_embedding_profile():
    configure = config
    cfg = configure.configuration(analytics_worker='http://rvbbit-clover-analytics:8080')
    routes = {r['name']: r for r in cfg['backends']}
    assert all(routes[r]['enabled'] for r in configure.ANALYTICS_OPERATORS)
    assert routes['cluster']['model_version'].endswith(routes['embed']['model_version'])
    assert routes['anomaly_fit']['model_version'] == configure.ANALYTICS_CONTRACT
    default = {r['name']: r for r in configure.configuration()['backends']}
    assert not default['cluster']['enabled']
    changed = configure.configuration(analytics_worker='http://worker:8080', overrides={'embed': {
        'upstream_base': 'http://other:8080', 'upstream_path': '/predict', 'model_version': 'another-space'}})
    assert next(r for r in changed['backends'] if r['name'] == 'cluster')['model_version'].endswith('/another-space')


def test_cpu_utilities_default_and_metadata_are_complete():
    cfg = config.configuration(transcription_model='provider/stt')
    routes = {r['name']: r for r in cfg['backends']}
    assert next(r for r in cfg['media'] if r['name']=='calliope-stt')['upstream_model']=='provider/stt'
    for name in ('language','web_scrape','transcribe'):
        assert routes[name]['enabled']
        assert routes[name]['upstream_base'].startswith('http://clover-')
    assert routes['transcribe']['model_version'] == 'clover-transcribe-v1/provider/stt'
    assert routes['transcribe']['health_url'].endswith('/health/transcribe')
    for name, route in routes.items():
        if not route.get('enabled', True):
            assert name in config.OPTIONS and len(route['unavailable_reason']) > 50
    assert config.OPTIONS['extract']['pack'] == 'builtin/clover-gliner'
    assert 'reference' in routes['tabular_fit']['unavailable_reason']


def test_generated_utility_models_and_inventory(tmp_path):
    subprocess.run([sys.executable, str(Path(__file__).with_name('configure.py')),
                    '--output', str(tmp_path), '--transcription-model','provider/test-stt'], check=True, capture_output=True)
    sql = (tmp_path/'catalog.sql').read_text()
    assert "model='clover-language-v1/lingua-2.1.1-low'" in sql
    assert "'clover_language_info'" in sql
    assert "model='clover-transcribe-v1/provider/test-stt'" in sql
    assert 'CLOVER_TRANSCRIBE_MODEL=provider/test-stt' in (tmp_path/'providers.env').read_text()
    inventory = json.loads((tmp_path/'capabilities.json').read_text())
    assert len(inventory) == len(config.SPECIALISTS) + 3  # decisions, embed, extractions
    assert all(not r['inference_verified'] for r in inventory)
    assert all('upstream_base' not in r for r in inventory)


def test_research_is_explicit_and_forwards_server_owned_scope():
    cfg = config.configuration(research=True, research_model='provider/research')
    route = next(r for r in cfg['backends'] if r['name'] == 'web_research')
    assert route['enabled'] and route['forward_tenant_scope']
    assert route['upstream_base'] == 'http://clover-research:8092'
    assert route['model_version'] == 'clover-research-v0.3/provider/research/exa/postgres-v1'
    assert not next(r for r in config.configuration()['backends'] if r['name'] == 'web_research')['enabled']
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run([sys.executable, str(Path(__file__).with_name('configure.py')), '--output', directory,
                        '--research', '--research-model', 'provider/research'], check=True, capture_output=True)
        catalog = (Path(directory) / 'catalog.sql').read_text()
        assert "cache_policy='never'" in catalog
        assert 'clover_web_watch' in catalog
        assert "batch_size=2,max_concurrent=1,timeout_ms=300000" in catalog
        assert 'CLOVER_RESEARCH_MODEL=provider/research' in (Path(directory) / 'providers.env').read_text()


def test_model_workers_are_optional_and_have_pinned_cache_identities(tmp_path):
    defaults = {r['name']: r for r in config.configuration()['backends']}
    assert all(not defaults[name]['enabled'] for name in config.MODEL_OPERATORS)
    cfg = config.configuration(gliner_worker='http://gliner:8080/', tspulse_worker='http://tspulse:8080')
    routes = {r['name']: r for r in cfg['backends']}
    root = Path(__file__).resolve().parents[2]
    for short, contract, names in [('gliner', config.GLINER_CONTRACT, ['extract']),
                                    ('tspulse', config.TSPULSE_CONTRACT, [n for n in config.MODEL_OPERATORS if n.startswith('timeseries_')])]:
        pack = json.loads((root / f'capabilities/packs/builtin/clover-{short}/capability.json').read_text())
        assert pack['operators'][0]['model'] == contract
        assert pack['runtime']['device'] == 'cpu' and pack['resources']['gpu'] is False
        assert pack['runtime']['volumes'] == [{'name': 'model_cache', 'mount': '/models'}]
        for name in names:
            assert routes[name]['model_version'] == contract and routes[name]['enabled']
            assert routes[name]['upstream_path'] == f'/specialists/{name}/predict'
    for bad in ['file:///tmp/model', 'http://user:secret@worker', 'http://worker/path', 'http://worker?key=x']:
        with __import__('pytest').raises(ValueError):
            config.configuration(gliner_worker=bad)
    subprocess.run([sys.executable, str(Path(__file__).with_name('configure.py')), '--output', str(tmp_path),
                    '--gliner-worker', 'http://gliner:8080', '--tspulse-worker', 'http://tspulse:8080'],
                   check=True, capture_output=True)
    catalog = (tmp_path / 'catalog.sql').read_text()
    for names in config.MODEL_OPERATORS.values():
        assert all("'" + name + "'" in catalog for name in names)
    assert config.GLINER_CONTRACT in catalog and config.TSPULSE_CONTRACT in catalog


def test_sql_route_facts_cover_disabled_and_legacy_without_secrets(monkeypatch):
    monkeypatch.setenv('CLOVER_LEGACY_UPSTREAM_HOSTS','clover.example.com')
    cfg=config.configuration(overrides={'forecast':{'upstream_base':'https://clover.example.com','upstream_path':'/b/forecast/predict','model_version':'chronos-old','upstream_bearer_token_env':'LEGACY_PRIVATE'}})
    sql='\n'.join(config.route_catalog_sql(cfg))
    assert '"source": "legacy-clover"' in sql
    assert '"enabled": false' in sql
    assert 'NuExtract chat route is not configured' in sql
    assert 'LEGACY_PRIVATE' not in sql and 'https://' not in sql
    assert "auth_header_env='RVBBIT_CLOVER_KEY'" in sql


def test_explicit_nuextract_chat_route_reenables_its_sql_backend():
    cfg=config.configuration()
    cfg['llms'].append({'name':'clover-extract','upstream_base':'https://models.example','model_version':'nuextract-v2'})
    sql='\n'.join(config.route_catalog_sql(cfg))
    assert 'NuExtract chat route is not configured' not in sql
    assert "name IN ('clover_extract_llm')" in sql
    assert 'nuextract-v2' in sql


def test_typed_extraction_is_hosted_by_default_and_can_be_turned_off(tmp_path):
    routes = {r['name']: r for r in config.configuration()['backends']}
    extractions = routes['extractions']
    assert extractions['adapter'] == 'hosted-extractions'
    assert extractions['upstream_path'] == '/alpha/extractions'
    assert extractions['upstream_params'] == {'model': 'cloudflare/clairic'}
    assert extractions['upstream_bearer_token_env'] == 'OPENROUTER_API_KEY'
    assert extractions['model_version'] == 'openrouter/cloudflare/clairic'

    off = {r['name']: r for r in config.configuration(extraction_model=None)['backends']}['extractions']
    assert off['enabled'] is False and 'generalist' in off['unavailable_reason']

    subprocess.run([sys.executable, str(Path(__file__).with_name('configure.py')),
                    '--output', str(tmp_path)], check=True, capture_output=True)
    sql = (tmp_path / 'catalog.sql').read_text()
    # The extraction model is part of the operators' cache identity.
    assert ("UPDATE rvbbit.operators SET model='openrouter/cloudflare/clairic', updated_at=now() "
            "WHERE name IN ('clover_extract_fields','_clover_extract_typed');") in sql
    assert "SET batch_size=16,max_concurrent=4,timeout_ms=60000 WHERE name='extractions'" in sql
    facts = [line for line in sql.splitlines() if 'clover_route' in line and "'extractions'" in line]
    assert facts and '"enabled": true' in facts[0]



def test_published_clover_models_match_the_default_gateway_routes():
    """The catalog's managed/clover model list is what the default gateway runs."""
    import re
    pack = json.loads((Path(__file__).resolve().parents[2] / 'capabilities/packs/managed/clover/capability.json').read_text())
    cfg = config.configuration()
    routes = {r['name']: r for r in cfg['backends'] + cfg['llms']}
    listed = set()
    for entry in pack['managed']['models']:
        for slot in re.split(r',\s*', entry['slot'].split(' (')[0]):
            listed.add(slot)
            route = routes.get(slot)
            assert route is not None, f'{slot} is not a Clover route'
            optional = 'optional' in entry['runs'] or 'not shipped' in entry['runs']
            assert route.get('enabled', True) is not optional, f'{slot}: published as {entry["runs"]!r}'
            if 'version' in entry and not optional:
                assert route['model_version'] == entry['version'], slot
    enabled = {r['name'] for r in cfg['backends'] if r.get('enabled', True)}
    assert enabled <= listed, f'enabled routes missing from the catalog: {sorted(enabled - listed)}'


def test_install_provenance_matches_the_default_gateway_routes():
    import re
    pack = json.loads((Path(__file__).resolve().parents[2] / 'capabilities/packs/managed/clover/capability.json').read_text())
    sql = pack['managed']['install']['sql']
    assert not any("source_provider='rvbbit.ai'" in s for s in sql)
    provenance = next(s for s in sql if s.startswith("UPDATE rvbbit.backends b SET source_provider='self-hosted-clover'"))
    rows = dict((name, revision) for name, _model, revision in
                re.findall(r"\('([a-z_0-9]+)','((?:[^']|'')*)','((?:[^']|'')*)'\)", provenance))
    for statement in sql:  # single-route provenance updates
        one = re.match(r"UPDATE rvbbit\.backends SET source_provider='self-hosted-clover'.*"
                       r"source_revision='([^']*)'.* WHERE name='([a-z_]+)';$", statement)
        if one:
            rows[one.group(2)] = one.group(1)
    routes = {r['name']: r for r in config.configuration()['backends']}
    for name, route in routes.items():
        if route.get('enabled', True):
            assert rows.get(name) == route['model_version'], name
        elif name in rows:
            # Off by default: a marker, or what the route runs once its flag turns it on.
            opted_in = {r['name']: r for r in config.configuration(research=True)['backends']}[name]
            assert rows[name] in ('optional-worker', 'compatible-worker') or (
                opted_in.get('enabled', True) and rows[name] == opted_in['model_version']), name
