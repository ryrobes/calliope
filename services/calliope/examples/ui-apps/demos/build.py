"""Author the published regression apps. Output is JSON + SQL, never app Python.

The UI helpers run only at authoring time. All live reads/commands use the shared
Calliope app endpoint and existing PostgreSQL action executor.
"""
import copy
import datetime as dt
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5]
sys.path[:0]=[str(ROOT/'apps/calliope-canvas/business'),str(ROOT/'services/calliope')]
from ui import UI, bind
from adaptive import TREE
from case_ui import support
from ui_decisions import validate as decision_policy

HERE=Path(__file__).parent
T=lambda name:'calliope_apps.demo_'+name
item=lambda path:{'item':'/'+path}
PAGES={'dossier':'Transaction dossier','pipeline':'Transaction pipeline','briefing':'Morning briefing','hvac':'Service operations','counter':'Parts desk','receiving':'Receiving','dispatch':'Dispatch','field':'Field notebook','adaptive':'Adaptive workspace','case':'Shared case','charts':'Chart gallery'}
TECHS=['Alex Morgan','Sam Rivera','Jordan Lee']
TODAY=dt.date.today().isoformat()

def make():
    doc={'schema':'calliope.app.v1','title':'Company workspace','components':[], 'data':{
      'state':{'page':'hvac','selection':{'deal':'cedar','job':'HV-1001','quote':'Q-1042','po':'PO-CASE-01'},
        'filter':{'owner':['Everyone'],'technician':['Everyone'],'days':['28'],'status':['All'],'search':'','date':TODAY},
        'controls':{'product':['CAP-45-5'],'quantity':'1','allow_backorder':False,'focus':['auto'],'chart':['layered'],'mode':['overview'],'review':''},
        'dealForm':{},'jobForm':{},'quoteForm':{},'receiptForm':{},'scheduleForm':{},'fieldForm':{},'caseForm':{}},
      'errors':{},'deals':[],'deal':{'id':'','version':0,'address':'','buyer':'','price_label':'','owner':['Alex Morgan'],'closing_date':TODAY,'notes':'','stage':''},'tasks':[],
      'jobs':[],'job':{'id':'','version':0,'customer':'','address':'','technician':['Alex Morgan'],'job_type':'','scheduled_date':TODAY,'start':'08:00','end':'10:00','reason':'','notes':'','status':'','equipment':'','parts':'','order_id':'','amount_label':''},
      'products':[],'quotes':[],'quote':{'id':'','version':0,'customer_id':'','branch':['north'],'notes':'','status':'','lines':{},'total_label':'','order_id':''},'quoteLines':[],
      'purchaseOrders':[],'po':{'id':'','version':0,'supplier':'','branch':'','expected_date':TODAY,'received_date':TODAY,'received_time':'09:00','reference':'','notes':'','lines':{}},'poLines':[],
      'equipment':[],'reports':[],'activity':[],
      'case':{'id':'','version':0,'title':'','notes':'','handoff':'','phase':'waiting','reason':'','equipment':'','history':'','parts':'','visit_id':'','po_id':'','focus':['auto'],'role':['coordinator']},
      'attachmentNames':{},'fieldDefaults':{'id':'','notes':'','temperature':'','pressure':'','correction_of':'','attachments':[]},
      'decision':{'label':'clarify','status':'pending','steps':0},
      'policies':{'adaptive':TREE,'case':decision_policy({'state':{},'tree':support()['tree']})[1]},
      'charts':{}},'queries':{},'events':{'filter':{}},'forms':[], 'fragments':{},'repeat':{},'switch':{},
      'navigation':{'paths':['/state/controls/review','/state/page','/state/selection/deal','/state/selection/job','/state/selection/quote','/state/selection/po']},
      'watch':['/state/controls/focus','/state/controls/chart','/state/controls/mode','/state/jobForm/notes','/state/caseForm/notes']}
    # Watch defaults exist before first hydration.
    for form in ('jobForm','caseForm'):doc['data']['state'][form]['notes']=''
    Q=doc['queries']; E=doc['events']; A={}
    def query(name,sql,params=(),shape='rows',path=None,**kw):
        Q[name]={'sql':sql,'params':[bind(p) for p in params],'path':path or '/'+name,'shape':shape,**kw}
    query('deals',f"SELECT *, '$'||to_char(price,'FM999,999,990') AS price_label, address||' · '||owner AS title FROM {T('deals')} WHERE ($1='Everyone' OR owner=$1) ORDER BY closing_date,id",['/state/filter/owner/0'])
    query('deal',f"SELECT id,version,address,buyer,stage,price,'$'||to_char(price,'FM999,999,990') price_label,jsonb_build_array(owner) owner,closing_date,notes FROM {T('deals')} WHERE id=$1",['/state/selection/deal'],'first')
    query('tasks',f"SELECT t.*,CASE WHEN done=1 THEN 'Complete' ELSE 'Open' END AS label FROM {T('tasks')} t WHERE deal_id=$1 ORDER BY due,id",['/state/selection/deal'])
    query('jobs',f"""SELECT j.*, j.scheduled_date||' · '||j.slot||' · '||j.technician AS detail FROM {T('hvac_jobs')} j
      WHERE ($1='Everyone' OR j.technician=$1) AND j.scheduled_date::date >= current_date - $2::integer
      AND ($3='All' OR j.status=$3) AND (j.customer ILIKE '%'||$4||'%' OR j.id ILIKE '%'||$4||'%')
      ORDER BY j.scheduled_date DESC,j.slot,j.id LIMIT 30""",['/state/filter/technician/0','/state/filter/days/0','/state/filter/status/0','/state/filter/search'])
    query('job',f"""SELECT j.id,j.version,j.customer,j.address,jsonb_build_array(j.technician) technician,j.job_type,j.scheduled_date,
      coalesce(s.start,'08:00') start,coalesce(s.\"end\",'10:00') \"end\",'' reason,j.notes,j.status,j.equipment,
      '$'||to_char(j.amount_cents/100.0,'FM999,999,990.00') amount_label,
      coalesce(o.id,'') order_id, CASE WHEN coalesce(sum(l.backorder),0)>0 THEN 'Waiting for parts' ELSE 'Parts ready' END AS parts
      FROM {T('hvac_jobs')} j LEFT JOIN {T('dispatch_slots')} s ON s.job_id=j.id
      LEFT JOIN {T('counter_orders')} o ON o.visit_id=j.id LEFT JOIN {T('counter_order_lines')} l ON l.order_id=o.id
      WHERE j.id=$1 GROUP BY j.id,s.start,s.\"end\",o.id""",['/state/selection/job'],'first')
    query('products',f"SELECT p.*, '$'||to_char(price_cents/100.0,'FM999,990.00') AS price_label, coalesce(s.on_hand-s.reserved,0)/100.0 available FROM {T('counter_products')} p LEFT JOIN {T('counter_stock')} s ON s.product_id=p.id AND s.branch='north' ORDER BY p.name")
    query('quotes',f"SELECT id,customer_id,branch,status FROM {T('counter_quotes')} ORDER BY id DESC LIMIT 30")
    query('quote',f"""SELECT q.id,q.version,q.customer_id,jsonb_build_array(q.branch) branch,q.notes,q.status,
      coalesce((SELECT jsonb_object_agg(l.id,jsonb_build_object('quantity',round(l.quantity/100.0,2)::text,'unit_price',round(l.unit_price/100.0,2)::text)) FROM {T('counter_lines')} l WHERE l.quote_id=q.id),'{{}}'::jsonb) lines,
      '$'||to_char(coalesce((SELECT sum(round(l.quantity*l.unit_price/100.0))/100.0 FROM {T('counter_lines')} l WHERE l.quote_id=q.id),0),'FM999,999,990.00') total_label,
      coalesce((SELECT id FROM {T('counter_orders')} WHERE quote_id=q.id),'') order_id
      FROM {T('counter_quotes')} q WHERE q.id=$1""",['/state/selection/quote'],'first')
    query('quoteLines',f"""SELECT l.*,p.name,p.unit, (coalesce(s.on_hand,0)-coalesce(s.reserved,0))/100.0 available,
      '/state/quoteForm/lines/'||l.id||'/quantity' quantity_path, '/state/quoteForm/lines/'||l.id||'/unit_price' price_path
      FROM {T('counter_lines')} l JOIN {T('counter_products')} p ON p.id=l.product_id JOIN {T('counter_quotes')} q ON q.id=l.quote_id
      LEFT JOIN {T('counter_stock')} s ON s.product_id=l.product_id AND s.branch=q.branch WHERE l.quote_id=$1 ORDER BY l.position,l.id""",['/state/selection/quote'])
    query('purchaseOrders',f"SELECT *,supplier||' · '||branch||' · '||expected_date AS detail FROM {T('receiving_orders')} WHERE supplier ILIKE '%'||$1||'%' OR id ILIKE '%'||$1||'%' ORDER BY expected_date,id LIMIT 30",['/state/filter/search'])
    query('po',f"""SELECT p.*,current_date::text received_date,'09:00' received_time,'' reference,'' notes,
      coalesce((SELECT jsonb_object_agg(l.id,jsonb_build_object('quantity','0')) FROM {T('receiving_lines')} l WHERE l.po_id=p.id),'{{}}'::jsonb) lines
      FROM {T('receiving_orders')} p WHERE id=$1""",['/state/selection/po'],'first')
    query('poLines',f"SELECT l.*,p.name,p.unit,round((l.expected-l.received)/100.0,2)::text remaining, '/state/receiptForm/lines/'||l.id||'/quantity' quantity_path FROM {T('receiving_lines')} l JOIN {T('counter_products')} p ON p.id=l.product_id WHERE po_id=$1 ORDER BY l.id",['/state/selection/po'])
    query('equipment',f"SELECT *,model||' · '||serial AS identity FROM {T('counter_equipment')} ORDER BY id")
    query('reports',f"SELECT id,job_id,notes,attachments,coalesce(temperature,'') temperature,coalesce(pressure,'') pressure,coalesce(correction_of,'') correction_of,created||' · '||source AS detail FROM {T('field_reports')} WHERE job_id=$1 ORDER BY created DESC,id LIMIT 20",['/state/selection/job'])
    query('fieldDefaults',"SELECT $1::text AS id,'' AS notes,'' AS temperature,'' AS pressure,'' AS correction_of,'[]'::jsonb AS attachments",['/state/selection/job'],'first')
    query('case',f"""SELECT c.id,c.version,c.title,c.notes,c.handoff,c.visit_id,c.po_id,jsonb_build_array(c.focus) focus,jsonb_build_array(c.role) role,
      CASE WHEN sum(l.backorder)>0 THEN 'waiting' WHEN j.status='Scheduled' THEN 'ready' ELSE lower(j.status) END phase,
      CASE WHEN sum(l.backorder)>0 THEN 'The replacement has not arrived. Receive and allocate stock before scheduling.' ELSE 'Parts are reserved. Review the return visit and handoff.' END reason,
      e.model||' · '||e.serial||E'\\n'||e.service_notes equipment,
      coalesce((SELECT string_agg(h.scheduled_date||' · '||h.notes,E'\\n' ORDER BY h.scheduled_date DESC) FROM (SELECT * FROM {T('hvac_jobs')} WHERE customer=j.customer AND id<>j.id ORDER BY scheduled_date DESC LIMIT 4) h),'No previous visits') history,
      string_agg(p.name||': '||round(l.reserved/100.0,2)::text||' reserved / '||round(l.backorder/100.0,2)::text||' waiting',E'\\n') parts
      FROM {T('work_cases')} c JOIN {T('hvac_jobs')} j ON j.id=c.visit_id JOIN {T('counter_orders')} o ON o.visit_id=j.id
      JOIN {T('counter_order_lines')} l ON l.order_id=o.id JOIN {T('counter_products')} p ON p.id=l.product_id
      JOIN {T('counter_quote_equipment')} qe ON qe.quote_id=o.quote_id JOIN {T('counter_equipment')} e ON e.id=qe.equipment_id
      WHERE c.id='CASE-HARBOR' GROUP BY c.id,j.id,e.id""",shape='first')
    query('activity',f"""WITH combined AS (SELECT 'j-'||id::text id,description,created FROM {T('hvac_activity')} WHERE job_id=$1
      UNION ALL SELECT 'c-'||id::text,description,created FROM {T('work_case_activity')} WHERE case_id='CASE-HARBOR'
      UNION ALL SELECT 'p-'||id::text,description,created FROM {T('receiving_activity')} WHERE po_id=$2) SELECT * FROM combined
      ORDER BY created DESC,id LIMIT 12""",['/state/selection/job','/state/selection/po'])
    query('decision',"""SELECT CASE WHEN $1::text NOT IN ('adaptive','case') THEN jsonb_build_object('label','clarify','status','idle','steps',0)
      WHEN $2::text<>'auto' THEN jsonb_build_object('label',$2::text,'status','held','steps',0)
      ELSE rvbbit.decision_tree(CASE WHEN $1='case' THEN jsonb_build_object('facts',$3::jsonb || jsonb_build_object('readiness',$3::jsonb->>'phase','current_readiness_explanation',$3::jsonb->>'reason'),'notes',$4::text) ELSE jsonb_build_object('job',$5::jsonb,'notes',$6::text) END,
      CASE WHEN $1='case' THEN $7::jsonb ELSE $8::jsonb END,4) END AS decision""",
      ['/state/page','/state/controls/focus/0','/case','/state/caseForm/notes','/job','/state/jobForm/notes','/policies/case','/policies/adaptive'],
      'value',dependsOn=['job','case'],cacheMs=30000,timeoutMs=8000)
    for form,source in [('dealForm','deal'),('jobForm','job'),('quoteForm','quote'),('receiptForm','po'),('scheduleForm','job'),('fieldForm','fieldDefaults'),('caseForm','case')]:
        doc['forms'].append({'path':'/state/'+form,'source':'/'+source,'key':'id'})
        doc['data']['state'][form]=copy.deepcopy(doc['data'][source])
    for page in PAGES:
        setting={'/state/page':page,'/state/controls/review':''}
        if page=='case':setting.update({'/state/selection/job':'HV-CASE-01','/state/selection/po':'PO-CASE-01'})
        E['page_'+page]={'set':setting}
    for kind in ('deal','job','quote','po'):
        E['select_'+kind]={'set':{'/state/selection/'+kind:bind('/context/id'),'/state/controls/review':''}}
    E['discard']={'reset':['/state/'+k for k in ('dealForm','jobForm','quoteForm','receiptForm','scheduleForm','fieldForm','caseForm')], 'set':{'/state/controls/review':''}}
    E['chart_status']={'set':{'/state/filter/status':[bind('/context/datum/status')]}}
    def action(name, description, fields, steps, context=None, reset=()):
        inputs={k:({'type':v} if isinstance(v,str) else v) for k,v in fields.items()}
        A[name]={'description':description,'inputs':inputs,'steps':[{'sql':s,'params':p,**({'expect_rows':n} if n is not None else {})} for s,p,n in steps]}
        E[name]={'action':name,'inputs':context or {k:bind('/context/'+k) for k in fields},'reset':['/state/'+k for k in reset]}
    # Explicit typed SQL commands, including optimistic record versions.
    action('save_deal','Save transaction working notes',{'id':'string','version':'integer','notes':{'type':'string','max_length':4000},'owner':'string','date':'string'},[(f"UPDATE {T('deals')} SET notes=$3,owner=$4,closing_date=($5::date)::text,version=version+1 WHERE id=$1 AND version=$2 AND $4 IN ('Alex Morgan','Sam Rivera') RETURNING id",['id','version','notes','owner','date'],1)],reset=['dealForm'])
    action('advance_deal','Advance transaction stage',{'id':'string','version':'integer'},[(f"UPDATE {T('deals')} SET stage=CASE stage WHEN 'Offer' THEN 'Due diligence' WHEN 'Due diligence' THEN 'Closing' WHEN 'Closing' THEN 'Closed' END,version=version+1 WHERE id=$1 AND version=$2 AND stage<>'Closed' RETURNING id",['id','version'],1)],reset=['dealForm'])
    action('complete_task','Complete a transaction commitment',{'id':'string','version':'integer','task':'string'},[(f"UPDATE {T('deals')} SET version=version+1 WHERE id=$1 AND version=$2 RETURNING id",['id','version'],1),(f"UPDATE {T('tasks')} SET done=1 WHERE id=$1 AND deal_id=$2 AND done=0 RETURNING id",['task','id'],1)],reset=['dealForm'])
    action('save_job','Save visit notes',{'id':'string','version':'integer','notes':{'type':'string','max_length':4000}},[(f"UPDATE {T('hvac_jobs')} SET notes=$3,version=version+1 WHERE id=$1 AND version=$2 RETURNING id",['id','version','notes'],1)],reset=['jobForm','scheduleForm'])
    action('advance_job','Advance visit status when parts are ready',{'id':'string','version':'integer'},[(f"UPDATE {T('hvac_jobs')} j SET status=CASE status WHEN 'Scheduled' THEN 'Dispatched' WHEN 'Dispatched' THEN 'On site' WHEN 'On site' THEN 'Complete' END,version=version+1 WHERE id=$1 AND version=$2 AND status<>'Complete' AND NOT EXISTS(SELECT 1 FROM {T('counter_orders')} o JOIN {T('counter_order_lines')} l ON l.order_id=o.id WHERE o.visit_id=j.id AND l.backorder>0) RETURNING id",['id','version'],1)],reset=['jobForm','scheduleForm'])
    action('save_case','Save shared case notes and handoff',{'id':'string','version':'integer','notes':{'type':'string','max_length':4000},'handoff':{'type':'string','max_length':4000}},[(f"UPDATE {T('work_cases')} SET notes=$3,handoff=$4,version=version+1 WHERE id=$1 AND version=$2 RETURNING id",['id','version','notes','handoff'],1)],reset=['caseForm'])
    from commands import add_commands
    add_commands(doc,A,action,T)
    from pages import add_pages
    add_pages(doc,PAGES,TECHS)
    from charts import add_charts
    add_charts(doc,query,T)
    from design import compose
    compose(doc)
    from interactions import compose as compose_interactions
    compose_interactions(doc)
    from record_views import compose as compose_records
    compose_records(doc)
    # Every field has an addressable, initially empty error slot.
    for node in doc['components']+[n for f in doc['fragments'].values() for n in f['components']]:
        if node['component']=='CField':doc['data']['errors'][node['id']]=''
    return doc,{'actions':A}

if __name__=='__main__':
    doc,manifest=make()
    import ui_apps
    ui_apps.ROOT=ROOT/'apps/calliope-canvas/business'
    for page,name in PAGES.items():
        instance=copy.deepcopy(doc);instance['title']=name;instance['data']['state']['page']=page
        if page=='case':instance['data']['state']['selection'].update(job='HV-CASE-01',po='PO-CASE-01')
        ui_apps.validate(instance);ui_apps.validate_manifest(instance,manifest)
        (HERE/(page+'.json')).write_text(json.dumps(instance,indent=2,ensure_ascii=False)+'\n')
    (HERE/'workspace.json').write_text(json.dumps(doc,indent=2,ensure_ascii=False)+'\n')
    (HERE/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
