# Meaning connections, canonical metrics, and reusable cubes

The Meaning graph is a read-only projection of published areas and registered
objects. It loads the complete authorized area neighborhood, including declared
upstream sources and cube dependencies. Clients collapse whole categories with
exact counts; they never choose a few formulas/sources as a representative sample.
An expanded group or selected object's connections refer to the same full catalog.
Shared-source associations are labeled separately from direct SQL lineage and
explicit subject membership. A connection through a branch/product table does not
establish that two objects measure the same business fact.

Both web and GPUI show cubes, their declared grain, source lineage, registry metadata
and consumers. Numeric cube columns are hints, not declarations of additivity.
Registry SQL stays inspectable. Authorization is enforced before projecting objects;
restricted database roles do not receive service-account aggregate observations.
Opening the graph never evaluates metric SQL or calls a model.

## Canonical identity and snapshots

A metric must give a useful answer without arguments. `metric_contract` records
population, unit/denominator, time basis, default window, business purpose, defaults,
and time zone (UTC if omitted). Concrete default parameters are part of that
versioned identity. The creation, semantic editing, and proposal-acceptance tools
require this contract and prove the scalar default invocation. This does not add an
approval ceremony. Low-level SQL definition primitives remain available; incomplete
legacy metadata is surfaced for review rather than silently inferred.

Example: `sightings_last_30_days`, all reported sightings, `sighted_at` in
`[now() - interval '30 days', now())`, count of sightings, UTC. The existing scheduler
saves observations using the default invocation. Its trend means “the value saved
on each day,” not “recompute each previous date using today's data.” A useful fixed
region or alternate window can be a separately named definition with its own defaults
and snapshots. A temporary interactive query is not automatically a new identity.

Migration 0470 adds `metric_default_observations`, a read-only view restricted to
one current definition version and its effective default scope (`defaults ||
observation.params = defaults`). `{}` and explicit default parameters match; actual
overrides, including explicit nulls, do not. The original observation rows are
untouched. Meaning uses the last saved observation per UTC day, up to 24 days with
receipts, positioned by observation time. Data-as-of remains separately visible.
Floor/Gallery canonical reads use the same version/scope boundary; their existing
period/tick selection remains intact. A definition revision starts a separate series.
No missing historical values are backfilled or recomputed.

Discovery no longer registers implied metrics by choosing a date column and a
30-day default. The compatibility registrar reports `needs_canonical_definition`;
Callie can complete and prove the proposal through the ordinary tools. Earlier
objects and saved receipts stay available, with explicit review notes where their
canonical contract is absent or their scope was inferred.

## Cube reuse

New cube creation and proposal acceptance compare source overlap with latest cube
definitions. Candidates include names, grain, purpose and sources. The caller must
record the reviewed names, a distinction (`grain`, `population`, `security`,
`refresh`, `performance`) and a concrete reason. Exact duplicate SQL is rejected.
This is an evidence requirement, not proof of business equivalence. Filtering or
re-grouping existing facts belongs in a view; new fact grains can justify separate
cubes. Existing definitions are never merged/retired solely on source overlap.

## Verification

`test_semantic_contracts.py` exercises the migration in disposable PostgreSQL,
effective default equivalence, explicit-null overrides, version separation, receipt
preservation, proposal behavior, scalar proof and reuse checks. Graph tests cover
complete neighborhoods, cube lineage and shared-source distinctions; JavaScript
and Rust tests cover whole-group collapse/expansion without identity loss.

## Web exploration

The web client shares evidence and identities with native, not its geometry.
Explore is a complete searchable collection with saved readings, definitions and
review filters. Trace follows one object's recorded inputs and consumers, keeping
shared-source associations, suggested links, and topic membership separate. Its
navigation trail lets the user follow the evidence without losing their place.
The optional map retains card content and uses a minimum readable scale; large
category cards open their complete collection. Expanded categories pack into grids.
The inspector is capped at 520px on desktop, charts at 190px high, and reading mode
at 980px wide. Narrow windows use a bounded bottom inspector. No view runs SQL,
invents dependencies, or changes the recorded business definitions.
