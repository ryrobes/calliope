import asyncio
from types import SimpleNamespace
import pytest
import thread_export as export

SID = '55555555-5555-4555-8555-555555555555'
TID = '66666666-6666-4666-8666-666666666666'


def notebook():
    return {'session': {'id': SID, 'title': 'September / enrollment', 'owner_email': 'ryan@example.com',
                        'created_at': '2026-09-01T12:00:00Z'},
            'presentation': {'title': 'September / enrollment', 'summary': 'Comparing regions.'},
            'turns': [{'id': TID, 'ordinal': 1, 'status': 'complete', 'turn_kind': 'chat',
                       'created_at': '2026-09-01T08:00:00-04:00', 'completed_at': '2026-09-01T12:04:00Z',
                       'user_message': 'Compare **regions**.', 'assistant_message': 'Original answer with all qualifications.',
                       'response_receipt': {'tools': [{'name': 'run_sql', 'count': 2}],
                                            'presentation': {'script': 'A shortened TTS rewrite'}}}],
            'surfaces': [
                {'id': 'image', 'turn_id': TID, 'kind': 'image', 'title': 'Second design',
                 'created_at': '2026-09-01T12:03:00Z', 'payload': {'image_url': '/api/calliope/surfaces/image/image'}},
                {'id': 'query', 'turn_id': TID, 'kind': 'query', 'title': 'Enrollment result',
                 'created_at': '2026-09-01T12:02:00Z', 'source': {'sql': 'SELECT region, count(*) FROM enrollments GROUP BY region'},
                 'payload': {'columns': ['region', 'count'], 'rows': [['East|West\n<script>', 24], [None, 0]], 'truncated': True}}]}


def test_chronological_export_original_voice_stage_rows_and_offsets():
    data = notebook()
    progress = [{'turn_id': TID, 'created_at': '2026-09-01T12:01:00Z', 'text': 'Checking definitions.'},
                {'turn_id': TID, 'created_at': '2026-09-01T12:01:02Z', 'text': 'Checking definitions.'}]
    text, name = export.render(data, 'https://pilot.test', progress=progress)
    positions = [text.index(value) for value in ['Compare **regions**.', 'Checking definitions.',
                 'Stage: Enrollment result', 'Stage: Second design', 'Original answer with all qualifications.', '— Receipt']]
    assert positions == sorted(positions)
    assert text.count('Checking definitions.') == 1
    assert '2026-09-01T12:00:00.000Z' in text
    assert 'shortened TTS rewrite' not in text
    assert '```sql\nSELECT region' in text
    assert '| East\\|West<br>&lt;script&gt; | 24 |' in text
    assert '| NULL | 0 |' in text
    assert '_Saved preview: 2 rows.' in text
    assert 'https://pilot.test/api/calliope/surfaces/image/image' in text
    assert name == 'september-enrollment-55555555.md'


def test_all_revisions_live_specs_documents_and_unknown_dates():
    data = notebook()
    data['surfaces'] += [
        {'id': 'old', 'kind': 'artifact', 'title': 'Old dashboard', 'artifact_slug': 'my dashboard', 'artifact_version': 1,
         'created_at': '2026-09-01T12:03:01Z', 'payload': {}},
        {'id': 'new', 'kind': 'artifact', 'title': 'New dashboard', 'artifact_slug': 'my dashboard', 'artifact_version': 2,
         'created_at': '2026-09-01T12:03:02Z', 'payload': {}},
        {'id': 'chart', 'kind': 'chart', 'title': 'Live chart', 'payload': {'spec': {'mark': 'bar'}, 'queries': {'source': 'select 1'}, 'runtime': {'scripts': ['internal']}}},
        {'id': 'doc', 'kind': 'document', 'title': 'Notes', 'payload': {'download_url': '/api/calliope/files/doc'}}]
    data['page'] = {'title': 'Project', 'revision': {'version': 2, 'body': 'Living page body', 'created_at': '2026-09-01T12:03:30Z'}}
    text, _ = export.render(data, 'https://pilot.test', documents={'doc': {'text': '# Long-form notes', 'language': 'markdown'}})
    assert 'Old dashboard' in text and 'New dashboard' in text
    assert 'version 1' in text and 'version 2' in text
    assert 'https://pilot.test/apps/my%20dashboard' in text
    assert 'no queries were rerun' in text and '"mark": "bar"' in text
    assert '"runtime"' not in text
    assert '# Long-form notes' in text and 'Living page body' in text
    assert 'Timestamp unavailable — Stage: Live chart' in text


def test_text_file_scope_symlinks_and_explicit_truncation(tmp_path, monkeypatch):
    files = tmp_path / 'files'; files.mkdir()
    (files/'notes.md').write_text('A saved document.')
    secret = tmp_path / 'outside.txt'; secret.write_text('SHOULD NEVER BE READ')
    (files/'escape.txt').symlink_to(secret)
    (files/'big.txt').write_text('x' * 80)
    rows = [{'id': name, 'payload': {'original_name': name, 'storage_path': str(files/name)}}
            for name in ['notes.md', 'escape.txt', 'big.txt']]
    calls = []
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params):
            calls.append((sql, params)); return SimpleNamespace(fetchall=lambda: rows)
    monkeypatch.setattr(export, 'FILE_LIMIT', 40)
    result = export.text_documents(Conn, SID, [{'id': r['id'], 'kind': 'document'} for r in rows], tmp_path)
    assert result['notes.md']['text'] == 'A saved document.'
    assert 'text' not in result['escape.txt']
    assert len(result['big.txt']['text']) == 40 and 'shortened' in result['big.txt']['note']
    assert calls[0][1][0] == SID
    assert 'session_id=%s::uuid' in calls[0][0]


