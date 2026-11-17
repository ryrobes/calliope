"""Stage items as A2UI documents: open questions become answerable native cards."""
import copy

import stage_views

CLIENT = {'catalog_id': stage_views.CATALOG, 'catalog_version': 10,
          'components': sorted(stage_views.INPUT_COMPONENTS | stage_views.QUERY_COMPONENTS | {'CStack', 'CDataGrid'})}
REQUEST = '1857fd0d-0cd3-4b47-98ed-a23f71994c7d'
# The question Callie asked on the lab instance, as stored.
SURFACE = {
    'id': '7f3c0d5e-1b8a-4a51-9b9e-2f4f0c4d1a11', 'kind': 'input', 'turn_id': 't',
    'payload': {
        'title': 'Rank Oracle sample products',
        'prompt': 'Should I rank the Oracle sample products by revenue or by units sold?',
        'fields': [
            {'key': 'ranking_measure', 'type': 'choice', 'label': 'Rank products by', 'required': True,
             'options': [{'label': 'Revenue', 'value': 'revenue'}, {'label': 'Units sold', 'value': 'units_sold'}]},
            {'key': 'notes', 'type': 'textarea', 'label': 'Anything else?'},
            {'key': 'include_returns', 'type': 'boolean', 'label': 'Include returns'},
        ],
        'blocking': True, 'submit_label': 'Continue',
        'request_state': {'id': REQUEST, 'status': 'open', 'answers': {}, 'revision': 1},
    },
}


def components(view):
    return {c['id']: c for c in view['messages'][1]['updateComponents']['components']}


def test_open_question_becomes_a_native_form_with_runnable_actions():
    view = stage_views.project(SURFACE, CLIENT)
    assert 'fallback' not in view
    create, update, data = view['messages']
    assert create['createSurface'] == {'surfaceId': f"stage-{SURFACE['id']}", 'catalogId': stage_views.CATALOG}
    by_id = components(view)
    assert by_id['root']['children'] == ['title', 'prompt', 'field-0', 'field-1', 'field-2', 'actions']
    assert by_id['field-0']['component'] == 'ChoicePicker'
    assert by_id['field-0']['label'] == 'Rank products by *'
    assert by_id['field-0']['options'][1] == {'label': 'Units sold', 'value': 'units_sold'}
    assert by_id['field-1'] == {'id': 'field-1', 'component': 'TextField', 'label': 'Anything else?',
                                'value': {'path': '/answers/f1'}, 'variant': 'longText'}
    assert by_id['field-2']['component'] == 'CheckBox'
    assert by_id['submit-label']['text'] == 'Continue'
    assert data['updateDataModel']['value'] == {'answers': {'f0': [], 'f1': '', 'f2': False}}
    answer = view['actions'][by_id['submit']['action']['event']['name']]
    assert answer['path'] == f'/api/calliope/input-requests/{REQUEST}/answer'
    assert answer['body'] == {'revision': 1}
    assert answer['fields'] == {'answers': {'ranking_measure': '/answers/f0', 'notes': '/answers/f1',
                                            'include_returns': '/answers/f2'}}
    cancel = view['actions'][by_id['skip']['action']['event']['name']]
    assert cancel['path'].endswith('/cancel') and cancel['body'] == {'revision': 1}


def test_built_in_components_follow_the_strict_studio_rules():
    for component in components(stage_views.project(SURFACE, CLIENT)).values():
        if component['component'] == 'Button':
            assert component['variant'] in ('primary', 'borderless') and component['child']
            assert set(component['action']) == {'event'} and set(component['action']['event']) <= {'name', 'context'}
        if component['component'] == 'ChoicePicker':
            assert 1 <= len(component['options']) <= 100 and component['variant'] == 'mutuallyExclusive'
            assert all(set(o) == {'label', 'value'} and all(isinstance(v, str) for v in o.values())
                       for o in component['options'])
        if component['component'] == 'TextField':
            assert component['variant'] in ('shortText', 'longText')


