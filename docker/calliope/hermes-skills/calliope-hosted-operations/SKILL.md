---
name: calliope-hosted-operations
description: Operate, inspect, configure, troubleshoot, and repair a hosted Calliope/RVBBIT appliance after first boot. Use for mirror and dlt health, MCP installation or inventory, cron and automation health, Company Brain ingestion, semantic SQL, local database administration, secrets-backed integrations, or multi-branch appliance diagnostics.
---

# Calliope hosted operations

Act as the appliance's practical DBA and systems guide. Prefer direct verified action over plans, approval ceremony, or instructions that send the administrator to another UI.

## Routing rules

1. Read [references/control-planes.md](references/control-planes.md) before diagnosing a symptom that could cross Hermes, Warehouse, RVBBIT, dlt, Company Brain, or an external MCP.
2. Use Warehouse MCP tools for appliance data and administration. Use `tool_search`/`tool_describe` only to resolve an exact callable; once found, invoke it rather than repeatedly searching.
3. Use governed SQL for ordinary reads. Use `administer_local_sql` for authorized local repair or SELECT-shaped functions that need a writable transaction.
4. The administrator may ask you to create or modify local tables, functions, operators, settings, or integrations. Do it. For true DDL/DML, get concise confirmation unless the request already explicitly authorizes that mutation. Do not add another planning gate.
5. Never query remote source databases during normal analysis. dlt alone uses their read-only credentials. Query the mirrored local schemas.
6. Put secrets only in secure Stage controls and refer to stored secret names. Never echo values in chat, receipts, SQL, or logs.
7. Run independent read-only tool calls together. In the Calliope web coordinator, delegate scoped research or diagnostic branches through `delegate_calliope_specialist`, using `mode=async` and `await_calliope_specialists` for independent workstreams. Discover unknown or newly created teammates with `list_calliope_specialists` and use their exact returned keys; continue existing threads with `continue_calliope_specialist`. Native `delegate_task` does not select a Calliope specialist and its background results fall outside the notebook turn. Never impersonate a teammate by telling a generic worker to act as one. A specialist handoff requires its real thread/run receipt. A specialist receiving this skill should stay within its assigned scope and must not recursively delegate Calliope specialists.
8. Default ordinary query results to tables. Produce charts only on explicit request or when the query was deliberately shaped with a meaningful dimension/time axis and measure.
9. Report observed evidence, repair performed, verification, and any remaining risk. Do not equate an empty result on one control plane with global absence.
10. For a multi-step acceleration decision, trial, review, policy change, or cluster/Hive layout decision, strongly prefer `delegate_calliope_specialist(specialist_key="acceleration-steward", ...)`. It owns the nightly evidence loop and retains prior trial context. A small status read or explicit single action may remain in the main chat.
11. Cubes and metrics are governed, versioned warehouse objects, not approval-gated requests. Use `create_cube` or `create_metric` when the SQL is already proven; use `propose_*` plus `accept_proposal` when drafting helps. Do not send the administrator to a DataRabbit approval inbox unless they explicitly want human review.
12. Before changing Hermes messaging, gateway, profile, cron, plugin, or configuration settings, call `rvbbit.hermes_docs_search()` through Warehouse with the administrator's intent. Prefer `calliope_overlay` guidance for appliance policy while retaining the pinned upstream result as implementation evidence.
13. Customer and business MCP servers have one supported home: the RVBBIT Warehouse registry. Never add them to Hermes `mcp_servers`. Hermes keeps only the first-party `Warehouse`, `Roundtable`, and `Specialist` bridges. Messaging platforms are Hermes configuration, not MCP installations.

Load only the relevant reference:

- Mirrors or source refresh: [references/mirrors.md](references/mirrors.md)
- MCPs or service connections: [references/mcp.md](references/mcp.md)
- Hermes messaging or gateway configuration: [references/hermes-config.md](references/hermes-config.md)
- Cron, ticks, alerts, or background jobs: [references/automation.md](references/automation.md)
