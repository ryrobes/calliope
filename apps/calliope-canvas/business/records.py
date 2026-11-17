"""Related-record inspectors composed from the same catalog as the primary view."""
import copy
from counter import BRANCHES,CUSTOMERS,quantity,money
from ui import UI

def validate_refs(store,panes):
    if not isinstance(panes,list) or len(panes)>8 or any(not isinstance(p,dict) or set(p)!={'kind','id'} or not all(isinstance(v,str) for v in p.values()) for p in panes):raise store.rejection('Invalid record navigation.')
    tables={'order':'counter_orders','equipment':'counter_equipment','visit':'hvac_jobs','product':'counter_products','receipt':'receiving_receipts'}
    with store.connect() as db:
        for ref in panes:
            if ref['kind']=='customer':exists=ref['id'] in CUSTOMERS
            elif ref['kind'] in tables:exists=db.execute('SELECT 1 FROM '+tables[ref['kind']]+' WHERE id=?',(ref['id'],)).fetchone()
            else:raise store.rejection('Unsupported record kind.')
            if not exists:raise store.rejection('Related record not found.',404)

def decorate(store,snapshot,panes=None):
    panes=panes or []
    if not isinstance(panes,list) or len(panes)>8 or any(not isinstance(p,dict) or set(p)!={'kind','id'} or not all(isinstance(v,str) for v in p.values()) for p in panes):raise store.rejection('Invalid record navigation.')
    s=copy.deepcopy(snapshot);components=s['messages'][1]['updateComponents']['components']
    if not any(n['id']=='workspace-main' for n in components):
        root=next(n for n in components if n['id']=='root');root['id']='workspace-main';root['weight']=3
    else:components[:]=[n for n in components if n['id']!='root' and not n['id'].startswith('record-pane')]
    u=UI();children=['workspace-main']
    if panes:
        ref=panes[-1];kind=ref['kind'];id=ref['id'];p='record-pane-'
        controls=[u.button(p+'close','Close inspector','close_record')]
        if len(panes)>1:controls.insert(0,u.button(p+'back','← Previous record','back_record'))
        content=[u.stack(p+'controls',controls,'row','sm',wrap=True)]
        with store.connect() as db:
            if kind=='order':
                row=db.execute('SELECT o.*,q.customer_id FROM counter_orders o JOIN counter_quotes q ON q.id=o.quote_id WHERE o.id=?',(id,)).fetchone()
                if not row:raise store.rejection('Order not found.',404)
                lines=[dict(r) for r in db.execute('SELECT ol.*,p.name,p.unit FROM counter_order_lines ol JOIN counter_products p ON p.id=ol.product_id WHERE order_id=?',(id,))]
                ready=all(not r['backorder'] for r in lines)
                content += [u.badge(p+'status','All parts reserved' if ready else 'Waiting on parts','success' if ready else 'warm'),u.text(p+'amount',money(row['total_cents']),'metric','warm',family='mono'),u.text(p+'branch',BRANCHES[row['branch']]),u.record(p+'customer',CUSTOMERS[row['customer_id']][0],'customer',row['customer_id'])]
                for i,r in enumerate(lines):content.append(u.text(p+'line-'+str(i),r['product_id']+' · '+quantity(r['reserved'])+' '+r['unit']+' reserved / '+quantity(r['backorder'])+' due','caption','muted'))
                if row['visit_id']:content.append(u.record(p+'visit','Inspect linked visit ↗','visit',row['visit_id']))
                content.append(u.button(p+'open','Open parts workspace ↗','set_view',{'view':'counter','id':row['quote_id']}))
            elif kind=='customer':
                if id not in CUSTOMERS:raise store.rejection('Customer not found.',404)
                content += [u.text(p+'name',CUSTOMERS[id][0],'title',family='display'),u.text(p+'address',CUSTOMERS[id][1])]
                for r in db.execute('SELECT * FROM counter_equipment WHERE customer_id=?',(id,)):content.append(u.record(p+r['id'],r['name']+' ↗','equipment',r['id']))
                for r in db.execute('SELECT o.id FROM counter_orders o JOIN counter_quotes q ON q.id=o.quote_id WHERE q.customer_id=? ORDER BY o.created DESC LIMIT 8',(id,)):content.append(u.record(p+r['id'],r['id']+' ↗','order',r['id']))
            elif kind=='equipment':
                r=db.execute('SELECT * FROM counter_equipment WHERE id=?',(id,)).fetchone()
                if not r:raise store.rejection('Equipment not found.',404)
                content += [u.text(p+'name',r['name'],'title',family='display'),u.text(p+'location',r['location']),u.text(p+'model','Model · '+r['model'],'caption',family='mono'),u.text(p+'serial','Serial · '+r['serial'],'caption',family='mono'),u.text(p+'installed','Installed '+r['installed'],'caption','muted'),u.text(p+'notes',r['service_notes'],'body','muted'),u.record(p+'customer',CUSTOMERS[r['customer_id']][0]+' ↗','customer',r['customer_id'])]
            elif kind=='visit':
                r=db.execute('SELECT * FROM hvac_jobs WHERE id=?',(id,)).fetchone()
                if not r:raise store.rejection('Visit not found.',404)
                content += [u.badge(p+'status',r['status'],'cool'),u.text(p+'name',r['customer'],'title',family='display'),u.text(p+'date',r['scheduled_date']+' · '+r['slot']),u.text(p+'tech',r['technician']),u.text(p+'equipment',r['equipment'],'caption','muted')]
                o=db.execute('SELECT o.id,SUM(ol.backorder) AS shortage FROM counter_orders o JOIN counter_order_lines ol ON ol.order_id=o.id WHERE o.visit_id=? GROUP BY o.id',(id,)).fetchone()
                if o:content += [u.badge(p+'readiness','Parts ready' if not o['shortage'] else 'Parts still backordered','success' if not o['shortage'] else 'warm'),u.record(p+'order',o['id']+' ↗','order',o['id'])]
                content.extend([u.button(p+'open','Open service workspace ↗','set_view',{'view':'hvac','id':id}),u.button(p+'dispatch','Open dispatch ↗','set_view',{'view':'dispatch','id':id}),u.button(p+'field','Open field notebook ↗','set_view',{'view':'field','id':id})])
            elif kind=='product':
                r=db.execute('SELECT * FROM counter_products WHERE id=?',(id,)).fetchone()
                if not r:raise store.rejection('Part not found.',404)
                content += [u.text(p+'name',r['name'],'title',family='display'),u.text(p+'price',money(r['price_cents'])+' / '+r['unit'],'metric','accent',family='mono'),u.text(p+'details',r['details'],'body','muted')]
                for st in db.execute('SELECT * FROM counter_stock WHERE product_id=?',(id,)):content.append(u.text(p+st['branch'],BRANCHES[st['branch']]+' · '+quantity(st['on_hand']-st['reserved'])+' '+r['unit']+' available','caption'))
            elif kind=='receipt':
                r=db.execute('SELECT * FROM receiving_receipts WHERE id=?',(id,)).fetchone()
                if not r:raise store.rejection('Receipt not found.',404)
                content += [u.badge(p+'posted','Posted · immutable','success'),u.text(p+'reference',r['reference'],'title',family='display'),u.text(p+'date',r['received_date']+' '+r['received_time']+' · '+r['source'],'caption'),u.text(p+'notes',r['notes'] or 'No extra notes.','body','muted')]
                if r['has_damage']:content.append(u.text(p+'damage','Damage recorded · '+r['damage_notes'],'body','warm'))
                for i,l in enumerate(db.execute('SELECT l.product_id,p.unit,rl.quantity FROM receiving_receipt_lines rl JOIN receiving_lines l ON l.id=rl.line_id JOIN counter_products p ON p.id=l.product_id WHERE receipt_id=?',(id,))):content.append(u.text(p+'line-'+str(i),quantity(l['quantity'])+' '+l['unit']+' · '+l['product_id'],'caption'))
                for i,a in enumerate(db.execute('SELECT DISTINCT order_id FROM receiving_allocations WHERE receipt_id=?',(id,))):content.append(u.record(p+'allocated-'+str(i),'Replenished '+a['order_id']+' ↗','order',a['order_id']))
                for i,a in enumerate(db.execute('SELECT id,name FROM business_attachments WHERE receipt_id=?',(id,))):content.append(u.button(p+'attachment-'+str(i),'Open '+a['name']+' ↗','open_attachment',{'attachmentId':a['id']}))
                content.append(u.button(p+'open','Open receiving workspace ↗','set_view',{'view':'receiving','id':r['po_id']}))
            else:raise store.rejection('Unsupported record kind.')
        children.append(u.add('record-pane','CRecordPane',title=kind.title()+' / '+id,child=u.stack(p+'body',content)))
    u.stack('root',children,'row','lg')
    components.extend(u.nodes);s['panes']=panes
    # Reactive hosts compose from this baseline on every watched update. Keep the
    # inspector in that baseline so a decision cannot remove an open record pane.
    if 'baseComponents' in s:
        base=s['baseComponents']
        root=next(n for n in base if n['id']=='root')
        root['id']='workspace-main';root['weight']=3
        base.extend(copy.deepcopy(u.nodes))
    from presentation import validate_components
    validate_components(components)
    return s
