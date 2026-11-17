# First-boot sequence

1. Read setup state and run preflight without asking the operator to click a diagnostic they do not understand.
2. Collect company name, description, timezone/reporting context, terminology, and other non-secret company context in a native Stage form. Keep optional fields optional. Do not ask for a business question yet.
3. Explain that each remote database needs a dedicated read-only account. Use friendly connection fields and construct the DSN internally.
4. Discover schemas when the source supports introspection. Let the operator select tables from more than one schema and create one mirror job per destination schema without overwriting earlier plans.
5. Queue the mirror and verify controller health, tables succeeded/failed, loaded rows, destination schema, and latest run. Remote access stops at dlt; Calliope queries local copies.
6. Remain on data sources until the operator chooses **Data sources are ready**. Then introduce optional documents and services. Hide unavailable integrations; do not force Google setup.
7. For a named service, install and test it. A verified MCP counts as a connected service in launch review. Do not leave stale secret forms on the Stage after success.
8. Inspect local relation metadata and ask one useful question that the mirrored columns can actually answer. Do not ask the operator to describe what they checked.
9. Run bounded governed SQL with table view. Verify a non-empty result, local RVBBIT engine, and direct lineage to successfully mirrored relations.
10. Put a one-choice approval control on the Stage. Approval records proof; review leaves it pending.
11. Prepare launch review from authoritative state. Show company, administrator, mirror jobs, successful runs, proof, and connected services. Launch and expose a prominent **Open Calliope** action.

Do not advance setup state merely because an agent action succeeded. The setup controller and explicit operator choices own progression.