def test_markdown_delimiters_and_unsafe_links():
    assert export.fence('```\npretend fence', 'text').startswith('````text\n')
    assert export.link('Bad', 'javascript:alert(1)', 'https://pilot.test') == ''
    assert '%3E' in export.link('image', '/image>evil', 'https://pilot.test')
    assert export.timestamp('bad date') is None


def test_download_route_reuses_chat_permissions_and_private_stage_checks(monkeypatch, tmp_path):
    import calliope
    from starlette.requests import Request
    routes = {}
    data = notebook()
    private_id = '77777777-7777-4777-8777-777777777777'
    data['surfaces'].append({'id': 'private-book', 'kind': 'playbook', 'title': 'PRIVATE PLAYBOOK',
        'payload': {'playbook': {'id': private_id, 'version': 1, 'body': 'PRIVATE CONTENT'}}})
    class MCP:
        @staticmethod
        def custom_route(path, methods):
            def register(fn): routes[path] = fn; return fn
            return register
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def transaction(self): return self
        def execute(self, sql, params=()):
            if 'SELECT t.*' in sql:
                rows = data['turns']
            elif 'SELECT * FROM rvbbit.calliope_surfaces' in sql:
                rows = data['surfaces']
            elif "e.event_type='calliope.progress'" in sql:
                rows = [{'turn_id': TID, 'sequence': 1, 'created_at': '2026-09-01T12:01:00Z', 'text': 'Visible progress'}]
            else:
                rows = []
            return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: None)
    monkeypatch.setenv('WAREHOUSE_HERMES_URL', 'http://hermes:8642')
    monkeypatch.setenv('WAREHOUSE_HERMES_API_KEY', 'test-key')
    monkeypatch.setenv('WAREHOUSE_CALLIOPE_DIR', str(tmp_path))
    monkeypatch.setenv('WAREHOUSE_PUBLIC_URL', 'https://public.pilot.test')
    for fn in ['ensure_tables', '_start_session_synopsis_worker']:
        monkeypatch.setattr(calliope, fn, lambda *args: None)
    for module in [calliope.hermes_docs, calliope.hermes_config, calliope.provider_settings]:
        monkeypatch.setattr(module, 'ensure', lambda *args: None)
    monkeypatch.setattr(calliope, '_canonical_owner', lambda request: (request.headers.get('x-test-owner'), {'mapped': True}))
    access = []
    def require_view(conn, session_id, owner):
        access.append((session_id, owner))
        if owner not in ['owner', 'shared-reader']:
            raise calliope.calliope_access.CalliopeAccessError('NOT_FOUND', 'Session not found', 404)
        return {**data['session'], 'access_role': 'owner' if owner == 'owner' else 'viewer'}
    monkeypatch.setattr(calliope.calliope_access, 'require_view', require_view)
    monkeypatch.setattr(calliope.calliope_access, '_audience', lambda *args: {})
    monkeypatch.setattr(calliope, '_specialist_thread_rows', lambda *args: [])
    monkeypatch.setattr(calliope.calliope_pages, 'page_for_session', lambda *args: None)
    monkeypatch.setattr(calliope.floor_presentations, 'read_thread', lambda *args: data['presentation'])
    def should_not_run(*args, **kwargs):
        raise AssertionError('An export must not run a refresh or reconcile')
    for fn in ['_reconcile_session_files', '_refresh_local_sql_surfaces', '_session_agent_work']:
        monkeypatch.setattr(calliope, fn, should_not_run)
    def private_playbook(*args):
        raise calliope.playbook_access.PlaybookError('NOT_FOUND', 'Private', 404)
    monkeypatch.setattr(calliope.playbook_access, 'read', private_playbook)
    calliope.register_calliope_routes(MCP(), Conn, '', lambda doc: doc)
    fn = routes['/api/calliope/sessions/{session_id}/export.md']
    def request(owner):
        return Request({'type': 'http', 'method': 'GET', 'scheme': 'http', 'server': ('internal', 8765),
                        'path': '/api/calliope/sessions/' + SID + '/export.md', 'root_path': '', 'query_string': b'',
                        'headers': [(b'x-test-owner', owner.encode())] if owner else [], 'path_params': {'session_id': SID}})
    for owner in ['owner', 'shared-reader']:
        response = asyncio.run(fn(request(owner)))
        assert response.status_code == 200
        body = response.body.decode()
        assert 'Original answer' in body and 'Visible progress' in body
        assert 'PRIVATE CONTENT' not in body and 'PRIVATE PLAYBOOK' not in body
        assert 'https://public.pilot.test/calliope?' in body and 'http://internal' not in body
        assert response.headers['content-disposition'].endswith('.md"')
        assert response.headers['content-type'].startswith('text/markdown')
        assert response.headers['cache-control'] == 'private, no-store'
    assert asyncio.run(fn(request(''))).status_code == 401
    assert asyncio.run(fn(request('stranger'))).status_code == 404
    assert len(access) == 3
