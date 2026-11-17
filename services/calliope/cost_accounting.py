"""Central metadata-only accounting. Source ledgers remain the durable evidence.

A single DB-locked importer tails complete JSONL lines and commits its cursor
with the request rows. Retrying a batch cannot charge a request twice.
"""
from __future__ import annotations
import json
import hashlib
import logging
import math
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)
_THREAD = None
_LOCK = threading.Lock()
LEDGER = Path(os.environ.get('CALLIOPE_LEDGER_DIR', '/opt/data/calliope-ledger'))
MEDIA_LEDGER = Path(os.environ.get('CALLIOPE_MEDIA_LEDGER_DIR') or str(LEDGER))
SUBSCRIPTION = ('subscription', 'codex', 'claude-code', 'anthropic-cli', 'gemini-cli')
RATE_FIELDS = ('prompt', 'completion', 'input_cache_read', 'input_cache_write')


def number(value):
    if isinstance(value, bool): return None
    try: value = float(value)
    except (ValueError, TypeError, OverflowError): return None
    return value if math.isfinite(value) and value >= 0 else None


def model_id(value):
    value = str(value or 'unknown')[:240]
    return value.removeprefix('openrouter/')


def included_hosts():
    # Gateways whose calls are prepaid (none by default: a self-hosted Clover
    # bills your own provider key, so its calls are real spend).
    return {h.strip() for h in os.environ.get('CALLIOPE_INCLUDED_INFERENCE_HOSTS', '').split(',') if h.strip()}


def included(backend, host=''):
    return host in included_hosts() or backend.startswith('clover') or any(x in backend.lower() for x in SUBSCRIPTION)


def price_usage(usage, rates, *, canonical=False):
    """Hermes input excludes cache buckets; OpenAI prompt tokens include them."""
    read = number(usage.get('cache_read_tokens')) or 0
    write = number(usage.get('cache_write_tokens')) or 0
    if canonical:
        fresh = number(usage.get('input_tokens'))
    else:
        details = usage.get('prompt_tokens_details') or {}
        read = number(details.get('cached_tokens')) or read
        prompt = number(usage.get('prompt_tokens', usage.get('input_tokens')))
        fresh = max(0, prompt-read-write) if prompt is not None else None
    output = number(usage.get('output_tokens', usage.get('completion_tokens')))
    if fresh is None or output is None: return None
    # Missing cache rates use ordinary input price, explicitly marked as a
    # standard-input equivalent. No invented cache discount or write premium.
    ip = number(rates.get('prompt')); op = number(rates.get('completion'))
    if ip is None or op is None: return None
    rp = number(rates.get('input_cache_read')); wp = number(rates.get('input_cache_write'))
    return fresh*ip + read*(ip if rp is None else rp) + write*(ip if wp is None else wp) + output*op


def catalog_rates(pricing):
    """Retain token tariffs and documented context tiers, with explicit units."""
    rates = {k: number(pricing.get(k)) for k in RATE_FIELDS}
    rates['catalog_version'] = 2
    rates['unit'] = 'USD_per_token'
    rates['overrides'] = []
    rates['unsupported_conditions'] = []
    for tier in pricing.get('overrides') or []:
        conditional = [k for k in tier if k.startswith('utc_')]
        if conditional:
            rates['unsupported_conditions'].extend(conditional)
            continue  # Do not accidentally apply a time-limited price all day.
        threshold = number(tier.get('min_prompt_tokens'))
        if threshold is not None:
            rates['overrides'].append({'min_prompt_tokens': threshold,
                **{k: number(tier[k]) for k in RATE_FIELDS if k in tier}})
    return rates


def resolve_price_model(model, backend, catalog):
    """Resolve native provider IDs by exact identity, never by model-family guessing."""
    if model in catalog:
        return model
    if '/' in model:
        return None
    vendor = ('openai' if 'codex' in backend or backend == 'openai' else
              'anthropic' if 'claude' in backend or 'anthropic' in backend else
              'google' if 'gemini' in backend else None)
    if vendor:
        name = vendor + '/' + model
        return name if name in catalog else None
    matches = [name for name in catalog if name.endswith('/' + model)]
    return matches[0] if len(matches) == 1 else None


