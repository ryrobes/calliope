-- 0378: roster slim-down — merge the appliance-internals specialists into
-- the Warehouse Steward, and hand all domain staffing to the factory.
--
-- Atalanta (acceleration-steward) and Ariadne (integration-scout) fold into
-- Hestia (warehouse-steward): same audience (the appliance and its operator),
-- same evidence surfaces, one keeper. The four seeded domain experts (0373)
-- were pre-factory prototypes; the domain agent factory (0374) hires
-- equivalents from each business's own promoted topology, so a fresh install
-- fields zero presumed domains and staffs itself. Disabling (not deleting)
-- keeps history and memory; domains freed here re-enter
-- rvbbit.domain_agent_candidates and the factory re-hires them per client
-- (register_generated_specialist upserts the same specialist_key row back to
-- active).

UPDATE rvbbit.calliope_specialists
   SET status = 'disabled', updated_at = now()
 WHERE specialist_key IN (
           'acceleration-steward', 'integration-scout',
           'sales-order-book', 'outbound-fulfillment',
           'inventory-stock-position', 'invoice-payment-cycle')
   AND status = 'active'
   AND managed
   -- A factory rebirth of one of these keys must never be knocked back down
   -- if this migration replays against a restored appliance.
   AND NOT EXISTS (
           SELECT 1 FROM rvbbit.generated_specialists g
            WHERE g.specialist_key = calliope_specialists.specialist_key
              AND g.status = 'active');

UPDATE rvbbit.calliope_specialists
   SET description = 'Warehouse Steward — diagnoses and tends the local '
       || 'RVBBIT appliance end to end. Platform health (warehouse, mirrors, '
       || 'catalog, Business Context), physical performance (acceleration '
       || 'trials and layouts), and external integrations (service and MCP '
       || 'discovery, installation, verification).',
       updated_at = now()
 WHERE specialist_key = 'warehouse-steward' AND managed;
