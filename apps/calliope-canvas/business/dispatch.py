"""Resource scheduling with server-owned command previews and guarded corrections."""
import datetime as dt
import hashlib
import json
import re
import uuid
from contextlib import nullcontext
import hvac
import receiving
from forms import checked, FieldErrors
from ui import UI, bind


def seed(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS dispatch_slots (
      job_id TEXT PRIMARY KEY REFERENCES hvac_jobs(id), start TEXT NOT NULL, end TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS dispatch_commands (
      id TEXT PRIMARY KEY, job_id TEXT NOT NULL, kind TEXT NOT NULL, version INTEGER NOT NULL,
      payload TEXT NOT NULL, before_state TEXT NOT NULL, after_state TEXT NOT NULL,
      expires TEXT NOT NULL, committed_version INTEGER, undone INTEGER NOT NULL DEFAULT 0);
      ''')
    for row in db.execute('SELECT id,slot FROM hvac_jobs WHERE id NOT IN (SELECT job_id FROM dispatch_slots)'):
        match = re.fullmatch(r'(\d\d:\d\d)[–-](\d\d:\d\d)', row['slot'])
        if match: db.execute('INSERT INTO dispatch_slots VALUES (?,?,?)', (row['id'], *match.groups()))


def minutes(value):
    h,m = value.split(':'); return int(h)*60+int(m)


def filters(state):
    state=state if isinstance(state,dict) else {}
    date=state.get('date',dt.date.today().isoformat())
    checked({'dispatch-day':(date,{'kind':'date','required':True})})
    return {'date':date, 'command':str(state.get('command',''))[:80]}


def current(db,id):
    row=db.execute('SELECT j.*,s.start,s.end FROM hvac_jobs j LEFT JOIN dispatch_slots s ON s.job_id=j.id WHERE j.id=?',(id,)).fetchone()
    return dict(row) if row else None


def assignment(row):
    return {k:row[k] for k in ('scheduled_date','technician','start','end','slot','status')}


def draft(row):
    return {'id':row['id'],'version':row['version'],'date':row['scheduled_date'],
      'technician':[row['technician']], 'start':row['start'] or '08:00','end':row['end'] or '10:00','reason':''}


def validate_draft(d):
    checked({id:(d.get(key),rules) for id,key,rules in [
      ('dispatch-date','date',{'kind':'date','required':True}),
      ('dispatch-start','start',{'kind':'time','required':True}),
      ('dispatch-end','end',{'kind':'time','required':True}),
      ('dispatch-reason','reason',{'required':True,'max_length':500})]})
    tech=d.get('technician')
    if not isinstance(tech,list) or len(tech)!=1 or tech[0] not in hvac.TECHS[1:]:raise ValueError('Choose a listed technician.')
    if not 360<=minutes(d['start'])<minutes(d['end'])<=1200:raise FieldErrors({'dispatch-end':'Use a positive appointment between 06:00 and 20:00.'})
    day=dt.date.fromisoformat(d['date']);today=dt.date.today()
    if not today<=day<=today+dt.timedelta(days=90):raise FieldErrors({'dispatch-date':'Schedule from today through the next 90 days.'})


def conflicts(db,id,after):
    if not after['start'] or not after['end']:return []
    return [r['id'] for r in db.execute('''SELECT j.id FROM hvac_jobs j JOIN dispatch_slots s ON s.job_id=j.id
      WHERE j.id<>? AND j.scheduled_date=? AND j.technician=? AND j.status<>'Complete'
      AND s.start<? AND s.end>? ORDER BY s.start''',(id,after['scheduled_date'],after['technician'],after['end'],after['start']))]


def snapshot(store,selected,state=None,notice='',_db=None):
    from presentation import SPEC, validate_components
    state=filters(state)
    with (nullcontext(_db) if _db is not None else store.connect()) as db:
        jobs=[dict(r) for r in db.execute('''SELECT j.*,s.start,s.end FROM hvac_jobs j LEFT JOIN dispatch_slots s ON s.job_id=j.id
          WHERE j.scheduled_date=? ORDER BY j.technician,s.start,j.id LIMIT 60''',(state['date'],))]
        job=current(db,selected) or (current(db,jobs[0]['id']) if jobs else current(db,'HV-1001'))
        planning=[dict(r) for r in db.execute("SELECT id,customer,scheduled_date FROM hvac_jobs WHERE status='Scheduled' ORDER BY scheduled_date,id LIMIT 100")]
        command=db.execute('SELECT * FROM dispatch_commands WHERE id=? AND job_id=?',(state['command'],job['id'])).fetchone()
        command=dict(command) if command else None
        if command:
            command['before']=json.loads(command['before_state']);command['after']=json.loads(command['after_state'])
            command['valid']=not command['committed_version'] and command['expires']>dt.datetime.now(dt.timezone.utc).isoformat() and command['version']==job['version']
        last=db.execute('SELECT id FROM dispatch_commands WHERE job_id=? AND kind=\'assign\' AND committed_version=? AND undone=0 ORDER BY rowid DESC LIMIT 1',(job['id'],job['version'])).fetchone()
        parts=receiving.readiness(db,job['id'])
        log=[dict(r) for r in db.execute('SELECT * FROM hvac_activity WHERE job_id=? ORDER BY id DESC LIMIT 5',(job['id'],))]
        revision=store.revision(db)
    data={'draft':draft(job),'dispatch_filter':{'date':state['date']},'planning':{'visit':[job['id'] if any(r['id']==job['id'] for r in planning) else planning[0]['id'] if planning else job['id']]},'job':job,'jobs':jobs,'command':command,'errors':{}}
    u=UI();nav=u.nav('dispatch')
    header=u.panel('dispatch-hero',[u.text('dispatch-kicker','NORTHSTAR SERVICE / DISPATCH','eyebrow','cool'),u.text('dispatch-heading','Make room for the work.','display',family='display'),u.text('dispatch-intro','A shared day plan. Review each change before it becomes the schedule.','body','muted')],'cool','plain')
    controls=u.filters('dispatch-controls','Day plan','/dispatch_filter','dispatch_filter',[u.field('dispatch-day','Schedule date','/dispatch_filter/date','date',required=True,weight=1)])
    planning_controls=u.stack('dispatch-planning',[u.picker('dispatch-pick-visit','Visit to plan','/planning/visit',[(j['id'],j['customer']+' · '+j['scheduled_date']) for j in planning],weight=1),u.button('dispatch-open-visit','Open appointment','dispatch_select',{'visit':bind('/planning/visit')})],'row','sm','phone') if planning else u.text('dispatch-no-planning','No visits waiting to be scheduled.','caption','muted')
    cards=[];positions=[]
    for j in jobs:
        if j['start'] and j['status']!='Complete':
            key='dispatch-slot-'+j['id']
            cards.append(u.stack(key,[u.button(key+'-open',j['customer'],'dispatch_select',{'id':j['id']}),u.text(key+'-time',j['start']+'–'+j['end']+' · '+j['job_type'],'caption'),u.badge(key+'-status',j['status'],'cool')],gap='xs'))
            positions.append({'lane':hvac.TECHS[1:].index(j['technician']),'start':minutes(j['start']),'end':minutes(j['end'])})
    timeline=u.add('dispatch-timeline','CResourceDay',children=cards,resources=list(hvac.TECHS[1:]),positions=positions,start=360,end=1200,label='Technician day plan')
    unplaced=[u.button('unslotted-'+j['id'],j['customer']+' · '+j['slot'],'dispatch_select',{'id':j['id']}) for j in jobs if not j['start'] and j['status']!='Complete']
    d=bind('/draft');editable=job['status']=='Scheduled'
    editor=[u.text('dispatch-customer',job['customer'],'title',family='display'),u.badge('dispatch-status',job['status'],'cool'),u.text('dispatch-address',job['address'],'body','muted'),u.record('dispatch-inspect','Inspect visit ↗','visit',job['id']),u.button('dispatch-field','Open field report ↗','set_view',{'view':'field','id':job['id']}),
      u.field('dispatch-date','Visit date','/draft/date','date',required=True,readOnly=not editable),(u.picker('dispatch-tech','Assigned technician','/draft/technician',[(x,x) for x in hvac.TECHS[1:]]) if editable else u.text('dispatch-tech-saved',job['technician'])),
      u.stack('dispatch-times',[u.field('dispatch-start','Starts','/draft/start','time',required=True,weight=1,readOnly=not editable),u.field('dispatch-end','Ends','/draft/end','time',required=True,weight=1,readOnly=not editable)],'row','sm'),u.field('dispatch-reason','Reason for change','/draft/reason',required=True,maxLength=500,visible=editable),
      *([u.button('dispatch-preview','Review schedule change','dispatch_preview',{'kind':'assign','draft':d},True)] if editable else [u.text('dispatch-locked','Scheduling is locked after dispatch. Open the field notebook to record findings.','body','muted')]),u.button('dispatch-discard','Discard draft','discard')]
    if job['status']=='Scheduled':editor.append(u.button('dispatch-release','Review dispatch release','dispatch_preview',{'kind':'release','draft':d}))
    if last and job['status']=='Scheduled':editor.append(u.button('dispatch-undo','Review undo of last schedule change','dispatch_preview',{'kind':'undo','draft':d,'undo':last['id']}))
    if parts:editor.append(u.badge('dispatch-parts','Parts ready' if not parts['shortage'] else 'Waiting for parts','success' if not parts['shortage'] else 'warm'))
    review=[u.text('review-heading','Before it changes.','title',family='display'),u.text('review-help','Choose a change to see its exact before/after. Conflicting appointments block confirmation. Undo is available only for the latest schedule edit before dispatch.','body','muted')]
    if command:
        review += [u.badge('review-kind',command['kind'].title(),'warm')]
        for key,title in [('before','Currently saved'),('after','After confirmation')]:
            v=command[key];review.append(u.panel('review-'+key,[u.text('review-'+key+'-title',title,'eyebrow','muted'),u.text('review-'+key+'-date',v['scheduled_date']),u.text('review-'+key+'-tech',v['technician']),u.text('review-'+key+'-slot',v['slot']),u.badge('review-'+key+'-status',v['status'],'cool')]))
        if command['valid']:review.append(u.button('dispatch-confirm','Confirm reviewed change','dispatch_confirm',{'command':command['id'],'draft':d},True))
        else:review.append(u.text('review-expired','This preview expired or the record changed. Review again.','body','warm'))
        review.append(u.text('review-effects','Changes the local schedule and work log. No technician notification is sent.','caption','muted'))
    review.append(u.panel('dispatch-history',[u.text('dispatch-history-title','Recent decisions','title',family='display')]+[u.text('dispatch-log-'+str(r['id']),r['source']+' · '+r['description'],'caption','muted') for r in log],treatment='plain'))
    u.stack('root',[nav,header,controls,u.section('dispatch-schedule','The day ahead',[planning_controls,timeline],subtitle='Appointments by technician. Changes are reviewed before they are saved.',tone='cool'),*([u.panel('dispatch-unplaced',[u.text('unplaced-heading','Time to confirm','title')]+unplaced)] if unplaced else []),u.stack('dispatch-work',[u.panel('dispatch-editor',editor,weight=1),u.panel('dispatch-review',review,tone='warm',weight=1)],'row','lg','tablet')],gap='lg')
    validate_components(u.nodes)
    return {'messages':[{'version':'v0.9','createSurface':{'surfaceId':'transaction-desk','catalogId':SPEC['id']}},{'version':'v0.9','updateComponents':{'surfaceId':'transaction-desk','components':u.nodes}},{'version':'v0.9','updateDataModel':{'surfaceId':'transaction-desk','path':'/','value':data}}], 'selected':job['id'],'owner':'Everyone','view':'dispatch','draft':data['draft'],'state':state,'revision':revision,'notice':notice}


def action(store,body,name,context,selected,_db=None):
    try:
        state=filters(body.get('state'))
        if name=='dispatch_filter':state=filters(context)
        if name=='dispatch_select' and isinstance(context.get('visit'),list) and len(context['visit'])==1:selected=context['visit'][0]
        if name in ('dispatch_select','set_view'):
            with store.connect() as db:opened=current(db,selected)
            if opened:state={'date':opened['scheduled_date'],'command':''}
        if name in ('refresh','discard','set_view','dispatch_select','dispatch_filter'):return snapshot(store,selected,state)
        if name not in ('dispatch_preview','dispatch_confirm'):raise store.rejection('Unknown dispatch action.')
        request=body.get('requestId')
        if not isinstance(request,str) or not 8<=len(request)<=100:raise ValueError('A request ID is required.')
        fingerprint=hashlib.sha256(json.dumps(['dispatch',selected,name,context],sort_keys=True).encode()).hexdigest()
        with store.lock,(nullcontext(_db) if _db is not None else store.connect()) as db:
            if _db is None: db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT * FROM requests WHERE id=?',(request,)).fetchone()
            if prior:
                if prior['fingerprint']!=fingerprint:raise store.rejection('Request ID already used for another change.',409)
                return json.loads(prior['result'])
            job=current(db,selected)
            if not job:raise store.rejection('Visit not found.',404)
            d=context.get('draft')
            if not isinstance(d,dict) or d.get('id')!=selected or type(d.get('version')) is not int or d['version']!=job['version']:raise store.rejection('This visit changed. Refresh and review again.',409)
            if name=='dispatch_preview':
                kind=context.get('kind');before=assignment(job);after=before.copy()
                if job['status']!='Scheduled':raise ValueError('Only scheduled visits can be reassigned, released or undone.')
                checked({'dispatch-reason':(d.get('reason'),{'required':True,'max_length':500})})
                if kind=='assign':
                    validate_draft(d);after.update(scheduled_date=d['date'],technician=d['technician'][0],start=d['start'],end=d['end'],slot=d['start']+'–'+d['end'])
                elif kind=='release':
                    if not job['start']:raise ValueError('Assign an appointment time before dispatch.')
                    parts=receiving.readiness(db,selected)
                    if parts and parts['shortage']:raise ValueError('Resolve the parts shortage before dispatch.')
                    after['status']='Dispatched'
                elif kind=='undo':
                    previous=db.execute('SELECT * FROM dispatch_commands WHERE id=? AND job_id=? AND kind=\'assign\' AND committed_version=? AND undone=0',(context.get('undo'),selected,job['version'])).fetchone()
                    if not previous:raise store.rejection('Only the latest unchanged schedule edit can be undone.',409)
                    after=json.loads(previous['before_state'])
                else:raise ValueError('Unknown command kind.')
                overlap=conflicts(db,selected,after)
                if overlap:raise FieldErrors({'dispatch-start':'Conflicts with '+', '.join(overlap)+'. Choose another time or technician.'})
                token='CMD-'+uuid.uuid4().hex
                expires=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=10)).isoformat()
                db.execute('INSERT INTO dispatch_commands(id,job_id,kind,version,payload,before_state,after_state,expires) VALUES (?,?,?,?,?,?,?,?)',(token,selected,kind,job['version'],json.dumps(context,sort_keys=True),json.dumps(before),json.dumps(after),expires))
                state['command']=token;notice='Review the before/after, then confirm.'
            else:
                command=db.execute('SELECT * FROM dispatch_commands WHERE id=? AND job_id=?',(context.get('command'),selected)).fetchone()
                if not command or command['committed_version'] or command['version']!=job['version'] or command['expires']<=dt.datetime.now(dt.timezone.utc).isoformat():raise store.rejection('This preview expired or changed. Review again.',409)
                payload=json.loads(command['payload'])
                if payload['draft']!=d:raise store.rejection('Your inputs changed after preview. Review again.',409)
                after=json.loads(command['after_state'])
                if assignment(job)!=json.loads(command['before_state']) or conflicts(db,selected,after):raise store.rejection('The schedule changed after preview. Review again.',409)
                if command['kind']=='release':
                    parts=receiving.readiness(db,selected)
                    if parts and parts['shortage']:raise store.rejection('Parts availability changed. Review again.',409)
                db.execute('UPDATE hvac_jobs SET scheduled_date=?,technician=?,slot=?,status=?,version=version+1 WHERE id=?',(after['scheduled_date'],after['technician'],after['slot'],after['status'],selected))
                if after['start']:db.execute('INSERT INTO dispatch_slots VALUES (?,?,?) ON CONFLICT(job_id) DO UPDATE SET start=excluded.start,end=excluded.end',(selected,after['start'],after['end']))
                else:db.execute('DELETE FROM dispatch_slots WHERE job_id=?',(selected,))
                db.execute('UPDATE dispatch_commands SET committed_version=? WHERE id=?',(job['version']+1,command['id']))
                if command['kind']=='undo':db.execute('UPDATE dispatch_commands SET undone=1 WHERE id=?',(payload['undo'],))
                source='Desktop' if body.get('source')=='Desktop' else 'Web'
                notice={'assign':'Schedule changed','undo':'Latest schedule change undone','release':'Visit released to dispatch'}[command['kind']]
                db.execute('INSERT INTO hvac_activity(job_id,description,source) VALUES (?,?,?)',(selected,notice+' · '+d['reason'],source))
                state={'date':after['scheduled_date'],'command':''}
            result=snapshot(store,selected,state,notice,db)
            db.execute('INSERT INTO requests VALUES (?,?,?)',(request,fingerprint,json.dumps(result)))
            return result
    except FieldErrors as e:raise store.rejection(str(e),422,e.fields) from e
    except (ValueError,TypeError,KeyError) as e:
        if isinstance(e,store.rejection):raise
        raise store.rejection(str(e)) from e
