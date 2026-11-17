# Business logic: an editable SQL decision graph

Open **Meaning → Business logic** (`/meaning/logic`). This is a web-native editor
for the existing `rvbbit.decision_tree` SQL primitive. It uses Calliope's shared
rail, theme, Superior fonts and translucent surfaces. There is no GPUI/A2UI
translation, separate local database, or Python tree interpreter.

## Work in the graph

Questions, route names/descriptions, outcome labels and thresholds are editable
on their nodes. Add a split at an outcome, add/remove a route at its question,
or undo a structural edit. Bind fields on a node, or drag a column heading from
the source rows onto it. The definition JSON remains available for interchange;
it is not the main authoring surface.

The row picker and results table select the same record. Its field values appear
inside the questions that read them; evaluated ports, edges and outcomes light
up along its actual path. Click a field value to make a **what-if** edit. The
preview is explicitly separate from the SQL result and never writes source data.
Reset restores the source row. After an explicit run, live preview debounces edits
to the current row/tree through the existing scalar evaluator. It can be disabled.
Changing the tree invalidates cohort results; use Run SQL to recompute all rows.
Late responses cannot overwrite newer edits. Scores are model scores, not
calibrated confidence. Incomplete decisions never become a fallback label.

The canvas can be panned, scrolled and zoomed without dropping node content.
Questions, route descriptions and outcome labels grow to show all their text.
The graph measures the rendered nodes and port positions, then relays out the
branches without replacing the focused editor. Font/theme changes remeasure it.
The SQL/rows pane can be collapsed for more canvas space. Definitions and saved
versions live in the Business logic menu; graph edits do not open an inspector.
Dock the SQL/rows pane at the bottom or right, and drag its divider to resize it.
The divider also supports arrow keys (Shift for larger steps). Dock preference
and each orientation's independent percentage are saved in this browser; narrow
windows constrain the displayed size without overwriting the preferred percentage.
Panning suppresses text selection only during a drag that starts on empty canvas;
text inside nodes retains normal selection and copy behavior.

Source SQL, executable SQL and the JSON definition use CodeMirror with PostgreSQL
and JSON grammars and Calliope theme colors. Source SQL and JSON remain editable;
the generated SQL is read-only and selectable. Ctrl/Command+Enter runs source SQL
or applies an open JSON definition. Build the checked-in editor module with
`npm run build:code` in `services/calliope/calliope-editor`.

**Data at step** opens an inspection view attached to a question. For the selected
evaluated row it shows the bound `state.evidence`, fields excluded by the binding,
earlier `state.prior_judgments`, and this question's answer. Expand the full request
to inspect the request envelope reconstructed from the source row and SQL trace;
a regression test compares this directly with the SQL interpreter's recorded calls.
Opening the view makes no inference call. Changed drafts, unvisited branches and
stopped paths never display a speculative request as an evaluated one.

Earlier answers are bundled automatically in path order as judgments containing
`question`, `answer`, `score` and either `choices` or `threshold`. Choice answers
are route strings; binary answers are booleans. A question receives only earlier
judgments, not its own answer. `inputs` selects source keys, not prior answer paths;
named output bindings are not implemented. The final leaf label exists only at the
end of the tree. For independent trees, SQL can explicitly project a first tree's
`decision->>'label'` into a named column for a subsequent scalar call.

## SQL first

**Source SQL** is a normal SELECT returning named columns from authorized tables,
views, joins or CTEs. The initial HVAC example uses a fictional six-row `VALUES`
relation; it creates no tables or policies. It has three questions and six outcomes.

**Run SQL** submits one composed query to Calliope's existing governed SQL runner,
under the caller's normal database role and read-only execution rules. A bounded
source CTE limits inference to 1–24 rows (default 12). PostgreSQL builds each
context with `to_jsonb(source_rows)` and evaluates the scalar operator:

