"""Arrangements: the validator's contract, the asset routes, and the editor's
layout rules (no overlap, snapping to free cells, tidy)."""
import subprocess
import uuid
from pathlib import Path

import pytest

import stage_arrangements as sa

ROOT = Path(__file__).parent
REF = str(uuid.uuid4())


def cell(**over):
    base = {"id": "a", "kind": "surface", "ref": REF, "x": 0, "y": 0, "w": 6, "h": 4}
    base.update(over)
    return base


def payload(*cells, **over):
    body = {"title": "Morning", "width": 20, "cells": list(cells)}
    body.update(over)
    return body


def test_validate_shapes_sorts_and_defaults():
    out = sa.validate(payload(cell(id="b", x=6, y=2), cell(id="a", version="latest", view="chart")))
    assert out["unit"] == 60 and out["width"] == 20 and out["deleted"] is False
    assert [c["id"] for c in out["cells"]] == ["a", "b"]  # y, then x
    assert out["cells"][0] == {"id": "a", "kind": "surface", "x": 0, "y": 0, "w": 6, "h": 4, "ref": REF, "version": "latest", "view": "chart"}
    assert "view" not in out["cells"][1]


@pytest.mark.parametrize("bad,message", [
    (payload(cell(x=15)), "right edge"),
    (payload(cell(), cell(id="b", x=3, y=3)), "overlap"),
    (payload(cell(), cell(id="a", x=10)), "used twice"),
    (payload(cell(id="not ok")), "letters, digits"),
    (payload(cell(ref="nope")), "ref"),
    (payload(cell(version="v2")), "version"),
    (payload(cell(view="map")), "table, chart or sql"),
    (payload(cell(kind="text", w=1)), "heading or a body"),
    (payload(cell(kind="text", heading="Hi", w=1)), "smaller than a text cell"),
    (payload(cell(kind="stat", column="total", w=1, h=1)), "smaller than a stat cell"),
    (payload(cell(kind="stat", column="total", w=3, h=2, format="money")), "format"),
    (payload(cell(), unit=40), "unit must be 60"),
    (payload(cell(), width=3), "width"),
    (payload(cell(), title=""), "title"),
    ({"title": "x", "width": 12, "cells": [cell()] * 201}, "at most 200"),
])
def test_validate_rejects(bad, message):
    with pytest.raises(ValueError, match=message):
        sa.validate(bad)


def test_the_editors_stat_shape_validates():
    out = sa.validate(payload(cell(), cell(id="s", kind="stat", x=6, w=3, h=2, column="total", row=0, format="auto", label="total")))
    assert out["cells"][1] == {"id": "s", "kind": "stat", "x": 6, "y": 0, "w": 3, "h": 2, "ref": REF, "version": "latest",
                               "column": "total", "row": 0, "label": "total", "caption": "", "format": "auto"}


def test_any_piece_can_shrink_to_two_by_two():
    """validate() knows a surface cell's kind only after resolve(), so the
    per-kind floor lives in MIN_SIZE (checked there) and in the editor's MIN."""
    assert sa.validate(payload(cell(w=2, h=2)))["cells"][0]["w"] == 2
    assert all(size == (2, 2) for kind, size in sa.MIN_SIZE.items() if kind not in ("stat", "text", "value"))
    assert sa.MIN_SIZE["stat"] == (2, 1) and sa.MIN_SIZE["text"] == (2, 1)
    js = (ROOT / 'calliope' / 'stage-arrange.js').read_text()
    block = js[js.index('const MIN = {'):js.index('};', js.index('const MIN = {'))]
    import re
    assert all(size == '[2, 2]' for kind, size in re.findall(r'(\w+): (\[\d+, \d+\])', block) if kind not in ('stat', 'text', 'value')), block


def test_adjacent_cells_do_not_overlap():
    out = sa.validate(payload(cell(), cell(id="b", x=6), cell(id="c", y=4)))
    assert len(out["cells"]) == 3


def test_fingerprint_ignores_cell_order_and_deleted_flag():
    one = sa.validate(payload(cell(), cell(id="b", x=6)))
    two = sa.validate(payload(cell(id="b", x=6), cell()))
    assert sa.fingerprint(one) == sa.fingerprint(two)
    assert sa.fingerprint(one) != sa.fingerprint(sa.validate(payload(cell(), cell(id="b", x=7))))


def test_superseded_error_carries_the_latest_id():
    error = sa.SupersededError("abc")
    assert isinstance(error, ValueError) and error.latest_surface_id == "abc"
    assert "revised elsewhere" in str(error)


@pytest.mark.parametrize('name,mime', [('stage-arrange.js', 'text/javascript'), ('stage-arrange.css', 'text/css')])
def test_arrange_assets_are_registered_authenticated_and_cache_versioned(name, mime):
    import ast
    import asyncio
    from types import SimpleNamespace
    from starlette.responses import Response
    import calliope
    path = '/calliope/' + name
    tree = ast.parse((ROOT / 'calliope.py').read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
              and any(isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant)
                      and d.args[0].value == path for d in n.decorator_list))
    fn.decorator_list = []
    namespace = {'_canonical_owner': lambda r: (r.owner, None), 'Response': Response,
                 'browser_asset': lambda request, asset, media_type: Response(
                     (ROOT / 'calliope' / asset).read_bytes(), media_type=media_type)}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), '<arrange-asset-route>', 'exec'), namespace)
    request = SimpleNamespace(owner=None, url=SimpleNamespace(path=path))
    assert asyncio.run(namespace[fn.name](request)).status_code == 401
    request.owner = 'viewer@example.com'
    response = asyncio.run(namespace[fn.name](request))
    assert response.status_code == 200
    assert response.body == (ROOT / 'calliope' / name).read_bytes()
    assert response.headers['content-type'].startswith(mime)
    assert name in calliope._CACHE_VERSIONED_ASSETS