def test_prior_answers_prefill_and_unknown_choices_do_not():
    surface = copy.deepcopy(SURFACE)
    surface['payload']['request_state']['answers'] = {'ranking_measure': 'units_sold', 'notes': 'Q4 only',
                                                      'include_returns': True}
    assert stage_views.project(surface, CLIENT)['messages'][2]['updateDataModel']['value']['answers'] == {
        'f0': ['units_sold'], 'f1': 'Q4 only', 'f2': True}
    surface['payload']['request_state']['answers'] = {'ranking_measure': 'profit'}
    assert stage_views.project(surface, CLIENT)['messages'][2]['updateDataModel']['value']['answers']['f0'] == []


def test_items_a_client_cannot_answer_or_render_keep_their_hand_off():
    answered = copy.deepcopy(SURFACE)
    answered['payload']['request_state']['status'] = 'answered'
    assert stage_views.project(answered, CLIENT)['fallback'] == 'not_projected'
    assert stage_views.project({'id': 'q', 'kind': 'map', 'payload': {}}, CLIENT)['fallback'] == 'not_projected'
    assert stage_views.project(SURFACE, CLIENT, can_answer=False)['fallback'] == 'read_only'
    older = {**CLIENT, 'components': ['Column', 'Text', 'Button']}
    assert stage_views.project(SURFACE, older)['fallback'] == 'unsupported_client'
    assert stage_views.project(SURFACE, {**CLIENT, 'catalog_id': 'basic'})['fallback'] == 'unsupported_client'
    assert stage_views.project(SURFACE, None)['fallback'] == 'unsupported_client'
    broken = copy.deepcopy(SURFACE)
    broken['payload']['request_state']['revision'] = None
    assert stage_views.project(broken, CLIENT)['fallback'] == 'unprojectable'
    no_options = copy.deepcopy(SURFACE)
    no_options['payload']['fields'] = [{'key': 'k', 'type': 'choice', 'label': 'Pick', 'options': []}]
    assert stage_views.project(no_options, CLIENT)['fallback'] == 'unprojectable'


def test_catalog_matches_published_apps():
    import ui_apps
    assert stage_views.CATALOG == ui_apps.CATALOG