def request_rates(usage, rate, *, canonical=False, aggregate=False):
    """Choose a tier per request, not from a run's cumulative token count."""
    chosen = dict(rate)
    prompt = number(usage.get('prompt_tokens'))
    if prompt is None and number(usage.get('input_tokens')) is not None:
        prompt = number(usage['input_tokens'])
        if canonical:
            prompt += (number(usage.get('cache_read_tokens')) or 0) + (number(usage.get('cache_write_tokens')) or 0)
    tiers = rate.get('overrides') or []  # Catalog order: later matching keys win.
    if not aggregate and prompt is not None:
        for tier in tiers:
            if prompt > tier['min_prompt_tokens']:
                chosen.update(tier)
    return chosen


def ensure_schema(conn):
    conn.execute(Path(__file__).with_suffix('.sql').read_text())
    ensure_memory_metadata(conn)


def refresh_prices(conn):
    """Public catalog only; do not send tenant credentials or usage externally."""
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT max(fetched_at)>now()-interval '24 hours' AS fresh FROM rvbbit.model_price_snapshots WHERE rates->>'catalog_version'='2'")
        if cur.fetchone()['fresh']: return
    import httpx
    response = httpx.get('https://openrouter.ai/api/v1/models', timeout=10)
    response.raise_for_status()
    for item in response.json().get('data', []):
        pricing = item.get('pricing') or {}
        rates = catalog_rates(pricing)
        if not isinstance(item.get('id'),str) or rates['prompt'] is None or rates['completion'] is None: continue
        conn.execute('INSERT INTO rvbbit.model_price_snapshots(model,rates) VALUES (%s,%s::jsonb) '
                     'ON CONFLICT(model) DO UPDATE SET rates=excluded.rates,fetched_at=now()',
                     (item['id'],json.dumps(rates)))


def prices(conn):
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT model,rates,fetched_at FROM rvbbit.model_price_snapshots')
        found = {r['model']:{**r['rates'],'fetched_at':r['fetched_at'].isoformat(),'source':'openrouter_catalog'} for r in cur.fetchall()}
        cur.execute('SELECT model,input_per_mtok,output_per_mtok,updated_at FROM rvbbit.model_rates')
        for r in cur.fetchall():
            found.setdefault(r['model'], {'prompt':float(r['input_per_mtok'])/1e6,'completion':float(r['output_per_mtok'])/1e6,
                'source':'rvbbit.model_rates','fetched_at':r['updated_at'].isoformat()})
        return found


