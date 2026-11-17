# MCP connection, health, and repair

## One supported customer MCP plane

Customer and business MCP servers belong to the RVBBIT Warehouse registry. Do
not install one into Hermes `mcp_servers`, even when upstream Hermes docs show
that generic installation path. The hosted Hermes root keeps only `Warehouse`,
`Roundtable`, and `Specialist`, which are Calliope's internal bridges.

Locate customer servers through Calliope action search plus
`rvbbit.mcp_servers`, `rvbbit.mcp_tools`, `rvbbit.mcp_health`, and generated
routines. `capability_search` is the normal agent-facing inventory.

`tool_search` reports tools exposed to Hermes through its current tool session;
it does not inventory the Warehouse registry. Never infer that a customer MCP
is absent from `tool_search`. There is no `hermes mcp status` command. Do not
print environment maps, authorization headers, or secret values.
`search_calliope_actions` requires the Calliope notebook UUID, not the internal
Hermes session id.

## Minimal Warehouse inspection

Use governed SELECTs over non-secret columns:

```sql
SELECT name, transport, url, command, timeout_ms, description, auth_header_env, created_at
FROM rvbbit.mcp_servers WHERE name = '<server>';

SELECT name, transport, n_tools, n_resources, last_discovered_at,
       last_call_at, last_error_at, created_at
FROM rvbbit.mcp_health WHERE name = '<server>';

SELECT server, name, description, discovered_at
FROM rvbbit.mcp_tools WHERE server = '<server>' ORDER BY name;

SELECT routine_schema, routine_name
FROM information_schema.routines
WHERE routine_schema = '<server>' OR routine_name ILIKE '%<server>%';
```

Classify explicitly from Warehouse evidence:

- Warehouse configured and discovered: registry, discovered-tool, and health evidence agree.
- Configured but not connected: configuration exists without discovered tools or successful health evidence.
- Missing: no registry row, generated routine, catalog action, or capability result exists.

A Hermes gateway probe with an empty loaded-server list does not override
durable Warehouse registry/tool/last-call evidence. For an inspect-only
request, stop here; do not reinstall, restart, call a remote tool, or run a
mutation merely to prove health.

## Install or repair

Search the full packaged action catalog first. If absent, use `mcp.connect_custom`, discover tools, create typed SQL functions/operators, and invoke one harmless live tool. Use exact generated argument names. For stored credentials, render a secure Stage form once; after saving, immediately test and replace the form with clear success or repair feedback.

Generated wrappers and `rvbbit.mcp_call` may write gateway bookkeeping rows. If a harmless SELECT-shaped live test fails only because governed SQL is read-only, use `administer_local_sql` for that bounded call instead of weakening the governed read path.

For live verification, inspect the discovered tool's stored `input_schema` or
generated tool help before constructing arguments. Then call the generated
wrapper or `rvbbit.mcp_call` once with that exact typed JSON through the local
admin SQL path. Do not spend turns guessing argument shapes or trying the
governed read path first when the gateway will record invocation bookkeeping;
read the health/invocation evidence after the harmless call succeeds.

Do not require a separate planning ceremony for adding or repairing an MCP. Do not ask for a key again when a valid stored secret reference already exists.

## Existing Hermes-native drift

If inspection finds a third-party entry in Hermes `mcp_servers`, do not silently
delete it. Report the duplicated control plane, inspect its non-secret transport
and tool metadata, and offer to recreate it in the Warehouse registry. Verify
the RVBBIT version before removing the Hermes entry.
