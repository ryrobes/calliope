"""Persistent fictional work objects over the existing parts/receiving/dispatch commands.

SQL owns readiness. Semantic decisions only select supporting workspace regions.
All mutations, including delegated commands and their replay receipts, commit together.
"""
import copy
import datetime as dt
import hashlib
import json
from contextlib import nullcontext

import counter
import dispatch
import equipment
import receiving

CASE_ID = 'CASE-HARBOR'
POLICY = 'hvac.case-support@2'
FOCUSES = ('auto', 'equipment', 'parts', 'schedule')
ROLES = ('coordinator', 'technician', 'counter', 'dispatcher')


def seed(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS work_cases (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, visit_id TEXT NOT NULL UNIQUE REFERENCES hvac_jobs(id),
        po_id TEXT NOT NULL REFERENCES receiving_orders(id), notes TEXT NOT NULL, handoff TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL DEFAULT 1, pinned INTEGER NOT NULL DEFAULT 1,
        focus TEXT NOT NULL DEFAULT 'auto', role TEXT NOT NULL DEFAULT 'coordinator', history INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS work_case_activity (
        id INTEGER PRIMARY KEY, case_id TEXT NOT NULL REFERENCES work_cases(id), description TEXT NOT NULL,
        source TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
      CREATE TABLE IF NOT EXISTS work_case_receipt_reviews (
        id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES work_cases(id), draft TEXT NOT NULL,
        expires TEXT NOT NULL, receipt_id TEXT);
    ''')
    if db.execute('SELECT 1 FROM work_cases LIMIT 1').fetchone(): return
    # Dedicated fixtures: no resetting existing quotes, appointments, or stock.
    tomorrow = (dt.date.today()+dt.timedelta(days=1)).isoformat()
    notes = 'Cooling failed again after the last visit. Technician identified the ECM motor; model and rotation checked. Waiting for the replacement before a return visit.'
    db.execute('INSERT INTO hvac_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,1)',
      ('HV-CASE-01','Harbor Dental','8 Harbor View','Sam Rivera','Repair',tomorrow,'Time to confirm','Scheduled',46500,'Rooftop unit · Demo RT90',notes))
    db.execute('INSERT INTO counter_quotes(id,customer_id,branch,notes,status) VALUES (?,?,?,?,?)',
      ('Q-CASE-01','harbor','north',notes,'Ordered'))
    db.execute('INSERT INTO counter_quote_equipment VALUES (?,?)',('Q-CASE-01','EQ-HARBOR-1'))
    price = db.execute("SELECT price_cents FROM counter_products WHERE id='MOTOR-ECM'").fetchone()[0]
    available = db.execute("SELECT on_hand-reserved FROM counter_stock WHERE product_id='MOTOR-ECM' AND branch='north'").fetchone()[0]
    reserved = min(100,available); tax=(price*counter.TAX_BPS+5000)//10000
    db.execute('INSERT INTO counter_lines VALUES (?,?,?,?,?,?)',('Q-CASE-01-0','Q-CASE-01','MOTOR-ECM',100,price,0))
    db.execute('INSERT INTO counter_orders(id,quote_id,branch,subtotal_cents,tax_cents,total_cents,visit_id) VALUES (?,?,?,?,?,?,?)',
      ('SO-CASE-01','Q-CASE-01','north',price,tax,price+tax,'HV-CASE-01'))
    db.execute('INSERT INTO counter_order_lines VALUES (?,?,?,?,?,?,?)',('SO-CASE-01','MOTOR-ECM',100,reserved,100-reserved,price,price))
    db.execute("UPDATE counter_stock SET reserved=reserved+? WHERE product_id='MOTOR-ECM' AND branch='north'",(reserved,))
    db.execute('INSERT INTO receiving_orders(id,supplier,branch,expected_date) VALUES (?,?,?,?)',('PO-CASE-01','Summit Parts Co.','north',tomorrow))
    db.execute('INSERT INTO receiving_lines(id,po_id,product_id,expected) VALUES (?,?,?,?)',('PO-CASE-01-0','PO-CASE-01','MOTOR-ECM',100))
    db.execute('INSERT INTO work_cases(id,title,visit_id,po_id,notes) VALUES (?,?,?,?,?)',(CASE_ID,'Harbor rooftop unit','HV-CASE-01','PO-CASE-01',notes))
    db.execute('INSERT INTO work_case_activity(case_id,description,source) VALUES (?,?,?)',(CASE_ID,'Replacement motor identified; return visit waiting on parts.','Fixture'))


def state(value):
    value=value if isinstance(value,dict) else {}
    mode=value.get('mode','overview')
    if mode not in ('overview','receipt','schedule'): raise ValueError('Unknown case tool.')
    return {'mode':mode,'command':str(value.get('command',''))[:80],'receipt_review':str(value.get('receipt_review',''))[:80]}


def current(db, selected):
    if not selected or selected=='cedar': selected=CASE_ID
    row=db.execute('SELECT * FROM work_cases WHERE id=?',(selected,)).fetchone()
    if not row: raise ValueError('Case no longer exists.')
    return dict(row)


def facts(db, case):
    job=dispatch.current(db,case['visit_id'])
    parts=receiving.readiness(db,job['id'])
    phase='complete' if job['status']=='Complete' else 'released' if job['status']!='Scheduled' else 'waiting' if parts and parts['shortage'] else 'scheduled' if job['start'] else 'ready'
    labels={'waiting':'Waiting for parts','ready':'Ready to schedule','scheduled':'Return visit scheduled','released':'Technician dispatched','complete':'Work complete'}
    reason={'waiting':'The linked order still has unreserved parts. A delivery must be posted and allocated before this return visit can be confirmed.',
      'ready':'Every line on the linked order is reserved. Review a technician and appointment time.',
      'scheduled':f"{job['technician']} · {job['scheduled_date']} · {job['slot']}",
      'released':'The visit has been released to dispatch. Record subsequent findings in the field notebook.',
      'complete':'Service is complete. The case retains its equipment, parts and appointment evidence.'}[phase]
    return {'phase':phase,'label':labels[phase],'reason':reason,'parts':parts,'job':job}


def summaries(store):
    with store.lock,store.connect() as db:
        return [{'id':r['id'],'title':r['title'],'pinned':bool(r['pinned']),**{k:v for k,v in facts(db,dict(r)).items() if k in ('phase','label','reason')}} for r in db.execute('SELECT * FROM work_cases ORDER BY id LIMIT 50')]


def snapshot(store, selected=CASE_ID, selection=None, notice='', _db=None):
    from case_ui import definition
    from reactive import prepare
    selection=state(selection)
    with store.lock,(nullcontext(_db) if _db is not None else store.connect()) as db:
        case=current(db,selected); status=facts(db,case); job=status['job']
        po=dict(db.execute('SELECT * FROM receiving_orders WHERE id=?',(case['po_id'],)).fetchone())
        items=receiving.lines(db,po['id'])
        order=dict(db.execute('SELECT * FROM counter_orders WHERE visit_id=?',(job['id'],)).fetchone())
        parts=[dict(r) for r in db.execute('SELECT l.*,p.name,p.unit FROM counter_order_lines l JOIN counter_products p ON p.id=l.product_id WHERE order_id=?',(order['id'],))]
        allocations=[dict(r) for r in db.execute('SELECT a.*,r.reference,r.created FROM receiving_allocations a JOIN receiving_receipts r ON r.id=a.receipt_id WHERE order_id=? ORDER BY r.created DESC LIMIT 10',(order['id'],))]
        history=[dict(r) for r in db.execute('SELECT id,scheduled_date,notes,status FROM hvac_jobs WHERE customer=? AND id<>? ORDER BY scheduled_date DESC LIMIT 4',(job['customer'],job['id']))]
        log=[dict(r) for r in db.execute('SELECT * FROM work_case_activity WHERE case_id=? ORDER BY id DESC LIMIT 8',(case['id'],))]
        command=db.execute('SELECT * FROM dispatch_commands WHERE id=? AND job_id=?',(selection['command'],job['id'])).fetchone()
        command=dict(command) if command else None
        if command:
            command['before']=json.loads(command['before_state']);command['after']=json.loads(command['after_state'])
            command['valid']=command['version']==job['version'] and not command['committed_version'] and command['expires']>dt.datetime.now(dt.timezone.utc).isoformat()
        review=db.execute('SELECT * FROM work_case_receipt_reviews WHERE id=? AND case_id=?',(selection['receipt_review'],case['id'])).fetchone()
        review=dict(review) if review else None
        if review:
            review['draft']=json.loads(review['draft'])
            review['valid']=not review['receipt_id'] and review['expires']>dt.datetime.now(dt.timezone.utc).isoformat() and review['draft']['version']==po['version']
        receipt={'id':po['id'],'version':po['version'],'received_date':dt.date.today().isoformat(),'received_time':'09:00','reference':'','notes':'','has_damage':False,'damage_notes':'','attachments':[], 'lines':{r['id']:{'quantity':'0'} for r in items}}
        draft={'id':case['id'],'version':case['version'],'notes':case['notes'],'handoff':case['handoff'],'appointment':dispatch.draft(job),'receipt':receipt}
        asset=equipment.for_visit(db,job['id'])
        context={'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),'schema':'calliope.case-context/v1','scope':'fictional-local-business-desk','case':case,'status':status,'equipment':asset,
          'parts':parts,'order':order,'purchase_order':po,'allocations':allocations,'history':history,'policy':POLICY,
          'allowed_actions':(['review_receipt'] if any(r['received']<r['expected'] for r in items) else [])+(['review_return_visit'] if status['phase'] in ('ready','scheduled') else []),
          'provenance':{'readiness':'counter_order_lines.backorder','appointment':'hvac_jobs + dispatch_slots','receipts':'receiving_allocations'},
          'units':{'quantities':'Integer hundredths of the part unit: quantity=100 and unit=ea means one motor.','money':'Integer USD cents.'},
          'authority':'Context for discussion. Drafts and semantic suggestions are not committed commands. Local records are not accessible through Lab SQL.'}
        data={'draft':draft,'case':case,'status':status,'equipment':asset,'parts':parts,'purchase_order':po,'receiving_lines':items,
          'allocations':allocations,'history':history,'command':command,'receipt_review':review,'context':context,'errors':{},
          'evidence':{'readiness':status['phase'],'current_readiness_explanation':status['reason'],'parts':parts,'equipment':asset,'role':case['role']},
          'preferences':{'focus':[case['focus']],'role':[case['role']]},
          'copy':{'equipment':f"{asset['model']} · {asset['serial']}\n{asset['location']}\n{asset['service_notes']}" if asset else job['equipment'],
             'history':'\n\n'.join(r['scheduled_date']+' · '+r['notes'] for r in history) or 'No prior visits.',
             'parts':'\n'.join(f"{p['name']} · {counter.quantity(p['reserved'])} reserved / {counter.quantity(p['backorder'])} waiting" for p in parts),
             'allocation':'\n'.join(r['receipt_id']+' · '+r['reference']+' · '+counter.quantity(r['quantity'])+' allocated' for r in allocations) or 'No receipt has allocated parts to this order yet.'}}
        revision=store.revision(db)
    components,resources=definition(data,selection,log)
    result=prepare({'components':components,'data':data,'reactive':resources})
    result.update(view='case',selected=case['id'],state=selection,revision=revision,notice=notice,owner='Everyone',application='calliope-transaction-desk')
    return result


def action(store,body,name,context,selected):
    try:
        selection=state(body.get('state'))
        if name=='set_view' and not context.get('id'): selected=CASE_ID
        if name=='case_tool': selection=state({'mode':context.get('mode')})
        if name in ('set_view','case_select'):selection=state({})
        if name in ('refresh','set_view','discard','case_select','case_tool'):
            return snapshot(store,selected,selection)
        allowed=('case_save','case_pin','case_preferences','case_history','case_receipt_preview','case_receive_confirm','case_schedule_preview','case_schedule_confirm')
        if name not in allowed:raise ValueError('Unknown case command.')
        request=body.get('requestId')
        if not isinstance(request,str) or not 8<=len(request)<=100: raise ValueError('A request ID is required.')
        fingerprint=hashlib.sha256(json.dumps(['case',selected,name,context],sort_keys=True).encode()).hexdigest()
        with store.lock,store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT * FROM requests WHERE id=?',(request,)).fetchone()
            if previous:
                if previous['fingerprint']!=fingerprint:raise store.rejection('Request ID already used for another change.',409)
                return json.loads(previous['result'])
            case=current(db,selected); status=facts(db,case); ack=[]
            source='Desktop' if body.get('source')=='Desktop' else 'Web'
            if name=='case_save':
                d=context.get('draft',{})
                if d.get('id')!=case['id'] or type(d.get('version')) is not int or d['version']!=case['version']:raise store.rejection('Case notes changed. Refresh and reconcile.',409)
                if any(not isinstance(d.get(k),str) or len(d[k])>4000 for k in ('notes','handoff')):raise ValueError('Notes and handoff must be at most 4,000 characters.')
                db.execute('UPDATE work_cases SET notes=?,handoff=?,version=version+1 WHERE id=?',(d['notes'],d['handoff'],case['id']))
                ack=['/notes','/handoff'];notice='Saved working notes and handoff.'
            elif name in ('case_pin','case_history'):
                field='pinned' if name=='case_pin' else 'history'
                value=context.get('value')
                if type(value) is not bool:raise ValueError('Choose an explicit preference.')
                db.execute(f'UPDATE work_cases SET {field}=? WHERE id=?',(int(value),case['id']))
                notice=('Pinned on your canvas.' if value else 'Removed from your canvas.') if field=='pinned' else 'History preference saved.'
            elif name=='case_preferences':
                focus=counter.one(context.get('focus'),FOCUSES,'workspace focus');role=counter.one(context.get('role'),ROLES,'working perspective')
                db.execute('UPDATE work_cases SET focus=?,role=? WHERE id=?',(focus,role,case['id']))
                notice='Workspace preference saved for this case.'
            elif name in ('case_receipt_preview','case_receive_confirm'):
                d=context.get('draft')
                if not isinstance(d,dict) or d.get('id')!=case['po_id']:raise ValueError('Delivery does not belong to this case.')
                # Preview executes the actual receiving validation under a savepoint,
                # then rolls it back. Confirmation runs the same command transactionally.
                nested={**body,'requestId':hashlib.sha256((request+':receipt').encode()).hexdigest(),'state':None}
                if name=='case_receipt_preview':
                    db.execute('SAVEPOINT receipt_preview')
                    try: receiving.action(store,nested,'receiving_post',{'draft':d},case['po_id'],db)
                    finally:
                        db.execute('ROLLBACK TO receipt_preview');db.execute('RELEASE receipt_preview')
                    token='CR-'+hashlib.sha256(request.encode()).hexdigest()[:32]
                    db.execute('INSERT INTO work_case_receipt_reviews VALUES (?,?,?,?,NULL)',(token,case['id'],json.dumps(d,sort_keys=True),(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=10)).isoformat()))
                    selection.update(mode='receipt',receipt_review=token);notice='Review the delivery; nothing has been posted.'
                else:
                    review=db.execute('SELECT * FROM work_case_receipt_reviews WHERE id=? AND case_id=?',(context.get('review'),case['id'])).fetchone()
                    if not review or review['receipt_id'] or review['expires']<=dt.datetime.now(dt.timezone.utc).isoformat() or json.loads(review['draft'])!=d:raise store.rejection('Delivery review expired or inputs changed. Review again.',409)
                    posted=receiving.action(store,nested,'receiving_post',{'draft':d},case['po_id'],db)
                    receipts=posted['messages'][2]['updateDataModel']['value']['receipts'];receipt=receipts[0]['id']
                    db.execute('UPDATE work_case_receipt_reviews SET receipt_id=? WHERE id=?',(receipt,review['id']))
                    ack=['/receipt'];selection['receipt_review']='';notice=f'Posted {receipt}. '+facts(db,case)['label']+'.'
            else:
                # Recheck shortage on BOTH preview and confirmation, under the write lock.
                if status['phase'] not in ('ready','scheduled'):raise store.rejection('Resolve the parts shortage before scheduling this return visit.',409)
                d=context.get('draft')
                nested={**body,'state':{},'requestId':hashlib.sha256((request+':schedule').encode()).hexdigest()}
                if name=='case_schedule_confirm':
                    cmd=db.execute('SELECT kind FROM dispatch_commands WHERE id=? AND job_id=?',(context.get('command'),case['visit_id'])).fetchone()
                    if not cmd or cmd['kind']!='assign':raise ValueError('Only an appointment review can be confirmed here.')
                result=dispatch.action(store,nested,'dispatch_preview' if name=='case_schedule_preview' else 'dispatch_confirm',{'kind':'assign','draft':d,'command':context.get('command')},case['visit_id'],db)
                selection.update(mode='schedule',command=result['state']['command'])
                notice=result['notice']
                if name=='case_schedule_confirm':ack=['/appointment']
            if name not in ('case_receipt_preview','case_schedule_preview'):
                db.execute('INSERT INTO work_case_activity(case_id,description,source) VALUES (?,?,?)',(case['id'],notice,source))
            result=snapshot(store,case['id'],selection,notice,db)
            result['acknowledgedDraftPaths']=ack
            db.execute('INSERT INTO requests VALUES (?,?,?)',(request,fingerprint,json.dumps(result)))
            return result
    except (ValueError,TypeError,KeyError) as exc:
        if isinstance(exc,store.rejection):raise
        from forms import FieldErrors
        raise store.rejection(str(exc),422 if isinstance(exc,FieldErrors) else 400, getattr(exc,'fields',None)) from exc
