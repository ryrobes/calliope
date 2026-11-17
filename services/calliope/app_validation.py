"""Private replay fixtures and revision checks, using the existing inert renderers."""
import copy
import json
import uuid
import shutil
import time
from pathlib import Path

from psycopg.types.json import Jsonb

import app_revisions
import ui_apps
import ui_app_preview as preview
import ui_composition


def load_version(server, actor, slug, version=None):
    server._artifact_require_view(slug, caller_override=actor)
    app, row = server._load_live_app_version(slug, version)
    if not app or not row:
        raise ValueError('App version unavailable')
    return app, row


def replay_model(document, context):
    """Restore only observed bindings. Never query as or impersonate a reporter."""
    model = copy.deepcopy(document['data'])
    restored, omitted = [], []

    def put(path, value):
        try:
            ui_apps.pointer(model, path)
            ui_apps.put(model, path, value)
            restored.append(path)
        except (ValueError, KeyError, IndexError, TypeError):
            omitted.append(str(path))

    navigation = ui_apps.navigation(document).get('paths', [])
    try:
        location = json.loads(context.get('location', ''))
        if isinstance(location, list):
            for entry in location:
                if isinstance(entry, list) and len(entry) == 2 and entry[0] in navigation:
                    put(entry[0], entry[1])
    except (ValueError, TypeError):
        pass
    import ui_records
    for link in context.get('record_links', [])[:1]:
        try:
            ref = ui_records.reference(link['reference'], context['slug'])
            ui_records.open_record(document, model, ref)
            restored.extend(document['records'][ref['record']]['open'])
        except (ValueError, KeyError, TypeError):
            omitted.append('record navigation')
    for record in context.get('records', []):
        if not isinstance(record, dict):
            omitted.append('invalid record observation')
            continue
        for form in document.get('forms', []):
            if record.get('form') == form['path']:
                key = '/' + form.get('key', 'id')
                put(form['path'] + key, record.get('id'))
                put(form['source'] + key, record.get('id'))
    nodes = {n['id']: n for n in ui_composition.expand(document, model)}
    for observed in context.get('visible', []):
        if not isinstance(observed, dict) or not isinstance(observed.get('content', {}), dict):
            omitted.append('invalid visible observation')
            continue
        node = nodes.get(observed.get('id'), {})
        for prop, value in (observed.get('content') or {}).items():
            binding = node.get(prop)
            if isinstance(binding, dict) and set(binding) == {'path'}:
                put(binding['path'], value)
        if not node and observed.get('id'):
            omitted.append('component:' + observed['id'])
    checks = []
    for field in context.get('fields', []):
        if not isinstance(field, dict):
            omitted.append('invalid field observation')
            continue
        path = field.get('path')
        node = nodes.get(field.get('id'), {})
        if not path or node.get('value') != {'path': path}:
            omitted.append('field:' + str(field.get('id')))
            continue
        form = next((f for f in document.get('forms', []) if path.startswith(f['path'] + '/')), None)
        if form and field.get('saved_available'):
            put(form['source'] + path[len(form['path']):], field.get('saved_value'))
        put(path, field.get('value'))
        checks.append({'path': path, 'equals': field.get('value')})
        checks.append({'component': field['id'], 'property': 'value', 'equals': field.get('value')})
    provenance = {'kind': 'captured-observations', 'complete': False,
        'notice': 'Partial replay: captured visible values and unsent edits over definition defaults. '
                  'Uncaptured data is not historical evidence. No SQL or business actions run.',
        'restored_paths': sorted(set(restored)), 'unmapped': sorted(set(omitted)),
        'context_id': context.get('context_id'), 'fetched_at': context.get('fetched_at')}
    return model, checks, provenance


def request_replay(server, actor, slug, ident, version=None):
    result = app_revisions.requests(server._conn, actor, slug, ident)
    if not result['requests']:
        raise ValueError('Shared request unavailable')
    context = result['requests'][0]['origin_context']
    _, source = load_version(server, actor, slug, context['version'])
    if source['kind'] != 'a2ui':
        raise ValueError('HTML feedback has context evidence, not a native replay. Use check_app_revision for an isolated HTML capture.')
    original = ui_apps.definition(source['source_files'])
    data, checks, provenance = replay_model(original, context)
    _, row = load_version(server, actor, slug, version or context['version'])
    document = ui_apps.definition(row['source_files'])
    data, removed = overlay(document['data'], data)
    provenance['unmapped'].extend(removed)
    snapshot = preview.snapshot(document, data=data, state=data['state'])
    snapshot['replay'] = provenance
    return document, snapshot, checks, provenance


