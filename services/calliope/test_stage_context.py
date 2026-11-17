import asyncio
import json
from types import SimpleNamespace

import pytest
import stage_context as sc


def item(n, **kw):
    return dict(surface_id=str(n), title=f'Object {n}', kind='query', _lineage=f'lineage-{n}', **kw)


def response(items, useful=()):
    return {'answers': {f's{i}': {'type': 'choice', 'choice': 'useful' if i in useful else 'unrelated',
                                 'probabilities': {'useful': .9 if i in useful else .01}}
                        for i in range(len(items))}}


def test_scope_and_off_default(monkeypatch):
    monkeypatch.delenv('CALLIOPE_STAGE_CONTEXT_MODE', raising=False)
    assert not sc.enabled('a@b', 'one')
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_MODE', 'select')
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_OWNERS', 'A@B')
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_SESSIONS', 'one')
    assert sc.enabled('a@b', 'one')
    assert not sc.enabled('c@d', 'one')
    assert not sc.enabled('a@b', 'two')


def test_empty_rollout_filters_enable_all_speakers_and_threads(monkeypatch):
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_MODE', 'select')
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_OWNERS', '')
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_SESSIONS', '')
    for speaker, session in [('admin@example.com', 'one'), ('member@example.com', 'two'),
                             ('collaborator@example.com', 'shared')]:
        assert sc.enabled(speaker, session)


def test_candidates_dedupe_but_preserve_explicit_old_version_and_old_matches():
    items = [item(i) for i in range(120)]
    items[0]['_lineage'] = items[1]['_lineage'] = 'same'
    items[-1]['title'] = 'September enrollment geography'
    pool = sc.candidates(items, 'September enrollment geography', selected=items[1])
    assert items[-1] in pool
    assert items[1] in pool
    assert len(pool) <= 33
    assert items[1] not in sc.candidates(items, 'September enrollment geography')


def test_explicit_selection_and_recent_anchor_survive_false_negatives():
    items = [item(i) for i in range(20)]
    result = sc.apply_answers(items, items[-1], response(items))
    assert [i['surface_id'] for i in result] == ['0', '1', '19']
    assert all('_lineage' not in i for i in result)


def test_selected_outside_pool_and_cap():
    items = [item(i) for i in range(20)]
    result = sc.apply_answers(items, item('outside'), response(items, range(20)))
    assert len(result) == 8
    assert 'outside' in {i['surface_id'] for i in result}


@pytest.mark.parametrize('bad', [None, {}, {'s0': {'type': 'choice', 'choice': 'invented'}}])
def test_missing_or_invalid_answers_fail_closed_to_caller_fallback(bad):
    with pytest.raises(ValueError):
        sc.apply_answers([item(0)], None, {'answers': bad})


def test_next_hop_keeps_new_work_and_older_selected_items():
    chosen = [item(0), item(1), item(100)]
    current = [item(25), item(24)] + [item(i) for i in range(24)]
    result = sc.refresh_hop(current, chosen, {str(i) for i in range(24)})
    assert [i['surface_id'] for i in result] == ['25','24','0','1','100']


def test_descriptor_excludes_result_rows_and_document_contents():
    obj = item(1, payload={'rows': ['private'], 'document': 'private'}, private_document={'excerpt': 'private'})
    text = json.dumps(sc.payload('question', [], [obj]))
    assert 'private' not in text


@pytest.mark.parametrize('encode', [False, True])
def test_batch_sql_recovery_is_per_result_and_preserves_explicit_sql(encode):
    queries = {'weekly_coverage': 'select lag from calliope.enrichment',
               'other': 'select secret from unrelated.table_name'}
    source = {'args': {'batch': {'queries': json.dumps(queries) if encode else queries}}}
    assert sc.surface_sql(source, 'Weekly Coverage') == queries['weekly_coverage']
    assert sc.surface_sql(source, 'Unknown') == ''
    source['args']['query_name'] = 'other'
    assert sc.surface_sql(source, 'Weekly Coverage') == queries['other']
    source['sql'] = 'select 1'
    assert sc.surface_sql(source, 'Weekly Coverage') == 'select 1'


