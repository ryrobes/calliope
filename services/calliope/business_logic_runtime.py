"""Published rule contracts, authorized SQL bindings and frozen run receipts.

The registry stays private. Each governed SQL connection receives only the
definitions this verified subject may execute, before it becomes read-only.
"""
import hashlib
import json
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

from psycopg.types.json import Jsonb
from app_actions import ActionError

KIND = 'cap_business_logic'
MAX_DETAILS = 200
MAX_DETAIL_BYTES = 2_000_000


def tree_contract(tree):
    fields, labels = set(), set()
    def walk(node):
        if node['kind'] == 'leaf':
            labels.add(node['label']); return
        fields.update(node.get('inputs', []))
        for child in ([node['yes'], node['no']] if node['kind'] == 'branch' else node['routes'].values()):
            walk(child)
    walk(tree)
    return sorted(fields), sorted(labels)


def metadata(value, tree):
    from business_logic import bounded_text
    key = bounded_text(value.get('key', ''), 'Rule key', 100, True)
    if key and not re.fullmatch(r'[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*', key):
        raise ValueError('Use a stable key such as hvac.service_triage')
    fields, labels = tree_contract(tree)
    inputs = value.get('inputs', {})
    outcomes = value.get('outcomes', {})
    if not isinstance(inputs, dict) or len(inputs) > 64 or not isinstance(outcomes, dict) or len(outcomes) > 63:
        raise ValueError('Use input and outcome description objects')
    clean = {}
    for name, spec in inputs.items():
        bounded_text(name, 'Input name', 80)
        if not isinstance(spec, dict) or set(spec) - {'type', 'description'} or spec.get('type', 'any') not in {'any','string','number','boolean','object','array'}:
            raise ValueError('Input has type and description')
        clean[name] = {'type': spec.get('type', 'any'), 'description': bounded_text(spec.get('description',''), 'Input description', 500, True)}
    for name in fields:
        clean.setdefault(name, {'type':'any','description':''})
    return {'key':key, 'description':bounded_text(value.get('description',''), 'When to use', 1200, True),
            'inputs':clean, 'outcomes':{name:bounded_text(outcomes.get(name,''), 'Outcome description',500,True) for name in labels}}


def _has_catalog(conn):
    return bool(conn.execute("SELECT to_regclass('rvbbit.capability_access_policies') IS NOT NULL AS ok").fetchone()['ok'])


def _access_sql(conn):
    # Absence of a capability policy NEVER makes a rule public.
    if not _has_catalog(conn):
        return 'b.owner_email=%(actor)s'
    return """(b.owner_email=%(actor)s OR (EXISTS (
      SELECT 1 FROM rvbbit.capability_access_policies p
      WHERE p.capability_kind='cap_business_logic' AND p.capability_name=b.rule_key)
      AND rvbbit.capability_can_use('cap_business_logic',b.rule_key,%(actor)s)))"""


def published(conn, actor, rule=None, version=None):
    if not actor:
        return []
    access = _access_sql(conn)
    return conn.execute(f"""SELECT b.id::text,b.rule_key,b.published_version,v.version,v.definition,
      md5(v.definition::text) AS definition_hash
      FROM rvbbit.calliope_business_logic b JOIN rvbbit.calliope_business_logic_versions v ON v.logic_id=b.id
      WHERE b.published_version IS NOT NULL AND v.published_at IS NOT NULL AND {access}
      AND (%(rule)s::text IS NULL OR b.rule_key=%(rule)s)
      AND (%(version)s::integer IS NULL OR v.version=%(version)s)
      ORDER BY b.rule_key,v.version""", {'actor':actor,'rule':rule,'version':version}).fetchall()


def snapshot(row):
    doc = row['definition']
    return {'id':row['id'],'key':row['rule_key'],'name':doc['name'],'version':row['version'],
            'definition_hash':row['definition_hash'],'definition':doc,
            'url':f"/meaning/logic?id={row['id']}&version={row['version']}"}


