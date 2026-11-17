-- Correct the MCP catalog's old external-only/SQL-sibling guidance. Rewrite
-- the installed crawler rather than replacing later extensions to its body.
-- Refresh documents through the normal stale-index path; no embedding calls
-- or provider dependency in the migration transaction.
DO $migration$
DECLARE
    old_signature text := 'Warehouse MCP connector tool (external agents)';
    new_signature text := 'Warehouse MCP tool; use its tool contract';
    old_suffix text := ' NOT a local SQL function — exposed to external agents via the Warehouse MCP connector. The same ability is usually reachable locally through the rvbbit SQL surface (search this graph for the SQL-side sibling).';
    new_suffix text := ' Call this tool through your connected Warehouse tool catalog. It is not a SQL function; no SQL equivalent is implied.';
    definition text;
BEGIN
    SELECT pg_get_functiondef('rvbbit.capability_crawl(boolean,text)'::regprocedure) INTO definition;
    IF position(old_signature IN definition) = 0 AND position(new_signature IN definition) = 0 THEN
        RAISE EXCEPTION 'capability_crawl MCP contract changed; review migration 0464';
    END IF;
    IF position(old_suffix IN definition) = 0 AND position(new_suffix IN definition) = 0 THEN
        RAISE EXCEPTION 'capability_crawl MCP guidance changed; review migration 0464';
    END IF;
    EXECUTE replace(replace(definition, old_signature, new_signature), old_suffix, new_suffix);
    UPDATE rvbbit.capability_mcp_tools SET updated_at = clock_timestamp();
END
$migration$;