def prepare(row, catalog):
    usage = row.get('usage') or {}
    canonical = row.get('source') in ('hermes_ledger', 'hermes_aux')
    model = model_id(row.get('response_model') or row.get('model'))
    backend = str(row.get('backend') or row.get('provider') or 'unknown')[:120]
    host = row.get('endpoint_host') or ''
    managed = included(backend,host)
    if host in included_hosts():
        backend='clover/hutch'
    price_model = resolve_price_model(model, backend, catalog)
    rate = catalog.get(price_model) or {}
    aggregate = (number(row.get('calls')) or number((row.get('evidence') or {}).get('calls')) or number(usage.get('request_count')) or 1) > 1
    effective_rate = request_rates(usage, rate, canonical=canonical, aggregate=aggregate)
    reported = number(row.get('reported_cost_usd'))
    if reported is None: reported=number(usage.get('cost'))
    relative_cost = price_usage(usage, effective_rate, canonical=canonical)
    cost = reported if reported is not None else relative_cost
    kind = 'included' if managed and cost is not None else 'reported' if reported is not None else 'estimated' if cost is not None else 'unknown'
    failed = row.get('status')=='error' or row.get('event')=='model.failed'
    status = 'error' if failed else 'settled' if reported is not None and not managed else 'estimated' if cost is not None else 'uncosted'
    tin = number(usage.get('prompt_tokens'))
    if tin is None:
        tin=number(usage.get('input_tokens'))
        if canonical and tin is not None: tin += (number(usage.get('cache_read_tokens')) or 0)+(number(usage.get('cache_write_tokens')) or 0)
    details=usage.get('prompt_tokens_details') or {}
    pricing = {'source':'upstream_usage' if reported is not None else rate.get('source','unavailable'),
               'rates':effective_rate,'catalog_rates':rate,
               'cache_fallback':'standard_input_rate','valued_at':datetime.now(timezone.utc).isoformat(),
               'model':price_model,
               'relative_cost_usd':relative_cost,
               'relative_cost_basis':'catalog_token_equivalent_not_provider_charge',
               'cache_rates_missing':[k for k, bucket in [('input_cache_read','cache_read_tokens'),('input_cache_write','cache_write_tokens')]
                                      if (number(usage.get(bucket)) or (number(details.get('cached_tokens')) if bucket == 'cache_read_tokens' else 0)) and number(effective_rate.get(k)) is None],
               'tier_basis':'aggregate_base_rate_only' if aggregate and rate.get('overrides') else 'request_prompt_tokens'}
    if row.get('alias_resolution'): pricing['alias_resolution'] = row['alias_resolution']
    evidence={k:row[k] for k in ('event_id','attempt_id','provider_request_id','response_provider','parent_agent_span_id',
              'hutch_request_id','usage_scope','source_revision','endpoint_host','request_json_bytes','response_json_bytes','error_type','transport_attempts','work_key','billing_mode') if k in row}
    evidence['usage']=usage
    pricing['billing_mode'] = ('subscription_included' if any(x in backend.lower() for x in SUBSCRIPTION)
                              else 'managed_included' if managed else row.get('billing_mode', 'unknown'))
    return dict(call_id=row['call_id'],source=row['source'],category=row.get('category','Calliope'),
        operation=row.get('operation','hermes.model_request'),backend=backend,model=model,
        requested_model=str(row.get('model') or '')[:240],run_id=row.get('run_id'),session_id=row.get('session_id'),caller=row.get('caller'),
        occurred_at=row.get('ts') or datetime.now(timezone.utc).isoformat(),status=status,
        tokens_in=tin,tokens_out=number(usage.get('output_tokens',usage.get('completion_tokens'))),
        cache_read_tokens=number(usage.get('cache_read_tokens',details.get('cached_tokens'))),cache_write_tokens=number(usage.get('cache_write_tokens')),
        latency_ms=number(row.get('attempt_elapsed_ms',row.get('latency_ms'))),cost_usd=cost,cost_kind=kind,
        pricing=pricing,evidence=evidence)


def insert(conn, row):
    fields=list(row)
    values=[json.dumps(row[k],default=str) if k in ('pricing','evidence') else row[k] for k in fields]
    placeholders=[('%s::jsonb' if k in ('pricing','evidence') else '%s') for k in fields]
    conn.execute(f"INSERT INTO rvbbit.model_call_usage ({','.join(fields)}) VALUES ({','.join(placeholders)}) ON CONFLICT(call_id) DO NOTHING",values)


