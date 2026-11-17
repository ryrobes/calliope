import copy
import json
from types import SimpleNamespace
import pytest
import ui_apps
import ui_composition
import ui_charts


def document():
 return {'schema':'calliope.app.v1','components':[{'id':'root','component':'CStack','children':['list','editor']},{'id':'list','component':'CStack','children':[]},{'id':'editor','component':'TextField','label':'Notes','value':{'path':'/state/draft/notes'}}],
  'data':{'state':{'id':'a','draft':{'id':'','notes':''}},'rows':[],'record':{'id':'','notes':''},'summary':{}},
  'fragments':{'row':{'root':'label','components':[{'id':'label','component':'CText','text':{'item':'/label'}}]}},
  'repeat':{'list':{'path':'/rows','key':'id','fragment':'row'}},
  'forms':[{'path':'/state/draft','source':'/record','key':'id'}],
  'queries':{'summary':{'sql':'SELECT $1::jsonb','params':[{'path':'/record'}],'path':'/summary','shape':'value'},'record':{'sql':'SELECT $1','params':[{'path':'/state/id'}],'path':'/record','shape':'first'}},
  'events':{'reset':{'reset':['/state/draft']}}}


def host(doc):
 calls=[]
 def query(sql,**kw):
  calls.append(sql)
  return {'rows':[{'id':'a','notes':'saved'}], 'row_count':1}
 return SimpleNamespace(_load_live_app_version=lambda *a:({'name':'Test'},{'kind':'a2ui','version':1,'source_files':{'app.json':json.dumps(doc)}}),tool_run_sql=query,calls=calls)


def test_repeat_keeps_row_identity_when_sql_order_changes():
 d=document();d['data']['rows']=[{'id':'one','label':'First'},{'id':'two','label':'Second'}]
 a=ui_composition.expand(d,d['data']);d['data']['rows'].reverse();b=ui_composition.expand(d,d['data'])
 assert {n['text']:n['id'] for n in a if 'text' in n}=={n['text']:n['id'] for n in b if 'text' in n}
 assert next(n for n in a if n['id']=='list')['children']==list(reversed(next(n for n in b if n['id']=='list')['children']))
 d['data']['rows'].append(d['data']['rows'][0])
 with pytest.raises(ValueError,match='unique'):ui_composition.expand(d,d['data'])


def test_dependencies_sort_and_reject_cycles():
 d=document();assert ui_apps.query_order(d['queries'])[0][0]=='record'
 d['queries']['record']['params']=[{'path':'/summary'}]
 with pytest.raises(ValueError,match='Cyclic'):ui_apps.validate(d)


def test_forms_initialize_and_preserve_unsaved_edits_on_poll():
 d=document();h=host(d)
 first=ui_apps.snapshot(h,'forms','a',{})['messages'][2]['updateDataModel']['value']
 assert first['state']['draft']=={'id':'a','notes':'saved'}
 first['state']['draft']['notes']='unsaved'
 body={'version':1,'state':first['state']}
 second=ui_apps.snapshot(h,'forms','a',body)['messages'][2]['updateDataModel']['value']
 assert second['state']['draft']['notes']=='unsaved'
 third=ui_apps.snapshot(h,'forms','a',{**body,'event':'reset'})['messages'][2]['updateDataModel']['value']
 assert third['state']['draft']['notes']=='saved'


def test_cache_is_scoped_to_actor_app_version_and_parameters():
 d=document();d['queries']['record']['cacheMs']=30000;h=host(d)
 with ui_apps._CACHE_LOCK:ui_apps._QUERY_CACHE.clear()
 a=ui_apps.snapshot(h,'cache','alice',{})
 b=ui_apps.snapshot(h,'cache','alice',{})
 c=ui_apps.snapshot(h,'cache','bob',{})
 assert not a['queries']['record']['cache_hit']
 assert b['queries']['record']['cache_hit']
 assert not c['queries']['record']['cache_hit']
 body={'state':copy.deepcopy(d['data']['state'])};body['state']['id']='b'
 assert not ui_apps.snapshot(h,'cache','alice',body)['queries']['record']['cache_hit']


def test_sql_column_names_are_not_mistaken_for_chart_capabilities():
 spec={'datasets':{'table':[{'url':'a string','signal':'a column','value':4}]},'data':{'name':'table'},'transform':[{'calculate':'datum.value*2','as':'twice'}],'mark':'point'}
 ui_charts.validate(spec)
 spec['layer']=[{'data':{'url':'file:///private'},'mark':'point'}]
 with pytest.raises(ValueError):ui_charts.validate(spec)


def test_committed_action_receipt_survives_invalid_repeated_sql_rows():
 d=document();d['queries']={'rows':{'sql':'SELECT 1','path':'/rows'}}
 d['events']['save']={'action':'save','inputs':{}}
 h=host(d);h.tool_run_sql=lambda *a,**kw:{'rows':[{'id':'same','label':'A'},{'id':'same','label':'B'}]}
 h._execute_app_action_request=lambda *a:{'request_key':'original','rows':[{'id':'a'}]}
 result=ui_apps.snapshot(h,'receipt','alice',{'version':1,'event':'save','request_key':'original'})
 assert result['receipt']['request_key']=='original'
 assert result['queries']['render']['error']['code']=='INVALID_RENDER_DATA'


def test_native_chart_titles_wrap_to_the_viewport():
    spec = {'title': {'text': 'Monthly NYC crime incidents · 2024',
                      'subtitle': 'Distinct NYPD complaints by incident start date · all offense categories'},
            'mark': 'line', 'data': {'values': []}}
    fitted = ui_charts.fit(spec, 328, 300)
    assert fitted['title']['text'] == 'Monthly NYC crime incidents · 2024'
    assert isinstance(fitted['title']['subtitle'], list) and all(len(line) <= 42 for line in fitted['title']['subtitle'])
    assert ' '.join(fitted['title']['subtitle']) == spec['title']['subtitle']
    assert ui_charts.fit({**spec, 'title': 'Short'}, 328, 300)['title'] == 'Short'
