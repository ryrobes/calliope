-- 0320: give managed Calliope specialists richer advisory discovery bearings.
--
-- These values feed specialist capability_search phrasing and operator-facing
-- identity surfaces. They are not permissions and do not restrict the shared
-- Warehouse capability plane.

UPDATE rvbbit.calliope_specialists AS specialist
SET capabilities = bearing.capabilities,
    updated_at = clock_timestamp()
FROM (VALUES
    ('dashboard-studio', '["dashboard_build","dashboard_repair","visual_analysis","design_profiles","artifact_publish","visual_validation","governed_metrics","cubes"]'::jsonb),
    ('warehouse-steward', '["warehouse_health","mirror_diagnosis","dlt_runs","catalog_health","business_context","acceleration_health","adaptive_routing","local_repair"]'::jsonb),
    ('acceleration-steward', '["acceleration_stewardship","adaptive_routing","parquet_layouts","vortex_layouts","workload_variants","refresh_cost","mirror_publication","physical_design_trials"]'::jsonb),
    ('semantic-architect', '["metric_architecture","cube_architecture","value_systems","business_topology","change_graphs","lineage","semantic_reuse"]'::jsonb),
    ('signal-watch', '["change_detection","business_metric_change","value_system_contributors","change_graphs","anomaly_investigation","seasonality","company_brain_evidence"]'::jsonb),
    ('knowledge-gardener', '["company_brain","document_quality","entity_linkage","source_coverage","knowledge_freshness","document_enrichment","business_topology","value_systems"]'::jsonb),
    ('integration-scout', '["capability_discovery","mcp_discovery","integration_install","credential_references","typed_sql_operators","document_ingestion","auth_diagnosis","adapter_synthesis"]'::jsonb),
    ('analytical-skeptic', '["analytical_review","metric_validation","cube_grain","business_topology","value_systems","join_audit","lineage","change_attribution","visual_critique"]'::jsonb)
) AS bearing(specialist_key, capabilities)
WHERE specialist.specialist_key = bearing.specialist_key
  AND specialist.managed;

COMMENT ON COLUMN rvbbit.calliope_specialists.capabilities IS
    'Advisory capability_search bearings for each specialist; not an authorization or tool allowlist.';
