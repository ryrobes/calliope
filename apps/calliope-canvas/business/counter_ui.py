"""Parts workflow composed from the shared A2UI catalog, not a bespoke client screen."""
from counter import BRANCHES,CUSTOMERS,money,quantity
from hvac import TECHS
import datetime as dt

def definition(q,quotes,items,products,stock,prices,order,order_lines,visit,log,assets,asset):
    c=[]
    def add(id,component,**p): c.append({'id':id,'component':component,**p}); return id
    def text(id,value,size='body',tone='normal',family='body',**p): return add(id,'CText',text=value,size=size,tone=tone,family=family,**p)
    def stack(id,children,direction='column',gap='md',collapse='never',**p): return add(id,'CStack',children=children,direction=direction,gap=gap,collapse=collapse,**p)
    def panel(id,children,tone='neutral',treatment='outlined',**p): return add(id,'CSurface',child=stack(id+'-body',children),tone=tone,treatment=treatment,padding='md',**p)
    def button(id,label,name,context=None,primary=False): return add(id,'Button',child=text(id+'-text',label),variant='primary' if primary else 'borderless',action={'event':{'name':name,'context':context or {}}})
    def badge(id,label,tone='neutral'): return add(id,'CBadge',text=label,tone=tone)
    bind=lambda path:{'path':path}
    def picker(id,label,path,options,**p): return add(id,'CRecordPicker',label=label,value=bind(path),options=[{'label':label,'value':value} for value,label in options],**p)
    def grid(id,columns,cells): return add(id,'CDataGrid',columns=[{'label':label,'weight':w} for label,w in columns],children=cells,rowKeys=[r['id'] for r in items],resizable=True)
    def field(id,label,path,kind='text',**p): return add(id,'CField',label=label,value=bind(path),kind=kind,error=bind('/errors/'+id),**p)
    draft={'draft':bind('/draft')}; identity={'version':bind('/draft/version')}
    nav=stack('views',[button('view-'+v,label,'set_view',{'view':v},v=='counter') for v,label in [('dossier','01 Dossier'),('pipeline','02 Pipeline'),('briefing','03 Briefing'),('hvac','04 HVAC'),('counter','05 Parts desk'),('receiving','06 Receiving')]],'row','sm',wrap=True)
    customer,address=CUSTOMERS[q['customer_id']]
    backordered=bool(order and any(r['backorder'] for r in order_lines))
    reserved=bool(order and any(r['reserved'] for r in order_lines))
    hero=panel('counter-hero',[
      text('counter-kicker','NORTHSTAR SUPPLY / COUNTER + FIELD','eyebrow','warm'),
      text('counter-heading','From the counter to the field.','display',family='display'),
      text('counter-deck','A quote becomes reserved parts, then a visit with the right context.','body','muted'),
      stack('counter-steps',[badge('step-quote','01 / Quote saved','success'),badge('step-stock','02 / Parts '+('partly reserved' if backordered and reserved else 'on backorder' if backordered else 'reserved' if order else 'to reserve'),'warm' if backordered or not order else 'success'),badge('step-visit','03 / Visit '+('planned' if visit else 'to plan'),'success' if visit else 'neutral')],'row','sm',wrap=True)
    ],'warm','wash')
    chooser=panel('counter-files',[
      text('counter-files-title','Working quotes','title',family='display'),
      stack('quote-links',[button('open-'+r['id'],r['id']+' · '+CUSTOMERS[r['customer_id']][0]+' · '+r['status'],'counter_select',{'id':r['id']},r['id']==q['id']) for r in quotes[:6]],'row','sm',wrap=True),
      *([stack('quote-archive',[picker('all-quotes','All quotes','/controls/quote',[(r['id'],r['id']+' · '+CUSTOMERS[r['customer_id']][0]+' · '+r['status']) for r in quotes],weight=2),button('open-quote','Open quote','counter_select',{'quote':bind('/controls/quote')})],'row','md','phone')] if len(quotes)>6 else []),
      stack('quote-new',[picker('new-customer','Customer for a new quote','/controls/customer',[(k,v[0]) for k,v in CUSTOMERS.items()],weight=2),button('new-quote','New quote','counter_new',{'customer':bind('/controls/customer')})],'row','md','phone')
    ],treatment='plain')
    head=[stack('quote-title-row',[text('quote-title',customer,'title',family='display',weight=1),badge('quote-status',q['id']+' / '+q['status'],'success' if order else 'cool')],'row','md','phone'),text('quote-address',address,'caption','muted'),button('inspect-customer','Inspect customer ↗','open_record',{'kind':'customer','recordId':q['customer_id']})]
    cells=[]
    for r in items:
      p='line-'+r['id']; path='/draft/lines/'+r['id']; sku=r['product_id']
      product=[text(p+'-sku',sku,'eyebrow','cool','mono'),text(p+'-name',r['name']),text(p+'-unit','Sold by '+('the foot' if r['unit']=='ft' else 'each'),'caption','muted')]
      if not order: product.append(button(p+'-remove','Remove '+sku,'counter_remove',{**draft,'line_id':r['id']}))
      cells.append(stack(p+'-product',product,gap='xs'))
      if order:
        cells.extend([text(p+'-qty',quantity(r['quantity'])+' '+r['unit'],'body',family='mono'),text(p+'-price',money(r['unit_price']),'body',family='mono')])
      else:
        cells.extend([field(p+'-qty','Qty · '+sku,path+'/quantity','quantity',unit=r['unit'],required=True,minimum='1' if r['unit']=='ea' else '0.01',maximum='9999',precision=0 if r['unit']=='ea' else 2),field(p+'-price','Unit price · '+sku,path+'/unit_price','money',unit='USD',required=True,minimum='0',maximum='1000000',precision=2)])
      cells.append(text(p+'-total',money(r['total_cents']),'body','accent','mono'))
      if order:
        ol=next(o for o in order_lines if o['product_id']==sku)
        availability=[badge(p+'-stock',quantity(ol['reserved'])+' reserved','success')]
        if ol['backorder']: availability.append(badge(p+'-short',quantity(ol['backorder'])+' backordered','warm'))
      else:
        short=max(0,r['quantity']-r['available'])
        availability=[badge(p+'-stock',quantity(r['available'])+' '+r['unit']+' available','warm' if short else 'success')]
        if short: availability.append(text(p+'-short','Short '+quantity(short)+' '+r['unit'],'caption','warm'))
      cells.append(stack(p+'-availability',availability,gap='xs'))
    quote=[*head]
    if not order:
      quote += [picker('quote-equipment','Equipment for this work','/draft/equipment',[(a['id'],a['name']+' · '+a['location']) for a in assets]),picker('quote-branch','Fulfillment branch','/draft/branch',BRANCHES.items()),
        stack('add-part-row',[picker('part-picker','Find a part','/controls/product',[(p['id'],p['id']+' · '+p['name']) for p in products],weight=3),button('add-part','Add to quote','counter_add',{**draft,'product':bind('/controls/product')})],'row','md','phone'),
        text('editing-help','Edit quantities and unit prices, then Save & check stock. Adding or removing a part also saves current edits.','caption','muted')]
    else: quote.append(text('order-branch','Fulfillment · '+BRANCHES[q['branch']],'body','muted'))
    if cells: quote.append(grid('quote-grid',[('Part',3),('Quantity',1.1),('Unit price',1.4),('Line total',1.3),('Reservation' if order else 'Stock',1.8)],cells))
    else: quote.append(text('quote-empty','Start with a part from the catalog above.','body','muted'))
    if not order:
      quote += [field('quote-notes','Quote / handoff notes','/draft/notes','longText'),stack('quote-save-row',[button('quote-save','Save & check stock','counter_save',draft,True),button('quote-discard','Discard / reload','discard')],'row','sm','phone')]
    else: quote += [text('quote-note-label','HANDOFF NOTES','eyebrow','muted'),text('quote-note-value',q['notes'] or 'No extra handoff notes.','body','muted'),text('quote-locked','This order keeps its quoted quantities and prices. Start a new quote for new work.','caption','muted')]
    totalitems=[text('total-kicker','ORDER TOTAL' if order else 'SAVED QUOTE TOTAL','eyebrow','muted'),text('total-amount',money(prices['total']),'metric','warm','mono'),stack('total-sub',[text('subtotal-label','Parts subtotal',weight=1),text('subtotal-value',money(prices['subtotal']),family='mono')],'row','sm'),stack('total-tax',[text('tax-label','Demo tax · 6.5%',weight=1),text('tax-value',money(prices['tax']),family='mono')],'row','sm'),text('total-note','USD · illustrative tax. Totals and availability reflect the last saved quote.','caption','muted')]
    side=[panel('quote-total',totalitems,'warm','wash')]
    if not order:
      short=any(r['available']<r['quantity'] for r in items)
      actions=[text('reservation-heading','Make the handoff','title',family='display'),text('reservation-copy','Reserve stock in '+BRANCHES[q['branch']]+'. A parts order keeps the quantities, prices, and reservation together.','body','muted'),button('create-order','Reserve & create order','counter_order',identity,True)]
      if short: actions += [badge('shortage-badge','Some parts are short','warm'),text('backorder-help','Switch branches and save, or explicitly keep the shortage as a backorder.','caption','muted'),button('backorder','Reserve available + backorder remainder','counter_order',{**identity,'allow_backorder':True})]
      side.append(panel('reservation',actions))
    else:
      side.append(panel('order-receipt',[badge('order-number',order['id'],'success'),text('order-heading','Reserved + still to source.' if backordered and reserved else 'Awaiting stock.' if backordered else 'Parts set aside.','title',family='display'),text('order-copy','Reserved quantities are deducted from availability for other quotes. Backordered quantities still need sourcing.','body','muted'),text('order-local','Local reservation only · no supplier order, payment, or customer message is sent.','caption','muted')],'warm' if backordered else 'success','wash'))
      if visit:
        visitbody=[badge('linked-visit-id',visit['id'],'cool'),text('linked-visit-heading','Your field handoff','title',family='display'),text('linked-visit-date',visit['scheduled_date']+' · '+visit['technician']),text('linked-visit-status',visit['status']+' · '+visit['slot'],'caption','muted'),button('open-visit','Open service visit ↗','set_view',{'view':'hvac','id':visit['id']},True)]
      else:
        visitbody=[text('plan-visit-heading','Bring the parts to the job.','title',family='display'),text('plan-visit-help','Create a linked visit with this customer, address, parts order, and handoff notes. Choose a date and technician; confirm the arrival time with the customer.','body','muted'),field('visit-date','Visit date','/draft/visit_date','date',required=True,minimum=dt.date.today().isoformat(),maximum=(dt.date.today()+dt.timedelta(days=365)).isoformat()),picker('visit-technician','Assign technician','/draft/technician',[(t,t) for t in TECHS[1:]]),button('plan-visit','Create linked service visit','counter_visit',draft,True),button('visit-discard','Discard / reload','discard')]
      side.append(panel('visit-plan',visitbody,'cool','outlined'))
    if asset:
      side.append(panel('equipment-file',[text('equipment-title','Equipment file','title',family='display'),badge('equipment-id',asset['id'],'cool'),text('equipment-name',asset['name']),text('equipment-location',asset['location'],'caption','muted'),text('equipment-model','MODEL / '+asset['model'],'caption',family='mono'),text('equipment-serial','SERIAL / '+asset['serial'],'caption',family='mono'),text('equipment-installed','Installed '+asset['installed'],'caption','muted'),text('equipment-notes',asset['service_notes'],'caption','muted'),button('inspect-equipment','Inspect equipment ↗','open_record',{'kind':'equipment','recordId':asset['id']})],treatment='plain'))
    availability=[]
    for r in items:
      p='stock-'+r['id']; availability.append(text(p+'-part',r['product_id'],'caption',family='mono'))
      for branch in BRANCHES:
        available=next(s['available'] for s in stock if s['branch']==branch and s['product_id']==r['product_id'])
        availability.append(text(p+'-'+branch,quantity(available)+' '+r['unit'],'body','warm' if available<r['quantity'] else 'success',family='mono'))
    lower=[]
    if availability: lower.append(panel('branch-stock',[text('branch-stock-heading','Across the branches','title',family='display'),text('branch-stock-copy','Available = on hand less all reservations. Rechecked atomically when an order is created.','caption','muted'),grid('stock-grid',[('Part',2),('North branch',1),('South depot',1)],availability)],weight=2))
    lower.append(panel('counter-log',[text('counter-log-heading','Work log','title',family='display')]+[text('counter-log-'+str(r['id']),r['source']+' · '+r['description'],'caption','muted') for r in log],treatment='plain',weight=1))
    stack('root',[nav,hero,chooser,stack('counter-columns',[panel('quote-editor',quote,weight=3),stack('quote-sidebar',side,weight=1.5)],'row','lg','tablet'),stack('counter-lower',lower,'row','lg','tablet'),text('fixture-note','Fictional training workspace · generic parts; suitability and service pricing require confirmation.','caption','muted')],gap='lg')
    return c
