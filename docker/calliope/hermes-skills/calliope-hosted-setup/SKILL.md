---
name: calliope-hosted-setup
description: Guide hosted Calliope first boot from managed-service preflight through company context, read-only database mirroring, optional documents and MCP services, a local-data proof, and launch. Use for onboarding, setup checklist, database/schema selection, arbitrary MCP connection during setup, proof verification, or launch readiness.
---

# Calliope hosted setup

Treat the hosted setup state as authoritative. Follow `next_required_item` and `required_order`; do not jump ahead or ask the operator to navigate the checklist.

## Operating contract

1. Welcome the operator briefly, run managed-service preflight yourself, and move to the first item that needs human input.
2. Put structured fields and choices on the Stage. Send useful setup prompts directly. Do not make the operator copy canned prompts into chat.
3. Never request passwords, tokens, DSNs, service-account JSON, or other secrets in chat. Explain the minimum needed, then use the native secure Stage control. You receive references, not secret values.
4. Remote source access belongs exclusively to the dlt mirror controller and must use a read-only source account. Analysis uses only mirrored local RVBBIT relations.
5. Let the operator add multiple schemas and databases. After each successful mirror, stay on data sources until the operator explicitly says they are ready.
6. Introduce documents and services only after data sources are ready. This step is optional. If the operator names a service, search the complete packaged catalog; if absent, synthesize a custom MCP connection in the RVBBIT Warehouse registry instead of declaring it unavailable. Never create a parallel customer MCP installation in Hermes.
7. Ask the first real business question only near the end. Run it through governed local SQL, default to a table, and verify non-empty rows plus direct lineage to mirrored relations.
8. Record setup proof only after explicit approval. Launch only when the authoritative checklist is ready.
9. Keep prose short. State what passed, what needs input, and what control is now on the Stage.

Read [references/first-boot.md](references/first-boot.md) for the exact sequence and proof contract. Read [references/custom-mcp.md](references/custom-mcp.md) only when connecting an uncatalogued service during setup.
