"""Published native captures share the inert preview path, never a service runner."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import server
import artifact_app_preview
import ui_app_captures


@pytest.mark.parametrize('query_fails', [False, True])
def test_published_native_capture_uses_authorized_reads_and_retains_revision(tmp_path, monkeypatch, query_fails):
    document = {'schema': 'calliope.app.v1', 'title': 'Capture test',
        'data': {'state': {}, 'label': 'Waiting'},
        'components': [{'id': 'root', 'component': 'CText', 'text': {'path': '/label'}}],
        'queries': {'label': {'sql': 'SELECT 42 AS value', 'path': '/label', 'shape': 'value'}}}
    monkeypatch.setattr(server, '_load_live_app_version', lambda slug: (
        {'runtime_kind': 'a2ui'}, {'version': 7, 'source_files': {'app.json': json.dumps(document)}}))
    monkeypatch.setattr(server, '_active_calliope_design_profile', lambda: None)
    monkeypatch.setattr(server, '_require_application_subject', lambda: SimpleNamespace(subject='tech@example.test'))
    monkeypatch.setenv('WAREHOUSE_LIVE_APP_CAPTURE_DIR', str(tmp_path/'managed'))
    def no_runner(*args, **kwargs):
        pytest.fail('Native captures must never start/probe a Python service')
    monkeypatch.setattr(server, '_live_app_runner_status', no_runner)
    monkeypatch.setattr(server, '_execute_app_action_request', no_runner)
    def query(statement, **kwargs):
        assert server._SESSION_SUB.get() == 'tech@example.test'
        return {'error': {'code': 'OFFLINE'}} if query_fails else {'rows': [{'value': 42}], 'row_count': 1}
    monkeypatch.setattr(server, 'tool_run_sql', query)
    def theme(conn, subject):
        assert subject == 'tech@example.test'
        return {'theme': 'fixture'}
    monkeypatch.setattr(artifact_app_preview, 'theme_for', theme)
    def render(snapshot, output, size, theme, launch, *, full_page):
        assert full_page and size['width'] == 600 and size['height'] == 800
        assert theme == {'theme': 'fixture'} and snapshot['receipt'] is None
        assert snapshot['preview']['actions'] == 'simulated'
        if not query_fails:
            assert snapshot['messages'][2]['updateDataModel']['value']['label'] == 42
        output.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        return {'ready': snapshot['preview']['ready'], 'preview': snapshot['preview'], 'renderer': 'Web'}
    monkeypatch.setattr(ui_app_captures, 'web', render)
    export = tmp_path/'export.png'
    result = server.tool_capture_live_app('native-example', path=str(export), width=600, height=800)
    assert result['version'] == result['verification']['version'] == 7
    assert result['source'] == 'stored-a2ui' and result['runtime_kind'] == 'a2ui'
    assert result['verification']['healthy'] is (not query_fails)
    assert Path(result['path']) != export and Path(result['path']).read_bytes() == export.read_bytes()
    assert result['preview']['mode'] == 'server-read'