def test_page_loads_the_module_and_hosts():
    html = (ROOT / 'calliope' / 'index.html').read_text()
    assert 'stage-arrange.js?v=__CALLIOPE_ASSET_VERSION__' in html
    assert 'stage-arrange.css?v=__CALLIOPE_ASSET_VERSION__' in html
    assert 'id="stage-arrange"' in html and 'id="stage-arrange-control"' in html


def test_the_arranging_layout_is_a_class_the_stage_sets_not_a_has_selector():
    """A :has() rule on the Stage column that ended in a universal child made
    every edit anywhere on the page restyle the whole document: 12 ms of each
    keystroke in a 36-turn chat, growing with the chat. The Stage names the
    state itself, and no Calliope stylesheet pairs :has() with a universal
    selector again."""
    import re
    css = (ROOT / 'calliope' / 'stage-arrange.css').read_text()
    rules = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    assert '.stage-scroll.is-arranging > :not(.stage, #system-stage)' in rules
    assert '.stage.is-arranging' in rules
    assert not re.search(r'\.stage(-scroll)?:has\(', rules)
    script = (ROOT / 'calliope' / 'calliope.js').read_text()
    assert "els.stage.classList.toggle('is-arranging', arranging)" in script
    assert "els.stageScroll?.classList.toggle('is-arranging', arranging)" in script
    universal = re.compile(r':has\([^{}]*\)[^{},]*(?:[>+~ ]\s*(?:\*|:not\())')
    for sheet in sorted((ROOT / 'calliope').glob('*.css')):
        if sheet.name == 'data-runtime.css':  # generated bundle for the data panel frame
            continue
        text = re.sub(r'/\*.*?\*/', '', sheet.read_text(), flags=re.S)
        for selector in re.findall(r'([^{}]+)\{', text):
            assert not universal.search(selector), f'{sheet.name}: {selector.strip()[:120]}'


def test_page_route_and_callie_tools_are_wired():
    server = (ROOT / 'server.py').read_text()
    calliope_py = (ROOT / 'calliope.py').read_text()
    import calliope
    import stage_delivery
    for name in ("publish_arrangement", "read_arrangement", "update_arrangement"):
        assert f'mcp.tool(name="{name}")' in server
    assert {"publish_arrangement", "update_arrangement"} <= calliope._KNOWN_TOOLS
    assert {"publish_arrangement", "update_arrangement"} <= calliope._PUBLISHED_STAGE_TOOLS
    assert {"publish_arrangement", "update_arrangement"} <= stage_delivery.STAGE_TOOLS
    assert '("arrangement", "arrangement") if canonical in {"publish_arrangement", "update_arrangement"}' in calliope_py
    assert '@mcp.custom_route("/calliope/arrangements/{lineage}", methods=["GET"])' in calliope_py
    assert '"arrangement" if page else "chat"' in calliope_py and '.replace("__CALLIOPE_PAGE__", page)' in calliope_py
    assert 'data-calliope-page="__CALLIOPE_PAGE__"' in (ROOT / 'calliope' / 'index.html').read_text()


class _Conn:
    """Answers the arrangement reader's two lookups: by id, then by lineage."""
    def __init__(self, by_id=None, by_lineage=None, turn=None):
        self.by_id, self.by_lineage, self.turn, self.sql = by_id, by_lineage, turn, []
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def transaction(self):
        from contextlib import nullcontext
        return nullcontext()
    def execute(self, sql, params=()):
        from types import SimpleNamespace
        self.sql.append((sql, params))
        if "WHERE id=%s::uuid AND kind='arrangement'" in sql:
            return SimpleNamespace(fetchone=lambda: self.by_id)
        if "WHERE lineage_key=%s AND kind='arrangement'" in sql:
            return SimpleNamespace(fetchone=lambda: self.by_lineage)
        if "ORDER BY created_at DESC,ordinal DESC,id DESC LIMIT 200" in sql:
            return SimpleNamespace(fetchall=lambda: [{"id": (self.by_id or self.by_lineage)["id"], "title": "T", "created_at": None, "source": {}}])
        if "FROM rvbbit.calliope_turns WHERE id=%s::uuid AND session_id=%s::uuid" in sql:
            return SimpleNamespace(fetchone=lambda: self.turn)
        if "pg_advisory_xact_lock" in sql:
            return SimpleNamespace()
        raise AssertionError(sql)


def test_read_accepts_a_lineage_uuid_and_names_the_page():
    import datetime as dt
    lineage = str(uuid.uuid4()); session = str(uuid.uuid4()); surface = str(uuid.uuid4())
    row = {"id": surface, "title": "Morning", "payload": {"width": 12, "cells": []}, "parent_surface_id": None,
           "session_id": session, "lineage_key": f"arrangement:{lineage}", "created_at": dt.datetime(2026, 1, 1)}
    out = sa.read(lambda: _Conn(by_id=None, by_lineage=row), lineage, "alice@example.com")
    assert out["surface_id"] == surface and out["lineage_key"] == f"arrangement:{lineage}"
    assert out["page_url"] == f"/calliope/arrangements/{lineage}"
    assert out["chat_url"] == f"/calliope?session={session}&arrangement={lineage}"
    assert sa.read(lambda: _Conn(), "not-a-uuid", "alice@example.com") is None
    assert sa.read(lambda: _Conn(), lineage, "alice@example.com") is None


