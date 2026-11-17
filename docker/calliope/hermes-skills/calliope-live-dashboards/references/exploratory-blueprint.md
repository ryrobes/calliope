# Exploratory dashboard blueprint

This is an optional analytical checklist, not a default composition or visual style.
Use only the portions that serve the user's requested question and interactions.

## Start with a decision, not a component inventory

Write one sentence naming the question the first viewport should answer. Build
the artifact around that sentence. Avoid a wall of interchangeable KPI cards,
an obligatory chart for every query, or a generic executive-dashboard grid.

Prefer this evidence sequence when the data supports it:

1. **Current state** — the important governed number or operational condition,
   with its grain and as-of time.
2. **Movement** — a deliberately chart-shaped time series or before/after
   comparison that establishes direction and magnitude.
3. **Contributors** — a ranked, variance, bridge, composition, or Value System
   view explaining what produced the state or movement.
4. **Exact evidence** — a filterable table of the underlying entities or events
   so the user can inspect rather than merely trust the summary.
5. **Context** — freshness, caveats, lineage, and definitions close to the
   evidence they qualify.

Omit a section when it does not answer the decision question. Add domain-specific
views when they do: funnels for staged processes, cohorts for retention, maps for
geographic distributions, Sankey/flow views for value systems, distributions for
quality or risk, and change graphs for before/after business topology.

## Interaction defaults

- Choose one meaningful initial state; every panel should be useful before a click.
- Use a small number of shared filters that map to business concepts, not raw IDs.
- Let selection in an explanatory view narrow exact evidence when the bridge can
  preserve that relationship.
- Keep aggregate queries complete. Give intentional detail grids explicit bounded
  limits and disclose truncation.
- A result is a table unless its query and analytical purpose were deliberately
  shaped for a chart.

## Visual direction

Follow the user's prompt and references. Only an explicitly pinned Design Profile
supplies additional style constraints. When unpinned, choose the artifact's own
palette, fonts and composition for the task; do not suggest or attach a house style.
The ordering above is optional analytical structure, not a mandatory page layout.

## Quality check

Before publishing, ask whether the first viewport communicates a real finding,
whether each chart has an intelligible axis/encoding, whether the detail supports
the summary, and whether a skeptical user can trace the answer to live warehouse
queries. If not, revise the information architecture rather than adding decoration.
