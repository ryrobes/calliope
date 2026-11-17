"""Saved, owner-scoped decision trees using the existing JSON/SQL evaluator."""
import json
from pathlib import Path
import uuid

from psycopg.types.json import Jsonb
from app_actions import ActionError
import ui_decisions
import business_logic_runtime as runtime
import business_logic_actions as actions
import business_logic_usage as usage


def ensure(factory):
    with factory() as conn:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('calliope-business-logic',0))")
            conn.execute(Path(__file__).with_suffix('.sql').read_text())
            conn.execute(Path(__file__).with_name('business_logic_runtime.sql').read_text())


def identity(actor):
    if not actor:
        raise ActionError('UNAUTHORIZED', 'Sign in to Calliope', 401)
    return actor


def uid(value):
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise ValueError('Invalid decision ID') from None


def bounded_text(value, name, maximum, empty=False):
    if not isinstance(value, str) or len(value.strip()) > maximum or (not empty and not value.strip()):
        raise ValueError(f'{name} must be text of {1 if not empty else 0}..{maximum} characters')
    return value.strip()


def definition(value):
    if not isinstance(value, dict) or set(value) - {'name', 'topic', 'tree', 'examples', 'source_sql', 'key', 'description', 'inputs', 'outcomes'}:
        raise ValueError('Use name, topic, tree and optional examples')
    if len(json.dumps(value, allow_nan=False).encode()) > 160000:
        raise ValueError('Decision exceeds 160 KB')
    tree, pure = actions.trees(value.get('tree'))
    examples = value.get('examples', [])
    if not isinstance(examples, list) or len(examples) > 12:
        raise ValueError('Use at most 12 examples')
    clean = []
    for item in examples:
        if not isinstance(item, dict) or set(item) - {'name', 'context', 'expected'}:
            raise ValueError('An example has name, context and optional expected label')
        ui_decisions.validate({'state': item.get('context'), 'tree': pure})
        clean.append({'name': bounded_text(item.get('name'), 'Example name', 100),
                      'context': item['context'],
                      'expected': bounded_text(item.get('expected', ''), 'Expected label', 100, True)})
    source = bounded_text(value.get('source_sql', ''), 'Source SQL', 16000, True)
    return {'source_sql': source, 'name': bounded_text(value.get('name'), 'Name', 160),
            'topic': bounded_text(value.get('topic', ''), 'Topic', 160, True),
            'tree': tree, 'examples': clean, **runtime.metadata(value, tree)}


def catalog(factory, actor, query=''):
    identity(actor)
    query = bounded_text(query, 'Search', 160, True)
    with factory() as conn:
        rows = conn.execute("SELECT id::text,name,topic,version,rule_key,published_version,updated_at::text FROM rvbbit.calliope_business_logic "
                            "WHERE owner_email=%s AND (strpos(lower(name || ' ' || topic),lower(%s))>0) "
                            "ORDER BY updated_at DESC LIMIT 101", (actor, query)).fetchall()
    return {'items': rows[:100], 'has_more': len(rows) > 100}


def read(factory, actor, logic_id, version=None):
    identity(actor)
    if version is not None and (type(version) is not int or version < 1):
        raise ValueError('Version must be a positive integer')
    with factory() as conn:
        row = conn.execute("SELECT id::text,version,definition,rule_key,published_version,updated_at::text FROM rvbbit.calliope_business_logic "
                           "WHERE id=%s AND owner_email=%s", (uid(logic_id), actor)).fetchone()
        if not row:
            raise ActionError('NOT_FOUND', 'Decision not found', 404)
        versions = conn.execute("SELECT version,created_at::text FROM rvbbit.calliope_business_logic_versions "
                                "WHERE logic_id=%s ORDER BY version DESC LIMIT 100", (row['id'],)).fetchall()
        if version is not None:
            old = conn.execute("SELECT definition FROM rvbbit.calliope_business_logic_versions WHERE logic_id=%s AND version=%s",
                               (row['id'], version)).fetchone()
            if not old:
                raise ActionError('NOT_FOUND', 'Version not found', 404)
            row = {**row, 'current_version': row['version'], 'version': version, 'definition': old['definition']}
    return {**row, 'versions': versions, 'url': '/meaning/logic?id=' + row['id']}


