"""Typed transactional SQL actions for the fictional regression data."""
from ui import bind

def add_commands(doc, actions, add, T):
    S=doc['data']['state'];E=doc['events']
    S.update(receiptReview={},scheduleReview={})
    fields={'draft':'json'}
    quote_guard=f"""UPDATE {T('counter_quotes')} q SET notes=$1::jsonb->>'notes',branch=$1::jsonb->'branch'->>0,version=version+1
      WHERE id=$1::jsonb->>'id' AND version=($1::jsonb->>'version')::integer AND status='Draft'
      AND $1::jsonb->'branch'->>0 IN ('north','south') AND length($1::jsonb->>'notes')<=4000
      AND (SELECT count(*) FROM jsonb_object_keys($1::jsonb->'lines'))=(SELECT count(*) FROM {T('counter_lines')} WHERE quote_id=q.id)
      AND NOT EXISTS(SELECT 1 FROM {T('counter_lines')} l JOIN {T('counter_products')} p ON p.id=l.product_id WHERE l.quote_id=q.id AND
        (NOT ($1::jsonb->'lines' ? l.id) OR coalesce(($1::jsonb->'lines'->l.id->>'quantity')::numeric,0)<=0
         OR ($1::jsonb->'lines'->l.id->>'quantity')::numeric>10000 OR ($1::jsonb->'lines'->l.id->>'unit_price')::numeric<0
         OR (p.unit='ea' AND ($1::jsonb->'lines'->l.id->>'quantity')::numeric<>trunc(($1::jsonb->'lines'->l.id->>'quantity')::numeric)))) RETURNING id"""
    quote_lines=f"""UPDATE {T('counter_lines')} l SET quantity=round((v.value->>'quantity')::numeric*100)::integer,
      unit_price=round((v.value->>'unit_price')::numeric*100)::integer FROM jsonb_each($1::jsonb->'lines') v
      WHERE l.quote_id=$1::jsonb->>'id' AND l.id=v.key RETURNING id"""
    add('save_quote','Save quote lines and current prices',fields,[(quote_guard,['draft'],1),(quote_lines,['draft'],None)],reset=['quoteForm'])
    add('add_part','Save quote and add a catalog part',{'draft':'json','product':'string'},[(quote_guard,['draft'],1),(quote_lines,['draft'],None),
      (f"""INSERT INTO {T('counter_lines')}(id,quote_id,product_id,quantity,unit_price,position)
      SELECT 'L-'||gen_random_uuid()::text,$1::jsonb->>'id',p.id,100,p.price_cents,999 FROM {T('counter_products')} p WHERE p.id=$2
      ON CONFLICT(quote_id,product_id) DO UPDATE SET quantity={T('counter_lines')}.quantity+100 RETURNING id""",['draft','product'],1)],reset=['quoteForm'])
    add('remove_part','Remove a quote line',{'draft':'json','line':'string'},[(quote_guard,['draft'],1),(quote_lines,['draft'],None),
      (f"DELETE FROM {T('counter_lines')} WHERE id=$1 AND quote_id=$2::jsonb->>'id' RETURNING id",['line','draft'],1)],reset=['quoteForm'])
    add('new_quote','Open a new fictional customer quote',{'key':'uuid'},[
      (f"INSERT INTO {T('counter_quotes')}(id,customer_id,branch,notes) VALUES('Q-'||$1::text,'harbor','north','') RETURNING id",['key'],1)],context={'key':bind('/request/key')})
    E['new_quote']['set']={'/state/selection/quote':bind('/receipt/rows/0/id')}
    add('order_quote','Reserve stock and optionally backorder the remainder',{'draft':'json','allow':'boolean'},[
      (quote_guard,['draft'],1),(quote_lines,['draft'],None),
      (f"SELECT s.product_id FROM {T('counter_stock')} s JOIN {T('counter_lines')} l ON l.product_id=s.product_id WHERE l.quote_id=$1::jsonb->>'id' AND s.branch=$1::jsonb->'branch'->>0 ORDER BY s.product_id FOR UPDATE OF s",['draft'],None),
      (f"""SELECT q.id FROM {T('counter_quotes')} q WHERE q.id=$1::jsonb->>'id'
       AND EXISTS(SELECT 1 FROM {T('counter_lines')} WHERE quote_id=q.id)
       AND ($2 OR NOT EXISTS(SELECT 1 FROM {T('counter_lines')} l JOIN {T('counter_stock')} s ON s.product_id=l.product_id AND s.branch=q.branch WHERE l.quote_id=q.id AND l.quantity>s.on_hand-s.reserved))""",['draft','allow'],1),
      (f"""INSERT INTO {T('counter_orders')}(id,quote_id,branch,subtotal_cents,tax_cents,total_cents)
       SELECT 'SO-'||q.id,q.id,q.branch,sum(round(l.quantity*l.unit_price/100.0))::integer,
       round(sum(round(l.quantity*l.unit_price/100.0))*.065)::integer,round(sum(round(l.quantity*l.unit_price/100.0))*1.065)::integer
       FROM {T('counter_quotes')} q JOIN {T('counter_lines')} l ON l.quote_id=q.id WHERE q.id=$1::jsonb->>'id' GROUP BY q.id RETURNING id""",['draft'],1),
      (f"""INSERT INTO {T('counter_order_lines')}(order_id,product_id,quantity,reserved,backorder,unit_price,total_cents)
       SELECT 'SO-'||q.id,l.product_id,l.quantity,least(l.quantity,s.on_hand-s.reserved),greatest(0,l.quantity-(s.on_hand-s.reserved)),l.unit_price,round(l.quantity*l.unit_price/100.0)::integer
       FROM {T('counter_quotes')} q JOIN {T('counter_lines')} l ON l.quote_id=q.id JOIN {T('counter_stock')} s ON s.product_id=l.product_id AND s.branch=q.branch WHERE q.id=$1::jsonb->>'id'""",['draft'],None),
      (f"UPDATE {T('counter_stock')} s SET reserved=s.reserved+l.reserved FROM {T('counter_order_lines')} l WHERE l.order_id='SO-'||($1::jsonb->>'id') AND s.product_id=l.product_id AND s.branch=$1::jsonb->'branch'->>0",['draft'],None),
      (f"UPDATE {T('counter_quotes')} SET status='Ordered' WHERE id=$1::jsonb->>'id' RETURNING id",['draft'],1)],reset=['quoteForm'])
    add('create_visit','Create the service visit linked to a parts order',{'quote':'string'},[
      (f"""INSERT INTO {T('hvac_jobs')}(id,customer,address,technician,job_type,scheduled_date,slot,status,amount_cents,equipment,notes)
       SELECT 'HV-'||o.id,CASE q.customer_id WHEN 'harbor' THEN 'Harbor Dental' WHEN 'cedar' THEN 'Cedar House' ELSE 'Oak Apartments' END,
       CASE q.customer_id WHEN 'harbor' THEN '8 Harbor View' WHEN 'cedar' THEN '142 Cedar Lane' ELSE '306 Oak Street' END,'Alex Morgan','Repair',(current_date+1)::text,'Time to confirm','Scheduled',0,coalesce(e.name,'Equipment to confirm'),q.notes
       FROM {T('counter_orders')} o JOIN {T('counter_quotes')} q ON q.id=o.quote_id
       LEFT JOIN {T('counter_quote_equipment')} qe ON qe.quote_id=q.id LEFT JOIN {T('counter_equipment')} e ON e.id=qe.equipment_id
       WHERE o.quote_id=$1 AND o.visit_id IS NULL RETURNING id""",['quote'],1),
      (f"UPDATE {T('counter_orders')} SET visit_id='HV-'||id WHERE quote_id=$1 AND visit_id IS NULL RETURNING visit_id",['quote'],1)],reset=['jobForm','scheduleForm'])
    E['create_visit']['set']={'/state/selection/job':bind('/receipt/rows/0/visit_id'),'/state/page':'dispatch'}

    # Review values are captured in transient state; confirmation always repeats
    # authoritative version, quantity, stock, date and conflict checks in SQL.
    E['review_receive']={'set':{'/state/receiptReview':bind('/context/draft'),'/state/controls/review':'receipt'}}
    E['review_schedule']={'set':{'/state/scheduleReview':bind('/context/draft'),'/state/controls/review':'schedule'}}
    receive_guard=f"""UPDATE {T('receiving_orders')} p SET version=version+1 WHERE id=$1::jsonb->>'id' AND version=($1::jsonb->>'version')::integer
      AND ($1::jsonb->>'received_date')::date BETWEEN current_date-365 AND current_date
      AND ($1::jsonb->>'received_time')::time IS NOT NULL AND length(trim($1::jsonb->>'reference')) BETWEEN 1 AND 100
      AND (SELECT count(*) FROM jsonb_object_keys($1::jsonb->'lines'))=(SELECT count(*) FROM {T('receiving_lines')} WHERE po_id=p.id)
      AND EXISTS(SELECT 1 FROM jsonb_each($1::jsonb->'lines') v WHERE (v.value->>'quantity')::numeric>0)
      AND NOT EXISTS(SELECT 1 FROM {T('receiving_lines')} l JOIN {T('counter_products')} pr ON pr.id=l.product_id WHERE l.po_id=p.id AND
       (NOT ($1::jsonb->'lines' ? l.id) OR ($1::jsonb->'lines'->l.id->>'quantity')::numeric<0 OR round(($1::jsonb->'lines'->l.id->>'quantity')::numeric*100)>l.expected-l.received
        OR (pr.unit='ea' AND ($1::jsonb->'lines'->l.id->>'quantity')::numeric<>trunc(($1::jsonb->'lines'->l.id->>'quantity')::numeric)))) RETURNING id"""
    add('receive','Post a delivery and allocate stock to oldest waiting orders',{'draft':'json','key':'uuid'},[
      (receive_guard,['draft'],1),
      (f"SELECT s.product_id FROM {T('counter_stock')} s JOIN {T('receiving_lines')} l ON l.product_id=s.product_id JOIN {T('receiving_orders')} p ON p.id=l.po_id AND p.branch=s.branch WHERE p.id=$1::jsonb->>'id' ORDER BY s.product_id FOR UPDATE OF s",['draft'],None),
      (f"INSERT INTO {T('receiving_receipts')}(id,po_id,received_date,received_time,reference,notes,has_damage,damage_notes,source) VALUES('RC-'||$2::text,$1::jsonb->>'id',$1::jsonb->>'received_date',$1::jsonb->>'received_time',$1::jsonb->>'reference',$1::jsonb->>'notes',0,'','Company app') RETURNING id",['draft','key'],1),
      (f"INSERT INTO {T('receiving_receipt_lines')} SELECT 'RC-'||$2::text,v.key,round((v.value->>'quantity')::numeric*100)::integer FROM jsonb_each($1::jsonb->'lines') v WHERE (v.value->>'quantity')::numeric>0",['draft','key'],None),
      (f"UPDATE {T('receiving_lines')} l SET received=l.received+r.quantity FROM {T('receiving_receipt_lines')} r WHERE r.line_id=l.id AND r.receipt_id='RC-'||$1::text",['key'],None),
      (f"UPDATE {T('counter_stock')} s SET on_hand=s.on_hand+x.qty FROM (SELECT l.product_id,sum(r.quantity)::integer qty FROM {T('receiving_receipt_lines')} r JOIN {T('receiving_lines')} l ON l.id=r.line_id WHERE r.receipt_id='RC-'||$1::text GROUP BY l.product_id) x WHERE s.product_id=x.product_id AND s.branch=$2::jsonb->>'branch'",['key','draft'],None),
      (f"""INSERT INTO {T('receiving_allocations')}(receipt_id,order_id,product_id,quantity)
       SELECT 'RC-'||$1::text,order_id,product_id,qty FROM (
        SELECT l.order_id,l.product_id,least(l.backorder,greatest(0,s.on_hand-s.reserved-coalesce(sum(l.backorder) OVER(PARTITION BY l.product_id ORDER BY o.created,o.id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0)))::integer qty
        FROM {T('counter_order_lines')} l JOIN {T('counter_orders')} o ON o.id=l.order_id JOIN {T('counter_stock')} s ON s.product_id=l.product_id AND s.branch=o.branch
        WHERE o.branch=$2::jsonb->>'branch' AND l.backorder>0 AND l.product_id IN (SELECT product_id FROM {T('receiving_lines')} WHERE po_id=$2::jsonb->>'id')) x WHERE qty>0""",['key','draft'],None),
      (f"UPDATE {T('counter_order_lines')} l SET reserved=l.reserved+a.quantity,backorder=l.backorder-a.quantity FROM {T('receiving_allocations')} a WHERE a.receipt_id='RC-'||$1::text AND a.order_id=l.order_id AND a.product_id=l.product_id",['key'],None),
      (f"UPDATE {T('counter_stock')} s SET reserved=s.reserved+a.qty FROM (SELECT product_id,sum(quantity)::integer qty FROM {T('receiving_allocations')} WHERE receipt_id='RC-'||$1::text GROUP BY product_id) a WHERE s.product_id=a.product_id AND s.branch=$2::jsonb->>'branch'",['key','draft'],None),
      (f"INSERT INTO {T('receiving_activity')}(po_id,description,source) VALUES($1::jsonb->>'id','Delivery posted: '||($1::jsonb->>'reference'),'Company app') RETURNING id",['draft'],1)],context={'draft':bind('/state/receiptReview'),'key':bind('/request/key')},reset=['receiptForm'])
    E['receive']['set']={'/state/controls/review':''}

    add('schedule','Confirm an available appointment with reserved parts',{'draft':'json','key':'uuid'},[
      (f"UPDATE {T('schedule_lock')} SET revision=revision+1 WHERE id=1 RETURNING id",[],1),
      (f"""INSERT INTO {T('dispatch_commands')}(id,job_id,kind,version,payload,before_state,after_state,expires)
       SELECT $2::text,j.id,'assign',j.version,$1::jsonb::text,
       jsonb_build_object('date',j.scheduled_date,'technician',j.technician,'start',s.start,'end',s.\"end\",'slot',j.slot)::text,$1::jsonb::text,(now()+interval '5 minutes')::text
       FROM {T('hvac_jobs')} j LEFT JOIN {T('dispatch_slots')} s ON s.job_id=j.id WHERE j.id=$1::jsonb->>'id' AND j.version=($1::jsonb->>'version')::integer AND j.status='Scheduled' RETURNING id""",['draft','key'],1),
      (f"""UPDATE {T('hvac_jobs')} j SET scheduled_date=($1::jsonb->>'scheduled_date')::date::text,technician=$1::jsonb->'technician'->>0,
       slot=($1::jsonb->>'start')||'–'||($1::jsonb->>'end'),version=version+1
       WHERE j.id=$1::jsonb->>'id' AND version=($1::jsonb->>'version')::integer AND status='Scheduled'
       AND ($1::jsonb->>'scheduled_date')::date BETWEEN current_date AND current_date+90
       AND $1::jsonb->'technician'->>0 IN ('Alex Morgan','Sam Rivera','Jordan Lee')
       AND ($1::jsonb->>'start') ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' AND ($1::jsonb->>'end') ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
       AND ($1::jsonb->>'start')::time>=time '06:00' AND ($1::jsonb->>'end')::time<=time '20:00' AND ($1::jsonb->>'start')::time<($1::jsonb->>'end')::time
       AND length(trim($1::jsonb->>'reason')) BETWEEN 1 AND 500
       AND NOT EXISTS(SELECT 1 FROM {T('counter_orders')} o JOIN {T('counter_order_lines')} l ON l.order_id=o.id WHERE o.visit_id=j.id AND l.backorder>0)
       AND NOT EXISTS(SELECT 1 FROM {T('hvac_jobs')} other JOIN {T('dispatch_slots')} s ON s.job_id=other.id WHERE other.id<>j.id AND other.status<>'Complete'
        AND other.scheduled_date::date=($1::jsonb->>'scheduled_date')::date AND other.technician=$1::jsonb->'technician'->>0 AND s.start::time<($1::jsonb->>'end')::time AND s.\"end\"::time>($1::jsonb->>'start')::time) RETURNING id""",['draft'],1),
      (f"INSERT INTO {T('dispatch_slots')}(job_id,start,\"end\") VALUES($1::jsonb->>'id',$1::jsonb->>'start',$1::jsonb->>'end') ON CONFLICT(job_id) DO UPDATE SET start=excluded.start,\"end\"=excluded.\"end\"",['draft'],1),
      (f"UPDATE {T('dispatch_commands')} SET committed_version=version+1 WHERE id=$1::text RETURNING id",['key'],1),
      (f"INSERT INTO {T('hvac_activity')}(job_id,description,source) VALUES($1::jsonb->>'id','Appointment confirmed: '||($1::jsonb->>'reason'),'Company app') RETURNING id",['draft'],1)],context={'draft':bind('/state/scheduleReview'),'key':bind('/request/key')},reset=['scheduleForm','jobForm'])
    E['schedule']['set']={'/state/controls/review':''}
    add('submit_report','Append a field report without silently changing job status',{'draft':'json','key':'uuid'},[
      (f"""INSERT INTO {T('field_reports')}(id,job_id,request_id,fingerprint,notes,temperature,pressure,correction_of,source,attachments)
       SELECT 'FR-'||$2::text,j.id,$2::text,md5($1::jsonb::text),$1::jsonb->>'notes',$1::jsonb->>'temperature',$1::jsonb->>'pressure',nullif($1::jsonb->>'correction_of',''),'Company app',coalesce($1::jsonb->'attachments','[]'::jsonb)
       FROM {T('hvac_jobs')} j WHERE j.id=$1::jsonb->>'id' AND length(trim($1::jsonb->>'notes')) BETWEEN 1 AND 4000
       AND jsonb_typeof(coalesce($1::jsonb->'attachments','[]'::jsonb))='array'
       AND jsonb_array_length(coalesce($1::jsonb->'attachments','[]'::jsonb))<=5
       AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(coalesce($1::jsonb->'attachments','[]'::jsonb)) v WHERE jsonb_typeof(v)!='string' OR trim(both '"' from v::text)!~'^[0-9a-f]{{8}}(-[0-9a-f]{{4}}){{3}}-[0-9a-f]{{12}}$')
       AND (nullif($1::jsonb->>'temperature','') IS NULL OR ($1::jsonb->>'temperature')::numeric BETWEEN -50 AND 150)
       AND (nullif($1::jsonb->>'pressure','') IS NULL OR ($1::jsonb->>'pressure')::numeric BETWEEN 0 AND 1000)
       AND (nullif($1::jsonb->>'correction_of','') IS NULL OR EXISTS(SELECT 1 FROM {T('field_reports')} f WHERE f.id=$1::jsonb->>'correction_of' AND f.job_id=j.id)) RETURNING id""",['draft','key'],1)],context={'draft':bind('/state/fieldForm'),'key':bind('/request/key')},reset=['fieldForm'])
