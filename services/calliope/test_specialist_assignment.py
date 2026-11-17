"""Scope handoffs and receipts must not turn activity or failures into progress."""
import json
from copy import deepcopy
import pytest
import specialist_assignment as work


def test_assignment_repairs_typed_arrays_without_touching_other_context():
    expected = {key: ['Use the supplied evidence'] for key in work.SECTIONS}
    context = {'assignment': {key: {'item': {'item': value}} for key, value in expected.items()},
               'provided': {'item': ['literal user data']}}
    before = deepcopy(context)
    assert work.assignment_from_context(context) == expected
    assert context == before


def test_assignment_error_is_actionable_and_schema_is_exposed():
    import server
    context = {'assignment': {key: [] for key in work.SECTIONS}}
    context['assignment']['deliverables'] = {'item': 3}
    with pytest.raises(work.AssignmentValidationError) as caught:
        work.assignment_from_context(context)
    error = server._tool_error_envelope(caught.value)
    assert error['code'] == 'INVALID_ARGUMENT'
    assert 'context.assignment.deliverables' in error['message']
    assert '["Use the supplied query"]' in error['message']
    from mcp.server.fastmcp import FastMCP, Context
    mcp = FastMCP('schema-test')
    for name in ('delegate_calliope_specialist', 'continue_calliope_specialist'):
        function = getattr(server, '_mcp_' + name)
        function.__annotations__['ctx'] = Context
        mcp.tool(name=name)(function)
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    for name in ('delegate_calliope_specialist', 'continue_calliope_specialist'):
        schema = tools[name].parameters['properties']['context']['properties']['assignment']
        assert schema['required'] == list(work.SECTIONS)
        assert schema['properties']['settled_decisions']['items']['type'] == 'string'


def contract(**changes):
    return {**{key: [] for key in work.SECTIONS}, **changes}


def test_assignment_round_trip_inheritance_and_explicit_replacement():
    original = contract(settled_decisions=['Use the supplied story'], supplied_evidence=['SQL: SELECT 1'], deliverables=['Published app'])
    assert work.assignment_from_context({'assignment': original}) == original
    assert work.assignment_from_context({}, original) == original
    assert work.assignment_from_context({'assignment': contract()}, original) == contract()
    assert work.assignment_from_context({'task': 'free form'}) is None
    for bad in (None, {}, {'deliverables': 'app'}, contract(deliverables=[{}]), contract(deliverables=['x' * 24001])):
        with pytest.raises(ValueError):
            work.assignment_from_context({'assignment': bad})


def test_reopening_requires_both_decision_and_evidence():
    assert work.scope_deviation(None, None) is None
    with pytest.raises(ValueError):
        work.scope_deviation('Use state counts', '')
    assert work.scope_deviation('Use state counts', 'Query Q returned duplicate state keys') == {
        'decision': 'Use state counts', 'evidence': 'Query Q returned duplicate state keys'}


def receipt(name, result, arguments=None, event='tool.completed'):
    return work.tool_receipt(name, event, result, arguments or {})


def test_only_confirmed_tool_results_become_milestones():
    assert receipt('write_file', {'files_modified': ['/opt/data/app.html']})['milestone'] == 'file_written'
    assert receipt('write_file', {'files_modified': ['/opt/data/app.html'], 'error': 'denied'}) is None
    assert receipt('write_file', {'files_modified': ['/opt/data/app.html']}, event='tool.failed') is None
    assert receipt('terminal', {'exit_code': 0}, {'command': 'echo hi > /opt/data/app.html'}) is None
    args = {'command': 'calliope-web-check /opt/data/app.html'}
    assert receipt('terminal', {'exit_code': 0}, args)['milestone'] == 'validation_passed'
    assert receipt('terminal', {'exit_code': 1}, args)['milestone'] == 'validation_failed'
    assert receipt('terminal', {'exit_code': None}, args) is None
    for command in ('calliope-web-check app.html; true', 'calliope-web-check app.html || true', 'echo calliope-web-check app.html'):
        assert receipt('terminal', {'exit_code': 0}, {'command': command}) is None
    assert receipt('upload_artifact', {'artifact_id': 'a'})['milestone'] == 'uploaded'
    assert receipt('mcp__Specialist__create_live_app', {'slug': 'app', 'version': 1})['milestone'] == 'published'
    assert receipt('create_live_app', {'error': 'INVALID_JAVASCRIPT', 'slug': 'app', 'version': 1}) is None
    assert receipt('browser_exec', {'success': True}) is None


def test_mcp_envelopes_and_progress_projection_do_not_copy_contents():
    wrapped = {'content': [{'type': 'text', 'text': json.dumps({'slug': 'app', 'version': 2, 'html': 'SECRET CONTENT'})}]}
    milestone = receipt('update_live_app', wrapped)
    assert milestone == {'milestone': 'published', 'label': 'App version published'}
    assert receipt('update_live_app', {**wrapped, 'isError': True}) is None
    rows = [dict(payload={'work_receipt': milestone, 'unrelated': 'SECRET CONTENT'}, created_at=when)
            for when in ['2026-09-27T10:01:00Z', '2026-09-27T10:03:00Z']]
    rows.append(dict(payload={'scope_deviation': {'decision': 'story', 'evidence': 'duplicate keys'}}, created_at='2026-09-27T10:04:00Z'))
    summary = work.progress_summary(rows)
    assert summary['milestones'][0]['first_at'] == rows[0]['created_at']
    assert summary['milestones'][0]['last_at'] == rows[1]['created_at']
    assert summary['scope_deviation']['evidence'] == 'duplicate keys'
    assert 'SECRET CONTENT' not in json.dumps(summary)


def test_steering_carries_the_exact_contract_outside_bounded_provided_context():
    from specialist_followups import steer_text
    assignment = contract(settled_decisions=['Use the revised story'], deliverables=['Publish app'])
    message = steer_text({'id': 'f1', 'message': 'Implement the correction',
                          'context': {'assignment': assignment, 'provided': {'ref': 'q1'}}})
    assert json.dumps(assignment) in message
    assert 'q1' in message