def sync_ledger(conn, directory=None, catalog=None):
    from psycopg.rows import dict_row
    catalog = prices(conn) if catalog is None else catalog
    if directory is None:
        # Server receipts live in its writable volume; Hermes remains read-only.
        return sum(sync_ledger(conn, path, catalog) for path in
                   dict.fromkeys((LEDGER.resolve(), MEDIA_LEDGER.resolve())))
    count=0
    with conn.cursor(row_factory=dict_row) as cur:
        for path in sorted(directory.glob('*.jsonl')):
            stat=path.stat(); identity=f'{stat.st_dev}:{stat.st_ino}:aux-v1'  # replay old cursors once; inserts deduplicate
            cur.execute('SELECT * FROM rvbbit.model_usage_import_state WHERE path=%s',(str(path),))
            state=cur.fetchone()
            offset=state['byte_offset'] if state and state['identity']==identity and state['byte_offset']<=stat.st_size else 0
            with path.open('rb') as f:
                f.seek(offset)
                for _ in range(10000):
                    line=f.readline()
                    if not line.endswith(b'\n'): break # incomplete append: retry next tick
                    r=json.loads(line) # corrupt evidence must not be silently skipped
                    if r.get('event') in ('model.completed','model.failed'):
                        r.update(call_id='hermes:'+str(r.get('attempt_id') or r['event_id']),source='hermes_ledger',
                            category='Business topology' if str(r.get('session_id','')).startswith('business_topology_') else 'Specialists' if r.get('parent_agent_span_id') else 'Scheduled work' if r.get('platform')=='cron' else 'Calliope',operation='hermes.model_request')
                        insert(conn,prepare(r,catalog)); count+=1
                    elif r.get('event') == 'aux.completed':
                        import_aux_receipt(conn,r); count+=1
                    elif r.get('event') == 'media.completed':
                        r.update(call_id='media:'+r['event_id'],source='media',category='Speech to text' if str(r.get('operation','')).startswith('stt.') else 'Text to speech' if str(r.get('operation','')).startswith('tts.') else 'Image generation')
                        insert(conn,prepare(r,catalog)); count+=1
                    offset=f.tell()
            cur.execute('INSERT INTO rvbbit.model_usage_import_state(path,identity,byte_offset) VALUES (%s,%s,%s) '
                        'ON CONFLICT(path) DO UPDATE SET identity=excluded.identity,byte_offset=excluded.byte_offset,updated_at=now()',
                        (str(path),identity,offset))
    return count


def record_completion(conn_factory, call_id, category, operation, client, receipt=None, *, error=None, work_key=None):
    """Record a direct completion without copying its prompt, result or credentials."""
    receipt=receipt or {}
    if category=='Business topology' and receipt.get('provider_request_id'):
        call_id=topology_call_id(receipt)
    row={'call_id':call_id,'source':'direct','category':category,'operation':operation,
         'model':getattr(client,'model','unknown'),'response_model':receipt.get('model_version') or receipt.get('service_model'),
         'backend':getattr(client,'backend_name','unknown'),'endpoint_host':receipt.get('endpoint_host') or urlsplit(getattr(client,'endpoint_url','')).hostname,
         'usage':receipt.get('usage') or {},'latency_ms':receipt.get('latency_ms'),'provider_request_id':receipt.get('provider_request_id'),
         'request_json_bytes':receipt.get('request_json_bytes'),'response_json_bytes':receipt.get('response_json_bytes'),
         'transport_attempts':receipt.get('transport_attempts',1),'work_key':work_key,'status':'error' if error else 'completed'}
    if error: row['error_type']=type(error).__name__
    try:
        with conn_factory() as conn: insert(conn,prepare(row,prices(conn)))
    except Exception as exc:
        log.warning('Central model accounting unavailable (%s)', type(exc).__name__)


def settle_decisions(conn):
    """Recover upstream charges retained in batched decision receipt outputs.

    Use the native append-only settlement so every cost consumer benefits,
    without inserting a second activity or replaying inference. The importer
    holds its transaction advisory lock while reconciling.
    """
    conn.execute('SELECT rvbbit.settle_decision_costs(5000)')


class AccountedClient:
    def __init__(self, client, conn_factory, operation, work_key):
        self.client,self.conn_factory,self.operation,self.work_key=client,conn_factory,operation,work_key
    def __getattr__(self,name):return getattr(self.client,name)
    def complete_json(self,**kwargs):
        call_id='topology:'+str(uuid.uuid4())
        try: result=self.client.complete_json(**kwargs)
        except Exception as exc:
            record_completion(self.conn_factory,call_id,'Business topology',self.operation,self.client,error=exc,work_key=self.work_key)
            raise
        record_completion(self.conn_factory,call_id,'Business topology',self.operation,self.client,result[1],work_key=self.work_key)
        return result