def test_restoring_an_earlier_layout_is_a_new_revision(monkeypatch):
    """A restored layout repeats an older revision's content hash; it must
    still become its own row (the latest-identical reuse rule aside)."""
    from types import SimpleNamespace
    from contextlib import nullcontext
    monkeypatch.setattr(sa.calliope_access, "require_participant", lambda conn, session, actor: {"role": "owner"})
    session = str(uuid.uuid4()); parent = str(uuid.uuid4())
    old_payload = sa.validate(payload(cell()))
    latest_payload = sa.validate(payload(cell(), cell(id="b", x=6)))
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def transaction(self): return nullcontext()
        def execute(self, sql, params=()):
            if "pg_advisory_xact_lock" in sql: return SimpleNamespace()
            if "ORDER BY ordinal DESC LIMIT 1" in sql: return SimpleNamespace(fetchone=lambda: {"id": uuid.uuid4()})
            if "SELECT lineage_key FROM" in sql: return SimpleNamespace(fetchone=lambda: {"lineage_key": "arrangement:L"})
            if "SELECT id,payload,parent_surface_id" in sql: return SimpleNamespace(fetchone=lambda: {"id": parent, "payload": latest_payload, "parent_surface_id": None})
            if "SELECT id,kind,title,lineage_key FROM rvbbit.calliope_surfaces" in sql or ("kind" in sql and "WHERE id=%s::uuid AND session_id=%s::uuid" in sql):
                return SimpleNamespace(fetchone=lambda: {"id": REF, "kind": "query", "title": "Sales", "lineage_key": "q:1"})
            raise AssertionError(sql)
    # The stored latest carries the hash of its resolved payload, as publish writes it.
    sa.resolve(Conn(), session, latest_payload); latest_payload["content_sha256"] = sa.fingerprint(latest_payload)
    inserted = []
    import calliope
    monkeypatch.setattr(calliope, "_insert_surfaces", lambda factory, sid, turn, rows: inserted.append(rows[0]) or [{"id": str(uuid.uuid4()), **rows[0]}])
    monkeypatch.setattr(sa, "_surface_row", lambda conn, surface_id: {"id": str(surface_id)})
    one = sa.publish(lambda: Conn(), session, "alice@example.com", {k: old_payload[k] for k in ("title", "width", "cells")}, parent)
    two = sa.publish(lambda: Conn(), session, "alice@example.com", {k: old_payload[k] for k in ("title", "width", "cells")}, parent)
    assert one["reused"] is False and two["reused"] is False
    assert inserted[0]["tool_call_id"] != inserted[1]["tool_call_id"], "the row's identity is not the content hash"
    assert inserted[0]["payload"]["content_sha256"] == inserted[1]["payload"]["content_sha256"], "same layout, same hash, two rows"
    same = sa.publish(lambda: Conn(), session, "alice@example.com", {k: latest_payload[k] for k in ("title", "width", "cells")}, parent)
    assert same["reused"] is True and len(inserted) == 2, "identical to the latest revision: nothing new is written"


def test_callies_publish_attaches_to_the_turn_it_serves_and_refuses_a_foreign_turn(monkeypatch):
    monkeypatch.setattr(sa.calliope_access, "require_participant", lambda conn, session, actor: {"role": "owner"})
    session = str(uuid.uuid4())
    with pytest.raises(ValueError, match="not part of this chat"):
        sa.publish(lambda: _Conn(turn=None), session, "alice@example.com", payload(cell()), turn_id=str(uuid.uuid4()))
    with pytest.raises(ValueError, match="turn_id"):
        sa.publish(lambda: _Conn(turn=None), session, "alice@example.com", payload(cell()), turn_id="nope")


