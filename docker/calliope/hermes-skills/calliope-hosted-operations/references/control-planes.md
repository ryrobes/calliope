# Calliope appliance control planes

Identify the owning plane before searching or repairing.

| Plane | Owns | Normal access |
|---|---|---|
| Calliope server / Warehouse MCP | Chat, Stage, setup state, artifacts, admin actions, governed tools | Warehouse MCP tools |
| RVBBIT Postgres | Mirrored schemas, SQL functions/operators, metadata, Company Brain, configuration tables | `run_sql` or bounded admin SQL |
| Warren + dlt | Remote read-only connections, mirror jobs, scheduling, loads | mirror tools and action controller |
| Hermes | Agent loop, skills, delegation, gateway, raw Hermes cron | Hermes-native tools/config |
| Hindsight | Hermes assistant memory only, stored in RVBBIT Postgres | Hermes memory provider |
| Company Brain | Company documents, observations, KG, semantic retrieval | Brain tools and RVBBIT data; not Hindsight |
| Warehouse MCP registry | Installed customer MCP servers, discovered tools, generated SQL wrappers | `rvbbit.mcp_*` and Calliope actions |
| Hermes first-party MCP bridge | Calliope's fixed Warehouse, Roundtable, and Specialist lanes only | Hosted config validator and gateway health |
| DataRabbit / Burrow | Advanced inspection and debugging UI | Same Warehouse authentication, separate port |

Important distinctions:

- Every customer/business MCP installed through Calliope lives in the Warehouse registry, not Hermes native MCP. Its absence from `tool_search` is not proof it is missing. Additional Hermes-native entries are drift to report and migrate, never a second supported install path.
- Messaging transports such as Telegram, Discord, Slack, Google Chat, and WhatsApp are Hermes gateway platforms. They are not customer MCP servers.
- Hermes/Hindsight memory and Company Brain knowledge are separate systems.
- `USING rvbbit` still creates ordinary Postgres heap tables; it also registers them for later acceleration.
- RVBBIT settings and many mutations are exposed through SELECT-shaped functions, but some still require a writable transaction.
- The RVBBIT extension is database-bound. Customer copies belong in named schemas inside the RVBBIT database, not another Postgres database.
- Destination table names should preserve source lineage. Use a concise source/dataset schema rather than adding `mirror_` to every table.
