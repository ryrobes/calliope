# Uncatalogued MCP during setup

Use this when the operator names a service that the packaged action catalog does not contain.

The only supported customer MCP plane is the RVBBIT Warehouse registry. Do not
add the service to Hermes `mcp_servers`; those entries are reserved for
Calliope's fixed first-party Warehouse bridges.

1. Search `search_calliope_actions` broadly without a speculative category filter. A search result is not an installation.
2. If no packaged connector exists, research or use the supplied official endpoint and determine transport, authentication fields, and timeout. Do not invent a credential requirement for a public endpoint.
3. Call `administer_calliope_action` with `action_id=mcp.connect_custom`. Use a short stable server name. Enable SQL functions and RVBBIT operators unless the operator requests otherwise.
4. Verify the action receipt reports reachable/discovered tools. Then invoke one harmless live tool with realistic input.
5. Generated wrappers or `rvbbit.mcp_call` may write gateway bookkeeping rows. If governed `run_sql` rejects the otherwise SELECT-shaped live test as read-only, use `administer_local_sql` for that single call.
6. Obtain exact generated argument names from function metadata; do not guess casing. A remote application-level response can prove connectivity even if the requested remote object is absent.
7. Confirm `rvbbit.mcp_servers`, `rvbbit.mcp_tools`, `rvbbit.mcp_health`, and generated routines. Never select stored environment/secret columns.
8. Close or replace the credential form after success and stay on the optional knowledge step until the operator continues.

For later inspection or repair, switch to `calliope-hosted-operations` and read its MCP reference.