def capability(row):
    import business_logic_actions as actions
    doc = row['definition']
    fields, _ = tree_contract(doc['tree'])
    props = {key:{**({'type':[spec['type'],'null']} if spec['type']!='any' else {}),
                 'description':spec['description']} for key,spec in doc.get('inputs',{}).items()}
    return {'kind':KIND,'name':row['rule_key'],'doc':doc.get('description',''),
            'title':doc['name'],'topic':doc['topic'],'version':row['version'],
            'inputs':{'type':'object','properties':props},'bound_fields':fields,
            'input_requirements':'Only inputs used on the visited path are required. Null is supplied evidence, not a missing key.',
            'outcomes':doc.get('outcomes',{}),'examples':doc.get('examples',[])[:2],
            'invocation':{'tool':'run_business_logic','arguments':{'rule':row['rule_key'],'version':row['version']},'needs_arguments':['context']},
            'sql_name':'rvbbit.business_logic',
            'sql_example':f"rvbbit.business_logic('{row['rule_key']}', to_jsonb(source_row), {row['version']})",
            'actions':actions.inventory(doc['tree']),
            'execution':{'tool':'execute_business_logic','arguments':{'rule':row['rule_key'],'version':row['version']},
                         'needs_arguments':['context','request_key']} if actions.inventory(doc['tree']) else None,
            'inspect':{'tool':'read_business_logic','arguments':{'rule':row['rule_key'],'version':row['version']}},
            'url':f"/meaning/logic?id={row['id']}&version={row['version']}"}


def resolve(factory, actor, rule, version=None):
    if version is not None and (type(version) is not int or version < 1):
        raise ValueError('Version must be a positive integer')
    if not isinstance(rule,str) or not rule:
        raise ValueError('Supply a rule key from capability_search')
    with factory() as conn:
        rows = published(conn,actor,rule,version)
    row = next((r for r in rows if version is not None or r['version']==r['published_version']),None)
    if not row:
        raise ActionError('NOT_FOUND','Rule or published revision is unavailable',404)
    return row


def project(conn, row, actor):
    """Project the same definition into the existing semantic capability index."""
    if not _has_catalog(conn):
        return
    key = row['rule_key']
    conn.execute("""INSERT INTO rvbbit.capability_access_policies
       (capability_kind,capability_name,owner_email,created_by,updated_by)
       VALUES(%s,%s,%s,%s,%s) ON CONFLICT(capability_kind,capability_name) DO NOTHING""",(KIND,key,actor,actor,actor))
    if not row.get('published_version'):
        conn.execute("DELETE FROM rvbbit.catalog_docs WHERE graph_id='rvbbit_capabilities' AND kind=%s AND rel_name=%s",(KIND,key))
        return
    card = capability(row)
    doc = '\n'.join([f"Business logic: {card['title']} ({key})", f"Topic: {card['topic']}",card['doc'],
                     'Inputs: '+json.dumps(card['inputs']), 'Outcomes: '+json.dumps(card['outcomes']),
                     f"Evaluate: run_business_logic(rule='{key}',context=...,version={row['version']})",'SQL: '+card['sql_example'],
                     'Terminal actions (explicit execute_business_logic): '+json.dumps(card['actions'])])
    node = conn.execute("SELECT rvbbit.kg_assert_node(%s,%s,%s::jsonb,1.0,'',0.0,'rvbbit_capabilities') AS id",(KIND,key,Jsonb({'rule_id':str(row['id']),'version':row['version'],'search_doc':doc}))).fetchone()['id']
    vector = None
    try:
        with conn.transaction():
            vector = conn.execute("SELECT rvbbit.embed(%s,'','document') AS v",(doc,)).fetchone()['v']
    except Exception:
        pass  # lexical catalog search remains available if the embedder is down
    conn.execute("""INSERT INTO rvbbit.catalog_docs(node_id,graph_id,kind,rel_name,doc,embedding,embedded_at,updated_at)
      VALUES(%s,'rvbbit_capabilities',%s,%s,%s,%s,now(),now())
      ON CONFLICT(graph_id,node_id) DO UPDATE SET doc=excluded.doc,embedding=excluded.embedding,
      embedded_at=excluded.embedded_at,updated_at=now()""",(node,KIND,key,doc,vector))