def save(factory, actor, body):
    identity(actor)
    if not isinstance(body, dict) or set(body) - {'id', 'version', 'definition'}:
        raise ValueError('Use id, version and definition')
    doc = definition(body.get('definition'))
    logic_id = uid(body['id']) if body.get('id') else str(uuid.uuid4())
    expected = body.get('version', 0)
    if type(expected) is not int or expected < 0:
        raise ValueError('Version must be a nonnegative integer')
    with factory() as conn:
        with conn.transaction():
            if body.get('id'):
                row = conn.execute("SELECT version,rule_key FROM rvbbit.calliope_business_logic WHERE id=%s AND owner_email=%s FOR UPDATE",
                                   (logic_id, actor)).fetchone()
                if not row:
                    raise ActionError('NOT_FOUND', 'Decision not found', 404)
                if row['version'] != expected:
                    raise ActionError('VERSION_CONFLICT', 'This decision changed. Reload it before saving.', 409)
                if row['rule_key'] and doc['key'] != row['rule_key']:
                    raise ValueError('A saved rule key is stable. Create another decision to use a different key.')
            elif expected != 0:
                raise ValueError('A new decision starts at version 0')
            version = expected + 1
            if doc['key'] and conn.execute('SELECT id FROM rvbbit.calliope_business_logic WHERE rule_key=%s AND id<>%s',(doc['key'],logic_id)).fetchone():
                raise ValueError('That rule key is already in use. Choose another namespace or name.')
            if body.get('id'):
                conn.execute("UPDATE rvbbit.calliope_business_logic SET name=%s,topic=%s,version=%s,definition=%s,updated_at=now() "
                             "WHERE id=%s AND owner_email=%s", (doc['name'], doc['topic'], version, Jsonb(doc), logic_id, actor))
            else:
                conn.execute("INSERT INTO rvbbit.calliope_business_logic(id,owner_email,name,topic,version,definition) VALUES(%s,%s,%s,%s,%s,%s)",
                             (logic_id, actor, doc['name'], doc['topic'], version, Jsonb(doc)))
            conn.execute("INSERT INTO rvbbit.calliope_business_logic_versions(logic_id,version,definition) VALUES(%s,%s,%s)",
                         (logic_id, version, Jsonb(doc)))
            conn.execute('UPDATE rvbbit.calliope_business_logic SET rule_key=%s WHERE id=%s',(doc['key'] or None,logic_id))
    return read(factory, actor, logic_id)


def evaluate(factory, actor, body):
    identity(actor)
    if not isinstance(body, dict) or set(body) - {'id', 'version', 'tree', 'context'}:
        raise ValueError('Supply context and either a saved id/version or an inline tree')
    if bool(body.get('id')) == ('tree' in body):
        raise ValueError('Choose a saved decision or an inline tree')
    if 'version' in body and not body.get('id'):
        raise ValueError('Version requires a saved decision')
    record = read(factory, actor, body['id'], body.get('version')) if body.get('id') else None
    _, tree = actions.trees(record['definition']['tree'] if record else body['tree'])
    result = ui_decisions.evaluate(factory, {'state': body.get('context'), 'tree': tree}, capture_usage=True)
    result['usage'] = usage.record(factory, actor, [result['decision']])
    return {**result, 'id': record['id'] if record else None, 'version': record['version'] if record else None}


