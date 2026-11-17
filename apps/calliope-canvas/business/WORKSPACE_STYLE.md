# Calliope workspace pages

Start from `patterns.workspace_template()` or the `starter` object returned by
UI Studio's `catalog` command. That response also includes `pagePattern`, so the
authoring agent receives the recipe alongside the available components. The same
definition renders in web and GPUI; keep styles in catalog tokens, not page CSS.

Use one page identity followed by filters and named work sections. `CSection`
provides a heading, optional purpose, divider and spacing. Arrange sections with
ordinary `CStack` rows/columns and `collapse: "tablet"`; the recipe is not a fixed
dashboard grid. Keep an obvious main task and place related context beside it.

Give containers different jobs:

- `CSurface` / `row`: repeated records or activity, with a quiet bottom divider.
- `inset`: supporting information, notes or charts inside a section.
- `outlined`: a focused editor or actionable business object.
- `plain`: identity and content that need space, but no box.
- `wash`: a meaningful highlight. Avoid nested tinted cards everywhere.

Reserve display typography for page identity. Section headings use body type;
record titles are smaller. Use semantic accents to communicate state or the
current work area. Color and fonts still come from the user's theme.

## Filters

`CFilterBar` groups controls under one data object:

```json
{"id":"filters","component":"CFilterBar","label":"Find work",
 "value":{"path":"/filters"},"onChange":"work_filter",
 "debounceMs":350,"children":["search","owner"]}
```

Bind those controls to `/filters/search`, `/filters/owner`, etc. Keep draft
fields elsewhere, normally `/draft`. The host watches values, debounces changes
(250 ms default; 350 ms works well for search), and sends the resolved object as
the action's `context`. Initial mounting does not issue a filter action. Partial
dates, missing required values and empty single-choice pickers wait for valid
input. Controls stay editable while the request runs; stale replies are ignored.
Navigation cancels pending filter intent. Errors retain the inputs and draft.

Use one filter bar per page in this first version. Its action name must be
`filter` or end in `_filter`, and its server handler must be read-only. The name
constraint is an authoring convention, not authorization: the backend remains
responsible for permissions and SQL validation. Filters do not submit drafts.
Save, post, approve and other business commands remain explicit buttons.

Studio `fill` triggers these same events. Structural mode treats a completed fill
as settled immediately; native/web exercise the real debounce and in-flight
request handling. Generated previews record events without executing handlers.
Run structural assertions before screenshots, then review both wide and narrow
native/web layouts. Keep stable record IDs so input focus and drafts survive
data refreshes.
