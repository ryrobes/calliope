"""PostgreSQL-backed paging/search checks; set FLOOR_TEST_DSN to a disposable DB."""
import json
import os
import time
import uuid

import pytest

import floor_data


@pytest.fixture
def graph():
    psycopg = pytest.importorskip('psycopg')
    dsn = os.environ.get('FLOOR_TEST_DSN')
    if not dsn:
        pytest.skip('FLOOR_TEST_DSN required for PostgreSQL integration')
    table = 'floor_test_' + uuid.uuid4().hex[:10]
    from psycopg.rows import dict_row
    factory = lambda: psycopg.connect(dsn, row_factory=dict_row)
    with factory() as conn:
        conn.execute(f'''CREATE TABLE {table} AS
            SELECT 'piece:'||g AS item_id, 'chat'::text AS item_kind,
                CASE WHEN g=1 THEN 'Ancient needle' ELSE 'Notebook '||g END AS display_title,
                CASE WHEN g>2500 THEN 'in_motion' ELSE 'done' END::text AS column_state,
                CASE WHEN g=2600 THEN 'private@example.com' ELSE 'viewer@example.com' END::text AS owner_email,
                ARRAY[CASE WHEN g=1 THEN 'old-agent' ELSE 'calliope' END]::text[] AS agents,
                'Summary'::text AS summary,'Completed'::text AS state_reason,
                NULL::text AS action_prompt, g>2500 AS active_now,false AS default_hidden,
                (2600-g)*86400 AS idle_seconds, 2600-g AS attention_rank,
                '2020-01-01'::timestamptz + g*interval '1 day' AS last_activity_at
            FROM generate_series(1,2600) g''')
    # Represents an already authorized projection; the historical branch also
    # observes the request-local GUC, to catch scope escaping between page reads.
    projection = f'''SELECT * FROM {table}
        WHERE (owner_email=%s OR owner_email=%s)
        AND (nullif(current_setting('rvbbit.as_of_timestamp',true),'') IS NULL
          OR last_activity_at <= nullif(current_setting('rvbbit.as_of_timestamp',true),'')::timestamptz)'''
    yield factory, projection
    with factory() as conn:
        conn.execute(f'DROP TABLE {table}')


def read(graph, **params):
    return floor_data.read_page(*graph, 'viewer@example.com', floor_data.request_options(params))


def test_complete_counts_independent_lane_budgets_and_facets(graph):
    start = time.monotonic()
    data = read(graph)
    assert data['total'] == data['matched_total'] == 2599
    assert len(data['cards']) == 60
    assert {r['column_state']: r['pieces'] for r in data['lanes']} == {'done': 2500, 'in_motion': 99}
    assert 'old-agent' in data['facets']['people']
    assert 'private@example.com' not in data['facets']['people']
    assert next(c for c in data['cards'] if c['column_state']=='done')['item_id'] == 'piece:2500'
    print(f'2600-row full-count/page query: {(time.monotonic()-start)*1000:.1f}ms')


def test_old_match_and_filters_are_applied_before_limit(graph):
    data = read(graph, q='ancient needle')
    assert data['matched_total'] == 1
    assert [c['item_id'] for c in data['cards']] == ['piece:1']
    assert read(graph, person='old-agent')['matched_total'] == 1
    assert read(graph, q='%', freshness='older')['matched_total'] == 0
    assert read(graph, person='private@example.com')['matched_total'] == 0
    lanes = read(graph, lane_queries=json.dumps({'done':'Ancient needle'}))
    assert lanes['matched_total'] == 100  # Other lanes retain their own matches.


def test_keyset_walk_has_no_duplicates_or_missing_old_work(graph):
    data = read(graph, lane='done', limit='100')
    ids = [c['item_id'] for c in data['cards']]
    while data['pages']['done']['has_more']:
        data = read(graph, lane='done', limit='100', cursor=data['pages']['done']['next_cursor'])
        ids.extend(c['item_id'] for c in data['cards'])
    assert len(ids) == len(set(ids)) == 2500
    assert ids[-1] == 'piece:1'


def test_hide_notices_filters_before_paging_and_keeps_urgent_observations(graph):
    factory, projection = graph
    table = projection.split('FROM ')[1].split()[0]
    with factory() as conn:
        conn.execute(f"UPDATE {table} SET item_kind='dream', column_state='noticed' "
                     "WHERE item_id IN (SELECT 'piece:'||g FROM generate_series(2401,2500) g)")
        conn.execute(f"UPDATE {table} SET item_kind='dream', column_state='awaiting_you' WHERE item_id='piece:2400'")
        conn.execute(f"UPDATE {table} SET item_kind='dream' WHERE item_id='piece:2399'")
    shown = read(graph, lane='noticed', limit='2')
    assert len(shown['cards']) == 2 and shown['pages']['noticed']['has_more']
    hidden = read(graph, hide_notices='1', limit='2')
    assert hidden['total'] == 2599 and hidden['matched_total'] == 2499
    assert all(c['column_state'] != 'noticed' for c in hidden['cards'])
    assert {'piece:2400', 'piece:2399'} <= {c['item_id'] for c in hidden['cards']}
    assert hidden['pages']['noticed'] == {'next_cursor': None, 'has_more': False}
    notices = next(lane for lane in hidden['lanes'] if lane['column_state'] == 'noticed')
    assert notices['pieces'] == 100 and notices['matched'] == 0
    assert read(graph, hide_notices='1', kind='dream')['matched_total'] == 2
    assert read(graph, hide_notices='1', q='Notebook 2500')['matched_total'] == 0
    assert read(graph, hide_notices='0', q='Notebook 2500')['matched_total'] == 1
    with pytest.raises(ValueError, match='different Floor view'):
        read(graph, hide_notices='1', lane='noticed', cursor=shown['pages']['noticed']['next_cursor'])


