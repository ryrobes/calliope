"""Real Postgres + mock provider acceptance tests (no paid inference).

Requires CLOVER_LEDGER_TEST_DSN pointing at an expendable PostgreSQL database,
CLOVER_LEDGER_TEST_CONTAINER for the outage test, and a built Hutch binary.
Never point this at an application database: setUpClass resets clover_telemetry.
"""
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('ledger', Path(__file__).with_name('ledger.py'))
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


def wait_for(fn, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            value = fn()
            if value:
                return value
        except (OSError, psycopg.OperationalError):
            pass
        time.sleep(.1)
    raise AssertionError('Condition did not become true')


class Provider(http.server.BaseHTTPRequestHandler):
    cost_ready = False
    def log_message(self, *args):
        pass
    def reply(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.reply({'id': 'mock-generation', 'model': 'mock-model', 'choices': [{'message': {'role': 'assistant', 'content': 'test'}}], 'usage': {'prompt_tokens': 4, 'completion_tokens': 2}})
    def do_GET(self):
        if self.path.startswith('/v1/generation'):
            self.reply({'data': {'total_cost': .000123456, 'native_tokens_prompt': 4, 'native_tokens_completion': 2}} if self.cost_ready else {'data': {}})
        else:
            self.reply({'data': []})


@unittest.skipUnless(os.environ.get('CLOVER_LEDGER_TEST_DSN'), 'expendable PostgreSQL required')
class PostgresLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ['CLOVER_LEDGER_TEST_DSN']
        cls.pg = psycopg.connect(cls.dsn, autocommit=True)
        cls.pg.execute('DROP SCHEMA IF EXISTS clover_telemetry CASCADE')
        cls.bootstrap_temp = tempfile.TemporaryDirectory(prefix='clover-ledger-bootstrap-')
        bootstrap = Path(cls.bootstrap_temp.name)
        (bootstrap/'hutch.json').write_text('{}')
        params = psycopg.conninfo.conninfo_to_dict(cls.dsn)
        ledger.prepare(bootstrap,'test',params['host'],int(params['port']),params['dbname'])
        cls.pg.execute((bootstrap/'ledger-bootstrap.sql').read_text())
        cls.app_dsn = (bootstrap/'ledger.env').read_text().strip().split('=',1)[1]
        with psycopg.connect(cls.app_dsn) as writer:
            self_role = writer.execute("SELECT current_user,rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user").fetchone()
            assert self_role == ('clover_ledger',False,False,False)
        # Prove ordinary application tables remain inaccessible to the writer.
        cls.pg.execute('CREATE TABLE IF NOT EXISTS public.ledger_test_private(secret text)')
        with psycopg.connect(cls.app_dsn) as writer:
            assert writer.execute("SELECT has_table_privilege('public.ledger_test_private','select')").fetchone()[0] is False
        cls.provider = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        threading.Thread(target=cls.provider.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.provider.shutdown()
        cls.provider.server_close()
        cls.pg.close()
        cls.bootstrap_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='clover-ledger-test-')
        self.directory = Path(self.temp.name)
        self.app = None
        self.log = (self.directory / 'hutch.log').open('w')
        self.instance = self.id().split('.')[-1]
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.cfg = {'bind': f'127.0.0.1:{self.port}', 'admin_token': 'test-admin', 'tenants_file': 'tenants.json',
                    'meter_db': str(self.directory / 'must-not-exist.sqlite'),
                    'ledger_postgres': {'instance_id': self.instance, 'queue_capacity': 8},
                    'upstream': {'mode': 'mock'}, 'backends': [{'name': 'sentiment', 'model_version': 'mock',
                    'entitlement': 'clover', 'mock_output': .9, 'unit_microusd': 4}],
                    'provider_cost_reconcile_secs': 1, 'llms': [{'name': 'chat', 'entitlement': 'clover',
                    'upstream_base': f'http://127.0.0.1:{self.provider.server_port}', 'upstream_model': 'mock-model',
                    'model_version': 'mock', 'provider': 'openrouter', 'upstream_bearer_token_env': 'MOCK_PROVIDER_KEY'}]}
        (self.directory / 'tenants.json').write_text(json.dumps({'tenants': [{'id': 'test',
          'key_sha256': hashlib.sha256(b'test-key').hexdigest(), 'status': 'active', 'lanes': 8, 'entitlements': ['clover']}]}))

    def tearDown(self):
        self.stop()
        self.log.close()
        self.temp.cleanup()

    def start(self):
        (self.directory / 'hutch.json').write_text(json.dumps(self.cfg))
        binary = ROOT / 'crates/rvbbit_hutch/target/debug/rvbbit-hutch'
        self.app = subprocess.Popen([str(binary), '--config', str(self.directory / 'hutch.json')],
            env={**os.environ, 'CLOVER_LEDGER_DSN': self.app_dsn, 'MOCK_PROVIDER_KEY': 'mock'}, stdout=self.log, stderr=self.log)
        wait_for(lambda: self.request('/healthz'))

    def stop(self):
        if self.app:
            self.app.terminate()
            self.app.wait(timeout=15)
            self.app = None

    def request(self, path, data=None, key='test-admin'):
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}'+path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=2) as response:
            body = response.read().decode()
            return json.loads(body) if 'json' in response.headers.get('Content-Type', '') else body

    def ingest(self, kind, data, event_id=None):
        event = {'kind': kind, 'event_id': event_id or f'{self.instance}-{time.time_ns()}', 'instance_id': self.instance,
                 'unix_ms': int(time.time()*1000), 'data': data}
        self.pg.execute('SELECT clover_telemetry.ingest(%s)', [Jsonb([event])])
        return event

    def test_calls_nonblocking_outage_bounded_recovery(self):
        container = os.environ.get('CLOVER_LEDGER_TEST_CONTAINER', '')
        if not container.startswith('clover-ledger-test-'):
            self.skipTest('explicit isolated container needed for outage test')
        self.cfg['llms'] = []
        subprocess.run(['docker', 'stop', container], check=True, capture_output=True)
        try:
            self.start()  # Even startup must not require a live database.
            started = time.monotonic()
            for _ in range(20):
                self.assertEqual(self.request('/b/sentiment/predict', {'inputs': ['PRIVATE INPUT MUST NOT BE STORED']}, 'test-key')['outputs'], [.9])
            self.assertLess(time.monotonic()-started, 2)
            status = self.request('/admin/ledger')
            self.assertEqual(status['queue_depth'], 8)
            self.assertEqual(status['dropped_events'], 12)
            self.assertFalse((self.directory / 'must-not-exist.sqlite').exists())
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/admin/ledger', key='test-key')
            self.assertEqual(error.exception.code, 401)
        finally:
            subprocess.run(['docker', 'start', container], check=True, capture_output=True)
        wait_for(lambda: self.request('/admin/ledger')['queue_depth']==0)
        self.__class__.pg.close()
        self.__class__.pg = psycopg.connect(self.dsn, autocommit=True)
        count = self.pg.execute('SELECT count(*) FROM clover_telemetry.invocations WHERE instance_id=%s', [self.instance]).fetchone()[0]
        self.assertEqual(count, 8)
        self.assertIn('hutch_ledger_dropped_events_total 12', self.request('/metrics'))
        self.request('/b/sentiment/predict', {'inputs':['shutdown flush']}, 'test-key')
        self.stop()
        self.assertEqual(self.pg.execute('SELECT count(*) FROM clover_telemetry.invocations WHERE instance_id=%s', [self.instance]).fetchone()[0], 9)
        self.assertNotIn('PRIVATE INPUT', self.pg.execute('SELECT jsonb_agg(to_jsonb(i))::text FROM clover_telemetry.invocations i').fetchone()[0])

    def test_specialist_cost_precision_unknown_and_composed_child(self):
        self.cfg['llms']=[]
        self.cfg['backends'][0]['mock_output']={'usage':{'cost':.000014028,'input_tokens':3}}
        self.cfg['backends'] += [
            {'name':'unknown','entitlement':'clover','model_version':'mock','mock_output':{'usage':{'input_tokens':3}}},
            {'name':'parent','entitlement':'clover','model_version':'mock','mock_output':{'usage':{'cost':.1,'input_tokens':20,'already_metered':True}}},
        ]
        self.start()
        self.request('/b/sentiment/predict',{'inputs':['a','b']},'test-key')
        self.request('/b/unknown/predict',{'inputs':['a']},'test-key')
        self.request('/b/parent/predict',{'inputs':['a']},'test-key')
        wait_for(lambda: self.pg.execute('SELECT count(*) FROM clover_telemetry.calls WHERE instance_id=%s',[self.instance]).fetchone()[0]==3)
        rows=self.pg.execute('SELECT backend,reference_cost_nanousd,provider_cost_nanousd,prompt_tokens FROM clover_telemetry.calls WHERE instance_id=%s ORDER BY backend',[self.instance]).fetchall()
        self.assertEqual(rows,[('parent',0,0,0),('sentiment',8000,28056,6),('unknown',0,None,3)])

    def test_pending_cost_survives_restart_and_updates_same_call(self):
        Provider.cost_ready = False
        self.start()
        self.request('/v1/chat/completions', {'model':'chat', 'messages':[{'role':'user','content':'PRIVATE CHAT'}]}, 'test-key')
        wait_for(lambda: self.pg.execute('SELECT count(*) FROM clover_telemetry.upstream_attempts WHERE instance_id=%s', [self.instance]).fetchone()[0]==1)
        self.stop()
        row = self.pg.execute('SELECT provider_cost_status,provider_cost_nanousd FROM clover_telemetry.calls WHERE instance_id=%s', [self.instance]).fetchone()
        self.assertEqual(row, ('pending', None))
        Provider.cost_ready = True
        self.start()
        wait_for(lambda: self.pg.execute("SELECT count(*) FROM clover_telemetry.calls WHERE instance_id=%s AND provider_cost_status='complete'", [self.instance]).fetchone()[0]==1)
        row = self.pg.execute('SELECT attempt_count,provider_cost_nanousd FROM clover_telemetry.calls WHERE instance_id=%s', [self.instance]).fetchone()
        self.assertEqual(row, (1, 123456))
        wait_for(lambda: '0.000123456' in self.request('/metrics'))
        self.assertNotIn('PRIVATE CHAT', (self.directory / 'hutch.log').read_text())

    def test_import_idempotency_retry_rollups_and_metadata(self):
        old = self.directory / 'old.sqlite'
        with sqlite3.connect(old) as db:
            db.execute('CREATE TABLE invocations(id integer, ts text,tenant text,backend text,n_inputs integer,status integer,error_code text,duration_ms real,upstream_ms real,model_version text,would_be_cost_microusd integer,invocation_id text,reference_cost_nanousd integer,prompt_tokens integer,completion_tokens integer)')
            db.execute("INSERT INTO invocations VALUES(1,'2026-10-10T00:00:00Z','test','llm:chat',1,200,NULL,10,8,'mock',1,'imported',1000,4,2)")
            db.execute('CREATE TABLE upstream_attempts(id integer,ts text,invocation_id text,tenant text,canonical_model text,provider text,upstream_model text,provider_request_id text,attempt_no integer,mode text,status integer,prompt_tokens integer,completion_tokens integer,provider_cost_nanousd integer,cost_source text,cost_status text,reconcile_attempts integer,next_reconcile_unix integer,reconcile_error text,reconciled_at text)')
            db.execute("INSERT INTO upstream_attempts VALUES(1,'2026-10-10T00:00:00Z','imported','test','chat','openrouter','mock-model','old-generation',1,'response',200,4,2,NULL,'missing','pending',0,0,NULL,NULL)")
        os.environ['CLOVER_LEDGER_DSN'] = self.dsn
        ledger.import_sqlite(old, self.instance, 'fixture')
        row_id = self.pg.execute('SELECT id FROM clover_telemetry.upstream_attempts WHERE instance_id=%s', [self.instance]).fetchone()[0]
        event = self.ingest('reconcile', {'row_id':row_id,'provider_cost_nanousd':0})
        self.pg.execute('SELECT clover_telemetry.ingest(%s)', [Jsonb([event, event])])
        ledger.import_sqlite(old, self.instance, 'fixture')
        self.assertEqual(self.pg.execute('SELECT count(*),sum(provider_cost_nanousd) FROM clover_telemetry.calls WHERE instance_id=%s', [self.instance]).fetchone(), (1,0))
        self.ingest('attempt', {'invocation_id':'imported','tenant':'test','canonical_model':'chat','provider':'openrouter','upstream_model':'mock-model','attempt_no':2,'mode':'response','status':200,'provider_cost_nanousd':25,'cost_source':'inline','cost_status':'final'})
        row = self.pg.execute('SELECT calls,reference_cost_nanousd,known_provider_cost_nanousd FROM clover_telemetry.daily_usage WHERE instance_id=%s', [self.instance]).fetchone()
        self.assertEqual(row,(1,1000,25))
        self.ingest('speech', {'invocation_id':'imported','input_characters':42,'output_bytes':128,'audio_seconds':3.2,'first_audio_ms':15,'profile_revision':'mock'})
        self.ingest('media', {'invocation_id':'imported','capability':'test','input_bytes':16,'output_bytes':64,'usage_json':{'cost':.1}})
        self.assertEqual(self.pg.execute('SELECT input_characters,media_capability FROM clover_telemetry.calls WHERE instance_id=%s', [self.instance]).fetchone(), (42,'test'))
        self.ingest('price', {'provider':'openrouter','upstream_model':'mock-model','prompt_usd_per_token':.00001,'completion_usd_per_token':.00002,'source':'catalog'})
        self.start()
        wait_for(lambda: 'hutch_llm_catalog_prompt_usd_per_million' in self.request('/metrics') and '10.000000000' in self.request('/metrics'))



class PreparationTests(unittest.TestCase):
    def test_preserves_routes_and_credentials_on_repeat(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            (directory/'hutch.json').write_text(json.dumps({'admin_token':'private-admin','backends':[{'name':'embed'}]}))
            ledger.prepare(directory,'installation','postgres',5432,'rvbbit')
            first=(directory/'ledger.env').read_text()
            ledger.prepare(directory,'installation','postgres',5432,'rvbbit')
            self.assertEqual(first,(directory/'ledger.env').read_text())
            self.assertEqual(json.loads((directory/'hutch.json').read_text())['backends'],[{'name':'embed'}])
            for name in ['ledger.env','ledger-keys.json','ledger-bootstrap.sql','hutch.json']:
                self.assertEqual((directory/name).stat().st_mode & 0o777,0o600)

if __name__ == '__main__':
    unittest.main()