def publish(factory, actor, logic_id, version):
    import business_logic as logic
    logic.identity(actor);logic_id=logic.uid(logic_id)
    if version is not None and (type(version) is not int or version < 1):
        raise ValueError('Choose a saved version or null to remove availability')
    with factory() as conn, conn.transaction():
        row=conn.execute('SELECT * FROM rvbbit.calliope_business_logic WHERE id=%s AND owner_email=%s FOR UPDATE',(logic_id,actor)).fetchone()
        if not row:raise ActionError('NOT_FOUND','Decision not found',404)
        if version is not None:
            revision=conn.execute('SELECT definition FROM rvbbit.calliope_business_logic_versions WHERE logic_id=%s AND version=%s',(logic_id,version)).fetchone()
            if not revision:raise ActionError('NOT_FOUND','Version not found',404)
            doc=revision['definition']
            if not row['rule_key'] or not doc.get('description'):
                raise ValueError('Save a stable rule key and a short when-to-use description first')
            missing=[key for key,spec in doc.get('inputs',{}).items() if not spec.get('description')]
            if missing:raise ValueError('Describe the input fields: '+', '.join(missing))
            conn.execute('UPDATE rvbbit.calliope_business_logic_versions SET published_at=coalesce(published_at,now()) WHERE logic_id=%s AND version=%s',(logic_id,version))
        conn.execute('UPDATE rvbbit.calliope_business_logic SET published_version=%s WHERE id=%s',(version,logic_id))
        row['published_version']=version
        if version is not None:row.update(definition=doc,version=version)
        project(conn,row,actor)
    return logic.read(factory,actor,logic_id)


class Capture:
    def __init__(self, actor, source, sql=None):
        self.id=str(uuid.uuid4());self.actor=actor;self.source=source;self.sql=sql
        self.rows=[];self.counts=Counter();self.statuses=Counter();self.definitions={}
        self.total=0;self.bytes=0;self.started=time.monotonic();self.allowed={}

    def notice(self, diag):
        message=diag.message_primary or ''
        if not message.startswith('calliope_logic:'):return
        try: row=json.loads(message.removeprefix('calliope_logic:'))
        except (ValueError,TypeError):return
        if row.get('run_id')!=self.id:return
        rule=row.get('decision',{}).get('rule',{})
        key=(rule.get('key'),rule.get('version'))
        snap=self.allowed.get(key)
        if not snap or rule.get('definition_hash')!=snap['definition_hash']:return
        self.total+=1;decision=row['decision'];self.counts[decision.get('label') or '(no outcome)']+=1
        self.statuses[decision.get('status') or 'unknown']+=1
        self.definitions[f"{key[0]}@{key[1]}"]=snap
        size=len(message.encode())
        if len(self.rows)<MAX_DETAILS and self.bytes+size<=MAX_DETAIL_BYTES:
            self.rows.append({'row':self.total,'input':row['input'],'decision':decision});self.bytes+=size

    def persist(self, factory, error=None):
        if not self.total:return None
        receipt={'id':self.id,'type':'business_logic','source':self.source,
                 'created_at':datetime.now(timezone.utc).isoformat(),'elapsed_ms':round((time.monotonic()-self.started)*1000),
                 'evaluations':self.total,'outcomes':dict(self.counts),'statuses':dict(self.statuses),
                 'status':'failed' if error else 'complete' if self.statuses.get('complete',0)==self.total else 'partial',
                 'rows':self.rows,'definitions':self.definitions,'details_truncated':len(self.rows)<self.total,
                 'sql':self.sql,'error':error,'url':'/meaning/logic?run='+self.id}
        with factory() as conn:
            conn.execute('INSERT INTO rvbbit.calliope_business_logic_runs(id,owner_email,receipt) VALUES(%s,%s,%s) ON CONFLICT(id) DO NOTHING',(self.id,self.actor,Jsonb(receipt)))
        return receipt_summary(receipt)