def register_tools(mcp, factory, caller, logged=None, execute=None):
    def invoke(name, args, fn):
        return logged(name,args,fn) if logged else fn()
    @mcp.tool(name='read_business_logic')
    def read_business_logic(query: str = '', logic_id: str | None = None, version: int | None = None, rule: str | None = None):
        """Find saved decision trees by name/topic, or read one with its examples and version history.
        Definitions belong to the current user. Inspect optional outcome actions here; only execute_business_logic runs them.
        """
        if rule:
            row=runtime.resolve(factory,caller(),rule,version)
            return {**runtime.snapshot(row),'capability':runtime.capability(row)}
        return read(factory, caller(), logic_id, version) if logic_id else catalog(factory, caller(), query)

    @mcp.tool(name='save_business_logic')
    def save_business_logic(definition: dict, logic_id: str | None = None, version: int = 0):
        """Save a useful, user-requested decision, not speculative rules inferred from a topic.
        definition={name,topic,tree,key?,description?,inputs?:{field:{type,description}},
        outcomes?:{label:meaning},source_sql?,examples:[{name,context,expected?}]}.
        key is a stable name such as hvac.service_triage. description says when to use it.
        Saving creates a draft revision; publish_business_logic makes an exact saved revision discoverable.
        source_sql is an optional SELECT that supplies test rows. Compose in SQL with
        rvbbit.decision_tree(to_jsonb(source_row), tree_jsonb, 4); inspect status and label.
        Tree uses existing decision_tree JSON: leaf {kind:'leaf',label}; binary
        {kind:'branch',question,yes:node,no:node,threshold?:0.5}; multiway
        {kind:'choice',question,choices:{key:description},routes:{key:node}}.
        Branch/choice nodes can include inputs:['field_name',...] to read only those exact
        top-level context keys. Missing keys stop the visited path with missing_input;
        null is a present value. Without inputs the whole context is used (legacy behavior).
        Up to 4 questions along a path, 63 nodes, 2..8 choices.
        Leaves may include action:{kind:'sql',label,sql,external_effects?:'may_write'|'read_only'}
        or action:{kind:'callie',label,instruction}. SQL is one existing app-action statement;
        $1::jsonb contains {input:original_context,decision:recorded_result}. Use RETURNING for output.
        Actions run only with execute_business_logic, never SQL evaluation or test_business_logic.
        Build the bounded context object outside the tree using normal authorized SQL/data access.
        For edits, read first and pass the current version. Examples are explicit test inputs, not policy facts.
        """
        return save(factory, caller(), {'id': logic_id, 'version': version, 'definition': definition})

    @mcp.tool(name='test_business_logic')
    def test_business_logic(context: dict, logic_id: str | None = None, version: int | None = None, tree: dict | None = None):
        """Evaluate a saved decision or unsaved tree against a JSON context object (24 KB maximum).
        Returns label, status and the actual selected path. Non-complete means no answer; never treat
        failure as a negative branch. Scores are model scores, not calibrated confidence. No actions run.
        """
        body = {'context': context}
        if logic_id:
            body['id'] = logic_id
        if tree is not None:
            body['tree'] = tree
        if version is not None:
            body['version'] = version
        return evaluate(factory, caller(), body)

    @mcp.tool(name='publish_business_logic')
    def publish_business_logic(logic_id: str, version: int | None = None):
        """Make an owner's saved revision available to Callie in capability_search and named SQL.
        Supply its exact version; null removes availability. Later saves do not change the active version.
        Use only for rules the user wants available, not speculative business policy.
        """
        return invoke('publish_business_logic',{'logic_id':logic_id,'version':version},lambda:runtime.publish(factory,caller(),logic_id,version))

    @mcp.tool(name='run_business_logic')
    def run_business_logic(rule: str, context: dict, version: int | None = None):
        """Evaluate a published business rule against supplied JSON evidence; returns decision and frozen receipt.
        Discover purpose, input fields and outcomes with capability_search, or read_business_logic(rule=...).
        Pin the discovered version. SQL equivalent: rvbbit.business_logic(rule,to_jsonb(row),version).
        Check decision.status before using label. Non-complete means no answer; missing input is not false.
        No actions run. SQL/data access assembles context before evaluation. Scores are not calibrated confidence.
        """
        return invoke('run_business_logic',{'rule':rule,'context':context,'version':version},lambda:runtime.run(factory,caller(),rule,context,version))

    @mcp.tool(name='read_business_logic_receipt')
    def read_business_logic_receipt(run_id: str):
        """Read a frozen business-logic run: original inputs, exact definitions, outcomes and selected paths.
        Does not re-run inference. Large SQL runs retain counts and explicitly bounded row details.
        """
        return runtime.read_receipt(factory,caller(),run_id)

    @mcp.tool(name='execute_business_logic')
    def execute_business_logic(rule: str, context: dict, request_key: str, version: int | None = None):
        """Evaluate a published rule and execute the reached outcome's optional SQL or Callie action.
        Read the rule first to inspect its action. request_key is a UUID: reuse it for retries of the
        same operation. A fresh key deliberately starts another operation. Inputs and decisions are
        recorded with the action result. Only complete decisions execute actions. SQL previews and
        run_business_logic remain evaluation-only. Callie actions return a queued conversation link;
        that does not mean Callie has finished the requested work. Inspect receipt.status and action.
        """
        if execute is None:
            raise RuntimeError('Business logic actions are unavailable')
        args = {'rule':rule, 'context':context, 'request_key':request_key, 'version':version}
        return invoke('execute_business_logic',args,lambda:execute(caller(),args))


