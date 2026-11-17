# Native chart interactions

`CVegaChart` shares one declaration between web and GPUI:

```json
{
  "id": "chart-status",
  "component": "CVegaChart",
  "spec": {"path": "/charts/status"},
  "height": 260,
  "description": "Visits by status",
  "onSelect": "hvac_chart_select"
}
```

Tooltips follow the spec's `encoding.tooltip`, including titles and Vega's
currency/date formatting. `onSelect` is optional; omitting it makes hover-only
charts. A click sends a normal host action:

```json
{
  "name": "hvac_chart_select",
  "context": {
    "componentId": "chart-status",
    "datum": {"status": "Scheduled", "jobs": 1},
    "tooltip": "Status: Scheduled\nVisits: 1"
  }
}
```

The datum contains scalar fields available at that rendered mark, including
compiler-generated aggregate fields. Treat it as client input, not authority:
HVAC validates the chart ID and dimensions against freshly queried rows, then
uses SQL parameters for the drilldown. Clicks and clearing are navigation, so
unsaved notes remain in the existing per-record draft store. Applying a new
technician/period clears the old chart selection. Revenue points select completed
visits from that week; the other charts select status or technician/work type.
Lists show at most 30 matching visits. No business records are changed by a click.

## Rendering and picking

The worker compiles Vega-Lite once using pinned `vl-convert-python==1.9.0`,
Vega-Lite 6.1, then exports a 2x PNG and scene geometry. The Python package wraps
the same Rust/embedded-JS converter as the earlier CLI. `install-vega.py` unpacks
a SHA-256 verified abi3 wheel locally; system Python packages are untouched.
Re-run that installer for an existing desktop checkout. The Studio image's
existing installer step picks up the extra dependency on its next build.

The engine's scene serializer removes arbitrary datum/tooltip object keys. A
copy of the compiled scene receives metadata as arrays in its description
channel; the actual image keeps its original descriptions. These expressions
come from the trusted compiler/adapter, never authored JavaScript. The original
bounded-inline-data and no-expressions/no-network policy still applies.

GPUI performs pointer picking locally, applying scene origins, nested group
translations, rectangular clipping and ObjectFit::Contain scaling. It does not
invoke the converter or server on hover. Image and geometry install together;
pending or stale conversions cannot dispatch clicks. Leaving the chart clears
the tooltip. Clicks on axes, padding or blank plot space do nothing.

Supported targets: rect/bar marks, heatmap cells, and circle/axis-aligned square symbols
(including points on line charts). Layered heatmap labels use the cell beneath
them. Unsupported mark shapes still render but have no native hit targets.
This is not a full Vega interactive runtime: line segments, areas, arcs, custom
paths, animated transitions, brush/zoom, keyboard chart navigation, and arbitrary
signal/selection programs are not implemented. Rounded rectangle picking uses
the mark's rectangular extent. Exports are bounded to 10,000 targets, 128 datum
fields per mark, 64 tooltip fields and 4 KB tooltip text; the UI shows up to
eight tooltip lines. The existing eight-second worker deadline still applies.

## Verification

- Real pinned-engine tests: all three HVAC charts at 280/600 px, formatted
  tooltips, faceted negative bars and escaped tooltip titles.
- Rust checks: letterboxing/scaling, overlapping targets, circle boundaries,
  malformed geometry, plus actual 2x PNG generation.
- SQL tests: all three drilldowns, refresh/clear, invalid and stale values,
  unchanged database revision and existing mutation behavior.
- Actual GPUI mouse tests and screenshots: hover/click on bar, line point and
  heatmap (including label), two window sizes, blank-space clicks, mouse leave,
  and no chart conversion on hover/click when the spec is unchanged.
- Browser check: heatmap label click reaches the same visit drilldown.

The opt-in native probe reports `render.charts[id].scene`, chart bounds and
`hovered`. Callie's test harness can map a hit's center into the visible chart
rectangle rather than guessing pixels from an image. Production launches do not
enable that socket. Tests use disposable SQL fixtures.