def start_worker(conn_factory):
    global _THREAD
    with _LOCK:
        if _THREAD and _THREAD.is_alive(): return False
        with conn_factory() as conn:
            with conn.transaction(): ensure_schema(conn)
        def worker():
            next_prices=0
            while True:
                try:
                    # Commit delayed SQL receipts independently before settlement;
                    # a large batch can exceed the runner's immediate flush limit.
                    with conn_factory() as receipts:
                        receipts.execute('SELECT rvbbit.flush_receipt_queue(1000)')
                    with conn_factory() as conn, conn.transaction():
                        from psycopg.rows import dict_row
                        with conn.cursor(row_factory=dict_row) as cur:
                            cur.execute("SELECT pg_try_advisory_xact_lock(hashtext('calliope.cost_accounting')) AS locked")
                            if cur.fetchone()['locked']:
                                if time.monotonic()>=next_prices:
                                    next_prices=time.monotonic()+3600
                                    try:
                                        with conn.transaction(): refresh_prices(conn)
                                    except Exception: log.warning('Price catalog refresh unavailable; using saved rates')
                                sync_ledger(conn)
                                sync_memory(conn)
                                sync_auxiliary(conn)
                                settle_decisions(conn)
                                enrich_unpriced(conn)
                except Exception: log.exception('Central model usage sync failed')
                time.sleep(10)
        _THREAD=threading.Thread(target=worker,name='model-cost-accounting',daemon=True);_THREAD.start()
        return True


def enrich_unpriced(conn):
    from psycopg.rows import dict_row
    catalog=prices(conn)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM rvbbit.model_call_usage WHERE cost_kind='unknown' AND status<>'error' ORDER BY occurred_at LIMIT 1000")
        for old in cur.fetchall():
            row={**old['evidence'],**old,'usage':old['evidence'].get('usage',{}),'response_model':old['model'],
                 'model':old['requested_model'],'endpoint_host':old['evidence'].get('endpoint_host'),'ts':old['occurred_at']}
            new=prepare(row,catalog)
            if new['cost_usd'] is not None:
                cur.execute("UPDATE rvbbit.model_call_usage SET cost_usd=%s,cost_kind=%s,status=%s,pricing=%s::jsonb WHERE call_id=%s AND cost_kind='unknown'",
                    (new['cost_usd'],new['cost_kind'],new['status'],json.dumps(new['pricing']),old['call_id']))


def topology_call_id(receipt):
    identity=f"{receipt.get('endpoint_host','')}:{receipt.get('provider_request_id')}"
    return 'topology:'+hashlib.sha256(identity.encode()).hexdigest()


def import_topology_receipts(conn, root):
    """Backfill only receipts with provider IDs, deduplicating cached copies.

    Other private JSON (including prompts/results) never leaves this process.
    Old receipts without IDs cannot be deduplicated reliably and are skipped.
    """
    catalog=prices(conn); count=0
    def walk(value):
        if isinstance(value,dict):
            if str(value.get('schema_version','')).startswith('rvbbit.business-topology.hutch-receipt.') and value.get('provider_request_id'):
                yield value
            else:
                for child in value.values(): yield from walk(child)
        elif isinstance(value,list):
            for child in value: yield from walk(child)
    for path in root.rglob('*.json'):
        if path.stat().st_size>32*1024*1024: continue
        try: receipts=list(walk(json.loads(path.read_text())))
        except (ValueError,OSError):continue
        for r in receipts:
            row={'call_id':topology_call_id(r),'source':'direct','category':'Business topology','operation':'topology.completion',
                 'backend':r.get('backend','unknown'),'endpoint_host':r.get('endpoint_host'),'model':r.get('service_model'),
                 'response_model':r.get('model_version'),'usage':r.get('usage',{}),'latency_ms':r.get('latency_ms'),
                 'provider_request_id':r['provider_request_id'],'ts':datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat()}
            prepared=prepare(row,catalog)
            prepared['evidence']['time_basis']='legacy_receipt_file_mtime'
            insert(conn,prepared);count+=1
    return count