def overlay(defaults, captured, path=''):
    """Additive fields get explicit defaults; removed fields are reported."""
    if not isinstance(defaults, dict) or not isinstance(captured, dict):
        return copy.deepcopy(captured), []
    result, removed = copy.deepcopy(defaults), []
    for key, value in captured.items():
        pointer = path + '/' + key.replace('~', '~0').replace('/', '~1')
        if key in defaults:
            result[key], missing = overlay(defaults[key], value, pointer)
            removed.extend(missing)
        else:
            removed.append(pointer)
    return result, removed


def validate_checks(checks):
    if not isinstance(checks, list) or len(checks) > 100:
        raise ValueError('Use up to 100 checks')
    for check in checks:
        if not isinstance(check, dict) or set(check) - {'path', 'component', 'property', 'equals', 'event', 'context'}:
            raise ValueError('Checks use path/equals, component/property/equals, or event/context/equals')
        if sum(k in check for k in ('path','component','event')) != 1 or 'equals' not in check:
            raise ValueError('Each check needs one path, component or event and an expected equals value')
    return checks


def evaluate(document, data, checks):
    snapshot = preview.snapshot(document, data=data, state=data['state'])
    model = snapshot['messages'][2]['updateDataModel']['value']
    nodes = {n['id']: n for n in snapshot['messages'][1]['updateComponents']['components']}
    results = []
    for check in validate_checks(checks):
        try:
            if 'path' in check:
                actual = ui_apps.pointer(model, check['path'])
            elif 'component' in check:
                actual = ui_apps.resolve(nodes[check['component']][check.get('property','text')], model)
            else:
                event = preview.snapshot(document, data=model, state=model['state'],
                    event=check['event'], context=check.get('context'))
                actual = event['preview_event'].get('inputs')
            results.append({'check': check, 'passed': actual == check['equals'], 'actual': actual})
        except (ValueError, KeyError, TypeError) as exc:
            results.append({'check': check, 'passed': False, 'error': str(exc)[:300]})
    return snapshot, results


