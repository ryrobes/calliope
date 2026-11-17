---
title: Calliope hosted control planes
kind: calliope_overlay
priority: authoritative
---

# Calliope hosted control planes

This appliance uses Hermes as Calliope's agent and messaging gateway, not as a
second administration product. Do not send a customer to the Hermes dashboard
or ask them to edit files on the host.

## Messaging platforms belong to Hermes

Telegram, Discord, Slack, Google Chat, Teams, WhatsApp, Signal, email, and
other conversation transports are Hermes gateway platforms. Configure their
`platforms` sections through Calliope's Hermes configuration tools. Store all
secret values in the canonical RVBBIT credential store and place only immutable
credential references in durable configuration or revision records.

## Customer MCP servers belong to RVBBIT

Do not install a customer or business MCP server in Hermes merely because
upstream Hermes documentation describes that path. Install it in the RVBBIT
Warehouse registry so its tools become discoverable capabilities and typed,
composable SQL functions. This provides one credential store, one health and
invocation ledger, one repair path, and integration with Company Brain document
ingestion.

Hermes' root `mcp_servers` section is reserved for the three first-party bridges:

- `Warehouse` for Calliope's normal governed tool lane.
- `Roundtable` for the single long-running `@all` fan-out tool.
- `Specialist` for specialist agent callbacks.

Calliope should use `capability_search` before assuming an MCP is absent. Hermes
`tool_search` sees Hermes-native tools and is not an inventory of the Warehouse
registry.

If an existing appliance contains an additional Hermes-native MCP, report the
drift and recommend migration into RVBBIT. Do not silently delete it.

## Hosted configuration invariants

Any candidate Hermes configuration must preserve:

- the Clover-backed `calliope` model and environment-based key reference;
- the authenticated API server on the internal port expected by Calliope;
- local-external Hindsight memory;
- multiplexed specialist profiles and their allowlist;
- all first-party Warehouse MCP bridges, their authentication references,
  session identity forwarding, parallel tool calls, and timeouts.

Validate with the config loader from the exact pinned Hermes build, then run the
Calliope hosted invariant validator. Applying a candidate must create a durable
revision, atomically replace the file, restart only the gateway, verify a real
Warehouse round trip, and automatically roll back on failure.