def tree_sql(tree, depth=0):
    """Readable SQL constructors, never interpolated user code or JSON bird nests."""
    from psycopg import sql
    lit = lambda value: sql.Literal(value).as_string()
    pad = '  ' * depth
    if tree['kind'] == 'leaf':
        return f"rvbbit.leaf({lit(tree['label'])})"
    if tree['kind'] == 'branch':
        expression = (f"rvbbit.branch(\n{pad}  {lit(tree['question'])},\n"
                      f"{pad}  yes => {tree_sql(tree['yes'], depth+1)},\n"
                      f"{pad}  no => {tree_sql(tree['no'], depth+1)},\n"
                      f"{pad}  threshold => {lit(tree['threshold'])}\n{pad})")
    else:
        choices = (',\n'+pad+'    ').join(lit(k)+', '+lit(v) for k,v in tree['choices'].items())
        routes = (',\n'+pad+'    ').join(lit(k)+', '+tree_sql(v,depth+2) for k,v in tree['routes'].items())
        expression = (f"rvbbit.choice_branch(\n{pad}  {lit(tree['question'])},\n"
                      f"{pad}  choices => jsonb_build_object(\n{pad}    {choices}\n{pad}  ),\n"
                      f"{pad}  routes => jsonb_build_object(\n{pad}    {routes}\n{pad}  )\n{pad})")
    if 'inputs' in tree:
        expression = '('+expression+' || '+lit(json.dumps({'inputs':tree['inputs']}))+'::jsonb)'
    return expression


def source_sql_text(value):
    """Keep source SQL verbatim, removing only its final statement separator."""
    import sqlglot
    from sqlglot.tokens import TokenType
    source = bounded_text(value, 'Source SQL', 16000)
    try:
        tokens = sqlglot.Tokenizer(dialect='postgres').tokenize(source)
    except sqlglot.errors.TokenError:
        raise ValueError('Source must be one read-only SELECT query') from None
    if tokens and tokens[-1].token_type == TokenType.SEMICOLON:
        last = tokens[-1]
        source = source[:last.start] + source[last.end + 1:]
    return source.rstrip()


def inline_query(tree, source_clause):
    """Export the current definition with the same constructors as the runner."""
    _, pure = actions.trees(tree)
    return ('SELECT rvbbit.decision_tree(\n'
            '  to_jsonb(r),\n'
            f'  {tree_sql(pure, 1)},\n'
            f'  4\n) AS decision\n{source_clause}')