def test_history_counts_search_pages_and_cursor_scope(graph):
    at='2021-01-01T00:00:00Z'
    data = read(graph, as_of=at, lane='done', limit='10')
    assert data['total'] == 366
    cursor=data['pages']['done']['next_cursor']
    page=read(graph, as_of=at, lane='done', limit='10', cursor=cursor)
    assert page['total'] == 366
    assert not set(c['item_id'] for c in data['cards']) & set(c['item_id'] for c in page['cards'])
    assert read(graph, as_of=at, q='ancient needle')['matched_total'] == 1
    with pytest.raises(ValueError, match='different Floor view'):
        read(graph, lane='done', cursor=cursor)
    with pytest.raises(ValueError, match='different Floor view'):
        read(graph, as_of=at, lane='done', q='notebook', cursor=cursor)
    assert read(graph)['total'] == 2599  # AS-OF did not leak to live.


@pytest.mark.parametrize('params', [{'as_of':'bad'}, {'as_of':'2026-01-01'}, {'lane':'secret'},
    {'lane_queries':'[]'}, {'lane_queries':'{"done":4}'}, {'freshness':'wat'}, {'limit':'bad'}])
def test_invalid_options(params):
    with pytest.raises(ValueError):
        floor_data.request_options(params)


def test_visible_presentation_search_still_obeys_permissions_and_history(graph):
    aliases = [dict(item_id='piece:1', semantic_title='The enduring project', semantic_status='Shipped'),
               dict(item_id='piece:2600', semantic_title='Confidential project', semantic_status='Secret')]
    options = floor_data.request_options({'q':'enduring project'})
    data = floor_data.read_page(*graph, 'viewer@example.com', options, aliases)
    assert [c['item_id'] for c in data['cards']] == ['piece:1']
    data = floor_data.read_page(*graph, 'viewer@example.com', floor_data.request_options({'q':'confidential project'}), aliases)
    assert data['matched_total'] == 0
    options = floor_data.request_options({'q':'enduring project','as_of':'2021-01-01T00:00:00Z'})
    assert floor_data.read_page(*graph, 'viewer@example.com', options, aliases)['matched_total'] == 0


def test_private_presence_counts_without_becoming_searchable_content():
    def result():
        return dict(total=2600, matched_total=2600, lanes=[], cards=[], facets={'people':[], 'kinds':[]})
    shell = dict(item_id='shadow:opaque', column_state='in_motion', owner_email='other@example.com', agents=['calliope'], active_now=True, silhouette=True)
    data=result()
    floor_data.include_presence(data, [shell], floor_data.request_options({}))
    assert data['total']==data['matched_total']==2601
    assert data['lanes'][0]['pieces']==1
    assert data['cards']==[shell]
    data=result()
    floor_data.include_presence(data, [shell], floor_data.request_options({'q':'other'}))
    assert data['total']==2601 and data['matched_total']==2600
    assert data['cards']==[]


def test_favorites_precede_time_and_attention_across_pages_and_lane_changes(graph):
    factory,projection=graph
    with factory() as c:
        table=projection.split('FROM ')[1].split()[0]
        c.execute(f"UPDATE {table} SET item_id=replace(item_id,'piece:','chat:')")
    options=floor_data.request_options({'lane':'done','limit':'1'})
    result=floor_data.read_page(factory,projection,'viewer@example.com',options,favorites=['1','2'])
    assert result['cards'][0]['item_id']=='chat:2' and result['cards'][0]['favorite']
    options['cursor']=result['pages']['done']['next_cursor']
    result=floor_data.read_page(factory,projection,'viewer@example.com',options,favorites=['1','2'])
    assert result['cards'][0]['item_id']=='chat:1'
    options['cursor']=result['pages']['done']['next_cursor']
    result=floor_data.read_page(factory,projection,'viewer@example.com',options,favorites=['1','2'])
    assert result['cards'][0]['item_id']=='chat:2500' and not result['cards'][0]['favorite']
    with pytest.raises(ValueError):floor_data.read_page(factory,projection,'viewer@example.com',options,favorites=[])
    with factory() as c:c.execute(f"UPDATE {table} SET column_state='in_motion',attention_rank=-100 WHERE item_id='chat:1'")
    result=floor_data.read_page(factory,projection,'viewer@example.com',floor_data.request_options({'lane':'in_motion','limit':'1'}),favorites=['1'])
    assert result['cards'][0]['item_id']=='chat:1'
