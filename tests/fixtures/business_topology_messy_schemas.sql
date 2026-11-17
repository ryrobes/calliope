-- General Business Topology acceptance corpus.
-- These schemas are intentionally disposable and contain only synthetic data.
-- They combine ordinary joins with the inconsistencies found in operational
-- databases: missing references, duplicate business keys, mixed casing,
-- partial timestamps, negative adjustments, JSON attributes, and dump tables
-- without declared constraints.

DROP SCHEMA IF EXISTS topology_messy_business CASCADE;
DROP SCHEMA IF EXISTS topology_legacy_mess CASCADE;
CREATE SCHEMA topology_messy_business;
CREATE SCHEMA topology_legacy_mess;

CREATE TABLE topology_messy_business.customers (
    customer_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_code text NOT NULL,
    display_name text NOT NULL,
    segment text,
    region text,
    email text,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL
);

CREATE TABLE topology_messy_business.products (
    product_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sku text NOT NULL UNIQUE,
    product_name text NOT NULL,
    category text,
    list_price numeric(12,2),
    active boolean,
    introduced_at date
);

CREATE TABLE topology_messy_business.orders (
    order_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_number text NOT NULL UNIQUE,
    customer_id bigint REFERENCES topology_messy_business.customers(customer_id),
    order_status text NOT NULL,
    sales_channel text,
    currency_code text,
    subtotal numeric(14,2),
    tax_amount numeric(14,2),
    placed_at timestamptz,
    updated_at timestamptz NOT NULL
);

CREATE TABLE topology_messy_business.order_lines (
    order_id bigint NOT NULL REFERENCES topology_messy_business.orders(order_id),
    line_number integer NOT NULL,
    product_id bigint REFERENCES topology_messy_business.products(product_id),
    source_sku text,
    quantity numeric(12,2),
    unit_price numeric(12,2),
    discount_amount numeric(12,2),
    promised_date date,
    PRIMARY KEY (order_id,line_number)
);

CREATE TABLE topology_messy_business.payments (
    payment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint REFERENCES topology_messy_business.orders(order_id),
    processor_reference text,
    payment_status text,
    payment_method text,
    amount numeric(14,2),
    processed_at timestamptz
);

CREATE TABLE topology_messy_business.shipments (
    shipment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint REFERENCES topology_messy_business.orders(order_id),
    warehouse_code text,
    carrier text,
    tracking_number text,
    shipment_status text,
    shipped_at timestamptz,
    delivered_at timestamptz
);

CREATE TABLE topology_messy_business.support_tickets (
    ticket_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id bigint REFERENCES topology_messy_business.customers(customer_id),
    related_order_number text,
    source text,
    priority text,
    current_status text,
    subject text,
    satisfaction_score integer,
    opened_at timestamptz NOT NULL,
    closed_at timestamptz
);

CREATE TABLE topology_messy_business.ticket_events (
    ticket_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id bigint NOT NULL REFERENCES topology_messy_business.support_tickets(ticket_id),
    event_type text NOT NULL,
    actor_team text,
    from_status text,
    to_status text,
    event_payload jsonb,
    occurred_at timestamptz NOT NULL
);

CREATE TABLE topology_messy_business.service_assets (
    asset_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id bigint REFERENCES topology_messy_business.customers(customer_id),
    serial_number text,
    asset_type text,
    model_name text,
    site_region text,
    installed_at date,
    retired_at date
);

CREATE TABLE topology_messy_business.work_orders (
    work_order_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id bigint REFERENCES topology_messy_business.service_assets(asset_id),
    ticket_id bigint REFERENCES topology_messy_business.support_tickets(ticket_id),
    work_order_number text NOT NULL,
    work_type text,
    priority text,
    current_status text,
    estimated_hours numeric(8,2),
    actual_hours numeric(8,2),
    opened_at timestamptz,
    completed_at timestamptz
);

CREATE TABLE topology_messy_business.work_order_events (
    work_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    work_order_id bigint NOT NULL REFERENCES topology_messy_business.work_orders(work_order_id),
    event_type text,
    technician_code text,
    event_note text,
    labor_hours numeric(8,2),
    occurred_at timestamptz
);

CREATE TABLE topology_messy_business.technician_assignments (
    assignment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    work_order_id bigint NOT NULL REFERENCES topology_messy_business.work_orders(work_order_id),
    technician_code text NOT NULL,
    skill_group text,
    assignment_status text,
    assigned_at timestamptz,
    released_at timestamptz
);