```sql
WITH source_rows AS MATERIALIZED (
  SELECT * FROM your_authorized_source LIMIT 12
),
decision_definition AS (
  SELECT rvbbit.branch(
    'Are all required parts available?',
    yes => rvbbit.leaf('book'),
    no => rvbbit.leaf('check parts')
  ) || '{"inputs":["parts_status"]}'::jsonb AS tree
),
evaluated AS MATERIALIZED (
  SELECT to_jsonb(source_rows) AS input,
         rvbbit.decision_tree(to_jsonb(source_rows), tree, 4) AS decision
  FROM source_rows CROSS JOIN decision_definition
)
SELECT input, decision->>'label' AS outcome,
       decision->>'status' AS status, decision
FROM evaluated;
```

**Use in SQL** exposes the complete executable query for the current draft,
using `rvbbit.branch`, `choice_branch`, `leaf` and field bindings. Copy it into a
SQL editor and join/filter/aggregate its results. A materialized evaluation CTE
avoids repeating inference when projecting several receipt fields. The web
server never loops over rows making per-record Jev calls. It records execution
through the usual Calliope query audit. Preparing/copying SQL runs no inference;
Run SQL has a 30-second deadline. Save versions to retain the source SELECT with
the decision definition. Copying a query embeds that version of the tree rather
than depending on access to another user's private registry rows.

## Field bindings

Question nodes optionally bind `inputs: ["customer_message", "previous_visit_notes"]`:

- Only those exact top-level JSON keys are passed as evidence to that question.
- Missing keys stop the **visited** path with `missing_input`, `missing_inputs`
  and `stopped_at`. No inference runs for that question.
- Null counts as supplied evidence: the question should explicitly handle it.
- Omit `inputs` to retain whole-context behavior for existing definitions.
- Previous branch judgments still accompany the selected evidence as fallible
  interpretations. Binding is not a type checker or a security boundary.

Field bindings require migration **0471_decision_tree_inputs**. Update/apply that
migration before using bound trees against an older extension image. See
[inference/clover-decision-trees.md](inference/clover-decision-trees.md).

## Persistence and interfaces

Calliope Postgres stores current definitions in `rvbbit.calliope_business_logic`
and immutable application revisions in `rvbbit.calliope_business_logic_versions`.
Saving requires the current version; a stale writer receives HTTP 409. Historical
versions can be inspected or used as the starting point of a new version.
Definitions use `{name, topic, tree, source_sql?, examples:[{name, context, expected?}]}`.
Old definitions without `source_sql` continue to load. The saved source is inert
until explicitly run. A saved definition pins the tree, not model weights.

MCP: `read_business_logic(query, logic_id?, version?)`,
`save_business_logic(definition, logic_id?, version=0)`, and
`test_business_logic(context, logic_id?, version?, tree?)`.

Authenticated web endpoints:

- `GET/POST /api/calliope/business-logic`
- `GET /api/calliope/business-logic/{id}?version=N`
- `POST /api/calliope/business-logic/test`: one saved or inline tree + JSON context.
- `POST /api/calliope/business-logic/sql`: `{tree, source_sql, limit?, run?}`;
  defaults to preparing SQL without executing it. Run uses the governed runner.

Existing single-row preview limits remain: 24 KB context, 32 KB tree, 63 nodes,
four questions per path, 2–8 choices and an eight-second timeout. Source SQL is
limited to 16,000 characters; a saved definition including examples is 160 KB.
SQL evaluation retains the SQL operator's context limits and the query runner's
role/safety gates. Definitions are owner-private. No automatic policy discovery,
team sharing or native editor is introduced.

## Verification

`test_business_logic.py` covers persistence, source-query revisions, actor
isolation and authenticated endpoints. `test_business_logic_sql.py` executes
compiled constructors/scalar queries in disposable Postgres, checks inference
cardinality, escaping, limits and the caller-role boundary. Existing interpreter,
UI decision and Meaning route regressions are included. The browser pass checks
SQL cohort results, row selection, live what-ifs, inline splits, Undo and SQL export.
