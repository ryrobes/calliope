# Actionable tool discovery

Implemented 2026-09-29 following the six capability searches in
`bigfoot-ufo-v5-solo1-sol6`. Validation below covers contracts and replay;
live model behavior still needs a subsequent trial.

## Behavior

`capability_search` keeps the existing caller-authorized retrieval path and
flat `matches` array. `groups` indexes that array into tools, SQL, optional
methods, and other capabilities. Default technical searches reserve at most
two method slots; explicit method searches can still retrieve methods.
Retrieval scores remain ranks, not confidence or proof of suitability.

An exact registered tool name takes the authorization-checked direct lookup
path, without embedding, catalog repair, or data search. Explicit hosted API
questions can also return `artifact_runtime_help` with the relevant topic.
Neither path bypasses capability visibility.

Warehouse attaches its current registry contract. Hermes then resolves each
result against the calling agent's actual enabled/disabled toolsets, direct
tools and scoped deferred catalog. It uses the originating MCP namespace;
there is no fuzzy name repair or cross-server aliasing. A capability can exist
in Warehouse while being unavailable to that specialist.

The result contract includes:

- `catalog_status`: available or not in this agent's scope.
- `tool_name` and `invocation`: the exact callable name and direct/tool_call route.
- `parameters`: a complete small schema, never a truncated schema.
- `schema_status`: included, deferred, in_tool_list, or unavailable.
- `next`: invocation guidance or an exact `tool_describe` lookup for a deferred
  schema. Required arguments remain explicit; search does not manufacture them.

Schemas are bounded to three per result, 1,800 characters each, under a 9,000
character schema budget. Large directly exposed tools use the schema already
declared in the model's tool list. The same enrichment covers native Hermes
`tool_search`; remote connector entries retain their own schema resolver.
No tool is executed or enabled by discovery. No model call, dynamic tool-list
injection or cross-session catalog cache is added.

Enrichment happens at Hermes's shared result-commit boundary before middle
memory archival, result spilling and model-message wrapping. Sequential and
concurrent paths therefore save the contract the model sees. A catalog failure
preserves the original receipt, allowing a rolling upgrade to remain usable.

## Focused hosted API references

`artifact_runtime_help(topic)` offers overview, maps, queries, charts, theme,
publication and verification references. It reuses the existing map/chart
contracts and does not return credentials, a layout starter or a Playbook.
Examples cover array-row normalization, bounded results, partial query failure,
map selection/readiness, theme precedence and persistent browser testing.

Callie and Iris guidance now distinguishes API facts from optional methods and
allows known tools to be used directly. The full template remains available.
Migration 0464 replaces the obsolete external-only/SQL-sibling crawler text
without replacing other crawler extensions or running an embedder. It marks
the catalog stale for its normal refresh path. Warehouse also cleans the old
text while the new database migration is rolling out.

## Validation and limits

- Six recorded searches replay through the real FastMCP registry, Warehouse
  formatting and Hermes scope resolution. Each produces a callable first step
  without another search or schema lookup. These are deterministic receipt
  replays, not evidence that a live model will always choose that step.
- Tests cover both namespaces, restricted profiles, stale schemas, exact-name
  authorization, required arguments, optional methods, payload budgets, catalog
  failure, and the real patched Hermes result-commit function with I/O stubbed.
- Migration 0464 was applied to a disposable PostgreSQL 18 using the actual
  crawler definition. Repeat application, preservation of unrelated body text,
  timestamp invalidation and failure on an unfamiliar upstream body passed.
- FastMCP registration passed under local Python 3.14 and a network-isolated
  Python 3.12 server container. Internal Context annotations remain hidden from
  model schemas in both runtimes.
- Three broader test failures also occur at the starting revision: the retired
  computer-action pin being treated as a subset of current pins, and two old
  Sketch/Playbook markup assertions. Those unrelated tests are unchanged.

Focused checks:

```sh
PYTHONPATH=services/calliope python -m pytest -q \
  services/calliope/test_capability_discovery.py \
  services/calliope/test_capability_discovery_migration.py \
  services/calliope/test_capability_authorization.py

HERMES_DISCOVERY_TEST_ROOT=/path/to/patched/hermes \
  python docker/calliope/test-hermes-capability-discovery.py
```

The Hermes test also runs during the image build, after the patch is checked
and applied. A release needs the Warehouse server, Hermes overlay and prompt
files, plus database migration 0464. When layering images on an existing
release, inventory and preserve any runtime hotfixes before replacing
containers. Apply 0464 transactionally and record it in schema_migrations;
it does not require a PostgreSQL restart.