def test_editor_places_without_overlap_and_tidies_upward():
    """Drive the module against a minimal DOM: adds land in free cells, a move
    onto an occupied cell snaps to the nearest free one, tidy packs upward,
    and the autosave body carries only the layout fields."""
    result = subprocess.run(['node', '-'], input=r'''
      const assert = require('node:assert/strict');
      // A DOM just large enough for the module to build its skeleton.
      const el = () => { const node = {children: [], dataset: {}, style: {}, classList: {add(){}, remove(){}, toggle(){}},
        attributes: {}, hidden: false, innerHTML: '', textContent: '',
        addEventListener(){}, removeEventListener(){}, setAttribute(k, v){ this.attributes[k] = v; }, removeAttribute(){}, toggleAttribute(){},
        appendChild(c){ this.children.push(c); return c; }, append(c){ this.children.push(c); }, insertAdjacentHTML(){}, remove(){},
        replaceWith(){}, querySelector(){ return null; }, querySelectorAll(){ return []; }, closest(){ return null; },
        getBoundingClientRect(){ return {left: 0, top: 0, right: 1200, bottom: 800}; }, focus(){}, clientWidth: 1200, clientHeight: 800};
        return node; };
      const sheet = el(), scroll = el(), rail = el(), toolbar = el();
      const host = el(); host.querySelector = sel => ({'.arrange-rail': rail, '.arrange-toolbar': toolbar, '.arrange-sheet': sheet, '.arrange-scroll': scroll})[sel] || null;
      sheet.querySelector = () => null; rail.querySelector = () => el();
      global.window = {}; global.document = {createElement: el, getElementById: () => null, addEventListener(){}};
      global.location = {href: 'http://x/calliope?session=s1', search: '?session=s1'};
      global.history = {state: null, replaceState(){}}; global.localStorage = {getItem: () => null, setItem(){}};
      global.URL = require('node:url').URL; global.URLSearchParams = require('node:url').URLSearchParams;
      global.CSS = {escape: s => s};
      require('./calliope/stage-arrange.js');
      const saves = [];
      const api = async (path, options) => { saves.push({path, body: JSON.parse(options.body)}); return {surface: {id: 'arr1', lineage_key: 'arrangement:L', payload: {}}, lineage_key: 'arrangement:L'}; };
      const arrange = window.CalliopeStageArrange.create({host, control: null, scroll, api, renderSurface: () => '<i></i>', hydrate(){}, canEdit: () => true, toast(){}});
      const surfaces = [
        {id: 'q0', kind: 'query', title: 'Sales', lineage_key: 'q:1', turn_id: 't0', created_at: '2025-12-31T00:00:00Z', payload: {columns: [{name: 'region'}, {name: 'total'}], rows: [['North', 10]]}},
        {id: 'q1', kind: 'query', title: 'Sales', lineage_key: 'q:1', turn_id: 't1', created_at: '2026-01-01T00:00:00Z', payload: {columns: [{name: 'region'}, {name: 'total'}], rows: [['North', 12]]}},
        {id: 'c1', kind: 'chart', title: 'Trend', lineage_key: 'c:1', turn_id: 't1', created_at: '2026-01-01T00:01:00Z'},
        {id: 'a1', kind: 'artifact', title: 'Deck', lineage_key: 'a:1', turn_id: 't2', created_at: '2026-01-01T00:02:00Z', artifact_slug: 'deck', artifact_version: 3},
      ];
      arrange.update('s1', surfaces, [{id: 't0'}, {id: 't1'}, {id: 't2'}], surfaces);
      arrange.setActive(true);
      assert.equal(arrange.active, true);
      assert.ok(arrange.addSurface('q1'));          // 10x5 at 0,0
      assert.ok(arrange.addSurface('c1'));          // 8x5 lands beside it at 10,0 (20 columns)
      assert.ok(arrange.addSurface('a1', {x: 2, y: 1})); // asked for an occupied spot: snaps to the nearest free cell
      const cells = arrange.current.cells;
      assert.equal(cells, 3);
      // A stat block reads one column of the query block; a block can follow
      // one revision of its lineage instead of the latest.
      const queryCell = arrange.cells.find(c => c.ref === 'q1');
      assert.ok(arrange.addStat(queryCell.id, 'total'));
      assert.equal(arrange.addStat(queryCell.id, ''), false);
      assert.equal(arrange.setVersion(queryCell.id, 'c1'), false, 'a chart is not a revision of the query');
      assert.ok(arrange.setVersion(queryCell.id, 'q0'));
      assert.equal(arrange.current.cells, 4);
      setTimeout(() => {
        const body = saves.at(-1).body;
        const stat = body.cells.find(c => c.kind === 'stat');
        assert.deepEqual(stat, {...stat, ref: 'q1', version: 'latest', column: 'total', format: 'auto', label: 'total', w: 3, h: 2});
        assert.equal(stat.row, 0);
        assert.equal(body.cells.find(c => c.id === queryCell.id).version, 'q0');
        const rects = body.cells;
        for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
          const a = rects[i], b = rects[j];
          assert.ok(!(a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h), `${a.id} overlaps ${b.id}`);
        }
        assert.ok(rects.every(r => r.x >= 0 && r.x + r.w <= body.width));
        assert.equal(body.columns, undefined);
        assert.deepEqual(Object.keys(rects[0]).sort(), ['h', 'id', 'kind', 'ref', 'version', 'w', 'x', 'y']);
        assert.equal(body.unit, 60);
        assert.ok(saves.every(s => s.path.includes('/arrangements')));
        console.log('ok');
      }, 1200);
    ''', capture_output=True, text=True, cwd=ROOT, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'ok'


def test_history_scrubs_revisions_describes_edits_and_restores():
    """The arrangement's revisions are its history: grouped per person and
    pause, described by what changed, viewable read-only at any point with
    members as they stood then, and restorable as a new revision."""
    result = subprocess.run(['node', '-'], input=r'''
      const assert = require('node:assert/strict');
      const el = () => { const node = {children: [], dataset: {}, style: {}, classList: {add(){}, remove(){}, toggle(){}, contains(){ return false; }},
        attributes: {}, hidden: false, innerHTML: '', textContent: '',
        addEventListener(){}, removeEventListener(){}, setAttribute(k, v){ this.attributes[k] = v; }, removeAttribute(){}, toggleAttribute(){},
        appendChild(c){ this.children.push(c); return c; }, append(c){ this.children.push(c); }, insertAdjacentHTML(){}, remove(){},
        replaceWith(){}, querySelector(){ return null; }, querySelectorAll(){ return []; }, closest(){ return null; },
        getBoundingClientRect(){ return {left: 0, top: 0, right: 1200, bottom: 800}; }, focus(){}, clientWidth: 1200, clientHeight: 800};
        return node; };
      const sheet = el(), scroll = el(), rail = el(), toolbar = el(), parts = {};
      const host = el(); host.querySelector = sel => ({'.arrange-rail': rail, '.arrange-toolbar': toolbar, '.arrange-sheet': sheet, '.arrange-scroll': scroll})[sel] || null;
      sheet.querySelector = () => null; rail.querySelector = sel => (parts[sel] ||= el());
      global.window = {}; global.document = {createElement: el, getElementById: () => null, addEventListener(){}};
      global.location = {href: 'http://x/calliope?session=s1', search: '?session=s1'};
      global.history = {state: null, replaceState(){}}; global.localStorage = {getItem: () => null, setItem(){}};
      global.URL = require('node:url').URL; global.URLSearchParams = require('node:url').URLSearchParams; global.CSS = {escape: s => s};
      require('./calliope/stage-arrange.js');
      const {describeChange} = window.CalliopeStageArrange;
      const A = {id: 'a', kind: 'surface', ref: 'q1', member_title: 'Sales', x: 0, y: 0, w: 6, h: 3};
      assert.equal(describeChange(null, {cells: [A]}), 'created with 1 block');
      assert.equal(describeChange({title: 'T', width: 12, cells: [A]}, {title: 'T', width: 12, cells: [{...A, x: 2, w: 8}, {id: 'b', kind: 'text', heading: 'Note', x: 0, y: 4, w: 6, h: 1}]}), 'added Note, moved 1, resized 1');
      assert.equal(describeChange({title: 'T', width: 12, cells: [A]}, {title: 'Morning', width: 16, cells: []}), 'removed Sales, titled “Morning”, width 12→16');
      assert.equal(describeChange({cells: [A]}, {cells: [A]}), 'no layout change');

      const saves = []; let travelled = 0;
      const rendered = [];
      const arrange = window.CalliopeStageArrange.create({host, control: null, scroll, api: async (path, o) => { saves.push(JSON.parse(o.body)); return {surface: {id: 'r9', lineage_key: 'arrangement:L', payload: {}}, lineage_key: 'arrangement:L'}; },
        renderSurface: s => { rendered.push(s.id); return '<i></i>'; }, hydrate(){}, canEdit: () => true, toast(){}, viewer: () => 'alice@x', onTimeTravel: () => { travelled++; }});
      const q0 = {id: 'q0', kind: 'query', title: 'Sales', lineage_key: 'q:1', turn_id: 't1', created_at: '2026-01-01T00:00:00Z'};
      const q1 = {id: 'q1', kind: 'query', title: 'Sales', lineage_key: 'q:1', turn_id: 't2', created_at: '2026-01-01T02:00:00Z'};
      const rev = (id, at, actor, cells, title = 'Morning') => ({id, kind: 'arrangement', title, lineage_key: 'arrangement:L', turn_id: 't1', created_at: at, source: {actor},
        payload: {title, width: 12, unit: 60, cells}});
      const r1 = rev('r1', '2026-01-01T01:00:00Z', 'alice@x', [A]);
      const r2 = rev('r2', '2026-01-01T01:01:00Z', 'alice@x', [{...A, w: 8}]);                      // same person, a minute later: one edit
      const r3 = rev('r3', '2026-01-01T03:00:00Z', 'bob@x', [{...A, w: 8}, {id: 'b', kind: 'text', heading: 'Note', x: 0, y: 4, w: 6, h: 1}]);
      const surfaces = [q0, q1, r1, r2, r3];
      arrange.update('s1', surfaces, [{id: 't1'}, {id: 't2'}], [q0, q1]);
      arrange.setActive(true); arrange.mounted();
      const tl = arrange.timeline;
      assert.equal(tl.source, 'arrangement:arrangement:L');
      assert.deepEqual(tl.items.map(s => s.id), ['r2', 'r3'], 'alice’s two quick saves are one stop');
      assert.deepEqual(tl.groups.map(g => [g.actor, g.saves, g.change]), [['You', 2, 'created with 1 block'], ['bob', 1, 'added Note']]);
      assert.match(tl.describe({items: [r3]}), /^bob · .* · added Note$/);
      // Look back to alice's edit: the sheet shows that revision, read-only,
      // and the query block resolves to the version that existed then.
      rendered.length = 0;
      arrange.setCutoff(Date.parse(r2.created_at));
      assert.deepEqual(arrange.viewing, {...arrange.viewing, surface_id: 'r2', title: 'Morning', cells: 1});
      assert.equal(arrange.current.cells, 1);
      assert.ok(rendered.includes('q0') && !rendered.includes('q1'), 'member as it stood then: ' + rendered.join(','));
      assert.equal(arrange.addSurface('q1'), false, 'no edits while looking back');
      assert.match(toolbar.innerHTML, /arrange-viewing/); assert.match(toolbar.innerHTML, /Restore this/);
      // The newest revision is now, not a view.
      arrange.setCutoff(Date.parse(r3.created_at));
      assert.equal(arrange.viewing, null);
      // Restore alice's layout: history is left, the draft takes the old cells, and a new revision saves.
      arrange.setCutoff(Date.parse(r1.created_at));
      assert.equal(arrange.viewing.surface_id, 'r1');
      arrange.restore();
      assert.equal(arrange.viewing, null, 'restoring leaves history');
      assert.equal(travelled, 1, 'the host is asked to put the slider back at now');
      assert.deepEqual(arrange.cells.map(c => [c.id, c.w]), [['a', 6]], 'the draft took the old layout');
      setTimeout(() => {
        const body = saves.at(-1);
        assert.deepEqual(body.cells.map(c => [c.id, c.w]), [['a', 6]], 'and it saved as a new revision');
        assert.equal(arrange.history.past, 1, 'restore is one undo step');
        console.log('ok');
      }, 1200);
    ''', capture_output=True, text=True, cwd=ROOT, timeout=30)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == 'ok', result.stdout


def test_library_can_live_in_an_outside_host_and_still_acts():
    """With railHost, the library is built there (the chat column's Library
    tab), not on the Stage, keeps its own click delegation, and is never
    hidden by the Stage rail toggle."""
    result = subprocess.run(['node', '-'], input=r'''
      const assert = require('node:assert/strict');
      const el = () => { const node = {children: [], dataset: {}, style: {}, listeners: {}, classList: {add(){}, remove(){}, toggle(){}, contains(){ return false; }},
        attributes: {}, hidden: false, innerHTML: '', textContent: '',
        addEventListener(type, fn){ (this.listeners[type] ||= []).push(fn); }, removeEventListener(){}, setAttribute(k, v){ this.attributes[k] = v; }, removeAttribute(){}, toggleAttribute(){},
        appendChild(c){ this.children.push(c); return c; }, append(c){ this.children.push(c); }, insertAdjacentHTML(){}, remove(){},
        replaceWith(){}, querySelector(){ return null; }, querySelectorAll(){ return []; }, closest(){ return null; },
        getBoundingClientRect(){ return {left: 0, top: 0, right: 1200, bottom: 800}; }, focus(){}, clientWidth: 1200, clientHeight: 800};
        return node; };
      const sheet = el(), scroll = el(), rail = el(), toolbar = el(), parts = {}, shelf = el();
      const host = el(); host.querySelector = sel => ({'.arrange-toolbar': toolbar, '.arrange-sheet': sheet, '.arrange-scroll': scroll})[sel] || null;
      shelf.querySelector = sel => sel === '.arrange-rail' ? rail : null;
      sheet.querySelector = () => null; rail.querySelector = sel => (parts[sel] ||= el());
      global.window = {}; global.document = {createElement: el, getElementById: () => null, addEventListener(){}};
      global.location = {href: 'http://x/calliope?session=s1', search: '?session=s1'};
      global.history = {state: null, replaceState(){}}; global.localStorage = {getItem: () => null, setItem(){}};
      global.URL = require('node:url').URL; global.URLSearchParams = require('node:url').URLSearchParams; global.CSS = {escape: s => s};
      require('./calliope/stage-arrange.js');
      const arrange = window.CalliopeStageArrange.create({host, control: null, scroll, api: async () => ({}), renderSurface: () => '<i></i>', hydrate(){}, canEdit: () => true, toast(){}, railHost: shelf});
      assert.ok(!host.innerHTML.includes('arrange-rail'), 'no rail on the Stage');
      assert.match(shelf.innerHTML, /arrange-rail arrange-rail-shelf/);
      assert.ok(rail.listeners.click?.length, 'the library delegates its own clicks');
      assert.ok(rail.listeners.pointerdown?.length && rail.listeners.input?.length);
      const q = {id: 'q1', kind: 'query', title: 'Sales', lineage_key: 'q:1', turn_id: 't1', created_at: '2026-01-01T00:00:00Z'};
      arrange.update('s1', [q], [{id: 't1'}], [q]); arrange.setActive(true); arrange.mounted();
      assert.equal(rail.hidden, false);
      assert.ok(!rail.innerHTML.includes('data-arrange-act="rail"'), 'no hide-the-library button in the tab');
      assert.ok(!toolbar.innerHTML.includes('data-arrange-act="rail"'), 'no show-the-library button on the sheet');
      // Hovering a tile previews the piece with the sheet's renderer, in the
      // host's preview pane; leaving clears it.
      const previewPane = host.children.find(c => c.className === 'arrange-preview');
      arrange.preview('q1');
      setTimeout(() => {
        assert.equal(arrange.previewing, 'q1');
        assert.equal(previewPane.hidden, false);
        assert.match(previewPane.innerHTML, /<i><\/i>/, 'rendered with renderSurface');
        assert.match(previewPane.innerHTML, /Sales/);
        assert.match(previewPane.innerHTML, /aspect-ratio:10 \/ 5/, 'sized like a query block');
        arrange.preview(null);
        assert.equal(arrange.previewing, null);
        assert.equal(previewPane.hidden, true);
        console.log('ok');
      }, 30);
    ''', capture_output=True, text=True, cwd=ROOT, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'ok'


def test_page_mode_shows_one_lineage_read_only():
    """Created with `page`, the host is on as soon as its chat is here, shows
    that lineage without rail or tools, follows later revisions, and cannot be
    closed into the Stage."""
    result = subprocess.run(['node', '-'], input=r'''
      const assert = require('node:assert/strict');
      const el = () => { const node = {children: [], dataset: {}, style: {}, classList: {add(){}, remove(){}, toggle(){}, contains(){ return false; }},
        attributes: {}, hidden: false, innerHTML: '', textContent: '',
        addEventListener(){}, removeEventListener(){}, setAttribute(k, v){ this.attributes[k] = v; }, removeAttribute(){}, toggleAttribute(){},
        appendChild(c){ this.children.push(c); return c; }, append(c){ this.children.push(c); }, insertAdjacentHTML(){}, remove(){},
        replaceWith(){}, querySelector(){ return null; }, querySelectorAll(){ return []; }, closest(){ return null; },
        getBoundingClientRect(){ return {left: 0, top: 0, right: 1200, bottom: 800}; }, focus(){}, clientWidth: 1200, clientHeight: 800};
        return node; };
      const sheet = el(), scroll = el(), rail = el(), toolbar = el(), parts = {};
      const host = el(); host.querySelector = sel => ({'.arrange-rail': rail, '.arrange-toolbar': toolbar, '.arrange-sheet': sheet, '.arrange-scroll': scroll})[sel] || null;
      sheet.querySelector = () => null; rail.querySelector = sel => (parts[sel] ||= el());
      global.window = {}; global.document = {createElement: el, getElementById: () => null, addEventListener(){}};
      global.location = {href: 'http://x/calliope/arrangements/L', search: ''};
      global.history = {state: null, replaceState(){ throw new Error('a page never rewrites its address'); }};
      global.localStorage = {getItem: () => null, setItem(){ throw new Error('a page remembers nothing'); }};
      global.URL = require('node:url').URL; global.URLSearchParams = require('node:url').URLSearchParams; global.CSS = {escape: s => s};
      require('./calliope/stage-arrange.js');
      const arrange = window.CalliopeStageArrange.create({host, control: null, scroll, api: async () => ({}), renderSurface: () => '<i>block</i>', hydrate(){},
        canEdit: () => false, toast(){}, page: 'arrangement:L'});
      assert.equal(host.dataset.page, '1');
      const q = {id: 'q1', kind: 'query', title: 'Sales', lineage_key: 'q:1', turn_id: 't1', created_at: '2026-01-01T00:00:00Z'};
      const rev = (id, title, at) => ({id, kind: 'arrangement', title, lineage_key: 'arrangement:L', turn_id: 't1', created_at: at,
        payload: {title, width: 12, unit: 60, cells: [{id: 'a', kind: 'surface', ref: 'q1', version: 'latest', x: 0, y: 0, w: 6, h: 3}]}});
      arrange.update('s1', [q], [{id: 't1'}], [q]);
      assert.equal(arrange.active, true, 'on as soon as the chat is here');
      assert.equal(arrange.current, null, 'nothing to show yet');
      arrange.mounted();
      assert.match(sheet.innerHTML, /isn’t here/);
      arrange.update('s1', [q, rev('r1', 'Morning', '2026-01-01T01:00:00Z')], [{id: 't1'}], [q]);
      arrange.mounted();
      assert.equal(arrange.current.title, 'Morning');
      assert.equal(rail.hidden, true, 'no library on a page');
      assert.match(toolbar.innerHTML, /arrange-page-bar/);
      assert.match(toolbar.innerHTML, /Open in chat/);
      assert.ok(!/data-arrange-act="tidy"/.test(toolbar.innerHTML), 'no tools on a page');
      arrange.update('s1', [q, rev('r1', 'Morning', '2026-01-01T01:00:00Z'), rev('r2', 'Morning, revised', '2026-01-01T02:00:00Z')], [{id: 't1'}], [q]);
      assert.equal(arrange.current.title, 'Morning, revised', 'a later revision replaces the page');
      arrange.setActive(false);
      assert.equal(arrange.active, true, 'a page cannot be closed');
      console.log('ok');
    ''', capture_output=True, text=True, cwd=ROOT, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'ok'


def test_dashboard_values_are_a_library_section_and_place_as_live_blocks():
    """The library keeps a persistent "From dashboards" section, searched with
    the same box; a value placed from it is a `value` block read live, can move
    to another slice, and saves only the fields that name it."""
    result = subprocess.run(['node', '-'], input=r'''
      const assert = require('node:assert/strict');
      const el = () => { const node = {children: [], dataset: {}, style: {}, listeners: {}, classList: {add(){}, remove(){}, toggle(){}, contains(){ return false; }},
        attributes: {}, hidden: false, innerHTML: '', textContent: '',
        addEventListener(type, fn){ (this.listeners[type] ||= []).push(fn); }, removeEventListener(){}, setAttribute(k, v){ this.attributes[k] = v; }, removeAttribute(){}, toggleAttribute(){},
        appendChild(c){ this.children.push(c); return c; }, append(c){ this.children.push(c); }, insertAdjacentHTML(){}, remove(){},
        replaceWith(){}, querySelector(){ return null; }, querySelectorAll(){ return []; }, closest(){ return null; },
        getBoundingClientRect(){ return {left: 0, top: 0, right: 1200, bottom: 800}; }, focus(){}, clientWidth: 1200, clientHeight: 800};
        return node; };
      const sheet = el(), scroll = el(), rail = el(), toolbar = el(), parts = {}, shelf = el();
      const host = el(); host.querySelector = sel => ({'.arrange-toolbar': toolbar, '.arrange-sheet': sheet, '.arrange-scroll': scroll})[sel] || null;
      shelf.querySelector = sel => sel === '.arrange-rail' ? rail : null;
      sheet.querySelector = () => null; rail.querySelector = sel => (parts[sel] ||= el());
      global.window = {}; global.document = {createElement: el, getElementById: () => null, addEventListener(){}};
      global.location = {href: 'http://x/calliope?session=s1', search: '?session=s1'};
      global.history = {state: null, replaceState(){}}; global.localStorage = {getItem: () => null, setItem(){}};
      global.URL = require('node:url').URL; global.URLSearchParams = require('node:url').URLSearchParams; global.CSS = {escape: s => s};
      require('./calliope/stage-arrange.js');
      const catalog = [
        {key: 'paid-media:enrolled', artifact: 'paid-media', artifact_name: 'Paid Media', object: 'enrolled', version: 5, label: 'Enrollment pace', measure: 'net enrolled students',
          description: 'Enrolled so far.', slices: [{name: 'term', label: 'Term', value: 'Spring 2027', default: 'Spring 2027', dimension: 'cohort', fixed: true}]},
        {key: 'paid-media:spend', artifact: 'paid-media', artifact_name: 'Paid Media', object: 'spend', version: 5, label: 'Spend 7d', measure: 'paid media spend'},
      ];
      const searches = [], reads = [], saves = [];
      const values = {
        search: async q => { searches.push(q); const hit = catalog.filter(v => !q || v.label.toLowerCase().includes(q)); return {values: hit, total: hit.length, catalog: catalog.length, kinds: {value: 2, grid: 0}}; },
        read: async wanted => { reads.push(wanted); return {values: Object.fromEntries(wanted.map(w => [w.key, {status: 'ready', value: w.params.term === 'Fall 2026' ? 20 : 590, artifact_name: 'Paid Media', href: '/d/paid-media',
          slices: [{name: 'term', label: 'Term', type: 'text', default: 'Spring 2027', value: w.params.term || 'Spring 2027', changed: Boolean(w.params.term), dimension: 'cohort', fixed: true}]}]))}; },
      };
      const api = async (path, options) => { saves.push({path, body: JSON.parse(options.body)}); return {surface: {id: 'arr1', lineage_key: 'arrangement:L', payload: {}}, lineage_key: 'arrangement:L'}; };
      const rendered = [];
      const arrange = window.CalliopeStageArrange.create({host, control: null, scroll, api, values, renderSurface: () => '<i></i>',
        renderValue: (cell, data) => { rendered.push([cell.label, data?.status, data?.value]); return `<b>${data?.value ?? '…'}</b>`; },
        hydrate(){}, canEdit: () => true, toast(){}, railHost: shelf});
      const q = {id: 'q1', kind: 'query', title: 'Sales', lineage_key: 'q:1', turn_id: 't1', created_at: '2026-01-01T00:00:00Z'};
      arrange.update('s1', [q], [{id: 't1'}], [q]); arrange.setActive(true); arrange.mounted();
      const list = () => parts['.arrange-rail-list'].innerHTML, kinds = () => parts['.arrange-rail-kinds'].innerHTML;
      setTimeout(() => {
        assert.deepEqual(searches, [''], 'the section loads once when the library opens');
        // Grouped by the dashboard they come from: closed until opened.
        assert.match(list(), /From dashboards/); assert.match(list(), /data-arrange-act="dash" data-dash="paid-media" aria-expanded="false"/);
        assert.match(list(), /<b>Paid Media<\/b><\/button><span>2<\/span>/);
        assert.ok(!list().includes('data-library-value='), 'a closed dashboard shows no tiles');
        const act = (name, data) => rail.listeners.click[0]({preventDefault(){}, target: {closest: sel => sel === '[data-arrange-act]' ? {dataset: {arrangeAct: name, ...data}, closest: () => null} : null}});
        act('dash', {dash: 'paid-media'});
        assert.match(list(), /aria-expanded="true"/); assert.match(list(), /data-library-value="paid-media:enrolled"/);
        assert.match(list(), /<span>value · net enrolled students<\/span>/);
        act('dash', {dash: 'paid-media'});
        assert.ok(!list().includes('data-library-value='), 'and closes again');
        act('dash', {dash: 'paid-media'});
        assert.match(list(), /data-library-surface="q1"/, 'this chat’s pieces are still listed');
        assert.match(kinds(), /data-kind="value"/, 'values can be filtered like any other kind');
        assert.ok(!kinds().includes('data-kind="grid"'), 'no grid chip when no dashboard has a grid');
        // The value chip narrows the library to dashboard values and opens them.
        const chip = name => rail.listeners.click[0]({preventDefault(){}, target: {closest: sel => sel === '[data-arrange-act]' ? {dataset: {arrangeAct: 'kind', kind: name}, closest: () => null} : null}});
        chip('value');
        assert.ok(!list().includes('data-library-surface="q1"'), 'chat pieces are filtered out');
        assert.match(list(), /data-library-value="paid-media:enrolled"/, 'and the dashboards open to show their values');
        chip('value'); chip('grid');
        assert.match(list(), /No dashboard grids match/); assert.ok(!list().includes('data-library-value='));
        chip('grid');
        assert.match(list(), /data-library-surface="q1"/, 'chips off: the chat pieces are back');
        assert.match(list(), /data-dash="paid-media" aria-expanded="true"/, 'and the dashboard the person opened is still open');
        // The same filter box narrows the dashboard values.
        rail.listeners.input[0]({target: {matches: sel => sel === '[data-rail-query]', value: 'spend'}});
        setTimeout(() => {
          assert.deepEqual(searches, ['', 'spend']);
          assert.ok(!list().includes('paid-media:enrolled') && list().includes('paid-media:spend'), 'while filtering, matching dashboards are open');
          assert.ok(!list().includes('data-library-surface="q1"'), 'the chat piece does not match "spend"');
          rail.listeners.input[0]({target: {matches: sel => sel === '[data-rail-query]', value: ''}});
          // Placing: a value block names the artifact and the value; nothing is imported.
          assert.ok(arrange.addValue(catalog[0]));
          assert.equal(arrange.addValue({label: 'nothing'}), false);
          const cell = arrange.cells.find(c => c.kind === 'value');
          assert.deepEqual([cell.artifact, cell.object, cell.from_version, cell.w, cell.h, cell.label], ['paid-media', 'enrolled', 5, 3, 2, 'Enrollment pace']);
          setTimeout(() => {
            assert.equal(reads.length, 1, 'one read for the new block');
            assert.deepEqual(reads[0][0], {key: arrange.values.key(cell), shape: 'value', artifact: 'paid-media', object: 'enrolled', from_version: 5, source_label: 'Enrollment pace', measure: 'net enrolled students', params: {}});
            assert.deepEqual(rendered.at(-1), ['Enrollment pace', 'ready', 590]);
            // Moving it to another slice is a new read and an edit of the block.
            assert.ok(arrange.setSlice(cell.id, 'term', 'Fall 2026'));
            assert.equal(arrange.setSlice('nope', 'term', 'x'), false);
            setTimeout(() => {
              assert.equal(reads.length, 2); assert.deepEqual(reads[1][0].params, {term: 'Fall 2026'});
              assert.deepEqual(rendered.at(-1), ['Enrollment pace', 'ready', 20]);
              const saved = saves.at(-1).body.cells.find(c => c.kind === 'value');
              assert.deepEqual(Object.keys(saved).sort(), ['artifact', 'format', 'from_version', 'h', 'id', 'kind', 'label', 'measure', 'object', 'params', 'source_label', 'w', 'x', 'y']);
              assert.deepEqual(saved.params, {term: 'Fall 2026'});
              assert.match(list(), /arrange-value-tile\s+is-placed" data-library-value="paid-media:enrolled"/);
              // Back to the dashboard's own slice drops the override.
              arrange.setSlice(cell.id, 'term', undefined);
              assert.equal(arrange.cells.find(c => c.kind === 'value').params, undefined);
              // A grid from a dashboard places as a query-sized block and is read as a grid.
              const gridItem = {key: 'paid-media:grid:by_month', shape: 'grid', artifact: 'paid-media', artifact_name: 'Paid Media', object: 'by_month', version: 5,
                label: 'Enrolled by month', measures: ['enrolled students'], grain: 'one row per month'};
              assert.ok(arrange.addValue(gridItem));
              const gridCell = arrange.cells.find(c => c.shape === 'grid');
              assert.deepEqual([gridCell.kind, gridCell.object, gridCell.w, gridCell.h], ['value', 'by_month', 8, 5]);
              assert.notEqual(arrange.values.key(gridCell), arrange.values.key({...gridCell, shape: undefined}), 'a grid and a value never share a cache entry');
              setTimeout(() => {
                assert.equal(reads.at(-1).find(r => r.object === 'by_month').shape, 'grid');
                // Hovering a library value previews it live: one read, the number
                // rendered with the block's renderer above what is known about it.
                const pane = host.children.find(c => c.className === 'arrange-preview');
                const before = reads.length;
                arrange.previewValue('paid-media:spend');
                setTimeout(() => {
                  assert.equal(arrange.previewing, 'value:paid-media:spend');
                  assert.equal(reads.length, before + 1, 'the preview reads the value once');
                  assert.deepEqual(reads.at(-1)[0].params, {});
                  assert.match(pane.innerHTML, /arrange-preview-live is-value"><b>590<\/b>/, 'the live number, drawn by renderValue');
                  assert.match(pane.innerHTML, /value · Paid Media/); assert.match(pane.innerHTML, /paid media spend/);
                  arrange.preview(null);
                  arrange.previewValue('paid-media:spend');
                  setTimeout(() => {
                    assert.equal(reads.length, before + 1, 'a fresh value is not read again');
                    console.log('ok');
                  }, 30);
                }, 40);
              }, 60);
            }, 1100);
          }, 60);
        }, 320);
      }, 60);
    ''', capture_output=True, text=True, cwd=ROOT, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'ok'
