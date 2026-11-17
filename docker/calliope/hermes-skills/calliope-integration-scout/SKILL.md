---
name: calliope-integration-scout
description: Discover, install, synthesize, verify, and retain reusable MCP and external-service integration recipes.
---

# Calliope integration scouting

1. Clarify the desired business outcome and scope, not merely a product name.
2. Call `capability_search` first. It covers MCPs, functions, actions, and
   system-wide capabilities. Then use `search_calliope_actions`, `search_tools`,
   and `get_tool_help` for callable contracts.
3. If the service is not in the catalog, research its official API/MCP surface
   with available web tools. Prefer a maintained public MCP; otherwise create a
   narrow local adapter with explicit auth, pagination, rate-limit, and error
   behavior.
4. Collect credentials only through `administer_calliope_action` secure input.
   Its native card uses the connector's field definitions and supports conditional
   requirements. Never turn passwords, keys, certificate contents or tokens into
   ordinary chat questions. A saved secret is not a successful connection.
   An OAuth sign-in still waiting for authorization is not connected; point to
   the card's sign-in button and wait for the controller's continuation event.
   Failed attempts remain on the card for repair. Use those receipts rather than
   creating duplicate registrations or asking the user to report that they clicked.
5. Install or repair the reversible local configuration, test a harmless live
   read, and verify that Capability Search discovers the installed result.
   Execute generated MCP SQL wrappers (or `rvbbit.mcp_call`) with
   `run_workspace_sql`: even a read-only remote tool writes a local audit/cache
   receipt and cannot run inside `run_sql`'s read-only transaction. A local
   rollback does not undo remote effects; inspect outcomes before retrying a
   mutating tool.
6. Record reusable recipes, catalog gaps, and verified limitations through
   `record_specialist_finding`. Never put secret values in evidence or memory.

Do not limit discovery to the integrations preinstalled in a fresh appliance.
