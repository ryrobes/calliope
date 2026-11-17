import datetime as dt
from counter import BRANCHES,CUSTOMERS,quantity
from ui import UI,bind
from receiving import PAGE_SIZE

def definition(po,orders,total,state,items,receipts,pending,log):
    u=UI();draft={'draft':bind('/draft')}
    nav=u.nav('receiving')
    hero=u.panel('receiving-hero',[u.text('receiving-kicker','NORTHSTAR SUPPLY / INBOUND','eyebrow','cool'),u.text('receiving-heading','From the dock to the next job.','display',family='display'),u.text('receiving-intro','Receive what arrived. Keep the rest open. Put waiting work back in motion.','body','muted')],'cool','plain')
    controls=u.filters('receiving-filters','Find orders','/receiving_filter','receiving_filter',[
      u.field('receiving-search','Find a purchase order or supplier','/receiving_filter/search',maxLength=100,weight=2),
      u.picker('receiving-branch','Branch','/receiving_filter/branch',[('all','All branches'),*BRANCHES.items()],weight=1),
      u.picker('receiving-status','Queue','/receiving_filter/status',[('open','Awaiting delivery'),('received','Received'),('all','All orders')],weight=1),
      u.picker('receiving-sort','Sort by','/receiving_filter/sort',[('expected','Expected date'),('supplier','Supplier'),('branch','Branch')],weight=1)],debounce=350)
    rows=[]
    for r in orders:
        key=r['id'];rows.append((key,[u.button('select-'+key,key,'receiving_select',{'id':key},key==po['id']),u.text('supplier-'+key,r['supplier']),u.text('branch-'+key,BRANCHES[r['branch']],'caption'),u.text('due-'+key,r['expected_date'],'caption',family='mono'),u.badge('status-'+key,'Received' if not r['remaining'] else 'Partial' if r['received'] else 'Awaiting','success' if not r['remaining'] else 'warm')]))
    queue=[controls,u.grid('receiving-queue',[('Order',1.2),('Supplier',2),('Branch',1.2),('Expected',1.2),('Status',1)],rows,resizable=True) if rows else u.text('receiving-empty','No orders match these filters. Your open record stays below.','body','muted')]
    pagination=[u.text('receiving-count',f'{total} matches · page {state["page"]+1} of {max(1,(total+PAGE_SIZE-1)//PAGE_SIZE)}','caption','muted',weight=1)]
    if state['page']>0:pagination.append(u.button('receiving-prev','Previous page','receiving_page',{'page':state['page']-1}))
    if (state['page']+1)*PAGE_SIZE<total:pagination.append(u.button('receiving-next','Next page','receiving_page',{'page':state['page']+1}))
    queue.append(u.stack('receiving-pages',pagination,'row','sm','phone'))
    rows=[]
    for r in items:
        id=r['id'];remaining=r['expected']-r['received']
        rows.append((id,[u.stack('part-'+id,[u.text('sku-'+id,r['product_id'],'eyebrow','cool',family='mono'),u.text('name-'+id,r['name']),u.record('inspect-'+id,'Inspect part ↗','product',r['product_id'])],gap='xs'),u.text('ordered-'+id,quantity(r['expected'])+' '+r['unit'],family='mono'),u.text('received-'+id,quantity(r['received'])+' '+r['unit'],family='mono'),u.text('remaining-'+id,quantity(remaining)+' '+r['unit'],tone='warm' if remaining else 'success',family='mono'),u.field('receive-'+id,'Usable quantity · '+r['product_id'],'/draft/lines/'+id+'/quantity','quantity',required=True,minimum='0',maximum=quantity(remaining),precision=0 if r['unit']=='ea' else 2,unit=r['unit'],readOnly=not bool(remaining))]))
    outstanding=sum(r['expected']>r['received'] for r in items)
    editor=[u.stack('receipt-heading',[u.text('receipt-title',po['id'],'title',family='display',weight=1),u.badge('receipt-po-state','Complete' if not outstanding else f'{outstanding} lines still open','success' if not outstanding else 'warm')],'row','sm','phone'),u.text('receipt-supplier',po['supplier']+' · '+BRANCHES[po['branch']]),u.text('receipt-guidance','Enter usable quantities from this delivery. Zero leaves a line open. Paste a column of quantities or use Tab / Enter to move between fields.','caption','muted'),u.grid('receiving-line-grid',[('Part',3),('Ordered',1),('Received',1),('Still due',1),('This delivery',1.8)],rows,resizable=True),u.stack('receipt-datetime',[u.field('receipt-date','Received date','/draft/received_date','date',required=True,maximum=dt.date.today().isoformat(),weight=1),u.field('receipt-time','Received time','/draft/received_time','time',required=True,weight=1)],'row','md','phone'),u.field('receipt-reference','Delivery / packing-slip reference','/draft/reference',required=True,maxLength=100,help='A reference for this receipt, separate from the purchase order.'),u.add('receipt-damage','CheckBox',label='Delivery includes damaged goods',value=bind('/draft/has_damage')),u.field('receipt-damage-notes','Damage / shortage evidence','/draft/damage_notes','longText',visible=bind('/draft/has_damage'),required=bind('/draft/has_damage'),maxLength=2000,help='Only usable quantities above enter stock. Describe excluded damaged goods here.'),u.field('receipt-notes','Receiving notes','/draft/notes','longText',maxLength=4000),u.add('receipt-attachments','CAttachments',label='Delivery evidence',value=bind('/draft/attachments'),names=bind('/attachment_names')),u.stack('receipt-actions',[u.button('receipt-post','Post receipt & allocate stock','receiving_post',draft,True),u.button('receipt-discard','Discard this draft','discard')],'row','sm','phone')]
    side=[u.panel('allocation-note',[u.text('allocation-heading','Waiting work gets first call.','title',family='display'),u.text('allocation-copy','Usable receipts enter this branch. Available stock is then reserved against the oldest outstanding parts orders, using creation time and order ID.','body','muted'),u.text('allocation-boundary','Posting creates an immutable local receipt. It does not dispatch technicians or contact suppliers.','caption','muted')],'warm','wash')]
    wait=[u.text('waiting-heading','Backorders at this branch','title',family='display')]
    for i,r in enumerate(pending[:10]):
        p='waiting-'+str(i);wait.append(u.stack(p,[u.record(p+'-order',r['id']+' · '+CUSTOMERS[r['customer_id']][0],'order',r['id']),u.text(p+'-qty',quantity(r['backorder'])+' × '+r['product_id'],'caption','warm'),*([u.record(p+'-visit','Inspect linked visit ↗','visit',r['visit_id'])] if r['visit_id'] else [])],gap='xs'))
    if not pending:wait.append(u.text('waiting-empty','No matching backorders are waiting here.','caption','muted'))
    side.append(u.panel('waiting-orders',wait))
    history=[u.text('receipts-heading','Receipt history','title',family='display')]
    for r in receipts:history.extend([u.record('receipt-'+r['id'],r['id']+' · '+r['reference'],'receipt',r['id']),u.text('receipt-date-'+r['id'],r['received_date']+' '+r['received_time']+' · '+r['source'],'caption','muted')])
    if not receipts:history.append(u.text('receipts-empty','Each posted delivery leaves its own receipt.','caption','muted'))
    side.append(u.panel('receipts-history',history,treatment='plain'))
    u.stack('root',[nav,hero,u.section('receiving-list','Inbound orders',queue,subtitle='Find a delivery, then record what actually arrived.',tone='cool'),u.stack('receiving-work',[u.section('receipt-workspace','Receive this delivery',[u.panel('receipt-editor',editor)],tone='accent',weight=3),u.section('receiving-side','Allocation & evidence',side,weight=1.4)],'row','lg','tablet'),u.panel('receiving-log',[u.text('receiving-log-title','Dock log','title',family='display')]+[u.text('receiving-log-'+str(a['id']),a['source']+' · '+a['description'],'caption','muted') for a in log],treatment='plain')],gap='lg')
    return u.nodes
