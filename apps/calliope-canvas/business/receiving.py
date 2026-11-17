"""Local purchase receipts and deterministic oldest-order-first backorder allocation."""
import datetime as dt
import hashlib
import json
import uuid
from contextlib import nullcontext
from counter import BRANCHES,quantity
from forms import checked,cents,FieldErrors

SORTS={'expected':'p.expected_date,p.id','supplier':'p.supplier,p.expected_date,p.id','branch':'p.branch,p.expected_date,p.id'}
PAGE_SIZE=5

def seed(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS receiving_orders (
        id TEXT PRIMARY KEY,supplier TEXT NOT NULL,branch TEXT NOT NULL,
        expected_date TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1);
      CREATE TABLE IF NOT EXISTS receiving_lines (
        id TEXT PRIMARY KEY,po_id TEXT NOT NULL REFERENCES receiving_orders(id),
        product_id TEXT NOT NULL REFERENCES counter_products(id),expected INTEGER NOT NULL CHECK(expected>0),
        received INTEGER NOT NULL DEFAULT 0 CHECK(received>=0 AND received<=expected));
      CREATE TABLE IF NOT EXISTS receiving_receipts (
        id TEXT PRIMARY KEY,po_id TEXT NOT NULL REFERENCES receiving_orders(id),received_date TEXT NOT NULL,
        received_time TEXT NOT NULL,reference TEXT NOT NULL,notes TEXT NOT NULL,has_damage INTEGER NOT NULL,
        damage_notes TEXT NOT NULL,source TEXT NOT NULL,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
      CREATE TABLE IF NOT EXISTS receiving_receipt_lines (
        receipt_id TEXT NOT NULL REFERENCES receiving_receipts(id),line_id TEXT NOT NULL REFERENCES receiving_lines(id),
        quantity INTEGER NOT NULL CHECK(quantity>0),PRIMARY KEY(receipt_id,line_id));
      CREATE TABLE IF NOT EXISTS receiving_allocations (
        receipt_id TEXT NOT NULL REFERENCES receiving_receipts(id),order_id TEXT NOT NULL REFERENCES counter_orders(id),
        product_id TEXT NOT NULL REFERENCES counter_products(id),quantity INTEGER NOT NULL CHECK(quantity>0),
        PRIMARY KEY(receipt_id,order_id,product_id));
      CREATE TABLE IF NOT EXISTS receiving_activity (
        id INTEGER PRIMARY KEY,po_id TEXT NOT NULL,description TEXT NOT NULL,source TEXT NOT NULL,
        created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
      CREATE TABLE IF NOT EXISTS business_attachments (
        id TEXT PRIMARY KEY,name TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,
        content BLOB NOT NULL,receipt_id TEXT REFERENCES receiving_receipts(id),
        created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    ''')
    if db.execute('SELECT 1 FROM receiving_orders LIMIT 1').fetchone():return
    for i in range(8):
        ident=f'PO-{2001+i}';branch='north' if i%2==0 else 'south'
        db.execute('INSERT INTO receiving_orders(id,supplier,branch,expected_date) VALUES (?,?,?,?)',(ident,['Summit Parts Co.','Valley Mechanical Supply','Pine Air Distribution'][i%3],branch,(dt.date.today()+dt.timedelta(days=i//2)).isoformat()))
        for j,(sku,expected) in enumerate([('CAP-45-5',1000),('MOTOR-ECM',300),('FILTER-20',2000),('TUBE-38',10000)]):
            db.execute('INSERT INTO receiving_lines(id,po_id,product_id,expected) VALUES (?,?,?,?)',(ident+'-'+str(j),ident,sku,expected))
    db.execute("INSERT INTO receiving_activity(po_id,description,source) VALUES ('PO-2001','Fictional inbound orders opened','Demo')")

def filters(state):
    state=state if isinstance(state,dict) else {}
    search=state.get('search','');branch=state.get('branch','all');status=state.get('status','open');sort=state.get('sort','expected');page=state.get('page',0)
    if not isinstance(search,str) or len(search)>100 or branch not in ('all',*BRANCHES) or status not in ('all','open','received') or sort not in SORTS or type(page) is not int or not 0<=page<=100000:raise ValueError('Choose a valid receiving filter or page.')
    return {'search':search,'branch':branch,'status':status,'sort':sort,'page':page}

def queue(db,state):
    where="(?='all' OR p.branch=?) AND (p.id LIKE ? ESCAPE '\\' OR p.supplier LIKE ? ESCAPE '\\') AND (?='all' OR (?='open' AND EXISTS(SELECT 1 FROM receiving_lines l WHERE l.po_id=p.id AND l.received<l.expected)) OR (?='received' AND NOT EXISTS(SELECT 1 FROM receiving_lines l WHERE l.po_id=p.id AND l.received<l.expected)))"
    term='%'+state['search'].replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    args=(state['branch'],state['branch'],term,term,state['status'],state['status'],state['status'])
    count=db.execute('SELECT COUNT(*) FROM receiving_orders p WHERE '+where,args).fetchone()[0]
    state['page']=min(state['page'],max(0,(count-1)//PAGE_SIZE))
    rows=[dict(r) for r in db.execute("SELECT p.*,(SELECT SUM(received) FROM receiving_lines l WHERE l.po_id=p.id) AS received,(SELECT SUM(expected-received) FROM receiving_lines l WHERE l.po_id=p.id) AS remaining FROM receiving_orders p WHERE "+where+' ORDER BY '+SORTS[state['sort']]+' LIMIT ? OFFSET ?',(*args,PAGE_SIZE,state['page']*PAGE_SIZE))]
    return rows,count

def lines(db,id):return [dict(r) for r in db.execute('SELECT l.*,p.name,p.unit,s.on_hand-s.reserved AS available FROM receiving_lines l JOIN counter_products p ON p.id=l.product_id JOIN receiving_orders o ON o.id=l.po_id JOIN counter_stock s ON s.product_id=l.product_id AND s.branch=o.branch WHERE l.po_id=? ORDER BY l.id',(id,))]

def readiness(db,visit_id):
    row=db.execute('SELECT o.id,SUM(l.backorder) AS shortage FROM counter_orders o JOIN counter_order_lines l ON l.order_id=o.id WHERE o.visit_id=? GROUP BY o.id',(visit_id,)).fetchone()
    return dict(row) if row else None

def snapshot(store,selected,state=None,notice='',_db=None):
    from presentation import SPEC,validate_components
    from receiving_ui import definition
    state=filters(state)
    with (nullcontext(_db) if _db is not None else store.connect()) as db:
        orders,total=queue(db,state)
        po=db.execute('SELECT * FROM receiving_orders WHERE id=?',(selected,)).fetchone()
        if not po:
            if selected and selected.startswith('PO-'):raise ValueError('Purchase order no longer exists.')
            po=db.execute('SELECT * FROM receiving_orders ORDER BY expected_date,id LIMIT 1').fetchone()
        po=dict(po);items=lines(db,po['id'])
        receipts=[dict(r) for r in db.execute('SELECT * FROM receiving_receipts WHERE po_id=? ORDER BY created DESC,rowid DESC LIMIT 5',(po['id'],))]
        pending=[dict(r) for r in db.execute('SELECT o.id,o.quote_id,o.visit_id,ol.product_id,ol.backorder,q.customer_id FROM counter_orders o JOIN counter_order_lines ol ON ol.order_id=o.id JOIN counter_quotes q ON q.id=o.quote_id WHERE o.branch=? AND ol.backorder>0 AND ol.product_id IN(SELECT product_id FROM receiving_lines WHERE po_id=?) ORDER BY o.created,o.id',(po['branch'],po['id']))]
        log=[dict(r) for r in db.execute('SELECT * FROM receiving_activity WHERE po_id=? ORDER BY id DESC LIMIT 5',(po['id'],))]
        attachment_names={r['id']:r['name'] for r in db.execute('SELECT id,name FROM business_attachments')}
        revision=store.revision(db)
    draft={'id':po['id'],'version':po['version'],'received_date':dt.date.today().isoformat(),'received_time':'09:00','reference':'','notes':'','has_damage':False,'damage_notes':'','attachments':[], 'lines':{r['id']:{'quantity':'0'} for r in items}}
    data={'draft':draft,'receiving_filter':{'search':state['search'],'branch':[state['branch']],'status':[state['status']],'sort':[state['sort']]},'purchase_order':po,'receiving_lines':items,'receipts':receipts,'pending':pending,'errors':{},'attachment_names':attachment_names}
    components=definition(po,orders,total,state,items,receipts,pending,log)
    validate_components(components)
    return {'messages':[{'version':'v0.9','createSurface':{'surfaceId':'transaction-desk','catalogId':SPEC['id']}},{'version':'v0.9','updateComponents':{'surfaceId':'transaction-desk','components':components}},{'version':'v0.9','updateDataModel':{'surfaceId':'transaction-desk','path':'/','value':data}}], 'selected':po['id'],'owner':'Everyone','view':'receiving','state':state,'revision':revision,'draft':draft,'notice':notice,'application':'calliope-transaction-desk'}

def allocate(db,receipt_id,branch,sku):
    available=db.execute('SELECT on_hand-reserved FROM counter_stock WHERE branch=? AND product_id=?',(branch,sku)).fetchone()[0]
    affected=set()
    for row in db.execute('SELECT o.id,o.quote_id,o.visit_id,l.backorder FROM counter_orders o JOIN counter_order_lines l ON l.order_id=o.id WHERE o.branch=? AND l.product_id=? AND l.backorder>0 ORDER BY o.created,o.id',(branch,sku)).fetchall():
        amount=min(available,row['backorder'])
        if not amount:break
        db.execute('UPDATE counter_order_lines SET reserved=reserved+?,backorder=backorder-? WHERE order_id=? AND product_id=?',(amount,amount,row['id'],sku))
        db.execute('UPDATE counter_stock SET reserved=reserved+? WHERE product_id=? AND branch=?',(amount,sku,branch))
        db.execute('INSERT INTO receiving_allocations VALUES (?,?,?,?) ON CONFLICT(receipt_id,order_id,product_id) DO UPDATE SET quantity=quantity+excluded.quantity',(receipt_id,row['id'],sku,amount))
        db.execute('UPDATE counter_quotes SET version=version+1 WHERE id=?',(row['quote_id'],))
        description=f'{receipt_id}: allocated {quantity(amount)} of {sku} to {row["id"]}'
        db.execute('INSERT INTO counter_activity(quote_id,description,source) VALUES (?,?,?)',(row['quote_id'],description,'Receiving'))
        if row['visit_id']:db.execute('INSERT INTO hvac_activity(job_id,description,source) VALUES (?,?,?)',(row['visit_id'],description,'Receiving'))
        affected.add(row['id']);available-=amount
    return affected

def action(store,body,name,context,selected,_db=None):
    Rejected=store.rejection
    try:state=filters(body.get('state'))
    except ValueError as e:raise Rejected(str(e)) from e
    if name in ('set_view','refresh','discard','receiving_select','receiving_filter','receiving_page'):
        if name=='receiving_filter':
            try:
                state=filters({k:context.get(k) if k=='search' else context.get(k,[None])[0] for k in ('search','branch','status','sort')})
            except (ValueError,IndexError,TypeError) as e:raise Rejected('Choose valid receiving filters.') from e
        if name=='receiving_page':
            state['page']=context.get('page');state=filters(state)
        with store.lock:return snapshot(store,selected,state)
    if name!='receiving_post':raise Rejected('Unknown receiving action.')
    rid=body.get('requestId')
    if not isinstance(rid,str) or not 8<=len(rid)<=100:raise Rejected('A request ID is required.')
    fingerprint=hashlib.sha256(json.dumps(['receiving',name,selected,context],sort_keys=True).encode()).hexdigest()
    with store.lock,(nullcontext(_db) if _db is not None else store.connect()) as db:
        if _db is None: db.execute('BEGIN IMMEDIATE')
        previous=db.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
        if previous:
            if previous['fingerprint']!=fingerprint:raise Rejected('Request ID already used for another change.',409)
            return json.loads(previous['result'])
        po=db.execute('SELECT * FROM receiving_orders WHERE id=?',(selected,)).fetchone();draft=context.get('draft')
        if not po:raise Rejected('Purchase order not found.',404)
        if not isinstance(draft,dict) or draft.get('id')!=selected:raise Rejected('Draft does not match this purchase order.')
        if type(draft.get('version')) is not int or draft['version']!=po['version']:raise Rejected('This purchase order changed. Review the latest quantities before posting.',409)
        items=lines(db,selected)
        if not isinstance(draft.get('lines'),dict) or set(draft['lines'])!={r['id'] for r in items}:raise Rejected('Purchase-order lines changed; reload and reconcile.')
        rules={'receipt-date':(draft.get('received_date'),{'kind':'date','required':True,'minimum':(dt.date.today()-dt.timedelta(days=365)).isoformat(),'maximum':dt.date.today().isoformat()}),'receipt-time':(draft.get('received_time'),{'kind':'time','required':True}),'receipt-reference':(draft.get('reference'),{'required':True,'max_length':100}),'receipt-notes':(draft.get('notes'),{'max_length':4000})}
        if type(draft.get('has_damage')) is not bool:raise Rejected('Choose whether this delivery has damage.')
        rules['receipt-damage-notes']=(draft.get('damage_notes'),{'required':draft['has_damage'],'max_length':2000})
        for r in items:
            value=draft['lines'][r['id']]
            rules['receive-'+r['id']]=(value.get('quantity') if isinstance(value,dict) else None,{'kind':'quantity','required':True,'minimum':'0','maximum':quantity(r['expected']-r['received']),'precision':0 if r['unit']=='ea' else 2})
        try:checked(rules)
        except FieldErrors as e:raise Rejected(str(e),422,fields=e.fields) from e
        quantities={r['id']:cents(draft['lines'][r['id']]['quantity']) for r in items}
        if not any(quantities.values()):raise Rejected('Enter at least one usable received quantity.',422,fields={'receive-'+items[0]['id']:'Enter a quantity on at least one line.'})
        attachments=draft.get('attachments',[])
        if not isinstance(attachments,list) or len(attachments)>5 or any(not isinstance(x,str) for x in attachments) or len(set(attachments))!=len(attachments):raise Rejected('Choose at most five distinct delivery attachments.')
        for id in attachments:
            a=db.execute('SELECT receipt_id FROM business_attachments WHERE id=?',(id,)).fetchone()
            if not a or a['receipt_id']:raise Rejected('An attachment is missing or already belongs to a receipt.')
        receipt='RC-'+uuid.uuid4().hex[:8].upper();source='Desktop' if body.get('source')=='Desktop' else 'Web'
        db.execute('INSERT INTO receiving_receipts(id,po_id,received_date,received_time,reference,notes,has_damage,damage_notes,source) VALUES (?,?,?,?,?,?,?,?,?)',(receipt,selected,draft['received_date'],draft['received_time'],draft['reference'].strip(),draft['notes'],draft['has_damage'],draft['damage_notes'] if draft['has_damage'] else '',source))
        affected=set()
        for r in items:
            amount=quantities[r['id']]
            if not amount:continue
            db.execute('UPDATE receiving_lines SET received=received+? WHERE id=?',(amount,r['id']))
            db.execute('INSERT INTO receiving_receipt_lines VALUES (?,?,?)',(receipt,r['id'],amount))
            db.execute('UPDATE counter_stock SET on_hand=on_hand+? WHERE branch=? AND product_id=?',(amount,po['branch'],r['product_id']))
            affected.update(allocate(db,receipt,po['branch'],r['product_id']))
        for id in attachments:db.execute('UPDATE business_attachments SET receipt_id=? WHERE id=?',(receipt,id))
        db.execute('UPDATE receiving_orders SET version=version+1 WHERE id=?',(selected,))
        description=f'Posted {receipt} · {len(affected)} backordered orders replenished'
        db.execute('INSERT INTO receiving_activity(po_id,description,source) VALUES (?,?,?)',(selected,description,source))
        result=snapshot(store,selected,state,description,_db=db)
        db.execute('INSERT INTO requests VALUES (?,?,?)',(rid,fingerprint,json.dumps(result)))
        return result
