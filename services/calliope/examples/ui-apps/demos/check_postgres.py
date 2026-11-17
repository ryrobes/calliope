"""Run in the Calliope container: exercise scoped QA tables, then remove them.

No runtime handlers are installed. Loads the candidate generic modules from this
folder and uses the running server's SQL/action path and configured database.
"""
import copy
import importlib.util
import json
import os
import re
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid
sys.path.insert(0,'/app')
import server,auth,workspace_sql,app_actions
ROOT=Path(__file__).parent
for name in ('ui_charts','ui_composition','ui_apps'):
 spec=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'));module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
import ui_apps
ui_apps.ROOT=Path("/app/ui_app_assets")
from pglast.parser import split
ACTOR=os.environ['CALLIOPE_TEST_ACTOR']
PREFIX='calliope_apps.qa_ui_'+uuid.uuid4().hex[:12]+'_'
server._SESSION_SUB.set(ACTOR)
manifest=json.loads((ROOT/'manifest.json').read_text().replace('calliope_apps.demo_',PREFIX))
base=json.loads((ROOT/'workspace.json').read_text().replace('calliope_apps.demo_',PREFIX))

def sql(statement):
 result=workspace_sql.execute(server._conn,workspace_sql.plan(statement),seed=auth.JWT_SECRET,schema_allowed=server._schema_allowed)
 if result.get('error'):raise RuntimeError(result)
 return result

def act(name,inputs,key=None):
 return app_actions.execute(server._conn,slug='ui-demo-qa',version=1,name=name,actor=ACTOR,request_key=key or str(uuid.uuid4()),manifest=manifest,inputs=inputs,seed=auth.JWT_SECRET,schema_allowed=server._schema_allowed)

def snapshot(page='hvac',state=None):
 doc=copy.deepcopy(base);doc['data']['state']['page']=page
 if page=='case':doc['data']['state']['selection'].update(job='HV-CASE-01',po='PO-CASE-01')
 host=SimpleNamespace(tool_run_sql=server.tool_run_sql,_load_live_app_version=lambda *a:({'name':'Test'},{'kind':'a2ui','version':1,'source_files':{'app.json':json.dumps(doc)}}),_execute_app_action_request=lambda slug,name,actor,body:act(name,body['inputs'],body['request_key']))
 body={'state':state} if state else {}
 result=ui_apps.snapshot(host,'ui-demo-qa',ACTOR,body)
 errors={k:v for k,v in result['queries'].items() if v.get('error')}
 if errors:raise AssertionError(json.dumps(errors))
 return result,result['messages'][2]['updateDataModel']['value']

