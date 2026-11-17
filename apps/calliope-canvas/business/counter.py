"""Fictional HVAC distributor slice: quote -> reservation/order -> service visit.

Money and quantities use integers in SQL; all pricing and stock decisions are
server-authoritative. This module has no supplier, payment or messaging effects.
"""
import datetime as dt
import hashlib
import json
import re
import uuid
import equipment
from forms import checked,FieldErrors
from contextlib import nullcontext
from decimal import Decimal, ROUND_HALF_UP

BRANCHES={'north':'North branch','south':'South depot'}
CUSTOMERS={'cedar':('Cedar House','142 Cedar Lane'),'harbor':('Harbor Dental','8 Harbor View'),'oak':('Oak Apartments','306 Oak Street')}
TAX_BPS=650  # Illustrative fixture rate, not a tax integration.

def seed(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS counter_products (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, unit TEXT NOT NULL, price_cents INTEGER NOT NULL, details TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS counter_stock (
      product_id TEXT NOT NULL REFERENCES counter_products(id), branch TEXT NOT NULL,
      on_hand INTEGER NOT NULL CHECK(on_hand>=0), reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved>=0 AND reserved<=on_hand),
      PRIMARY KEY(product_id,branch));
    CREATE TABLE IF NOT EXISTS counter_quotes (
      id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, branch TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'Draft', version INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS counter_lines (
      id TEXT PRIMARY KEY, quote_id TEXT NOT NULL REFERENCES counter_quotes(id), product_id TEXT NOT NULL REFERENCES counter_products(id),
      quantity INTEGER NOT NULL CHECK(quantity>0), unit_price INTEGER NOT NULL CHECK(unit_price>=0), position INTEGER NOT NULL,
      UNIQUE(quote_id,product_id));
    CREATE TABLE IF NOT EXISTS counter_orders (
      id TEXT PRIMARY KEY, quote_id TEXT NOT NULL UNIQUE REFERENCES counter_quotes(id), branch TEXT NOT NULL,
      subtotal_cents INTEGER NOT NULL, tax_cents INTEGER NOT NULL, total_cents INTEGER NOT NULL,
      visit_id TEXT UNIQUE REFERENCES hvac_jobs(id), created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS counter_order_lines (
      order_id TEXT NOT NULL REFERENCES counter_orders(id), product_id TEXT NOT NULL REFERENCES counter_products(id),
      quantity INTEGER NOT NULL, reserved INTEGER NOT NULL, backorder INTEGER NOT NULL,
      unit_price INTEGER NOT NULL, total_cents INTEGER NOT NULL,
      CHECK(quantity=reserved+backorder AND reserved>=0 AND backorder>=0), PRIMARY KEY(order_id,product_id));
    CREATE TABLE IF NOT EXISTS counter_activity (
      id INTEGER PRIMARY KEY, quote_id TEXT NOT NULL, description TEXT NOT NULL, source TEXT NOT NULL,
      created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    ''')
    if db.execute('SELECT 1 FROM counter_products LIMIT 1').fetchone(): return
    products=[('CAP-45-5','Dual run capacitor · 45/5 µF','ea',2450,'440 V · verify equipment specification before substitution'),('CONTACTOR-2P','Two-pole contactor · 30 A','ea',3900,'24 V coil · two normally open poles'),('FILTER-20','Pleated filter · 20 × 20 × 1','ea',1850,'MERV 8 · replacement filter'),('TSTAT-PRO','Programmable thermostat','ea',12900,'24 V · conventional and heat-pump systems'),('TUBE-38','Copper tubing · 3/8 in','ft',425,'Soft copper · sold by the foot'),('MOTOR-ECM','Replacement ECM motor','ea',38500,'Model/rotation confirmation required'),('HP-3T','Heat pump · 3 ton','ea',245000,'Equipment selection requires a matched system'),('KIT-DRAIN','Condensate drain service kit','ea',3250,'Service fittings and treatment kit')]
    for i,p in enumerate(products):
        db.execute('INSERT INTO counter_products VALUES (?,?,?,?,?)',p)
        for branch,n in [('north',[3,8,24,4,125,0,2,10][i]),('south',[12,20,60,8,220,3,4,20][i])]:
            db.execute('INSERT INTO counter_stock VALUES (?,?,?,0)',(p[0],branch,n*100))
    for ident,customer in [('Q-1042','harbor'),('Q-1043','cedar')]:
        db.execute('INSERT INTO counter_quotes(id,customer_id,branch,notes) VALUES (?,?,?,?)',(ident,customer,'north','Prepare parts for the rooftop service visit.'))
        for i,(sku,qty) in enumerate([('CAP-45-5',400),('CONTACTOR-2P',200),('FILTER-20',300)]):
            price=next(p[3] for p in products if p[0]==sku)
            db.execute('INSERT INTO counter_lines VALUES (?,?,?,?,?,?)',(ident+'-'+str(i),ident,sku,qty,price,i))
        db.execute('INSERT INTO counter_activity(quote_id,description,source) VALUES (?,?,?)',(ident,'Fictional quote opened','Demo'))

def money(cents): return f'${cents/100:,.2f}'
def quantity(hundredths): return f'{hundredths/100:.2f}'.rstrip('0').rstrip('.')
def one(value,allowed,label):
    if not isinstance(value,list) or len(value)!=1 or not isinstance(value[0],str) or value[0] not in allowed:
        raise ValueError(f'Choose a valid {label}.')
    return value[0]
def decimal_input(value,label,max_value,whole=False):
    if not isinstance(value,str) or not re.fullmatch(r'\d{1,7}(?:\.\d{1,2})?',value.strip()):
        raise ValueError(f'{label}: enter a positive number with at most two decimal places.')
    n=Decimal(value.strip())
    if n<0 or n>max_value or (whole and n!=n.to_integral()):
        raise ValueError(f'{label}: choose a valid '+('whole-number quantity.' if whole else 'amount.'))
    return int((n*100).to_integral_value(rounding=ROUND_HALF_UP))
def lines(db,q):
    return [dict(r) for r in db.execute('''SELECT l.*,p.name,p.unit,p.details,s.on_hand,s.reserved,
      s.on_hand-s.reserved AS available, (l.quantity*l.unit_price+50)/100 AS total_cents
      FROM counter_lines l JOIN counter_products p ON p.id=l.product_id
      JOIN counter_stock s ON s.product_id=l.product_id AND s.branch=? WHERE quote_id=? ORDER BY position,id''',(q['branch'],q['id']))]
def totals(items):
    subtotal=sum(r['total_cents'] for r in items); tax=(subtotal*TAX_BPS+5000)//10000
    return {'subtotal':subtotal,'tax':tax,'total':subtotal+tax}

def snapshot(store,selected,notice='',_db=None):
    from presentation import SPEC,validate_components
    with (nullcontext(_db) if _db is not None else store.connect()) as db:
        quotes=[dict(r) for r in db.execute('SELECT * FROM counter_quotes ORDER BY rowid DESC')]
        q=next((r for r in quotes if r['id']==selected),None)
        if q is None:
            if selected and selected.startswith('Q-'): raise ValueError('Quote no longer exists.')
            q=next((r for r in quotes if r['status']=='Draft'),quotes[0])
        assets=[dict(r) for r in db.execute('SELECT * FROM counter_equipment WHERE customer_id=? ORDER BY id',(q['customer_id'],))]
        asset=equipment.for_quote(db,q['id'])
        items=lines(db,q); prices=totals(items)
        products=[dict(r) for r in db.execute('SELECT * FROM counter_products ORDER BY name')]
        stock=[dict(r) for r in db.execute('SELECT product_id,branch,on_hand-reserved AS available FROM counter_stock')]
        order=db.execute('SELECT * FROM counter_orders WHERE quote_id=?',(q['id'],)).fetchone()
        order=dict(order) if order else None
        order_lines=[dict(r) for r in db.execute('SELECT ol.*,p.name,p.unit FROM counter_order_lines ol JOIN counter_products p ON p.id=ol.product_id WHERE order_id=?',(order['id'],))] if order else []
        visit=dict(db.execute('SELECT * FROM hvac_jobs WHERE id=?',(order['visit_id'],)).fetchone()) if order and order['visit_id'] else None
        log=[dict(r) for r in db.execute('SELECT * FROM counter_activity WHERE quote_id=? ORDER BY id DESC LIMIT 5',(q['id'],))]
        revision=store.revision(db)
    draft={'id':q['id'],'version':q['version'],'branch':[q['branch']],'equipment':[asset['id']] if asset else [],'notes':q['notes'],
      'lines':{r['id']:{'product_id':r['product_id'],'quantity':quantity(r['quantity']),'unit_price':f'{r["unit_price"]/100:.2f}'} for r in items},
      'visit_date':(dt.date.today()+dt.timedelta(days=1)).isoformat(),'technician':['Alex Morgan']}
    data={'draft':draft,'controls':{'product':[products[0]['id']],'customer':[q['customer_id']],'quote':[q['id']]},'quote':q,'totals':prices,'order':order,'visit':visit,'equipment':asset}
    components=definition(q,quotes,items,products,stock,prices,order,order_lines,visit,log,assets,asset)
    validate_components(components)
    return {'messages':[
      {'version':'v0.9','createSurface':{'surfaceId':'transaction-desk','catalogId':SPEC['id']}},
      {'version':'v0.9','updateComponents':{'surfaceId':'transaction-desk','components':components}},
      {'version':'v0.9','updateDataModel':{'surfaceId':'transaction-desk','path':'/','value':data}}],
      'selected':q['id'],'owner':'Everyone','view':'counter','revision':revision,'draft':draft,'notice':notice,'application':'calliope-transaction-desk'}

def apply_draft(db,q,draft):
    if q['status']!='Draft': raise ValueError('This quote has an order and is read-only. Start a new quote to make changes.')
    branch=one(draft.get('branch'),BRANCHES,'fulfillment branch')
    asset=one(draft.get('equipment'),[r[0] for r in db.execute('SELECT id FROM counter_equipment WHERE customer_id=?',(q['customer_id'],))],'equipment for this customer')
    db.execute('INSERT INTO counter_quote_equipment VALUES (?,?) ON CONFLICT(quote_id) DO UPDATE SET equipment_id=excluded.equipment_id',(q['id'],asset))
    notes=draft.get('notes')
    if not isinstance(notes,str) or len(notes)>4000: raise ValueError('Notes must be at most 4,000 characters.')
    existing=lines(db,q); supplied=draft.get('lines')
    if not isinstance(supplied,dict) or set(supplied)!={r['id'] for r in existing}: raise ValueError('Quote lines changed. Reload this quote.')
    for r in existing:
        item=supplied[r['id']]
        if not isinstance(item,dict) or item.get('product_id')!=r['product_id']: raise ValueError('A quote line no longer matches its product.')
        checked({'line-'+r['id']+'-qty':(item.get('quantity'),{'kind':'quantity','required':True,'minimum':'1' if r['unit']=='ea' else '0.01','maximum':'9999','precision':0 if r['unit']=='ea' else 2}),'line-'+r['id']+'-price':(item.get('unit_price'),{'kind':'money','required':True,'minimum':'0','maximum':'1000000','precision':2})})
        qty=decimal_input(item.get('quantity'),r['product_id']+' quantity',9999, r['unit']=='ea')
        price=decimal_input(item.get('unit_price'),r['product_id']+' unit price',1000000)
        if qty<=0: raise ValueError('Quantity must be greater than zero; use Remove to delete a line.')
        db.execute('UPDATE counter_lines SET quantity=?,unit_price=? WHERE id=?',(qty,price,r['id']))
    db.execute('UPDATE counter_quotes SET branch=?,notes=? WHERE id=?',(branch,notes,q['id']))

def action(store,body,name,context,selected):
    Rejected,LOCK=store.rejection,store.lock
    if name in ('set_view','refresh','discard','counter_select'):
        with LOCK:
            try:
                if name=='counter_select' and 'quote' in context:
                    with store.connect() as db: choices=[r[0] for r in db.execute('SELECT id FROM counter_quotes')]
                    selected=one(context['quote'],choices,'quote')
                return snapshot(store,selected)
            except ValueError as e: raise Rejected(str(e)) from e
    if name not in ('counter_new','counter_save','counter_add','counter_remove','counter_order','counter_visit'):
        raise Rejected('Unknown parts-desk action.')
    request_id=body.get('requestId')
    if not isinstance(request_id,str) or not 8<=len(request_id)<=100: raise Rejected('A request ID is required.')
    fingerprint=hashlib.sha256(json.dumps(['counter',name,selected,context],sort_keys=True).encode()).hexdigest()
    with LOCK,store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous=db.execute('SELECT * FROM requests WHERE id=?',(request_id,)).fetchone()
        if previous:
            if previous['fingerprint']!=fingerprint: raise Rejected('Request ID already used for another change.',409)
            return json.loads(previous['result'])
        try:
            if name=='counter_new':
                if db.execute('SELECT COUNT(*) FROM counter_quotes').fetchone()[0]>=100: raise ValueError('This local prototype supports up to 100 quotes.')
                customer=one(context.get('customer'),CUSTOMERS,'customer')
                selected='Q-'+uuid.uuid4().hex[:8].upper()
                db.execute('INSERT INTO counter_quotes(id,customer_id,branch) VALUES (?,?,?)',(selected,customer,'north'))
                asset=db.execute('SELECT id FROM counter_equipment WHERE customer_id=? ORDER BY id LIMIT 1',(customer,)).fetchone()
                db.execute('INSERT INTO counter_quote_equipment VALUES (?,?)',(selected,asset['id']))
                description='Created a new quote'
            else:
                row=db.execute('SELECT * FROM counter_quotes WHERE id=?',(selected,)).fetchone()
                if not row: raise Rejected('Quote not found.',404)
                q=dict(row); draft=context.get('draft',{})
                if not isinstance(draft,dict): raise ValueError('Quote draft must be an object.')
                version=draft.get('version') if 'draft' in context else context.get('version')
                if type(version) is not int or version!=q['version']:
                    raise Rejected('This quote changed in another view. Your draft is kept; discard / reload to reconcile.',409)
                if 'draft' in context and draft.get('id')!=q['id']: raise ValueError('Draft belongs to a different quote.')
                if name in ('counter_save','counter_add','counter_remove'):
                    apply_draft(db,q,draft)
                    if name=='counter_add':
                        sku=one(context.get('product'),[r[0] for r in db.execute('SELECT id FROM counter_products')],'part')
                        r=db.execute('SELECT * FROM counter_lines WHERE quote_id=? AND product_id=?',(q['id'],sku)).fetchone()
                        if r:
                            if r['quantity']+100>999900: raise ValueError('Quantity limit reached.')
                            db.execute('UPDATE counter_lines SET quantity=quantity+100 WHERE id=?',(r['id'],))
                        else:
                            if db.execute('SELECT COUNT(*) FROM counter_lines WHERE quote_id=?',(q['id'],)).fetchone()[0]>=20: raise ValueError('This prototype supports 20 quote lines.')
                            price=db.execute('SELECT price_cents FROM counter_products WHERE id=?',(sku,)).fetchone()[0]
                            position=db.execute('SELECT COALESCE(MAX(position),0)+1 FROM counter_lines WHERE quote_id=?',(q['id'],)).fetchone()[0]
                            db.execute('INSERT INTO counter_lines VALUES (?,?,?,?,?,?)',('L-'+uuid.uuid4().hex[:12],q['id'],sku,100,price,position))
                    elif name=='counter_remove':
                        deleted=db.execute('DELETE FROM counter_lines WHERE quote_id=? AND id=?',(q['id'],context.get('line_id'))).rowcount
                        if not deleted: raise ValueError('Line no longer exists in this quote.')
                    description={'counter_save':'Saved quote and checked current stock','counter_add':'Saved edits and added a part','counter_remove':'Saved edits and removed a line'}[name]
                elif name=='counter_order':
                    if q['status']!='Draft': raise ValueError('This quote already has an order.')
                    items=lines(db,q)
                    if not items: raise ValueError('Add at least one part before creating an order.')
                    shortages=[r for r in items if r['available']<r['quantity']]
                    if shortages and context.get('allow_backorder') is not True:
                        raise ValueError('Stock is short. Choose another branch or explicitly reserve available stock and backorder the remainder.')
                    total=totals(items); order_id='SO-'+uuid.uuid4().hex[:8].upper()
                    db.execute('INSERT INTO counter_orders(id,quote_id,branch,subtotal_cents,tax_cents,total_cents) VALUES (?,?,?,?,?,?)',(order_id,q['id'],q['branch'],total['subtotal'],total['tax'],total['total']))
                    for r in items:
                        reserved=min(r['available'],r['quantity']); short=r['quantity']-reserved
                        db.execute('UPDATE counter_stock SET reserved=reserved+? WHERE product_id=? AND branch=?',(reserved,r['product_id'],q['branch']))
                        db.execute('INSERT INTO counter_order_lines VALUES (?,?,?,?,?,?,?)',(order_id,r['product_id'],r['quantity'],reserved,short,r['unit_price'],r['total_cents']))
                    db.execute("UPDATE counter_quotes SET status='Ordered' WHERE id=?",(q['id'],))
                    description='Created '+order_id+' · '+('partial reservation; remainder backordered' if shortages else 'all parts reserved')
                else:
                    from hvac import TECHS
                    order=db.execute('SELECT * FROM counter_orders WHERE quote_id=?',(q['id'],)).fetchone()
                    if not order: raise ValueError('Create a parts order before planning its service visit.')
                    if order['visit_id']: raise ValueError('This order already has a linked visit.')
                    date=draft.get('visit_date'); technician=one(draft.get('technician'),TECHS[1:],'technician')
                    try:
                        parsed=dt.date.fromisoformat(date)
                        if parsed.isoformat()!=date or not dt.date.today()<=parsed<=dt.date.today()+dt.timedelta(days=365): raise ValueError()
                    except (TypeError,ValueError): raise ValueError('Choose a real visit date from today through the next year (YYYY-MM-DD).') from None
                    asset=equipment.for_quote(db,q['id'])
                    if not asset: raise ValueError('Choose equipment on the quote before creating a visit.')
                    customer,address=CUSTOMERS[q['customer_id']]; visit_id='HV-'+uuid.uuid4().hex[:8].upper()
                    shorts=db.execute('SELECT COUNT(*) FROM counter_order_lines WHERE order_id=? AND backorder>0',(order['id'],)).fetchone()[0]
                    notes=f"Parts order {order['id']} from {BRANCHES[q['branch']]}. "+('Parts are on backorder; confirm availability before dispatch. ' if shorts else 'Parts reserved; arrange collection. ')+q['notes']
                    db.execute('INSERT INTO hvac_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,1)',(visit_id,customer,address,technician,'Repair',date,'Time to confirm','Scheduled',0,asset['name']+' · '+asset['model']+' · '+asset['serial'],notes+' Equipment '+asset['id']+'. '+asset['service_notes']))
                    db.execute('UPDATE counter_orders SET visit_id=? WHERE id=?',(visit_id,order['id']))
                    db.execute('INSERT INTO hvac_activity(job_id,description,source) VALUES (?,?,?)',(visit_id,'Visit planned from parts order '+order['id'],'Parts desk'))
                    description='Planned service visit '+visit_id+' · arrival time still to confirm'
                db.execute('UPDATE counter_quotes SET version=version+1 WHERE id=?',(q['id'],))
            source='Desktop' if body.get('source')=='Desktop' else 'Web'
            db.execute('INSERT INTO counter_activity(quote_id,description,source) VALUES (?,?,?)',(selected,description,source))
            result=snapshot(store,selected,description,_db=db)
            db.execute('INSERT INTO requests VALUES (?,?,?)',(request_id,fingerprint,json.dumps(result)))
            return result
        except FieldErrors as e: raise Rejected(str(e),422,fields=e.fields) from e
        except Rejected: raise
        except ValueError as e: raise Rejected(str(e)) from e


def definition(q,quotes,items,products,stock,prices,order,order_lines,visit,log,assets,asset):
    from counter_ui import definition as compose
    return compose(q,quotes,items,products,stock,prices,order,order_lines,visit,log,assets,asset)
