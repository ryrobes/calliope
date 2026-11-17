# Cube query shelves in Calliope

Open a cube in Data to start its worksheet immediately. At wider panel widths, the field palette and Values/Filters sit beside the result, with Columns and Rows above it. Narrow panels use compact shelves and a Fields toggle. The grid fills the remaining panel height. SQL, cube details and natural-language edits are toolbar actions rather than stacked accordions. Fields can be dragged or added by click to Rows, Columns, Values or Filters. Shared DataRabbit pills provide date grains, aggregation choices, sorting, measure filters and Top N. A single column dimension supports up to 24 distinct values and 96 output columns. Filter or change date grain when the limit is exceeded; categories are never silently discarded.

Each edit automatically queries the entire authorized cube, not its 200-row preview. The result renderer remains bounded. Edits are debounced and obsolete responses cannot replace newer state. The previous successful result remains visible while updating or after an error. Publication is allowed only for the currently applied configuration.

The `RollupSpec` is preserved in draft context and Stage source as `cube_shelf` (relation, spec, definition version). Recent and Explore in Data restore the configuration and saved result without rerunning it. Refresh re-resolves pivot values. Editing a published result creates a private branch and a new Stage surface; it does not mutate the original.

Dataset previews and regular queries use the same compact toolbar and expanding result grid. Their request composer stays below the result; Data opens dataset selection and available field details beside the grid on wide panels or above it on narrow panels. View SQL opens formatted SQL directly. Clarifications, errors and assumptions stay visible, including when optional details are closed.

## Measures

`cube_shelf.metadata` classifies fields and derives rollup semantics from the published cube SQL. Sums and counts roll up by sum; minima/maxima retain their aggregation. A ratio or average is recomputed only when its additive components are also exposed by the cube (for example `SUM(profit)/NULLIF(SUM(sales),0)` or total duration / duration count). Pre-aggregated distinct counts and unrecognized calculations are not offered as silently additive values. Numeric identifiers and grouped fields remain dimensions.

Definitions can explicitly declare `labels.query_shelf.fields.<column>` with `role`, `aggregates`, `default_aggregate` and an optional rule `{kind: "ratio_of_sums", numerator: "profit", denominator: "sales"}`. Components must exist. Rules are declarative, not arbitrary SQL supplied by the client.

## Execution

`GET cubes/metadata`, `POST cubes/values`, and `POST cubes/shelf` live under `/api/calliope/data/`. Current cube authorization precedes definition lookup. The server validates field names, aggregates, filters, date grains, sort/limit values and pivot cardinality; it compiles SQL deterministically and passes it through existing Data SQL validation and role-scoped execution. Model calls are not involved. Probe and run receipts use the existing central tool ledger.

Charts, multi-cube joins, custom SQL expressions and multiple column dimensions are outside this slice. The query model remains compatible with the shared DataRabbit controls; execution is hosted by Calliope rather than the desktop block graph.