def test_batch_recovery_does_not_guess_ambiguous_names_or_accept_bad_json():
    source = {'args': {'batch': {'queries': {'a_b': 'select 1', 'a b': 'select 2'}}}}
    assert sc.surface_sql(source, 'A B') == ''
    for value in ('broken', '[1,2]', None, {'bad': {'sql': 'select 1'}}):
        assert sc.query_map(value) == {}


def test_metadata_parses_tables_past_sql_prefix_and_excludes_ctes_and_rows():
    sql = 'with b as (select a from "calliope"."Enrichment") select ' + ','.join(
        f'a as col_{n}' for n in range(50)) + ' from b join salesforce.person p on true'
    row = {'kind': 'query', 'source': {'sql': sql}, 'payload': {
        'columns': [{'name': 'lag_days', 'type': 'numeric'}, 'cohort'],
        'rows': [['private-row']], 'warehouse_objects': ['stale.lineage']}}
    metadata = sc.surface_metadata(row)
    assert metadata['tables'] == ['salesforce.person', '"calliope"."Enrichment"']
    assert metadata['columns'] == ['lag_days', 'cohort']
    assert 'private-row' not in json.dumps(metadata)
    assert 'stale.lineage' not in json.dumps(metadata)
    assert sc.sql_tables('select (') == ()


def test_chart_metadata_and_column_counts_are_bounded():
    row = {'kind': 'chart', 'payload': {'queries': {'data': 'select * from calliope.enrichment'},
                                      'columns': [f'col{n}' for n in range(40)]}}
    metadata = sc.surface_metadata(row)
    assert metadata['tables'] == ['calliope.enrichment']
    assert len(metadata['columns']) == 16


def test_evidence_anchors_survive_receipt_flood_without_losing_explicit_receipt():
    items = [item(i) for i in range(50)]
    for obj in items[:40]:
        obj['kind'] = 'action'
    pool = sc.candidates(items, 'unrelated terms', selected=items[3])
    assert {'40', '41', '3'} <= {i['surface_id'] for i in pool}
    chosen = sc.apply_answers(pool, items[3], response(pool))
    assert {i['surface_id'] for i in chosen} == {'3', '40', '41'}
    assert len(pool) <= 34


def test_action_only_thread_still_keeps_recent_context():
    items = [dict(item(i), kind='action') for i in range(10)]
    assert {i['surface_id'] for i in sc.apply_answers(items, None, response(items))} == {'0', '1'}


class DB:
    def __init__(self): self.calls = []
    def __call__(self): return self
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def execute(self, sql, args=None):
        self.calls.append((sql,args))
        return SimpleNamespace(fetchall=lambda: [])