def receipt_summary(receipt):
    return {k:v for k,v in receipt.items() if k not in {'rows','definitions','sql','fingerprint','action'}} | {
        'rules':[{k:v for k,v in rule.items() if k!='definition'} for rule in receipt['definitions'].values()],
        'preview':[{k:v for k,v in row.items() if k!='input'} for row in receipt['rows'][:3]],
        **({'action':{k:v for k,v in receipt['action'].items() if k!='input'}} if receipt.get('action') else {})}


def input_errors(doc, context):
    def kind(value):
        return ('boolean' if isinstance(value,bool) else 'number' if isinstance(value,(int,float)) else
                'string' if isinstance(value,str) else 'object' if isinstance(value,dict) else 'array')
    return [{'field':key,'expected':spec['type'],'actual':kind(context[key])}
            for key,spec in doc.get('inputs',{}).items()
            if key in context and context[key] is not None and spec['type'] not in {'any',kind(context[key])}]


def prepare(conn, factory, actor, capture, only=None):
    with factory() as registry:
        rows=published(registry,actor,*(only or (None,None)))
    conn.execute('CREATE TEMP TABLE calliope_logic_bindings(rule_key text,revision integer,active boolean,snapshot jsonb,run_id uuid,PRIMARY KEY(rule_key,revision)) ON COMMIT PRESERVE ROWS')
    for row in rows:
        snap=snapshot(row);capture.allowed[(row['rule_key'],row['version'])]=snap
        conn.execute('INSERT INTO pg_temp.calliope_logic_bindings VALUES(%s,%s,%s,%s,%s)',(row['rule_key'],row['version'],row['version']==row['published_version'],Jsonb(snap),capture.id))
    # PostgreSQL permits temp-table DML even in read-only transactions. Freeze
    # these explicitly; the read-only boundary prevents dropping the trigger.
    conn.execute('CREATE TRIGGER frozen_logic_bindings BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON pg_temp.calliope_logic_bindings FOR EACH STATEMENT EXECUTE FUNCTION rvbbit._business_logic_frozen_bindings()')
    conn.execute('ALTER TABLE pg_temp.calliope_logic_bindings ENABLE ALWAYS TRIGGER frozen_logic_bindings')
    conn.add_notice_handler(capture.notice)


def run(factory, actor, rule, context, version=None):
    row=resolve(factory,actor,rule,version)
    capture=Capture(actor,'tool')
    with factory() as conn:
        prepare(conn,factory,actor,capture,(rule,row['version']))
        conn.autocommit=False;conn.read_only=True
        conn.execute("SET LOCAL statement_timeout='8000ms'")
        conn.execute("SET LOCAL client_min_messages='notice'")
        result=conn.execute('SELECT rvbbit.business_logic(%s,%s,%s) AS decision',(rule,Jsonb(context),row['version'])).fetchone()['decision']
    receipt=capture.persist(factory)
    return {'decision':result,'receipt':receipt}


def read_receipt(factory, actor, run_id):
    import business_logic as logic
    logic.identity(actor)
    with factory() as conn:
        row=conn.execute('SELECT receipt FROM rvbbit.calliope_business_logic_runs WHERE id=%s AND owner_email=%s',(logic.uid(run_id),actor)).fetchone()
    if not row:raise ActionError('NOT_FOUND','Receipt not found',404)
    return row['receipt']


def receipt_surfaces(value):
    receipt=value.get('business_logic_receipt') or value.get('receipt') if isinstance(value,dict) else None
    if not isinstance(receipt,dict) or receipt.get('type')!='business_logic':return []
    return [{'kind':'action','title':'Business logic · '+', '.join(r['name'] for r in receipt.get('rules',[])),
             'lineage_key':'business-logic-run:'+receipt['id'],
             'payload':{'mode':'business_logic_receipt','receipt':receipt},
             'source':{'origin':'business_logic','run_id':receipt['id']},'presentation':{'view':'business_logic_receipt'}}]
