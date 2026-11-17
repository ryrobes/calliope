# Stage boards — a quick dashboard is a composition, not a new document

Status: shipped 2026-09-19 (`services/calliope/stage_boards.py`, tools
`publish_board` / `read_board` / `update_board`, renderer `renderBoard` in
`calliope/calliope.js`, tests `test_stage_boards.py`).

## Why

Calliope can build full HTML dashboards and live apps, and they are good, but
a quick "show me these five things together" ask costs the same effort and
ends up either as one layered Vega-Lite spec that fights its own layout or as
six cards scattered down the Stage. Everything such a dashboard needs already
exists as a Stage card: live charts from `publish_chart`, query tables from
`run_sql`, governed metric tiles, maps, answers, images, Found tiles. The
missing piece was a card that holds several of them in a cohesive layout.

## What a board is

A `board` surface whose cells point at other surfaces in the same chat by
UUID, plus two primitives of its own:

- **stat** — one number read from a `run_sql` result (`column`, `row`,
  `format`, optional `delta_column`), shown large with a label and caption.
- **text** — an eyebrow / heading / body / aside block for the header band or
  a note.

The Stage renders every member cell with that surface's own renderer, so
theme, receipts, revisions and the chart data endpoint carry over untouched.
A live chart keeps its real surface id inside the board (its data endpoint is
keyed by id), and the standalone cards a visible board assembles fold into it,
so nothing renders twice.

## Layout

Twelve columns, cells placed in order, `grid-auto-flow: dense`. `layout:
"auto"` derives spans from the cell kinds:

| cells | spans |
| --- | --- |
| text with a heading | 12 |
| a run of stats or metric tiles | 12 ÷ min(4, n) each |
| a run of charts or maps | pairs at 6 + 6; a lone chart 12; an odd last chart 12 |
| a query table, answer, Found tile | 12 |
| an image | 6 |

A cell's own `span` (1–12) always wins, `rows` (1–3) is an upper bound on
height for charts, maps and tables (240px units; the cell auto-fits to half
its own width and to the viewport, so a 3-row hint on a narrow cell does not
leave a box of air, and cells sharing a row stretch to the tallest one with
the chart or map filling it), and `layout: {"rows": [[0, 1], [2]]}` places rows by
cell index with even widths for cells that carry no span. Below 720 px of
card width every cell spans the row.

## Guidance to the agent

The instruction block beside the chart rule says: for a quick dashboard,
publish the pieces (one mark per chart), then compose with `publish_board`
(a text header, a stat row from a `run_sql` result, then charts and tables);
revise with `update_board`. Boards are `published`-phase surfaces and
project progressively for Calliope and specialists alike.

## Revisions and receipts

Same contract as answers and charts: a lineage per board, `replaces_surface_id`
or `update_board` creates a new revision with the old one as parent, identical
content is reused, and only the latest revision shows. The board's payload
records `members` (the assembled surface ids) so the fold and the receipts
tooltip can name them.

## Members render in the board only

A surface referenced by a live board folds off the Stage: it is drawn inside
the board cell and nowhere else. This has to hold for maps in particular,
because the map renderer keys hosts by surface id and mounts only the last
host it finds; a map drawn twice left the board's copy saying "Map loads
when visible" forever. The fold check runs before any per-kind revision
check in `visibleStageSurfaces`.

## Card controls: width, pin, hover

Every sized card (board, chart, map, query, image, Found, metric, cube, value
system) has a header **width** toggle that cycles three whole-column sizes:
small (one packed column: a third of a wide Stage, half of a narrow one),
medium (two columns) and large (every column). Whole columns only, so cards
tile around each other instead of leaving slivers. The choice is stored per
surface id in the browser (`rvbbit-calliope-surface-width-v1`) and only when
it differs from the card's own default, so an agent's later `update_board` /
`update_chart` size change still lands. Values stored by the earlier
half/full toggle read back as small/large.

The **pin** toggle (⤒) keeps a card at the head of the Stage the way a pinned
thread stays at the head of a Page: pinned cards lead the packed timeline in
their own chronology and newer work flows in after them. In the notebook view
they collect in a "Pinned" stratum above the turns. Also viewer-local
(`rvbbit-calliope-surface-pin-v1`); answers, documents and artifacts can be
pinned as well as the sized kinds.

Zen cards are translucent at rest and solid under the pointer. The shared
warehouse theme's `:hover` rule used to outrank the zen rest state, which
produced the reverse (solid at rest, see-through on hover); the two values
were kept and only the triggers swapped. Artifact cards keep a solid rest
state and light mode already ran in this direction.

## Not in v1

Nested boards, refreshing all member queries as one action, drilling from a
stat into its query, and graduating a board into a live app. All four fall
out of the same payload if they earn their place.
