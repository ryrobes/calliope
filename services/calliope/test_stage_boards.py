"""Boards: one Stage card composing existing surfaces on a 12-column grid.

Validation and auto-layout are pure; publish/read need a database and are
exercised on a live appliance. The renderer runs in Chromium against a
synthetic state so the grid, folding and stat/text cells are pinned.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

import json
from test_zen_chat_layout import zen_page  # noqa: F401
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("WAREHOUSE_DSN", "postgresql://x")

import stage_boards  # noqa: E402
import calliope  # noqa: E402

JS = (HERE / "calliope" / "calliope.js").read_text(encoding="utf-8")
CSS = (HERE / "calliope" / "calliope.css").read_text(encoding="utf-8")
SERVER = (HERE / "server.py").read_text(encoding="utf-8")

A, B, C, D, E = (str(uuid4()) for _ in range(5))


def test_validate_shapes_cells_and_rejects_bad_input():
    payload = stage_boards.validate(
        "  Fivetran → Salesforce API calls ", [
            {"kind": "text", "eyebrow": "Warehouse activity", "heading": "API calls", "body": "Connector only."},
            {"kind": "stat", "ref": f"surface:{A}", "column": "calls", "label": "Calls", "format": "compact"},
            {"ref": B, "title": "Call volume", "span": 8},
            {"ref": C, "view": "table", "rows": 2},
        ], subtitle="Rolling window", meta="as of Sep 19")
    assert payload["title"] == "Fivetran → Salesforce API calls"
    assert payload["layout"] == "auto" and payload["size"] == "full"
    kinds = [c["kind"] for c in payload["cells"]]
    assert kinds == ["text", "stat", "surface", "surface"]
    assert payload["cells"][1]["ref"] == A and payload["cells"][1]["row"] == 0
    assert payload["cells"][2]["span"] == 8 and payload["cells"][3]["rows"] == 2
    for bad, message in [
        (dict(title="", cells=[{"ref": A}]), "title is required"),
        (dict(title="t", cells=[]), "cells must be"),
        (dict(title="t", cells=[{"ref": "nope"}]), "surface UUID"),
        (dict(title="t", cells=[{"ref": A, "span": 13}]), "1–12"),
        (dict(title="t", cells=[{"kind": "stat", "ref": A}]), "column is required"),
        (dict(title="t", cells=[{"kind": "text"}]), "needs an eyebrow"),
        (dict(title="t", cells=[{"ref": A}], size="compact"), "wide or full"),
        (dict(title="t", cells=[{"ref": A}, {"ref": B}], layout={"rows": [[0]]}), "every cell"),
        (dict(title="t", cells=[{"ref": A}, {"ref": B}], layout={"rows": [[0, 0], [1]]}), "placed twice"),
    ]:
        with pytest.raises(ValueError, match=message):
            stage_boards.validate(**bad)


def test_autolayout_places_header_stats_charts_and_tables_sensibly():
    cells = [
        {"kind": "text", "heading": "Overview"},
        {"kind": "stat", "ref": A}, {"kind": "stat", "ref": A}, {"kind": "stat", "ref": A},
        {"kind": "surface", "ref": B}, {"kind": "surface", "ref": C},
        {"kind": "surface", "ref": D},
        {"kind": "surface", "ref": E, "span": 5},
    ]
    kinds = {A: "query", B: "chart", C: "chart", D: "query", E: "image"}
    assert stage_boards.autolayout(cells, kinds) == [12, 4, 4, 4, 6, 6, 12, 5]
    # a lone chart takes the row; five stats wrap in quarters; three charts → pair + full
    assert stage_boards.autolayout([{"kind": "surface", "ref": B}], kinds) == [12]
    assert stage_boards.autolayout([{"kind": "stat", "ref": A}] * 5, kinds) == [3, 3, 3, 3, 3]
    assert stage_boards.autolayout([{"kind": "surface", "ref": B}] * 3, kinds) == [6, 6, 12]


def test_explicit_rows_reorder_cells_and_share_width():
    class Conn:
        def execute(self, sql, params):
            ref = params[0]
            kind = {A: "query", B: "chart", C: "chart"}[ref]
            class Row(dict):
                pass
            return type("R", (), {"fetchone": lambda self_: Row(id=ref, kind=kind, title=f"t-{kind}", lineage_key=f"l-{ref}")})()
    payload = stage_boards.validate("t", [{"ref": A}, {"ref": B}, {"ref": C, "span": 8}], layout={"rows": [[1, 2], [0]]})
    stage_boards.resolve(Conn(), "session", payload)
    assert [c["ref"] for c in payload["cells"]] == [B, C, A]
    assert [c["span"] for c in payload["cells"]] == [4, 8, 12]
    assert payload["members"] == sorted([A, B, C])
    assert payload["cells"][0]["member_kind"] == "chart"


def test_resolve_refuses_foreign_nested_or_wrong_kind_refs():
    class Conn:
        def __init__(self, kind):
            self.kind = kind
        def execute(self, sql, params):
            kind = self.kind
            ref = params[0]
            return type("R", (), {"fetchone": lambda self_: None if kind is None else {"id": ref, "kind": kind, "title": "x", "lineage_key": "l"}})()
    with pytest.raises(ValueError, match="not a Stage surface in this chat"):
        stage_boards.resolve(Conn(None), "s", stage_boards.validate("t", [{"ref": A}]))
    with pytest.raises(ValueError, match="cannot nest"):
        stage_boards.resolve(Conn("board"), "s", stage_boards.validate("t", [{"ref": A}]))
    with pytest.raises(ValueError, match="stat cells must reference a query"):
        stage_boards.resolve(Conn("chart"), "s", stage_boards.validate("t", [{"kind": "stat", "ref": A, "column": "n"}]))
    with pytest.raises(ValueError, match="cannot show"):
        stage_boards.resolve(Conn("sketch"), "s", stage_boards.validate("t", [{"ref": A}]))


def test_board_tools_and_projection_are_wired():
    for name in ("publish_board", "read_board", "update_board"):
        assert f'mcp.tool(name="{name}")' in SERVER
    assert "publish_board" in calliope._KNOWN_TOOLS and "update_board" in calliope._KNOWN_TOOLS
    assert "publish_board" in calliope._PUBLISHED_STAGE_TOOLS
    assert "publish_board" in calliope._PROGRESSIVE_TURN_TOOLS
    assert '("publish_board", "board") if canonical in {"publish_board", "update_board"}' in Path(HERE / "calliope.py").read_text()
    assert "QUICK BOARDS:" in Path(HERE / "calliope.py").read_text()
    assert "board: renderBoard," in JS and "function renderBoard(surface)" in JS
    assert "function boardMemberIds(surfaces = state.surfaces)" in JS and "if (assembled.has(String(surface.id))) return false;" in JS
    assert ".surface.kind-board{container:calliope-stage-card/inline-size}" in CSS


def _section(start, end):
    i = JS.index(start)
    return JS[i:JS.index(end, i)]


def test_render_board_in_chromium_folds_members_and_lays_out_cells():
    from playwright.sync_api import sync_playwright
    source = _section("  function boardMember(cell)", "  // Found tile: one `find` call")
    revisions = _section("  function boardRevisions(surface, surfaces = state.surfaces)", "  function visibleStageSurfaces(")
    query = {"id": A, "kind": "query", "title": "Calls by day", "lineage_key": "q1", "created_at": "2026-09-19T00:00:00Z",
             "payload": {"columns": [{"name": "calls"}, {"name": "delta"}], "rows": [{"calls": 48578, "delta": -3.2}]},
             "source": {"sql": "SELECT 1"}}
    chart = {"id": B, "kind": "chart", "title": "Volume", "lineage_key": "c1", "created_at": "2026-09-19T00:00:00Z",
             "payload": {"live": True, "spec": {"mark": "line"}, "size": "wide"}}
    board = {"id": C, "kind": "board", "title": "Board", "lineage_key": "b1", "created_at": "2026-09-19T00:00:00Z",
             "payload": {"subtitle": "Connector only", "meta": "AS OF SEP 19", "members": [A, B], "cells": [
                 {"kind": "text", "eyebrow": "Warehouse activity", "heading": "API calls", "span": 12, "rows": 1},
                 {"kind": "stat", "ref": A, "column": "calls", "label": "Calls", "format": "compact", "delta_column": "delta", "span": 4, "rows": 1},
                 {"kind": "surface", "ref": B, "span": 8, "rows": 1},
                 {"kind": "surface", "ref": A, "view": "table", "span": 12, "rows": 1},
                 {"kind": "surface", "ref": D, "span": 6, "rows": 1},
             ]}}
    import json
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        page.set_content(f"<style>{CSS}</style><div class='surface kind-board' id='root' style='width:1100px'></div>")
        page.add_script_tag(content=f"""
          const state = {{surfaces: {json.dumps([query, chart, board])}}};
          function escapeHtml(s){{return String(s).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;')}}
          function queryColumns(s){{return s.payload.columns}}
          function queryRows(s){{return s.payload.rows}}
          function chartRevisions(s){{return [s]}} function mapRevisions(s){{return [s]}} function answerRevisions(s){{return [s]}}
          function renderQuery(s, o){{return `<div class="stub-query" data-view="${{o?.defaultView}}">${{s.title}}</div>`}}
          function renderVegaChart(s){{return `<div class="chart-body" data-chart-surface="${{s.id}}"></div>`}}
          function renderMetric(){{return ''}} function renderMap(){{return ''}} function renderAnswer(){{return ''}} function renderImage(){{return ''}} function renderFind(){{return ''}}
          function highlightSql(s){{return s}} function formatSql(s){{return s}}
        """)
        page.add_script_tag(content=revisions + source + "\nwindow.renderBoard = renderBoard; window.boardMemberIds = boardMemberIds;")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        report = page.evaluate("""() => {
            const html = renderBoard(state.surfaces[2]);
            document.getElementById('root').innerHTML = html;
            const cells = [...document.querySelectorAll('.board-cell')];
            return {
              html,
              folded: [...boardMemberIds()],
              spans: cells.map(c => c.style.gridColumn),
              classes: cells.map(c => c.className),
              stat: document.querySelector('.board-stat-value')?.textContent.trim(),
              heading: document.querySelector('.board-heading')?.textContent,
              chartHost: document.querySelector('[data-chart-surface]')?.dataset.chartSurface,
              tops: [...new Set(cells.map(c => Math.round(c.getBoundingClientRect().top)))].length,
            };
        }""")
        browser.close()
    assert not errors, errors
    assert sorted(report["folded"]) == sorted([A, B])
    assert report["spans"] == ["span 12", "span 4", "span 8", "span 12", "span 6"]
    assert report["heading"] == "API calls"
    assert report["stat"].startswith("48.6K") and "↘" in report["stat"]
    assert report["chartHost"] == B                     # the live chart keeps its real id so its data endpoint resolves
    assert "member-missing" in report["classes"][4] and "no longer on the Stage" in report["html"]
    assert report["tops"] >= 3                           # header row, stat+chart row, table row, missing row


def test_board_module_ships_in_the_server_image():
    dockerfile = (HERE / "Dockerfile").read_text(encoding="utf-8")
    assert "services/calliope/stage_boards.py" in dockerfile   # the Dockerfile copies modules by name


def test_query_chart_draws_at_host_size_and_table_cells_scroll_once():
    """A query-result chart fills its host instead of letterboxing a fixed viewBox,
    and a table cell inside a board has exactly one scroll container."""
    from playwright.sync_api import sync_playwright
    source = _section("  function queryChartSvg(chart, W, H)", "  function compactNumber(value)")
    chart = {"type": "line", "xLabel": "hour", "yLabel": "calls",
             "points": [{"label": f"h{i}", "value": (i * 37) % 900, "x": i} for i in range(30)]}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        page.set_content(
            f"<style>{CSS}</style>"
            "<div class='surface kind-board' style='width:1000px'><div class='board-grid'>"
            "<section class='board-cell member-query' style='grid-column:span 8;--board-rows:1'><div class='board-cell-body' id='chartcell'></div></section>"
            "<section class='board-cell member-query' style='grid-column:span 4;--board-rows:2'><div class='board-cell-body' id='tablecell'>"
            "<div class='query-root'><div class='query-view' data-query-view='table'><div class='table-wrap'><table class='data-table'><tbody>"
            + "".join(f"<tr><td>row {i}</td></tr>" for i in range(60)) + "</tbody></table></div></div></div></div></section>"
            "</div></div>"
        )
        page.add_script_tag(content=f"""
          const state = {{surfaces: [{{id: 'q1', kind: 'query'}}]}};
          const els = {{stage: document.body}};
          const $$ = (sel, root=document) => [...root.querySelectorAll(sel)];
          function escapeHtml(s){{return String(s)}}
          function compactNumber(v){{return String(v)}}
          function formatValue(v){{return String(v)}}
          function classifyChart(){{return {json.dumps(chart)}}}
        """)
        page.add_script_tag(content=source + "\nwindow.renderChart = renderChart; window.hydrateQueryCharts = hydrateQueryCharts;")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        report = page.evaluate("""() => {
            document.getElementById('chartcell').innerHTML = renderChart(state.surfaces[0]);
            hydrateQueryCharts();
            const host = document.querySelector('[data-query-chart]');
            const svg = host.querySelector('svg');
            const style = getComputedStyle(host);
            const inner = host.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
            const scrollers = [...document.querySelectorAll('#tablecell, #tablecell *')].filter(el => {
              const cs = getComputedStyle(el); return ['auto','scroll'].includes(cs.overflowY) && el.scrollHeight > el.clientHeight;
            }).map(el => el.className);
            return {inner: Math.floor(inner), svgWidth: Number(svg.getAttribute('width')), hostHeight: host.offsetHeight,
                    tableWrapMax: getComputedStyle(document.querySelector('.table-wrap')).maxHeight, scrollers};
        }""")
        browser.close()
    assert not errors, errors
    assert report["svgWidth"] == report["inner"]          # drawn at the measured width, not a scaled 640 viewBox
    assert report["hostHeight"] == 240                     # one row unit
    assert report["tableWrapMax"] == "600px"               # rows: 2 → the table's own wrap grows
    assert report["scrollers"] == ["table-wrap"]           # exactly one scroll container in a table cell


def test_map_member_folds_into_its_board_and_mounts_there(zen_page):
    """A map referenced by a board renders in the board only. Before this the
    map kept its own card as well, and the maps module (hosts keyed by surface
    id) observed only the last host, so the board's copy said "Map loads when
    visible" forever. The canvas also fills the auto-fit cell."""
    import stage_maps
    from test_stage_maps import payload
    from test_stage_packing import prepare
    page = zen_page
    prepare(page, 1100)
    render_payload = payload()
    fc, stats = stage_maps.resolve_sources(render_payload, {
        'places': [{'lon': -74, 'lat': 40.7, 'name': 'One'}, {'lon': -73.95, 'lat': 40.73, 'name': 'Two'}]})
    style = {'version': 8, 'sources': {}, 'layers': [{'id': 'background', 'type': 'background', 'paint': {'background-color': '#172329'}}]}
    page.route('**/map-data', lambda r: r.fulfill(json={
        'sources': fc, 'source_stats': stats, 'results': {'places': {'truncated': False}}, 'render_attempt': 'attempt',
        'runtime': {'enabled': True, 'token': 'pk.test', 'styles': {'dark': style, 'light': style},
                    'gl_js_url': '/vendor/mapbox-gl@3.15.0/mapbox-gl.js', 'gl_css_url': '/vendor/mapbox-gl@3.15.0/mapbox-gl.css'}}))
    page.route('**/map-render', lambda r: r.fulfill(json={'accepted': True}))
    page.route('https://events.mapbox.com/**', lambda r: r.fulfill(status=204))
    page.add_script_tag(path=str(HERE / 'vendor/mapbox-gl@3.15.0/mapbox-gl.js'))
    page.add_style_tag(path=str(HERE / 'vendor/mapbox-gl@3.15.0/mapbox-gl.css'))
    page.evaluate('() => qa.setupEvents()')
    map_surface = {'id': 'map', 'kind': 'map', 'turn_id': 'one', 'ordinal': 1, 'title': 'Customer locations',
                   'lineage_key': 'map', 'payload': render_payload, 'created_at': '2026-09-13T12:00:00Z'}
    board = {'id': 'board', 'kind': 'board', 'turn_id': 'one', 'ordinal': 2, 'title': 'Board', 'lineage_key': 'b1',
             'created_at': '2026-09-13T12:01:00Z',
             'payload': {'size': 'full', 'members': ['map'], 'cells': [{'kind': 'surface', 'ref': 'map', 'span': 8, 'rows': 3}]}}
    page.evaluate('(s) => { qa.state.surfaces = s; qa.renderStage() }', [map_surface, board])
    assert page.locator('.surface.kind-map').count() == 0                 # folded: no standalone card
    assert page.locator('[data-map-surface]').count() == 1
    assert page.locator('.kind-board [data-map-surface]').count() == 1
    page.wait_for_function("document.querySelector('.kind-board .stage-map')?.classList.contains('is-ready')", timeout=25000)
    assert page.locator('.kind-board .mapboxgl-canvas').count() == 1
    body = page.locator('.kind-board .board-cell.member-map .board-cell-body').bounding_box()
    canvas = page.locator('.kind-board .stage-map-canvas').bounding_box()
    controls = page.locator('.kind-board .stage-map-controls').bounding_box()
    assert 380 <= body['height'] <= 420, body       # min(3 rows = 720, 8/12 × ~1100 × .5 + 40 ≈ 400, 70vh)
    assert abs(body['height'] - (canvas['height'] + controls['height'])) <= 6, (body, canvas, controls)


def test_chart_member_fills_its_auto_fit_board_cell(zen_page):
    """A chart cell's height is the rows hint capped by half the cell's width
    and the viewport, and the Vega drawing fills it, axes included. Cells in
    the same grid row stretch to the tallest one and their charts fill that."""
    from test_stage_packing import prepare
    page = zen_page
    prepare(page, 1100)
    for script in calliope._chart_runtime_scripts():
        if script.startswith('/vendor/'):
            page.add_script_tag(path=str(HERE / script.lstrip('/')))
        else:
            page.add_script_tag(content=calliope._chart_interpreter_script())
    spec = {'data': {'values': [{'x': c, 'y': i + 1} for i, c in enumerate('ABCD')]}, 'mark': 'bar',
            'encoding': {'x': {'field': 'x', 'type': 'nominal'}, 'y': {'field': 'y', 'type': 'quantitative'}}}
    charts = [{'id': f'chart-{i}', 'kind': 'chart', 'turn_id': 'one', 'ordinal': i + 1, 'title': f'Chart {i}',
               'lineage_key': f'c{i}', 'created_at': '2026-09-19T00:00:00Z',
               'payload': {'spec': spec, 'size': 'wide', 'content_sha256': f'c{i}'}} for i in range(2)]
    board = {'id': 'board', 'kind': 'board', 'turn_id': 'one', 'ordinal': 3, 'title': 'Board', 'lineage_key': 'b1',
             'created_at': '2026-09-19T00:01:00Z',
             'payload': {'size': 'full', 'members': ['chart-0', 'chart-1'], 'cells': [
                 {'kind': 'surface', 'ref': 'chart-0', 'span': 8, 'rows': 3},
                 {'kind': 'surface', 'ref': 'chart-1', 'span': 4, 'rows': 1}]}}
    page.evaluate('(s) => { qa.state.surfaces = s; qa.renderStage() }', charts + [board])
    page.wait_for_function("document.querySelectorAll('.kind-board .chart-body.is-ready').length === 2", timeout=15000)
    page.wait_for_timeout(300)
    assert page.locator('.surface.kind-chart').count() == 0
    bodies = page.locator('.kind-board .board-cell.member-chart .board-cell-body')
    tall, short = bodies.nth(0).bounding_box(), bodies.nth(1).bounding_box()
    assert 380 <= tall['height'] <= 420, tall           # min(720, 8/12 × ~1100 × .5 + 40 ≈ 400, 630)
    assert abs(short['height'] - tall['height']) <= 1     # the 1-row cell stretches to its row
    svgs = page.locator('.kind-board .chart-canvas svg')
    for index, body in enumerate((tall, short)):
        svg = svgs.nth(index).bounding_box()
        assert body['height'] - 24 <= svg['height'] <= body['height'], (index, svg, body)   # body minus the chart body's 18px padding
        assert svg['y'] + svg['height'] <= body['y'] + body['height'] + 1
        assert svg['width'] > 200
    assert '--board-cell-fit:min(calc(var(--board-rows,1)*240px),calc(var(--board-span,12)*100cqw/12*.5 + 40px),70vh)' in CSS
    assert '.board-cell.member-map .stage-map-canvas{flex:1;min-height:140px;height:auto!important}' in CSS