def save_scenario(server, actor, slug, name, request_id=None, version=None, data=None, checks=None):
    app_revisions.ensure(server._conn)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        raise ValueError('Scenario name needs 1..120 characters')
    if request_id:
        document, snapshot, defaults, provenance = request_replay(server, actor, slug, request_id, version)
        context = app_revisions.requests(server._conn, actor, slug, request_id)['requests'][0]['origin_context']
        version = version or context['version']
        captured_data = snapshot['messages'][2]['updateDataModel']['value']
        if data is None:
            data = captured_data
    else:
        _, row = load_version(server, actor, slug, version)
        version = row['version']
        document = ui_apps.definition(row['source_files'])
        defaults, provenance, captured_data = [], {}, None
        if data is None:
            raise ValueError('Provide fixture data or a shared request ID')
    if provenance.get('kind') != 'captured-observations' or data != captured_data:
        provenance = {'kind':'supplied-fixture', 'complete':True,
            'notice':'Author-supplied fixture. Not an authenticated run as another user.'}
    if len(json.dumps(data, allow_nan=False).encode()) > 500000:
        raise ValueError('Fixture exceeds 500 KB')
    preview.snapshot(document, data=data, state=data['state'])
    checks = validate_checks(defaults if checks is None else checks)
    ident = str(uuid.uuid4())
    with server._conn() as conn:
        conn.execute('INSERT INTO rvbbit.app_preview_scenarios '
            '(id,owner_email,slug,name,request_id,version,definition_sha256,data,checks,provenance) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (ident,actor,slug,name.strip(),request_id,version,preview.digest(document),Jsonb(data),Jsonb(checks),Jsonb(provenance)))
    return {'id':ident, 'name':name.strip(), 'version':version, 'provenance':provenance}


def scenario(server, actor, slug, ident):
    app_revisions.ensure(server._conn)
    load_version(server, actor, slug)
    with server._conn() as conn:
        row = conn.execute('SELECT * FROM rvbbit.app_preview_scenarios WHERE id=%s AND slug=%s '
            'AND lower(owner_email)=lower(%s)', (app_revisions.request_id(ident),slug,actor)).fetchone()
    if not row:
        raise ValueError('Private scenario unavailable')
    if row['request_id'] and not app_revisions.requests(server._conn,actor,slug,str(row['request_id']))['requests']:
        raise ValueError('Shared request unavailable')
    return dict(row)


def check_revision(server, actor, slug, version=None, scenario_id=None, document=None, html_source=None, captures=True):
    """One exact source + one frozen snapshot; evidence never submits app actions."""
    import artifact_app_preview
    import ui_app_captures
    import html_app_capture
    app_revisions.ensure(server._conn)
    app, row = load_version(server, actor, slug, version)
    if document is not None and html_source is not None:
        raise ValueError('Provide a native document or HTML, not both')
    fixture = scenario(server, actor, slug, scenario_id) if scenario_id else None
    ident = str(uuid.uuid4())
    root = server._live_app_capture_root()/'checks'
    # Durable receipts stay in Postgres; old image caches can be regenerated.
    for old in root.glob('*'):
        if old.is_dir() and time.time()-old.stat().st_mtime > 7*86400:
            shutil.rmtree(old,ignore_errors=True)
    directory = root/ident
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = {'id':ident,'slug':slug,'version':row['version'], 'scenario_id':scenario_id,
        'candidate':document is not None or html_source is not None, 'actions':'simulated',
        'device_acceptance':False, 'captures_requested':captures,
        'checks':[], 'captures':{}, 'image_retention_days':7}
    try:
        if row['kind'] == 'a2ui':
            if html_source is not None:
                raise ValueError('This app uses a native definition')
            document = ui_apps.definition({'app.json':json.dumps(document)}) if document is not None else ui_apps.definition(row['source_files'])
            if fixture:
                data, removed = overlay(document['data'], fixture['data'])
                snapshot, checks = evaluate(document, data, fixture['checks'])
                result.update(provenance=fixture['provenance'], checks=checks, unmapped=removed)
            else:
                snapshot = preview.request_snapshot(server, document, {'mode':'server-read'}, actor)
                result['provenance'] = {'kind':'current-authorized-data','notice':'Current reads as this caller; not historical or requester data.'}
            result['preview'] = snapshot['preview']
            result['definition_sha256'] = preview.digest(document)
            result['ready'] = snapshot['preview']['ready'] and all(c['passed'] for c in result['checks']) and not result.get('unmapped')
            if captures:
                evidence = ui_app_captures.matrix(document,snapshot,directory,
                    theme=artifact_app_preview.theme_for(server._conn,actor),launch=server._launch_playwright_chromium)
                result['captures'] = evidence['captures']
                result['ready'] = result['ready'] and evidence['ready']
        else:
            if document is not None or fixture:
                raise ValueError('Native fixtures cannot be applied to HTML. Use authored HTML and read-only capture data.')
            html_text = html_source if html_source is not None else row['html']
            if not isinstance(html_text, str) or len(html_text.encode()) > 4_000_000:
                raise ValueError('HTML source must be text under 4 MB')
            result.update(definition_sha256=preview.digest(html_text),
                provenance={'kind':'current-authorized-data','notice':'Isolated HTML; current caller-bound app reads; actions disabled.'})
            if not captures:
                raise ValueError('HTML validation requires captures')
            token = server._SESSION_SUB.set(actor)
            try:
                for name in ('phone','desktop'):
                    size = preview.viewport(name)
                    evidence = server._capture_html_with_playwright(
                        server._capturable_dashboard_html(slug, {**row,'html':html_text}), directory/f'web-{name}.png',
                        size['width'],size['height'],False,800,
                        app_read_runner=lambda target, body: html_app_capture.app_read(server,actor,target,body))
                    evidence['ready'] = bool((evidence.get('readiness') or {}).get('ready')) and not (
                        (evidence.get('readiness') or {}).get('failed') or any(evidence.get(k) for k in ('page_errors','runtime_errors','render_issues')))
                    result['captures']['web-'+name] = evidence
            finally:
                server._SESSION_SUB.reset(token)
            result['ready'] = all(c['ready'] for c in result['captures'].values())
    except Exception as exc:
        result.update(ready=False, error=str(exc)[:1500])
    load_version(server, actor, slug, row['version'])
    if fixture:
        scenario(server,actor,slug,scenario_id)
    for variant, capture in result['captures'].items():
        path = directory/(variant+'.png')
        if path.is_file():
            path.chmod(0o600)
            capture['url'] = f'/api/app-improvements/checks/{ident}/{variant}.png'
    with server._conn() as conn:
        conn.execute('INSERT INTO rvbbit.app_preview_checks(id,owner_email,slug,scenario_id,result) VALUES(%s,%s,%s,%s,%s)',
            (ident,actor,slug,scenario_id,Jsonb(result)))
    return result