def ensure_memory_metadata(conn):
    if os.environ.get('CALLIOPE_HINDSIGHT_METADATA_ONLY')!='true': return
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT to_regclass('hindsight.llm_requests') IS NOT NULL AS available")
        if not cur.fetchone()['available']: return
    conn.execute('''CREATE OR REPLACE FUNCTION rvbbit.memory_cost_metadata_only() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN NEW.input=NULL; NEW.output=NULL; NEW.error=CASE WHEN NEW.error IS NOT NULL THEN 'provider_error' ELSE NULL END;
      NEW.llm_info='{}'::jsonb; NEW.metadata='{}'::jsonb; RETURN NEW; END $$;
      DROP TRIGGER IF EXISTS calliope_cost_metadata_only ON hindsight.llm_requests;
      CREATE TRIGGER calliope_cost_metadata_only BEFORE INSERT OR UPDATE ON hindsight.llm_requests
      FOR EACH ROW EXECUTE FUNCTION rvbbit.memory_cost_metadata_only();''')


def sync_memory(conn):
    from psycopg.rows import dict_row
    catalog=prices(conn); count=0
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT to_regclass('hindsight.llm_requests') IS NOT NULL AS available")
        if not cur.fetchone()['available']: return 0
        # Explicit safe column list: never fetch memory prompts, outputs or errors.
        cur.execute('''SELECT id,operation,provider,model,status,started_at,duration_ms,input_tokens,output_tokens,cached_tokens
          FROM hindsight.llm_requests h WHERE ended_at IS NOT NULL AND NOT EXISTS
          (SELECT 1 FROM rvbbit.model_call_usage m WHERE m.call_id='memory:'||h.id) ORDER BY started_at LIMIT 2000''')
        for r in cur.fetchall():
            row={'call_id':'memory:'+str(r['id']),'source':'hindsight','category':'Memory','operation':'memory.'+str(r['operation']),
                 'provider':r['provider'],'model':r['model'],'ts':r['started_at'],
                 'endpoint_host':urlsplit(os.environ.get('CALLIOPE_MEMORY_INFERENCE_BASE_URL','')).hostname,'latency_ms':r['duration_ms'],
                 'status':'error' if r['status'] in ('error','failed') else 'completed',
                 'usage':{'prompt_tokens':r['input_tokens'],'completion_tokens':r['output_tokens'],
                          'prompt_tokens_details':{'cached_tokens':r['cached_tokens']}}}
            resolve_alias(conn, row)
            insert(conn,prepare(row,catalog));count+=1
    return count


