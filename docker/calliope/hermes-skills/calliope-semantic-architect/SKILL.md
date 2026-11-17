---
name: calliope-semantic-architect
description: Build and tend governed metrics, cubes, value systems, and Business Topology from real lineage and usage evidence.
---

# Calliope semantic architecture

1. Begin with `business_context_status`, `search_business_context`,
   `business_context_changes`, `list_metrics`, `list_cubes`, and
   `ask_system_learning` as appropriate. Use `capability_search` to discover
   semantic operators or less familiar methods. Do not begin with raw catalog
   inventory.
2. Identify one concrete semantic opportunity or conflict: repeated query
   logic, a missing reusable measure, competing definitions, ambiguous grain,
   a valuable topology/value-system gap, or a stale governed object.
3. Prove the proposed grain, joins, filters, and result with bounded SQL and
   lineage. Business-friendly names are not proof.
4. Reuse or revise an existing metric/cube when possible. Otherwise use
   `create_metric` or `create_cube` directly; no UI approval is required.
5. Record material proposals, warnings, or applied improvements with
   `record_specialist_finding`, using a stable dedupe key. Do not create a
   finding merely to say nothing changed.
6. Finish with the meaning improved, exact evidence, object receipt, and any
   unresolved definition question.

Favor a small coherent semantic layer over a large catalog of near-duplicates.
Never silently choose among genuinely competing business definitions.

## Canonical metrics and reusable cubes

A canonical metric is useful with **no caller arguments**. Define its population,
unit/denominator, actual time basis, default window/boundaries/time zone, and business
purpose. Pass these as `metric_contract` to `create_metric`, `accept_proposal`, or
semantic `edit_metric` changes (`population`, `unit`, `time_basis`, `window`, `why`,
optional `time_zone`, default UTC). Put concrete defaults in `params`. For example,
“sightings in the trailing 30 days, sighted_at in [now-30d, now), UTC, all regions”
is a coherent identity; “count in the selected date range” is incomplete.

Snapshots are saved observations of that identity as it was then. A chart window
selects saved receipts, not new arguments or a recalculation of past periods.
Empty calls and explicit canonical defaults are equivalent. Never combine other
parameter combinations or definition versions into its trend. A justified region
or window variant may have its own named default definition and scheduled snapshots;
do not enumerate combinations speculatively. Missing semantics remain discovery
ideas. Existing unclear metrics need a review, not an invented new meaning or
rewritten historical receipts.

Before creating a cube, compare existing cubes' fact populations, row keys/grains,
dimensions, measures and aggregation rules. Shared branches/products do not make
invoice lines, invoices, stock snapshots and warranty claims the same grain.
A filter, chart, grouping, ranking or selected date window usually needs an analysis
view over an existing cube. A new cube needs a distinct grain, population, security,
refresh or evidenced performance requirement. `create_cube`/`accept_proposal` return
`CUBE_REUSE_REVIEW` with candidate cubes when sources overlap: provide
`reuse={reviewed:[names],difference:"grain",reason:"specific comparison"}` only after
checking those candidates. Exact duplicate SQL is rejected. Preserve existing
consumers and retained history when proposing consolidation.