INSERT INTO topology_messy_business.customers
    (customer_code,display_name,segment,region,email,attributes,created_at)
SELECT
    'C-'||lpad(g::text,4,'0'),
    CASE WHEN g%17=0 THEN 'Northwind '||(g%5)::text ELSE 'Customer '||g::text END,
    (ARRAY['enterprise','SMB','mid-market',NULL])[1+(g%4)],
    (ARRAY['Northeast','northeast ','West','EMEA','unknown'])[1+(g%5)],
    CASE WHEN g%11=0 THEN '' ELSE 'buyer'||g::text||'@example.test' END,
    jsonb_build_object('source',CASE WHEN g%3=0 THEN 'partner' ELSE 'direct' END,
                       'legacy_tier',CASE WHEN g%9=0 THEN NULL ELSE g%4 END),
    timestamptz '2024-01-01 00:00:00+00'+g*interval '3 days'
FROM generate_series(1,80) g;

-- A duplicate business key with different casing is deliberate.
INSERT INTO topology_messy_business.customers
    (customer_code,display_name,segment,region,email,attributes,created_at)
VALUES ('c-0017','Northwind 2 duplicate','SMB','NORTHEAST',NULL,
        '{"source":"legacy_import"}','2026-01-03 12:00:00+00');

INSERT INTO topology_messy_business.products
    (sku,product_name,category,list_price,active,introduced_at)
SELECT
    'SKU-'||lpad(g::text,4,'0'),'Product '||g::text,
    (ARRAY['Hardware','software','Service Plan','Accessories',NULL])[1+(g%5)],
    CASE WHEN g%13=0 THEN NULL ELSE round((19.5+g*7.31)::numeric,2) END,
    CASE WHEN g%19=0 THEN NULL ELSE g%7<>0 END,
    date '2023-01-01'+g
FROM generate_series(1,45) g;

INSERT INTO topology_messy_business.orders
    (order_number,customer_id,order_status,sales_channel,currency_code,
     subtotal,tax_amount,placed_at,updated_at)
SELECT
    'SO-'||(10000+g)::text,
    CASE WHEN g%23=0 THEN NULL ELSE 1+(g%80) END,
    (ARRAY['paid','PAID','pending','shipped','partially_refunded','cancelled'])[1+(g%6)],
    (ARRAY['web','partner','field sales','Phone'])[1+(g%4)],
    (ARRAY['USD','usd','EUR',NULL])[1+(g%4)],
    CASE WHEN g%29=0 THEN NULL ELSE round((40+g*11.27)::numeric,2) END,
    CASE WHEN g%31=0 THEN -4.25 ELSE round((g*0.83)::numeric,2) END,
    CASE WHEN g%37=0 THEN NULL
         ELSE timestamptz '2025-01-01 08:00:00+00'+g*interval '14 hours' END,
    timestamptz '2025-01-02 08:00:00+00'+g*interval '14 hours'
FROM generate_series(1,240) g;

INSERT INTO topology_messy_business.order_lines
    (order_id,line_number,product_id,source_sku,quantity,unit_price,
     discount_amount,promised_date)
SELECT
    o.order_id,line_no,
    CASE WHEN (o.order_id+line_no)%41=0 THEN NULL
         ELSE 1+((o.order_id*3+line_no)%45) END,
    'SKU-'||lpad((1+((o.order_id*3+line_no)%47))::text,4,'0'),
    CASE WHEN (o.order_id+line_no)%53=0 THEN -1 ELSE 1+((o.order_id+line_no)%5) END,
    round((12+(o.order_id%45)*5.17+line_no)::numeric,2),
    CASE WHEN line_no=3 THEN round((o.order_id%9*1.25)::numeric,2) ELSE 0 END,
    date '2025-01-08'+o.order_id::integer
FROM topology_messy_business.orders o
CROSS JOIN LATERAL generate_series(1,1+(o.order_id%3)::integer) line_no;

INSERT INTO topology_messy_business.payments
    (order_id,processor_reference,payment_status,payment_method,amount,processed_at)
SELECT
    o.order_id,'PAY-'||o.order_id::text||'-'||part::text,
    (ARRAY['captured','settled','failed','refunded'])[1+((o.order_id+part)%4)],
    (ARRAY['card','ACH','wire','store credit'])[1+((o.order_id+part)%4)],
    CASE WHEN (o.order_id+part)%47=0 THEN -25
         ELSE round((coalesce(o.subtotal,0)/parts)::numeric,2) END,
    CASE WHEN o.placed_at IS NULL THEN NULL ELSE o.placed_at+part*interval '2 hours' END