def main():
 setup=(ROOT/'setup.sql').read_text().replace('calliope_apps.demo_',PREFIX)
 statements=list(split(setup))
 for i in range(0,len(statements),20):sql(';\n'.join(statements[i:i+20]))
 results={}
 for page in ('dossier','pipeline','briefing','hvac','counter','receiving','dispatch','field','adaptive','case','charts'):
  result,data=snapshot(page)
  results[page]={'nodes':len(result['messages'][1]['updateComponents']['components']),'queries':len(result['queries']),'decision':data['decision']}
  (ROOT/(page+'-snapshot.json')).write_text(json.dumps(result,default=str))
  print(page,results[page],flush=True)
 # Review regions are reusable and must not leak across tools.
 _,case=snapshot('case')
 for mode in ('receipt','schedule'):
  state=copy.deepcopy(case['state']);state['controls'].update(mode=[mode],review=mode)
  state[mode+'Review']=copy.deepcopy(state['receiptForm' if mode=='receipt' else 'scheduleForm'])
  result,_=snapshot('case',state)
  (ROOT/('case-'+mode+'-snapshot.json')).write_text(json.dumps(result,default=str))
 state['controls']['mode']=['receipt']
 snapshot('case',state)
 # A real stock receipt must change readiness in another independently loaded app.
 _,data=snapshot('case');assert data['case']['phase']=='waiting'
 draft=data['state']['receiptForm'];draft.update(reference='ISOLATED-PORT-TEST',notes='Fictional regression receipt')
 draft['lines']['PO-CASE-01-0']['quantity']='1'
 key=str(uuid.uuid4());receipt=act('receive',{'draft':draft,'key':key},key)
 replay=act('receive',{'draft':draft,'key':key},key);assert replay['replayed']
 _,fresh=snapshot('case');assert fresh['case']['phase']=='ready',fresh['case']
 # Save / stale write / exact replay.
 d=fresh['state']['caseForm'];inputs={k:d[k] for k in ('id','version','notes','handoff')};inputs['handoff']='Regression handoff'
 key=str(uuid.uuid4());act('save_case',inputs,key);assert act('save_case',inputs,key)['replayed']
 try:act('save_case',inputs)
 except app_actions.ActionError as e:assert e.code=='CONFLICT',str(e)
 else:raise AssertionError('Stale save was accepted')
 # Real scheduling and collision rejection, using the same SQL action path.
 draft=fresh['state']['scheduleForm'];draft['reason']='Regression return appointment';draft['start']='16:00';draft['end']='17:00'
 act('schedule',{'draft':draft,'key':str(uuid.uuid4())})
 _,scheduled=snapshot('case');assert scheduled['job']['start']=='16:00'
 # Reject a technician collision and preserve the second visit.
 other=server.tool_run_sql("SELECT id,version FROM "+PREFIX+"hvac_jobs WHERE status='Scheduled' AND id<>'HV-CASE-01' ORDER BY id LIMIT 1")['rows'][0]
 clash=copy.deepcopy(draft);clash.update(id=other['id'],version=other['version'])
 try:act('schedule',{'draft':clash,'key':str(uuid.uuid4())})
 except app_actions.ActionError as e:assert e.code=='CONFLICT',str(e)
 else:raise AssertionError('Colliding appointment was accepted')
 # Create/edit/order a quote, preserving reservations and replay semantics.
 quote_key=str(uuid.uuid4());q=act('new_quote',{'key':quote_key},quote_key)['rows'][0]['id']
 _,data=snapshot('counter');state=data['state'];state['selection']['quote']=q
 _,data=snapshot('counter',state);draft=data['state']['quoteForm']
 act('add_part',{'draft':draft,'product':'CAP-45-5'})
 state=data['state'];state['quoteForm']={}
 _,data=snapshot('counter',state);draft=data['state']['quoteForm']
 key=str(uuid.uuid4());order=act('order_quote',{'draft':draft,'allow':True},key)
 assert act('order_quote',{'draft':draft,'allow':True},key)['replayed']
 visit=act('create_visit',{'quote':q});assert visit['rows'][0]['visit_id']
 # Append and correct a report; neither submission changes job status.
 key=str(uuid.uuid4());draft={'id':'HV-CASE-01','notes':'Verified replacement','temperature':'22','pressure':'110','correction_of':''}
 report=act('submit_report',{'draft':draft,'key':key},key);assert act('submit_report',{'draft':draft,'key':key},key)['replayed']
 draft['correction_of']=report['rows'][0]['id'];draft['notes']='Corrected measurement';draft['temperature']='23'
 act('submit_report',{'draft':draft,'key':str(uuid.uuid4())})
 _,data=snapshot('field');state=data['state'];state['selection']['job']='HV-CASE-01'
 result,data=snapshot('field',state);assert len(data['reports'])==2 and data['job']['status']=='Scheduled'
 (ROOT/'field-posted-snapshot.json').write_text(json.dumps(result,default=str))
 results['checks']={'receipt_replay':True,'shared_readiness':True,'save_replay':True,'stale_save_rejected':True,'schedule_confirmed':True,'collision_rejected':True,'quote_order_replay':True,'linked_visit':True,'report_correction_and_replay':True}
 (ROOT/'verification.json').write_text(json.dumps(results,indent=2,default=str));print('PASS',flush=True)


if __name__=='__main__':
 tables=re.findall(r'CREATE TABLE IF NOT EXISTS (calliope_apps\.demo_\w+)',(ROOT/'setup.sql').read_text())
 try:main()
 finally:
  for table in reversed(tables):sql('DROP TABLE IF EXISTS '+table.replace('calliope_apps.demo_',PREFIX))