def resolve_alias(conn, row):
    """Use only a route observation preceding this activity, scoped to its host.

    This is explicitly an estimate: an alias can change between observations.
    Never rewrite a response model, or apply today's route to yesterday's calls.
    """
    from psycopg.rows import dict_row
    if row.get('response_model'): return
    host = row.get('endpoint_host')
    if not host: return
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT alias,endpoint_host,model,observed_at,evidence
          FROM rvbbit.model_alias_snapshots WHERE alias=%s AND endpoint_host=%s
          AND observed_at<=%s ORDER BY observed_at DESC LIMIT 1""",
          (row.get('model'),host,row['ts']))
        found=cur.fetchone()
    if found:
        row['response_model']=found['model']
        row['alias_resolution']={**found,'observed_at':found['observed_at'].isoformat(),
            'basis':'last_observed_route_estimate'}


def probe_aliases(conn):
    """Explicit operator command: tiny completions to resolve managed aliases.

    /models advertises names only. Persist response metadata, never prompts,
    completions or keys. These preparation probes are outside benchmark windows.
    """
    import httpx
    base=os.environ.get('RVBBIT_CLOVER_OPENAI_BASE_URL','http://clover:8090/v1').rstrip('/')
    host=urlsplit(base).hostname
    headers={'Authorization':'Bearer '+os.environ['RVBBIT_CLOVER_KEY']}
    unresolved=[]
    with httpx.Client(timeout=90) as client:
        response=client.get(base+'/models',headers=headers);response.raise_for_status()
        aliases=sorted({r['id'] for r in response.json()['data'] if str(r.get('id','')).startswith('calliope')})
        for alias in aliases:
            try:
                response=client.post(base+'/chat/completions',headers=headers,json={
                    'model':alias,'messages':[{'role':'user','content':'Reply OK.'}],'max_tokens':32})
                response.raise_for_status();r=response.json()
            except (httpx.HTTPError, ValueError) as exc:
                unresolved.append(alias)
                print(json.dumps({'alias':alias,'resolved':False,'error_type':type(exc).__name__}),flush=True)
                continue
            resolved=model_id(response.headers.get('x-hutch-model-version') or r.get('model'))
            if resolved=='unknown' or resolved==alias:
                unresolved.append(alias)
                print(json.dumps({'alias':alias,'resolved':False,'error_type':'missing_model_version'}),flush=True)
                continue
            usage=r.get('usage') or {}
            evidence={'source':'operator_route_probe','provider_request_id':r.get('id'),
                'model_basis':'x-hutch-model-version' if response.headers.get('x-hutch-model-version') else 'response_model',
                'usage':{k:usage[k] for k in ('prompt_tokens','completion_tokens','total_tokens','cost') if k in usage}}
            conn.execute("""INSERT INTO rvbbit.model_alias_snapshots(alias,endpoint_host,model,evidence)
              VALUES(%s,%s,%s,%s::jsonb)""",(alias,host,resolved,json.dumps(evidence)))
            print(json.dumps({'alias':alias,'model':resolved,'endpoint_host':host}),flush=True)
    return unresolved


def import_aux_receipt(conn, row):
    # Canonical counters only. No prompts, arbitrary payloads or provider URLs.
    usage = {k:number((row.get('usage') or {}).get(k)) for k in
             ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens')}
    reported = number(row.get('reported_cost_usd'))
    if reported is None: reported = number((row.get('usage') or {}).get('cost'))
    evidence = {k:row[k] for k in ('provider_request_id','run_id','model','response_model') if row.get(k)}
    conn.execute("""INSERT INTO rvbbit.model_aux_receipts
      (event_id,session_id,model,provider,endpoint_host,task,occurred_at,usage,reported_cost_usd,evidence)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb) ON CONFLICT(event_id) DO NOTHING""",
      (row['event_id'],row.get('session_id') or '',row.get('response_model') or row.get('model') or 'unknown',
       row.get('provider') or '',row.get('endpoint_host') or '',row.get('task_id') or '',row['ts'],
       json.dumps(usage),reported,json.dumps(evidence)))


def auxiliary_receipt_coverage(native, receipts):
    """Only replace an aggregate valuation when complete coverage is proven.

    A missing/late event or different billing bucket never turns a partial sum
    into a total. Retain partial evidence so the discrepancy is visible.
    """
    keys = ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens')
    complete = len(receipts) == native['api_call_count'] and bool(receipts)
    for key in keys:
        values = [number(r['usage'].get(key)) for r in receipts]
        complete = complete and all(v is not None for v in values) and sum(v or 0 for v in values) == native[key]
    charges = [number(r['reported_cost_usd']) for r in receipts]
    fully_priced = complete and all(c is not None for c in charges)
    return {'receipt_count':len(receipts),'expected_calls':native['api_call_count'],
            'tokens_match':bool(complete),'priced_receipts':sum(c is not None for c in charges),
            'reported_cost_usd':sum(charges) if fully_priced else None,
            'status':'complete' if fully_priced else 'partial' if receipts else 'missing'}


def sync_auxiliary(conn):
    """Value native session/model/task aggregates without duplicating main calls.

    Freeze the first valuation's tariff and route. Later token increments use
    that same basis; the original native aggregate remains the source evidence.
    """
    from psycopg.rows import dict_row
    catalog=prices(conn); count=0
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""SELECT h.*, 'hermes_aux:'||md5(concat_ws('|',h.session_id,h.model,
          h.billing_provider,h.billing_base_url,h.billing_mode,h.task)) AS call_id
          FROM rvbbit.hermes_model_usage h WHERE h.task<>''""")
        native_rows=cur.fetchall()
        from collections import Counter
        bucket=lambda r:(r['session_id'],r['model'],r['billing_provider'] or '',urlsplit(r['billing_base_url']).hostname or '',r['task'])
        buckets=Counter(bucket(r) for r in native_rows)
        for r in native_rows:
            cur.execute('SELECT * FROM rvbbit.model_call_usage WHERE call_id=%s',(r['call_id'],))
            old=cur.fetchone()
            usage={k:r[k] for k in ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens')}
            native={k:r[k] for k in ('api_call_count','actual_cost_usd','estimated_cost_usd','cost_source')}
            cur.execute('''SELECT usage,reported_cost_usd FROM rvbbit.model_aux_receipts
              WHERE session_id=%s AND model=%s AND provider=%s AND endpoint_host=%s AND task=%s''',
              (r['session_id'],r['model'],r['billing_provider'] or '',urlsplit(r['billing_base_url']).hostname or '',r['task']))
            coverage=auxiliary_receipt_coverage(r,cur.fetchall())
            if buckets[bucket(r)]>1:
                # Older metadata stores host, not credential/path/billing-mode identity.
                coverage.update(status='ambiguous_billing_buckets',reported_cost_usd=None)
            if old and old['evidence'].get('usage')==usage and old['evidence'].get('native')==native and old['evidence'].get('receipt_coverage')==coverage: continue
            row={'call_id':r['call_id'],'source':'hermes_aux','category':'Hermes auxiliary',
                'operation':r['task'],'model':r['model'],'backend':r['billing_provider'] or 'hermes',
                'endpoint_host':urlsplit(r['billing_base_url']).hostname,'billing_mode':r['billing_mode'],
                'session_id':r['session_id'],'usage':usage,'calls':r['api_call_count'],'ts':r['first_seen'] or r['last_seen']}
            if r['actual_cost_usd']>0: row['reported_cost_usd']=r['actual_cost_usd']
            elif coverage['reported_cost_usd'] is not None:
                row['reported_cost_usd']=coverage['reported_cost_usd']
            basis=catalog
            if old and old['pricing'].get('rates'):
                row['response_model']=old['model'];row['alias_resolution']=old['pricing'].get('alias_resolution')
                basis={old['pricing'].get('model') or old['model']:
                       old['pricing'].get('catalog_rates') or old['pricing']['rates']}
            else: resolve_alias(conn,row)
            prepared=prepare(row,basis)
            if prepared['cost_usd'] is None and r['estimated_cost_usd']>0:
                prepared.update(cost_usd=r['estimated_cost_usd'],cost_kind='estimated',status='estimated')
                prepared['pricing']['source']='native_auxiliary_estimate'
            prepared['occurred_at']=r['last_seen'] or prepared['occurred_at']
            prepared['evidence'].update(calls=r['api_call_count'],native=native,receipt_coverage=coverage,aggregation='session_model_task',
                first_seen=str(r['first_seen']),last_seen=str(r['last_seen']))
            # Safe to replace this derived valuation; never touch native usage.
            if old:
                cur.execute('DELETE FROM rvbbit.model_call_usage WHERE call_id=%s',(r['call_id'],))
            insert(conn,prepared);count+=1
    return count


if __name__=='__main__':
    import argparse
    import psycopg
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backfill-topology',action='store_true')
    parser.add_argument('--probe-aliases',action='store_true',help='Make small paid route probes before a benchmark')
    args=parser.parse_args()
    with psycopg.connect(os.environ['WAREHOUSE_DSN']) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('calliope.cost_accounting'))")
        ensure_schema(conn)
        try:
            with conn.transaction():refresh_prices(conn)
        except Exception:log.warning('Using stored model rates')
        unresolved=probe_aliases(conn) if args.probe_aliases else []
        imported=sync_ledger(conn)
        memory=sync_memory(conn)
        auxiliary=sync_auxiliary(conn)
        settle_decisions(conn)
        enrich_unpriced(conn)
        topology=import_topology_receipts(conn,Path(os.environ.get('WAREHOUSE_BUSINESS_TOPOLOGY_DIR','/app/data/business-topology'))) if args.backfill_topology else 0
        print(json.dumps({'ledger_events_processed':imported,'topology_receipts_processed':topology,'memory_requests_processed':memory,'auxiliary_aggregates_processed':auxiliary,'unresolved_aliases':unresolved}))