def compile_query(body, named=None, max_rows=100_000):
    """Compose the normal scalar SQL operator over a bounded source relation."""
    from psycopg import sql
    import sqlglot
    from sqlglot import exp
    if not isinstance(body, dict) or set(body) - {'tree', 'source_sql', 'limit', 'run'}:
        raise ValueError('Use tree, source_sql, limit and run')
    _, tree = actions.trees(body.get('tree'))
    source = source_sql_text(body.get('source_sql'))
    limit = body.get('limit', 12)
    if type(limit) is not int or not 1 <= limit <= max_rows:
        raise ValueError(f'Choose 1..{max_rows:,} source rows (the SQL row limit)')
    if type(body.get('run', False)) is not bool:
        raise ValueError('Run must be true or false')
    try:
        statements = sqlglot.parse(source, read='postgres')
    except sqlglot.errors.ParseError:
        raise ValueError('Source must be one read-only SELECT query') from None
    if len(statements) != 1 or not isinstance(statements[0], exp.Query) or any(
            isinstance(n, (exp.DML, exp.DDL, exp.Into, exp.Command)) for n in statements[0].walk()):
        raise ValueError('Source must be one read-only SELECT query')
    query = sql.SQL('''WITH source_rows AS MATERIALIZED (
  SELECT * FROM (
{source}
  ) AS source LIMIT {limit}
),
{definition}
evaluated AS MATERIALIZED (
  SELECT row_number() OVER () AS row_number,
         to_jsonb(source_rows) AS input,
         {call} AS decision
  FROM source_rows {join}
)
SELECT row_number, input,
       decision->>'label' AS outcome,
       decision->>'status' AS status,
       decision
FROM evaluated
ORDER BY row_number''').format(source=sql.SQL(source), limit=sql.Literal(limit),
                               definition=sql.SQL('') if named else sql.SQL('decision_definition AS (SELECT {} AS tree),').format(sql.SQL(tree_sql(tree,1))),
                               call=sql.SQL('rvbbit.business_logic({},to_jsonb(source_rows),{})').format(sql.Literal(named[0]),sql.Literal(named[1])) if named else sql.SQL('rvbbit.decision_tree(to_jsonb(source_rows), decision_definition.tree, 4)'),
                               join=sql.SQL('') if named else sql.SQL('CROSS JOIN decision_definition')).as_string()
    return query, limit


def query_rows(server, actor, body):
    """Use Calliope's governed read-only SQL runner, with the browser actor's role.

    PostgreSQL constructs each row's context and executes decision_tree. Python
    never loops over rows to invoke the model or accepts a privileged query path.
    """
    identity(actor)
    named = None
    if 'rule' in body:
        if set(body)-{'rule','version','source_sql','limit','run'}:
            raise ValueError('Use a published rule or an inline tree, not both')
        row=runtime.resolve(server._conn,actor,body['rule'],body.get('version'))
        named=(row['rule_key'],row['version'])
        body={k:v for k,v in body.items() if k not in {'rule','version'}} | {'tree':row['definition']['tree']}
    query, limit = compile_query(body, named, max_rows=sql_row_limit(server))
    # The examples are complete SELECTs over the same source, with no placeholder
    # relation or separate CTE setup. Share this clause with the named UI export.
    source = source_sql_text(body['source_sql'])
    source_clause = f'FROM (\n{source}\n) AS r\nLIMIT {limit};'
    exported = {'sql': query, 'inline_sql': inline_query(body['tree'], source_clause),
                'source_clause_sql': source_clause, 'limit': limit}
    if not body.get('run'):
        return exported
    if server is None:
        raise RuntimeError('SQL runner unavailable')
    if not ui_decisions.SLOTS.acquire(blocking=False):
        raise RuntimeError('Decision service is busy; try again')
    token = server._SESSION_SUB.set(actor)
    try:
        # A manually requested batch may include cold inference for many rows.
        # Use the SQL runner's existing maximum timeout and normal row cap.
        result = server.tool_run_sql(query, limit=limit, timeout_ms=600000, _decision_usage=True)
        server._record('business_logic.sql', {'sql': query, 'limit': limit}, result,
                       result.get('error'), result.get('elapsed_ms', 0), caller_override=actor)
        if result.get('error'):
            raise ValueError(result['error'].get('message') or 'Query could not be evaluated')
        if result.get('rows'):
            decisions = [(dict(zip((c['name'] for c in result['columns']), row)) if isinstance(row, (list, tuple)) else row)['decision'] for row in result['rows']]
            result['usage'] = usage.record(server._conn, actor, decisions)
        return {**exported, 'result': result}
    finally:
        server._SESSION_SUB.reset(token)
        ui_decisions.SLOTS.release()


def sql_row_limit(server):
    """Share the governed SQL runner's row budget, not a workbench-only cap."""
    return max(1, min(int(getattr(server, 'ROW_CAP', 1000)), 100_000))
