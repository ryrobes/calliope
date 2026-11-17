-- Personify the hosted specialist team without changing any durable keys,
-- Hermes profiles, memory banks, schedules, or conversation relationships.
UPDATE rvbbit.calliope_specialists AS specialist
SET display_name = identity.display_name,
    description = identity.description,
    updated_at = clock_timestamp()
FROM (VALUES
    ('dashboard-studio', 'Iris', 'Dashboard Studio — builds, repairs, validates, and publishes evidence-backed Calliope dashboards and live apps.'),
    ('warehouse-steward', 'Hestia', 'Warehouse Steward — diagnoses and tends the local RVBBIT warehouse, mirrors, catalog, acceleration, and business-context feedback loops.'),
    ('acceleration-steward', 'Atalanta', 'Acceleration Steward — runs reversible evidence-backed acceleration trials and tends canonical and derived RVBBIT layouts.'),
    ('semantic-architect', 'Athena', 'Semantic Architect — tends governed metrics, cubes, value systems, and the business-semantic model learned from actual usage.'),
    ('signal-watch', 'Argus', 'Signal Watch — finds and investigates material business changes while suppressing routine statistical noise.'),
    ('knowledge-gardener', 'Demeter', 'Knowledge Gardener — tends Company Brain coverage, freshness, identity linkage, duplication, and source quality.'),
    ('integration-scout', 'Ariadne', 'Integration Scout — discovers, synthesizes, installs, tests, and remembers external service and MCP integrations.'),
    ('analytical-skeptic', 'Cassandra', 'Analytical Skeptic — adversarially reviews analytical claims, governed definitions, evidence, and visual communication.')
) AS identity(specialist_key, display_name, description)
WHERE specialist.specialist_key = identity.specialist_key;