FROM topology_messy_business.orders o
CROSS JOIN LATERAL (SELECT 1+(o.order_id%2)::integer AS parts) p
CROSS JOIN LATERAL generate_series(1,p.parts) part;

INSERT INTO topology_messy_business.shipments
    (order_id,warehouse_code,carrier,tracking_number,shipment_status,shipped_at,delivered_at)
SELECT
    o.order_id,(ARRAY['EAST-1','west_02','3PL'])[1+(o.order_id%3)],
    (ARRAY['UPS','FedEx','Postal',NULL])[1+(o.order_id%4)],
    CASE WHEN o.order_id%17=0 THEN NULL ELSE 'TRACK-'||o.order_id::text END,
    (ARRAY['label_created','in_transit','delivered','lost'])[1+(o.order_id%4)],
    o.placed_at+interval '1 day',
    CASE WHEN o.order_id%4=2 THEN o.placed_at+interval '5 days' ELSE NULL END
FROM topology_messy_business.orders o
WHERE o.order_id%3<>0 AND o.placed_at IS NOT NULL;

INSERT INTO topology_messy_business.support_tickets
    (customer_id,related_order_number,source,priority,current_status,subject,
     satisfaction_score,opened_at,closed_at)
SELECT
    CASE WHEN g%19=0 THEN NULL ELSE 1+(g%80) END,
    CASE WHEN g%4=0 THEN 'SO-'||(10000+(g*2)%240+1)::text ELSE NULL END,
    (ARRAY['email','chat','phone','web form'])[1+(g%4)],
    (ARRAY['low','Normal','high','urgent'])[1+(g%4)],
    (ARRAY['open','waiting_on_customer','resolved','CLOSED'])[1+(g%4)],
    (ARRAY['Invoice question','Login problem','Damaged shipment','Installation help'])[1+(g%4)],
    CASE WHEN g%5=0 THEN NULL ELSE 1+(g%5) END,
    timestamptz '2025-02-01 10:00:00+00'+g*interval '9 hours',
    CASE WHEN g%4 IN (2,3) THEN timestamptz '2025-02-01 10:00:00+00'+g*interval '9 hours'+interval '30 hours' ELSE NULL END
FROM generate_series(1,130) g;

INSERT INTO topology_messy_business.ticket_events
    (ticket_id,event_type,actor_team,from_status,to_status,event_payload,occurred_at)
SELECT
    t.ticket_id,(ARRAY['created','assigned','status_changed','reply'])[1+(event_no%4)],
    (ARRAY['tier_1','billing','field service','customer'])[1+((t.ticket_id+event_no)%4)],
    CASE WHEN event_no=1 THEN NULL ELSE 'open' END,
    CASE WHEN event_no=3 THEN 'resolved' ELSE NULL END,
    jsonb_build_object('imported',t.ticket_id%17=0,'sequence',event_no),
    t.opened_at+event_no*interval '4 hours'
FROM topology_messy_business.support_tickets t
CROSS JOIN LATERAL generate_series(1,2+(t.ticket_id%3)::integer) event_no;

INSERT INTO topology_messy_business.service_assets
    (customer_id,serial_number,asset_type,model_name,site_region,installed_at,retired_at)
SELECT
    1+(g%80),CASE WHEN g%31=0 THEN NULL ELSE 'SN-'||lpad(g::text,6,'0') END,
    (ARRAY['compressor','sensor','gateway','controller'])[1+(g%4)],
    'Model '||(1+g%8)::text,
    (ARRAY['NE','West','emea','UNKNOWN'])[1+(g%4)],
    date '2022-01-01'+g*3,
    CASE WHEN g%23=0 THEN date '2025-01-01'+g ELSE NULL END
FROM generate_series(1,120) g;

INSERT INTO topology_messy_business.work_orders
    (asset_id,ticket_id,work_order_number,work_type,priority,current_status,
     estimated_hours,actual_hours,opened_at,completed_at)
SELECT
    CASE WHEN g%29=0 THEN NULL ELSE 1+(g%120) END,
    CASE WHEN g%3=0 THEN 1+(g%130) ELSE NULL END,
    'WO-'||(5000+g)::text,
    (ARRAY['preventive','repair','inspection','install'])[1+(g%4)],
    (ARRAY['routine','high','emergency',NULL])[1+(g%4)],
    (ARRAY['new','scheduled','in_progress','complete','cancelled'])[1+(g%5)],
    round((1+(g%12)*0.75)::numeric,2),
    CASE WHEN g%7=0 THEN -0.5 ELSE round(((g%15)*0.6)::numeric,2) END,
    timestamptz '2025-03-01 07:00:00+00'+g*interval '11 hours',
    CASE WHEN g%5=3 THEN timestamptz '2025-03-01 07:00:00+00'+g*interval '11 hours'+interval '2 days' ELSE NULL END
