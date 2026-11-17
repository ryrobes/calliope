-- 0373: register the Business Topology domain-expert specialists.
--
-- The Hermes profiles (SOULs cold-boot synthesized from governed domain
-- evidence) ship in the hermes image; this row registration is what makes
-- them part of Calliope's managed roster — the delegate tool's generated
-- doc, the specialist UIs, and delegation routing all read
-- rvbbit.calliope_specialists. No default_schedule: domain experts wake
-- when called.

INSERT INTO rvbbit.calliope_specialists (
    id,specialist_key,profile_name,display_name,description,capabilities,
    default_schedule,managed
) VALUES
(
    'aa00778b-9fbb-57a1-aec1-3b33eeaace75'::uuid,
    'sales-order-book','sales-order-book','Peitho',
    'Sales Order Book domain expert — orders, order lines, customers, products, pricing, and discounts in topology_messy_business.',
    '["domain_expert","sales_orders","order_lines","customers","products","discount_analysis"]'::jsonb,
    NULL,true
),
(
    '887f6e59-b702-5485-8461-acde1a503475'::uuid,
    'outbound-fulfillment','outbound-fulfillment','Talaria',
    'Outbound Fulfillment domain expert — shipments, carriers, dispatch, delivery, and cycle time in topology_messy_business.',
    '["domain_expert","shipments","carriers","fulfillment_cycle_time","dispatch_delivery"]'::jsonb,
    NULL,true
),
(
    'bacc50a7-c22a-5a10-90bc-794208e3167a'::uuid,
    'inventory-stock-position','inventory-stock-position','Amalthea',
    'Inventory Stock Position domain expert — on-hand, reserved, available, and reorder position in topology_legacy_mess.',
    '["domain_expert","inventory_levels","stock_position","reorder_analysis","snapshot_discipline"]'::jsonb,
    NULL,true
),
(
    '8b5827b7-04fe-58fd-8848-5921092e6ada'::uuid,
    'invoice-payment-cycle','invoice-payment-cycle','Danae',
    'Invoice Payment Cycle domain expert — invoice-header settlement, states, and lag in the legacy billing system (topology_legacy_mess).',
    '["domain_expert","invoices","settlement_states","payment_lag","legacy_billing"]'::jsonb,
    NULL,true
)
ON CONFLICT (specialist_key) DO UPDATE SET
    display_name=EXCLUDED.display_name,
    description=EXCLUDED.description,
    capabilities=EXCLUDED.capabilities,
    managed=EXCLUDED.managed,
    updated_at=now();
