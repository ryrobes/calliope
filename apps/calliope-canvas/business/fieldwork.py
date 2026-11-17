"""Append-only field reports. Queue delivery is idempotent; it never changes job status."""
import hashlib
import json
import uuid
from contextlib import nullcontext
import attachments
from forms import checked, FieldErrors
from ui import UI, bind


def seed(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS field_reports (
      id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES hvac_jobs(id), request_id TEXT NOT NULL UNIQUE,
      fingerprint TEXT NOT NULL, notes TEXT NOT NULL, temperature TEXT NOT NULL, pressure TEXT NOT NULL,
      correction_of TEXT REFERENCES field_reports(id), source TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
      CREATE TABLE IF NOT EXISTS field_evidence (
      report_id TEXT NOT NULL REFERENCES field_reports(id), attachment_id TEXT NOT NULL REFERENCES business_attachments(id),
      PRIMARY KEY(report_id,attachment_id));''')


def validate(d):
    checked({'field-notes':(d.get('notes'),{'required':True,'max_length':4000}),
      'field-temp':(d.get('temperature',''),{'kind':'quantity','minimum':-50,'maximum':150,'precision':1}),
      'field-pressure':(d.get('pressure',''),{'kind':'quantity','minimum':0,'maximum':1000,'precision':1})})


def submit(store,body):
    try:
        d=body.get('draft');files=body.get('files',[]);request=body.get('requestId')
        if not isinstance(d,dict) or not isinstance(files,list) or len(files)>3:raise ValueError('Use a report and at most three evidence files.')
        if not isinstance(request,str) or not 8<=len(request)<=100:raise ValueError('A stable submission ID is required.')
        if not isinstance(d.get('id'),str) or not isinstance(d.get('correction_of',''),str):raise ValueError('Use a valid visit and correction reference.')
        ids=d.get('attachments',[])
        if not isinstance(ids,list) or len(ids)>3 or not all(isinstance(x,str) and x.startswith('LOCAL-') for x in ids) or len(set(ids))!=len(ids):raise ValueError('Use distinct local evidence files.')
        validate(d)
        if len(json.dumps(body))>9*1024*1024:raise ValueError('Submission is too large.')
        validated=[]
        for f in files:
            content=attachments.validate(store,f)
            if len(content)>2*1024*1024:raise ValueError('Field evidence must be at most 2 MB per file.')
            validated.append((f,content))
        if len(d.get('attachments',[]))!=len(files):raise ValueError('Queued evidence is incomplete. Keep the report and retry.')
        fingerprint=hashlib.sha256(json.dumps([d,files],sort_keys=True).encode()).hexdigest()
        with store.lock,store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT * FROM field_reports WHERE request_id=?',(request,)).fetchone()
            if prior:
                if prior['fingerprint']!=fingerprint:raise store.rejection('Submission ID already belongs to another report.',409)
                return {'id':prior['id'],'duplicate':True,'revision':store.revision(db)}
            if not db.execute('SELECT 1 FROM hvac_jobs WHERE id=?',(d.get('id'),)).fetchone():raise store.rejection('Visit no longer exists. Report retained for review.',409)
            correction=d.get('correction_of') or None
            if correction and not db.execute('SELECT 1 FROM field_reports WHERE id=? AND job_id=?',(correction,d['id'])).fetchone():raise ValueError('A correction must refer to a report on this visit.')
            ident='FR-'+uuid.uuid4().hex[:10].upper();source='Desktop' if body.get('source')=='Desktop' else 'Web'
            db.execute('INSERT INTO field_reports(id,job_id,request_id,fingerprint,notes,temperature,pressure,correction_of,source) VALUES (?,?,?,?,?,?,?,?,?)',(ident,d['id'],request,fingerprint,d['notes'],d.get('temperature',''),d.get('pressure',''),correction,source))
            for f,content in validated:
                aid='ATT-'+uuid.uuid4().hex
                db.execute('INSERT INTO business_attachments(id,name,mime,size,content) VALUES (?,?,?,?,?)',(aid,f['name'],f['mime'],len(content),content))
                db.execute('INSERT INTO field_evidence VALUES (?,?)',(ident,aid))
            db.execute('INSERT INTO hvac_activity(job_id,description,source) VALUES (?,?,?)',(d['id'],('Correction recorded: ' if correction else 'Field report received: ')+ident,source))
            return {'id':ident,'duplicate':False,'revision':store.revision(db)}
    except FieldErrors as e:raise store.rejection(str(e),422,e.fields) from e
    except (ValueError,TypeError) as e:
        if isinstance(e,store.rejection):raise
        raise store.rejection(str(e)) from e


def snapshot(store,selected,notice='',_db=None):
    from presentation import SPEC,validate_components
    with (nullcontext(_db) if _db is not None else store.connect()) as db:
        jobs=[dict(r) for r in db.execute("SELECT * FROM hvac_jobs ORDER BY (status='Complete'),scheduled_date DESC,id LIMIT 100")]
        job=next((j for j in jobs if j['id']==selected),jobs[0])
        reports=[dict(r) for r in db.execute('SELECT * FROM field_reports WHERE job_id=? ORDER BY rowid DESC LIMIT 10',(job['id'],))]
        for r in reports:r['files']=[dict(a) for a in db.execute('SELECT a.id,a.name FROM business_attachments a JOIN field_evidence f ON f.attachment_id=a.id WHERE f.report_id=?',(r['id'],))]
        revision=store.revision(db)
    draft={'id':job['id'],'version':1,'notes':'','temperature':'','pressure':'','attachments':[],'correction_of':''}
    u=UI();nav=u.nav('field')
    choose=u.stack('field-choose',[u.picker('field-visit','Visit','/field_controls/visit',[(j['id'],j['customer']+' · '+j['id']) for j in jobs]),u.button('field-open','Open visit','field_select',{'visit':bind('/field_controls/visit')})],'row','sm','phone')
    hero=u.panel('field-hero',[u.text('field-kicker','NORTHSTAR SERVICE / FIELD NOTEBOOK','eyebrow','cool'),u.text('field-heading','Leave a useful record.','display',family='display'),u.text('field-customer',job['customer']+' · '+job['address']),u.badge('field-status',job['status'],'cool'),u.text('field-equipment',job['equipment'],'caption','muted'),u.record('field-inspect','Inspect visit ↗','visit',job['id'])],'cool','wash')
    editor=[u.field('field-notes','Work performed / findings','/draft/notes','longText',required=True,maxLength=4000),u.stack('field-readings',[u.field('field-temp','Supply temperature','/draft/temperature','quantity',unit='°C',precision=1,minimum='-50',maximum='150',weight=1),u.field('field-pressure','Pressure reading','/draft/pressure','quantity',unit='psi',precision=1,minimum='0',maximum='1000',weight=1)],'row','sm','phone'),
      u.add('field-evidence','CAttachments',label='Field photos / documents',value=bind('/draft/attachments'),names=bind('/attachment_names'),offline=True,pending=bind('/evidence_busy')),
      u.field('field-correction','Corrects report ID (optional)','/draft/correction_of',help='Adds a linked correction; the original report remains intact.'),
      u.button('field-queue','Save to device & send when online','field_queue',{'draft':bind('/draft')},True),u.button('field-discard','Discard draft','discard'),u.text('field-note','The device queue keeps the report and evidence until acknowledged. This records findings; it does not complete, dispatch or bill the visit.','caption','muted')]
    history=[u.text('field-history-title','Visit evidence','title',family='display')]
    for r in reports:
        p='field-report-'+r['id'];items=[u.badge(p+'id',r['id'],'success'),u.text(p+'date',r['created']+' · '+r['source'],'caption','muted'),u.text(p+'notes',r['notes'])]
        if r['correction_of']:items.append(u.text(p+'correction','Corrects '+r['correction_of'],'caption','warm'))
        if r['temperature'] or r['pressure']:items.append(u.text(p+'readings',(r['temperature']+' °C' if r['temperature'] else '')+' / '+(r['pressure']+' psi' if r['pressure'] else ''),'caption'))
        items.extend(u.button(p+'file'+str(i),'Open '+a['name'],'open_attachment',{'attachmentId':a['id']}) for i,a in enumerate(r['files']))
        history.append(u.panel(p,items))
    if not reports:history.append(u.text('field-empty','Reports appear here after delivery. Pending reports remain in your device queue.','body','muted'))
    u.stack('root',[nav,choose,hero,u.stack('field-work',[u.panel('field-editor',editor,weight=1),u.stack('field-history',history,weight=1)],'row','lg','tablet')],gap='lg')
    validate_components(u.nodes)
    data={'draft':draft,'field_controls':{'visit':[job['id']]},'attachment_names':{},'evidence_busy':False,'reports':reports,'errors':{}}
    return {'messages':[{'version':'v0.9','createSurface':{'surfaceId':'transaction-desk','catalogId':SPEC['id']}},{'version':'v0.9','updateComponents':{'surfaceId':'transaction-desk','components':u.nodes}},{'version':'v0.9','updateDataModel':{'surfaceId':'transaction-desk','path':'/','value':data}}],'draft':draft,'selected':job['id'],'view':'field','owner':'Everyone','revision':revision,'notice':notice}


def action(store,body,name,context,selected):
    if name=='field_select':
        v=context.get('visit');selected=v[0] if isinstance(v,list) and len(v)==1 else selected
    if name not in ('set_view','refresh','discard','field_select'):raise store.rejection('Unknown field action.')
    return snapshot(store,selected)