FROM generate_series(1,170) g;

INSERT INTO topology_messy_business.work_order_events
    (work_order_id,event_type,technician_code,event_note,labor_hours,occurred_at)
SELECT
    w.work_order_id,
    (ARRAY['dispatched','arrived','diagnosed','work_logged','completed'])[1+(event_no%5)],
    CASE WHEN event_no=1 THEN NULL ELSE 'TECH-'||(1+(w.work_order_id%18))::text END,
    CASE WHEN w.work_order_id%21=0 THEN 'copied from legacy: ???' ELSE 'normal service note' END,
    CASE WHEN event_no=4 THEN round(((w.work_order_id%7)*0.5)::numeric,2) ELSE NULL END,
    w.opened_at+event_no*interval '3 hours'
FROM topology_messy_business.work_orders w
CROSS JOIN LATERAL generate_series(1,2+(w.work_order_id%4)::integer) event_no;

INSERT INTO topology_messy_business.technician_assignments
    (work_order_id,technician_code,skill_group,assignment_status,assigned_at,released_at)
SELECT
    w.work_order_id,'TECH-'||(1+(w.work_order_id%18))::text,
    (ARRAY['electrical','mechanical','network','general'])[1+(w.work_order_id%4)],
    (ARRAY['assigned','accepted','released','no_show'])[1+(w.work_order_id%4)],
    w.opened_at-interval '2 hours',
    CASE WHEN w.completed_at IS NOT NULL THEN w.completed_at ELSE NULL END
FROM topology_messy_business.work_orders w;

-- Constraint-poor legacy exports. Similar-looking columns do not imply joins.
CREATE TABLE topology_legacy_mess.contact_export (
    contact_key text,
    account_ref text,
    full_name text,
    email_address text,
    state_or_region text,
    lifecycle text,
    created_on text,
    last_seen text,
    import_batch text
);

CREATE TABLE topology_legacy_mess.invoice_dump (
    invoice_no text,
    account_ref text,
    invoice_state text,
    amount_text text,
    currency text,
    issued_on text,
    paid_on text,
    owner_name text
);

CREATE TABLE topology_legacy_mess.product_usage_log (
    event_uuid text,
    account_ref text,
    user_ref text,
    event_name text,
    feature_name text,
    quantity_text text,
    event_time text,
    properties text
);

CREATE TABLE topology_legacy_mess.incident_notes (
    note_id text,
    external_case text,
    account_ref text,
    queue_name text,
    severity text,
    note_body text,
    author text,
    logged_at text,
    resolved_flag text
);

CREATE TABLE topology_legacy_mess.inventory_snapshot (
    snapshot_day text,
    warehouse text,
    item_code text,
    item_description text,
    on_hand text,
    reserved text,
    reorder_point text,
    source_file text
);

INSERT INTO topology_legacy_mess.contact_export
SELECT
    CASE WHEN g%17=0 THEN 'dup-'||(g/17)::text ELSE 'contact-'||g::text END,
    CASE WHEN g%13=0 THEN NULL ELSE 'acct-'||(1+g%35)::text END,
    CASE WHEN g%29=0 THEN '' ELSE 'Legacy Person '||g::text END,
    CASE WHEN g%11=0 THEN upper('person'||g::text||'@example.test ')
         ELSE 'person'||g::text||'@example.test' END,
    (ARRAY['NY','New York','ca ','EMEA','?'])[1+(g%5)],
    (ARRAY['Lead','qualified','CUSTOMER','churned',NULL])[1+(g%5)],
    CASE WHEN g%19=0 THEN 'not-a-date' ELSE to_char(date '2024-01-01'+g,'YYYY/MM/DD') END,
    CASE WHEN g%7=0 THEN '' ELSE to_char(timestamptz '2025-01-01+00'+g*interval '2 days','YYYY-MM-DD HH24:MI:SS') END,
    'batch_'||(1+g%6)::text
FROM generate_series(1,180) g;