def test_resolve_timeout_preserves_original_and_audits(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','test')
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_MODE','select')
    async def fail(*a): raise TimeoutError()
    monkeypatch.setattr(sc,'decide',fail)
    items=[item(i) for i in range(40)]; recent=items[:24]; db=DB()
    selected, receipt, _ = asyncio.run(sc.resolve(db,'session','turn','question',recent,None,lambda:(items,None)))
    assert selected is recent and receipt['status']=='fallback'
    assert any('INSERT INTO' in c[0] for c in db.calls)
    assert 'test' not in json.dumps(receipt)


@pytest.mark.parametrize('mode',['select','shadow'])
def test_resolve_single_call_shadow_and_usage(monkeypatch, mode):
    monkeypatch.setenv('OPENROUTER_API_KEY','test')
    monkeypatch.setenv('CALLIOPE_STAGE_CONTEXT_MODE',mode)
    calls=[]
    async def decide(m,h,p,s,k):
        calls.append(p)
        r=response(p,[5]); r.update(model='jev',usage={'cost':.0001})
        return sc.apply_answers(p,s,r),r
    monkeypatch.setattr(sc,'decide',decide)
    items=[item(i) for i in range(40)]; recent=items[:24]
    result, receipt, _=asyncio.run(sc.resolve(DB(),'s','t','question',recent,None,lambda:(items,None)))
    assert len(calls)==1 and receipt['usage']['cost']==.0001
    assert len(result)==(3 if mode=='select' else 24)


def test_small_inventory_makes_no_hosted_call(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','test')
    async def fail(*a): raise AssertionError('not called')
    monkeypatch.setattr(sc,'decide',fail)
    items=[item(i) for i in range(3)]
    result,r,_=asyncio.run(sc.resolve(DB(),'s','t','q',items,None,lambda:(items,None)))
    assert r['status']=='skipped' and result==items


def test_latest_editable_page_survives_when_jev_only_likes_capture():
    items=[item(i) for i in range(12)]
    items[5].update(kind='artifact',artifact='budget@v3')
    items[6].update(kind='image',title='Capture · budget')
    result=sc.apply_answers(items,None,response(items,[6]))
    assert {'5','6'} <= {i['surface_id'] for i in result}


def test_anonymous_image_flood_does_not_displace_evidence_from_pool_or_anchors():
    images = [dict(item(i), kind='document', title=f'img {i:012x}',
                   image={'purpose': 'saved_image', 'filename': f'img_{i:012x}.png'})
              for i in range(50)]
    evidence = [item('bounds'), item('maturity'), dict(item('page'), kind='artifact', artifact='lead@v4')]
    pool = sc.candidates(images + evidence, 'adjust the date dropdown')
    assert {'bounds', 'maturity', 'page'} <= {i['surface_id'] for i in pool}
    chosen = sc.apply_answers(pool, None, response(pool))
    assert {i['surface_id'] for i in chosen} == {'bounds', 'maturity', 'page'}


def test_opaque_images_remain_selectable_by_user_and_model():
    items = [dict(item(i), kind='image', title=f'img {i:012x}') for i in range(10)] + [item('query')]
    pool = sc.candidates(items, 'compare these images', selected=items[3])
    useful_index = next(n for n, i in enumerate(pool) if i['surface_id'] == '5')
    chosen = sc.apply_answers(pool, items[3], response(pool, [useful_index]))
    assert {i['surface_id'] for i in chosen} == {'query', '3', '5'}


def test_image_only_thread_preserves_recent_fallback():
    items = [dict(item(i), kind='image', title='Generated image') for i in range(10)]
    assert {i['surface_id'] for i in sc.apply_answers(items, None, response(items))} == {'0', '1'}


@pytest.mark.parametrize('title', ['img d2905dba2ea1', 'img_74c59f15fd46.png',
                                 'openrouter gen 20260910 232706 43682208',
                                 'Generated image', 'Screenshot', ''])
def test_placeholder_image_titles_are_not_informative(title):
    assert sc.opaque_image(dict(item(0), kind='image', title=title))


@pytest.mark.parametrize('changes', [
    {'title': 'Lead lifecycle mock v2'},
    {'title': 'Capture · lead'},
    {'image': {'title': 'Approved lifecycle mock'}},
    {'image': {'generation_brief': 'A lifecycle funnel with a maturity warning'}},
    {'image': {'edit_instructions': 'Move the warning above the funnel'}},
    {'image': {'artifact': 'lead@v4'}},
    {'image': {'purpose': 'visual_intent'}},
    {'image': {'purpose': 'page_capture'}},
])
def test_named_or_described_images_remain_recent_anchors(changes):
    image = dict(item(0), kind='image', title='Generated image')
    image.update(changes)
    assert not sc.opaque_image(image)
    items = [image, item(1), item(2)]
    assert {i['surface_id'] for i in sc.apply_answers(items, None, response(items))} == {'0', '1'}


def test_generic_title_on_non_image_document_is_not_penalized():
    document = dict(item(0), kind='document', title='Screenshot')
    assert not sc.opaque_image(document)