def strict(components):
    """The renderer-side validator every projection must pass."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / 'apps/calliope-canvas/business/presentation.py'
    if not path.exists():  # The server image ships without the Canvas sources.
        return
    spec = importlib.util.spec_from_file_location('presentation', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate_components(components)


QUERY = {
    'id': 'f415328f-ed17-4067-a454-bc1904c93a9b', 'kind': 'query', 'turn_id': 't',
    'payload': {
        'columns': [{'name': 'revenue_rank', 'type': 'int8'}, {'name': 'prod_id', 'type': 'int4'},
                    {'name': 'prod_name', 'type': 'text'}, {'name': 'revenue', 'type': 'numeric'},
                    {'name': 'calendar_year', 'type': 'int4'}, {'name': 'last_sale', 'type': 'timestamptz'},
                    {'name': 'units', 'type': '1700'}, {'name': 'attrs', 'type': 'jsonb'}],
        'rows': [{'revenue_rank': 1, 'prod_id': 18, 'prod_name': 'Lithium Electric Golf Caddy',
                  'revenue': '15011642.52', 'calendar_year': 2019, 'last_sale': '2021-12-31 00:00:00+00:00',
                  'units': 12.5, 'attrs': {'size': 'L'}},
                 {'revenue_rank': 2, 'prod_id': 17, 'prod_name': 'Soccer Goal', 'revenue': None,
                  'calendar_year': 2020, 'last_sale': None, 'units': 3, 'attrs': None}],
        'row_count': 2, 'truncated': False, 'default_view': 'table',
    },
}


def test_query_results_become_a_native_table_with_column_formats():
    view = stage_views.project(QUERY, CLIENT)
    assert 'fallback' not in view and view['actions'] == {}
    create, update, data = view['messages']
    assert create['createSurface']['surfaceId'] == f"stage-{QUERY['id']}"
    [table] = update['updateComponents']['components']
    strict([table])
    assert table['id'] == 'root' and table['rows'] == {'path': '/rows'}
    assert {c['name']: c['format'] for c in table['columns']} == {
        'revenue_rank': 'integer', 'prod_id': 'plain', 'prod_name': 'text', 'revenue': 'decimal',
        'calendar_year': 'plain', 'last_sale': 'time', 'units': 'decimal', 'attrs': 'json'}
    assert data['updateDataModel']['value']['rows'][0][:4] == [1, 18, 'Lithium Electric Golf Caddy', '15011642.52']
    assert data['updateDataModel']['value']['rows'][1][3] is None
    assert table['rowCount'] == 2 and table['truncated'] is False


def test_legacy_and_oversized_results_stay_bounded():
    legacy = {'id': 'q', 'kind': 'query', 'payload': {'rows': [[1, 'a'], [2, 'b', 'extra']], 'truncated': True}}
    view = stage_views.project(legacy, CLIENT)
    [table] = view['messages'][1]['updateComponents']['components']
    assert [c['name'] for c in table['columns']] == ['Column 1', 'Column 2', 'Column 3']
    assert view['messages'][2]['updateDataModel']['value']['rows'] == [[1, 'a', None], [2, 'b', 'extra']]
    assert table['truncated'] is True
    big = {'id': 'q', 'kind': 'query', 'payload': {
        'columns': ['body'], 'rows': [{'body': 'x' * 20_000}] * 5_000, 'row_count': 5_000}}
    view = stage_views.project(big, CLIENT)
    rows = view['messages'][2]['updateDataModel']['value']['rows']
    assert len(rows) < stage_views.MAX_ROWS and len(rows[0][0]) == stage_views.MAX_CELL_CHARS + 1
    assert view['messages'][1]['updateComponents']['components'][0]['rowCount'] == 5_000


def test_query_fallbacks():
    assert stage_views.project(QUERY, {**CLIENT, 'components': sorted(stage_views.INPUT_COMPONENTS)})['fallback'] == 'unsupported_client'
    assert stage_views.project({'id': 'q', 'kind': 'query', 'payload': {'rows': 'nope'}}, CLIENT)['fallback'] == 'unprojectable'
    assert stage_views.project({'id': 'q', 'kind': 'query', 'payload': {'rows': []}}, CLIENT)['fallback'] == 'unprojectable'


CHART_CLIENT = {**CLIENT, 'components': CLIENT['components'] + ['CVegaChart']}
LIVE_CHART = {
    'id': '0b6f1c1e-5d1e-4c55-9f77-3f2f4f0c9a01', 'kind': 'chart', 'title': 'Revenue by category',
    'payload': {'format': 'vega-lite', 'queries': {'revenue': 'select 1'}, 'live': True,
                'spec': {'data': {'name': 'revenue'}, 'mark': 'bar',
                         'encoding': {'x': {'field': 'category'}, 'y': {'field': 'revenue', 'type': 'quantitative'}}}},
}


def test_live_charts_carry_their_resolved_datasets():
    from decimal import Decimal
    assert stage_views.wants_chart_data(LIVE_CHART, CHART_CLIENT)
    assert not stage_views.wants_chart_data(LIVE_CHART, CLIENT)
    view = stage_views.project(LIVE_CHART, CHART_CLIENT, chart_data={
        'datasets': {'revenue': [{'category': 'Golf', 'revenue': Decimal('15011642.52')}]}})
    [chart] = view['messages'][1]['updateComponents']['components']
    strict([chart])
    assert chart == {'id': 'root', 'component': 'CVegaChart', 'spec': {'path': '/spec'},
                     'height': stage_views.CHART_HEIGHT, 'description': 'Revenue by category'}
    spec = view['messages'][2]['updateDataModel']['value']['spec']
    assert spec['datasets'] == {'revenue': [{'category': 'Golf', 'revenue': '15011642.52'}]}
    assert LIVE_CHART['payload']['spec'].get('datasets') is None  # The stored spec is untouched.


def test_chart_files_and_chart_fallbacks():
    inline = {'id': 'c', 'kind': 'chart', 'payload': {'spec': {'data': {'values': [{'a': 1}]}, 'mark': 'point'}}}
    view = stage_views.project(inline, CHART_CLIENT)
    assert 'fallback' not in view and view['messages'][1]['updateComponents']['components'][0]['description'] == 'Chart'
    assert stage_views.project(inline, CLIENT)['fallback'] == 'unsupported_client'
    assert stage_views.project(LIVE_CHART, CHART_CLIENT, chart_data={'error': 'revenue: timeout'}) == {
        'surface_id': LIVE_CHART['id'], 'kind': 'chart', 'template_version': 1,
        'fallback': 'unprojectable', 'reason': 'revenue: timeout'}
    remote = {'id': 'c', 'kind': 'chart', 'payload': {'spec': {'data': {'url': 'https://example.com/x.csv'}, 'mark': 'bar'}}}
    assert stage_views.project(remote, CHART_CLIENT)['fallback'] == 'unprojectable'
    assert stage_views.project({'id': 'm', 'kind': 'chart', 'payload': {'format': 'mapbox', 'spec': {}}}, CHART_CLIENT)['fallback'] == 'unprojectable'


def test_a_chart_title_repeating_the_card_title_is_dropped_but_its_subtitle_stays():
    spec = {'title': {'text': 'Revenue by  category', 'subtitle': 'All years'}, 'mark': 'bar'}
    assert stage_views.without_repeated_title(spec, 'Revenue by category')['title'] == {
        'text': ' ', 'fontSize': 1, 'subtitlePadding': 0, 'subtitle': 'All years'}
    assert 'title' not in stage_views.without_repeated_title({'title': 'Revenue by category'}, 'revenue BY category')
    assert stage_views.without_repeated_title({'title': 'Other'}, 'Revenue by category') == {'title': 'Other'}
    assert spec['title']['text'] == 'Revenue by  category'  # The stored spec is untouched.


FIND = {'id': 'f', 'kind': 'find', 'payload': {
    'query': 'crime_feeds', 'confidence': 'medium', 'rerank': True, 'judge': False,
    'hits': [{'object': 'crime_feeds.denver_incidents', 'kind': 'db_table', 'role': 'business', 'rows_est': 200330,
              'why': ['description', 'meaning'], 'used': False,
              'matched_values': [{'value': 'Larceny', 'n': 1200}]}]
            + [{'object': f'crime_feeds.t{i}', 'kind': 'db_table'} for i in range(6)]}}


def test_find_results_become_a_ranked_list():
    client = {**CLIENT, 'components': CLIENT['components'] + ['CSurface']}
    view = stage_views.project(FIND, client)
    components = view['messages'][1]['updateComponents']['components']
    strict(components)
    texts = {c['id']: c.get('text') for c in components}
    assert texts['summary'] == 'Searched for “crime_feeds” · 7 matches · medium confidence · reranked'
    assert texts['hit-0-name'] == 'crime_feeds.denver_incidents'
    assert texts['hit-0-facts'] == 'table · business · 200,330 rows'
    assert texts['hit-0-why'] == 'Matched on description, meaning'
    assert texts['hit-0-values'] == 'Values: Larceny ×1,200'
    assert 'hit-5' not in texts and texts['more'].startswith('Also considered 2 more')
    assert stage_views.project(FIND, CLIENT)['fallback'] == 'unsupported_client'


TEXT_CLIENT = {**CLIENT, 'components': CLIENT['components'] + ['CSurface', 'CMarkdown']}


def test_answers_project_as_markdown_with_their_caveat():
    view = stage_views.project({'id': 'a', 'kind': 'answer', 'payload': {
        'answer': '**27** school codes stayed under 5 leads.', 'caveat': 'Closed weeks only.',
        'evidence_refs': [{'id': 'q1'}, {'id': 'q2'}]}}, TEXT_CLIENT)
    components = view['messages'][1]['updateComponents']['components']
    strict(components)
    by_id = {c['id']: c for c in components}
    assert by_id['answer'] == {'id': 'answer', 'component': 'CMarkdown', 'text': '**27** school codes stayed under 5 leads.'}
    assert by_id['caveat']['text'] == 'Caveat · Closed weeks only.'
    assert by_id['evidence']['text'] == 'Backed by 2 Stage items'
    assert stage_views.project({'id': 'a', 'kind': 'answer', 'payload': {'answer': ' '}}, TEXT_CLIENT)['fallback'] == 'unprojectable'


def test_specialist_threads_show_who_how_far_and_the_latest_exchange():
    payload = {'mode': 'specialist_thread', 'status': 'complete', 'round_count': 2, 'elapsed_ms': 252_000,
               'specialist': {'key': 'analytical-skeptic', 'name': 'Cassandra'},
               'objective': 'Check the 2024 cohort counts.',
               'transcript': [{'role': 'lead', 'text': f'note {i}', 'round': 1} for i in range(3)]
               + [{'role': 'specialist', 'text': 'The counts **hold**.', 'round': 2}],
               'tool_activity': [{}, {}, {}], 'stage_surfaces': [{'id': 'q', 'kind': 'query'}]}
    view = stage_views.project({'id': 's', 'kind': 'action', 'payload': payload}, TEXT_CLIENT)
    components = view['messages'][1]['updateComponents']['components']
    strict(components)
    by_id = {c['id']: c for c in components}
    assert by_id['head']['text'] == 'Cassandra · finished · 2 rounds · 4m 12s'
    assert by_id['root']['children'] == ['head', 'objective', 'msg-0', 'msg-1', 'msg-2', 'facts']
    assert by_id['msg-2-who']['text'] == 'Cassandra · round 2' and by_id['msg-2-text']['text'] == 'The counts **hold**.'
    assert by_id['facts']['text'] == '1 earlier messages · 3 tool steps · 1 Stage output'
    payload['transcript'].insert(0, {'role': 'lead', 'kind': 'handoff', 'text': 'Check the 2024  cohort counts.', 'round': 1})
    again = stage_views.project({'id': 's', 'kind': 'action', 'payload': payload}, TEXT_CLIENT)
    texts = [c.get('text') for c in again['messages'][1]['updateComponents']['components']]
    assert 'Check the 2024  cohort counts.' not in texts
    receipt = {'id': 'r', 'kind': 'action', 'payload': {'mode': 'action_receipt'}}
    assert stage_views.project(receipt, TEXT_CLIENT)['fallback'] == 'not_projected'
    assert stage_views.project({'id': 's', 'kind': 'action', 'payload': payload}, CLIENT)['fallback'] == 'unsupported_client'


SHA = 'a' * 64
RUN_ID = '6ed37656-a476-4626-bf4d-fe51b4718b03'


def receipt(status, result=None, executor='local_sql', **extra):
    return {'id': 'r', 'kind': 'action', 'payload': {'mode': 'action_receipt', 'run': {
        'id': RUN_ID, 'status': status, 'result': result or {},
        'action_snapshot': {'executor': executor, 'receipt_only': True},
        'input_redacted': {'sql': 'SELECT count(*) AS n FROM crime_feeds.nypd_complaints', 'sql_sha256': SHA,
                           'statement_type': 'select'}, **extra}}}


RECEIPT_CLIENT = {**CLIENT, 'components': CLIENT['components'] + ['CSurface', 'CMarkdown', 'Row', 'Text', 'Button']}


def test_completed_sql_receipts_show_their_results_natively():
    view = stage_views.project(receipt('complete', {'columns': ['n'], 'rows': [[46951]], 'row_count': 1,
                                                    'elapsed_ms': 182}), RECEIPT_CLIENT)
    components = view['messages'][1]['updateComponents']['components']
    strict(components)
    by_id = {c['id']: c for c in components}
    assert by_id['status']['text'] == 'Local SQL · Complete · SELECT · 182 ms'
    assert by_id['result-0']['component'] == 'CResultTable' and by_id['result-0']['rows'] == {'path': '/results/r0'}
    assert view['messages'][2]['updateDataModel']['value'] == {'results': {'r0': [[46951]]}}
    assert 'sql' not in by_id and by_id['sha']['text'] == 'SHA-256 aaaaaaaaaaaaaaaa…' and view['actions'] == {}


def test_failed_and_workspace_receipts_keep_their_sql_and_outcomes():
    failed = stage_views.project(receipt('failed', error='relation does not exist'), RECEIPT_CLIENT)
    by_id = {c['id']: c for c in failed['messages'][1]['updateComponents']['components']}
    assert by_id['error']['text'] == 'relation does not exist' and by_id['status']['tone'] == 'warm'
    assert by_id['sql-text']['family'] == 'mono'
    batch = stage_views.project(receipt('complete', {'statements': [
        {'statement': 1, 'status': 'CREATE TABLE'}, {'statement': 2, 'status': 'INSERT 0 3', 'rows_affected': 3}]},
        executor='workspace_sql'), RECEIPT_CLIENT)
    by_id = {c['id']: c for c in batch['messages'][1]['updateComponents']['components']}
    assert by_id['status']['text'].startswith('Calliope workspace · Complete')
    assert by_id['result-1']['text'] == 'Statement 2 · INSERT 0 3 · 3 rows affected'


def test_pending_sql_approvals_carry_a_recipe_bound_to_the_frozen_hash():
    view = stage_views.project(receipt('planned'), RECEIPT_CLIENT)
    by_id = {c['id']: c for c in view['messages'][1]['updateComponents']['components']}
    assert by_id['status']['text'].startswith('Local SQL · Waiting for your approval')
    assert by_id['approve']['action'] == {'event': {'name': 'stage.sql.approve'}}
    recipe = view['actions']['stage.sql.approve']
    assert recipe['path'] == f'/api/calliope/action-runs/{RUN_ID}/approve-local-sql'
    assert recipe['body'] == {'expected_sql_sha256': SHA} and recipe['follow'] is False
    viewer = stage_views.project(receipt('planned'), RECEIPT_CLIENT, can_answer=False)
    assert 'approve' not in {c['id'] for c in viewer['messages'][1]['updateComponents']['components']}
    assert viewer['actions'] == {}


def test_other_receipts_state_their_outcome():
    other = {'id': 'r', 'kind': 'action', 'payload': {'mode': 'action_receipt', 'message': 'Connected to Stripe.',
             'run': {'id': RUN_ID, 'status': 'complete', 'action_snapshot': {'executor': 'mcp_connect', 'risk': 'reversible'}}}}
    view = stage_views.project(other, RECEIPT_CLIENT)
    components = view['messages'][1]['updateComponents']['components']
    strict(components)
    assert [c['text'] for c in components if c['component'] == 'CText'] == ['Complete · reversible', 'Connected to Stripe.']
    assert stage_views.project(other, CLIENT)['fallback'] == 'unsupported_client'


def test_connections_awaiting_oauth_offer_a_sign_in_link():
    url = 'https://accounts.example.com/oauth/authorize?state=abc'
    pending = {'id': 'c', 'kind': 'action', 'payload': {'mode': 'action_receipt', 'status': 'awaiting_authorization',
               'run': {'id': RUN_ID, 'status': 'running', 'action_snapshot': {'executor': 'mcp_connect'},
                       'steps': [{'id': 'authorize', 'result': {'authorization_url': url}}]}}}
    view = stage_views.project(pending, RECEIPT_CLIENT)
    assert view['actions'] == {'stage.connection.sign_in': {'method': 'OPEN', 'url': url}}
    assert {c['id'] for c in view['messages'][1]['updateComponents']['components']} >= {'sign-in', 'actions'}
    pending['payload']['run']['steps'][0]['result']['authorization_url'] = 'javascript:alert(1)'
    assert stage_views.project(pending, RECEIPT_CLIENT)['actions'] == {}


def test_metric_values_follow_the_shared_card_formatter():
    f = stage_views.format_metric
    assert f(0.7860481841064365, {'format': 'percent'}) == '78.6%'
    assert f(78.6, {'unit': '%'}) == '78.6%'
    assert f(1234.5, {}) == '1,234.5' and f(1234567, {}) == '1.23M' and f(42, {'format': 'count'}) == '42'
    assert f(15011642.52, {'format': 'currency'}) == '$15.01M' and f(1234.5, {'format': 'currency'}) == '$1,234.50'
    assert f(12, {'unit': 'days'}) == '12 days' and f(12, {'unit': 'count'}) == '12'
    assert stage_views.metric_number({'value': 0.5, 'value_column': 'rate'}) == 0.5
    assert stage_views.metric_number([{'placement_rate': '0.7'}]) == 0.7


def test_metrics_project_their_value_trend_and_status():
    payload = {'name': 'graduate_placement_rate', 'display': {'format': 'percent'},
               'result': {'value': 0.786, 'value_column': 'placement_rate'},
               'data_as_of': '2026-10-15 16:00:00.089547+00',
               'observations': [{'value': {'value': 0.786}, 'data_as_of': '2026-10-15', 'status': None, 'verdict': True},
                                {'value': {'value': 0.75}, 'data_as_of': '2026-10-01'}]}
    client = {**CLIENT, 'components': CLIENT['components'] + ['CMetric']}
    view = stage_views.project({'id': 'm', 'kind': 'metric', 'payload': payload}, client)
    components = view['messages'][1]['updateComponents']['components']
    strict(components)
    metric = next(c for c in components if c['id'] == 'metric')
    assert metric['value'] == '78.6%' and metric['delta'] == '↗ 4.8%' and metric['meaning'] == 'neutral'
    assert metric['status'] == 'Passing'
    assert metric['caption'] == 'As of 2026-10-15 16:00 UTC · 2 observations'
    assert stage_views.project({'id': 'm', 'kind': 'metric', 'payload': {'result': {'a': 1, 'b': 2}}}, client)['fallback'] == 'unprojectable'


DECISION = {'id': 'd', 'kind': 'decision', 'payload': {
    'id': '0b6f1c1e-5d1e-4c55-9f77-3f2f4f0c9a02', 'decision_kind': 'approval', 'status': 'open', 'revision': 3,
    'summary': 'Spend is 20% over plan.', 'recommended_option': 'pause',
    'options': [{'key': 'pause', 'label': 'Pause the campaign', 'consequence': 'Leads drop for a week.'},
                {'key': 'keep', 'label': 'Keep it running'}]}}


def test_open_decisions_offer_each_option_with_its_consequence():
    view = stage_views.project(DECISION, RECEIPT_CLIENT)
    by_id = {c['id']: c for c in view['messages'][1]['updateComponents']['components']}
    assert by_id['kicker']['text'] == 'Waiting on you · approval'
    assert by_id['option-0-label']['text'] == 'Pause the campaign · Recommended'
    assert by_id['option-0-detail']['text'] == 'Leads drop for a week.'
    assert by_id['option-0-choose']['variant'] == 'primary' and by_id['option-1-choose']['variant'] == 'borderless'
    recipe = view['actions']['stage.decision.choose.1']
    assert recipe['path'] == '/api/calliope/decisions/0b6f1c1e-5d1e-4c55-9f77-3f2f4f0c9a02/answer'
    assert recipe['body'] == {'selected_option': 'keep', 'revision': 3} and recipe['follow'] is False


def test_recorded_decisions_mark_the_choice_and_offer_nothing():
    answered = copy.deepcopy(DECISION)
    answered['payload'].update(status='answered', selected_option='keep', response_note='Budget approved.')
    view = stage_views.project(answered, RECEIPT_CLIENT)
    by_id = {c['id']: c for c in view['messages'][1]['updateComponents']['components']}
    assert by_id['kicker']['text'] == 'Decision recorded · approval'
    assert by_id['option-1-label']['text'] == 'Keep it running · Chosen'
    assert view['actions'] == {} and 'option-0-choose' not in by_id
    assert by_id['note']['text'] == 'Note · Budget approved.'
    assert stage_views.project(DECISION, RECEIPT_CLIENT, can_answer=False)['actions'] == {}