INSERT INTO topology_legacy_mess.invoice_dump
SELECT
    'INV-'||(8000+g)::text,
    CASE WHEN g%23=0 THEN 'ACCT-'||(1+g%35)::text ELSE 'acct-'||(1+g%35)::text END,
    (ARRAY['open','Paid','PAST DUE','void','partial'])[1+(g%5)],
    CASE WHEN g%31=0 THEN 'N/A' WHEN g%37=0 THEN '-25.00'
         ELSE to_char((25+g*9.17)::numeric,'FM9999990.00') END,
    (ARRAY['USD','usd','EUR',''])[1+(g%4)],
    CASE WHEN g%19=0 THEN '' ELSE to_char(date '2025-01-01'+g,'MM/DD/YYYY') END,
    CASE WHEN g%5=1 THEN to_char(date '2025-01-10'+g,'YYYY-MM-DD') ELSE NULL END,
    (ARRAY['A. Rivera','unassigned','Legacy Queue',NULL])[1+(g%4)]
FROM generate_series(1,220) g;

INSERT INTO topology_legacy_mess.product_usage_log
SELECT
    md5(g::text),
    CASE WHEN g%41=0 THEN NULL ELSE 'acct-'||(1+g%35)::text END,
    'user-'||(1+g%90)::text,
    (ARRAY['login','report_run','export','api_call','session_end'])[1+(g%5)],
    (ARRAY['dashboard','billing','search','api',''])[1+(g%5)],
    CASE WHEN g%47=0 THEN 'many' ELSE (g%12)::text END,
    CASE WHEN g%53=0 THEN '0' ELSE to_char(timestamptz '2025-02-01+00'+g*interval '45 minutes','YYYY-MM-DD"T"HH24:MI:SS"Z"') END,
    CASE WHEN g%9=0 THEN '{broken json' ELSE jsonb_build_object('source','web','seq',g)::text END
FROM generate_series(1,600) g;

INSERT INTO topology_legacy_mess.incident_notes
SELECT
    'N-'||g::text,
    CASE WHEN g%12=0 THEN NULL ELSE 'CASE-'||(1000+g/2)::text END,
    CASE WHEN g%17=0 THEN '' ELSE 'acct-'||(1+g%35)::text END,
    (ARRAY['support','SRE','billing ops','field-team'])[1+(g%4)],
    (ARRAY['sev1','SEV-2','medium','low','unknown'])[1+(g%5)],
    CASE WHEN g%14=0 THEN 'copy/pasted resolution\nwith stack trace'
         ELSE 'Customer-reported operational note '||g::text END,
    (ARRAY['agent-1','agent-2','automation','unknown'])[1+(g%4)],
    CASE WHEN g%27=0 THEN NULL ELSE to_char(timestamptz '2025-04-01+00'+g*interval '6 hours','YYYY-MM-DD HH24:MI') END,
    (ARRAY['Y','N','true','0',''])[1+(g%5)]
FROM generate_series(1,260) g;

INSERT INTO topology_legacy_mess.inventory_snapshot
SELECT
    to_char(date '2025-01-01'+day_no,'YYYY-MM-DD'),
    (ARRAY['EAST','west','3pl',''])[1+((day_no+item_no)%4)],
    'ITEM-'||lpad(item_no::text,4,'0'),
    'Legacy inventory item '||item_no::text,
    CASE WHEN (day_no+item_no)%41=0 THEN '-3' ELSE ((day_no*3+item_no)%120)::text END,
    ((day_no+item_no)%25)::text,
    CASE WHEN item_no%9=0 THEN NULL ELSE (5+item_no%12)::text END,
    'inventory_'||to_char(date '2025-01-01'+day_no,'YYYYMMDD')||'.csv'
FROM generate_series(0,20) day_no
CROSS JOIN generate_series(1,55) item_no;

ANALYZE topology_messy_business.customers;
ANALYZE topology_messy_business.products;
ANALYZE topology_messy_business.orders;
ANALYZE topology_messy_business.order_lines;
ANALYZE topology_messy_business.payments;
ANALYZE topology_messy_business.shipments;
ANALYZE topology_messy_business.support_tickets;
ANALYZE topology_messy_business.ticket_events;
ANALYZE topology_messy_business.service_assets;
ANALYZE topology_messy_business.work_orders;
ANALYZE topology_messy_business.work_order_events;
ANALYZE topology_messy_business.technician_assignments;
ANALYZE topology_legacy_mess.contact_export;
ANALYZE topology_legacy_mess.invoice_dump;
ANALYZE topology_legacy_mess.product_usage_log;
ANALYZE topology_legacy_mess.incident_notes;
ANALYZE topology_legacy_mess.inventory_snapshot;
