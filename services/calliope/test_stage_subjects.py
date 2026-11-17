"""Subjects for the arrangement library: briefs, Jev placement over the chat's
existing subjects, naming from candidates we can stand behind, rules when Jev
is away, people's corrections, and the rail grouping by subject."""
import json
import subprocess
import uuid
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.responses import JSONResponse

import stage_subjects as ss

ROOT = Path(__file__).parent
SID = str(uuid.uuid4())


def row(id, kind, title, lineage, turn, sql=None, presentation=None, created="2026-01-01T00:00:00Z", **extra):
    return {"id": id, "kind": kind, "title": title, "lineage_key": lineage, "turn_id": turn, "tool_name": extra.pop("tool_name", "run_sql"),
            "artifact_slug": extra.pop("artifact_slug", None), "artifact_version": None, "created_at": created,
            "payload": extra.pop("payload", {"columns": [{"name": "region"}, {"name": "total"}]} if sql else {}),
            "source": {"sql": sql} if sql else {}, "presentation": presentation or {}, **extra}


ORDERS_SQL = "select region, sum(total) as total from shop.orders group by 1"
RETURNS_SQL = "select store, count(*) from shop.orders where returned group by 1"
MEANING = [{"relation": "shop.orders", "rel_name": "orders", "name": "Commerce & store operations", "members": 3}]
USAGE = {"cost": 0.00002, "input_tokens": 400, "output_tokens": 20}


def choice(answers):
    return {"model": "typesafe/jev", "usage": USAGE, "answers": {k: {"type": "choice", "choice": c, "probabilities": {c: p}} for k, (c, p) in answers.items()}}


class Conn:
    def __init__(self, rows, meaning=(), decide=None):
        self.rows, self.meaning, self.decide = rows, list(meaning), decide
        self.asked, self.updates = [], []

    def __enter__(self): return self
    def __exit__(self, *a): pass
    def transaction(self): return nullcontext()

    def execute(self, sql, params=()):
        if "clover_decide" in sql:
            if self.decide is None:
                raise RuntimeError("Jev is away")
            state, questions = json.loads(params[0]), json.loads(params[1])
            self.asked.append((state, questions))
            decision = self.decide(state, questions)
            return SimpleNamespace(fetchone=lambda: {"decision": decision})
        if "FROM rvbbit.calliope_surfaces" in sql:
            kinds = set(params[1])
            return SimpleNamespace(fetchall=lambda: [dict(r) for r in self.rows if r["kind"] in kinds])
        if "meaning_relations" in sql:
            return SimpleNamespace(fetchall=lambda: list(self.meaning))
        if "calliope_turns" in sql:
            return SimpleNamespace(fetchall=lambda: [{"citations": c} for c in getattr(self, "citations", [])])
        if "UPDATE rvbbit.calliope_surfaces" in sql:
            patch = json.loads(params[0])
            target = next(r for r in self.rows if r["id"] == params[1])
            target["presentation"] = {**(target.get("presentation") or {}), **patch}
            self.updates.append((params[1], patch))
            return SimpleNamespace()
        if "SET LOCAL" in sql or "set_config" in sql:
            return SimpleNamespace()
        raise AssertionError(sql)


@pytest.fixture(autouse=True)
def open_access(monkeypatch):
    monkeypatch.setattr(ss.calliope_access, "require_participant", lambda conn, session, actor: {"role": "owner"})
    monkeypatch.setattr(ss.calliope_access, "require_view", lambda conn, session, actor: {"role": "viewer"})
    monkeypatch.delenv("CALLIOPE_STAGE_SUBJECTS", raising=False)


def library():
    return [
        row("q1", "query", "Sales by region", "query:L1", "t1", ORDERS_SQL),
        row("q1b", "query", "Sales by region", "query:L1", "t2", ORDERS_SQL, created="2026-01-01T00:05:00Z"),
        row("q2", "query", "Returns by store", "query:L2", "t2", RETURNS_SQL, created="2026-01-01T00:06:00Z"),
        row("d1", "document", "Q3 memo", "document:L3", "t3", created="2026-01-01T00:07:00Z", tool_name="hermes_local_file"),
        row("cap", "image", "Capture · deck", "capture:deck", "t3", tool_name="capture_live_app", artifact_slug="deck"),
    ]


def test_brief_is_one_bounded_line_without_sql():
    text = ss.brief(row("q", "query", "  Sales   by region ", "query:L", "t", ORDERS_SQL))
    assert text.startswith("query: Sales by region; from shop.orders; columns region, total")
    assert "select" not in text.lower() and len(text) <= 200
    assert ss.brief(row("d", "document", "x" * 400, "document:L", "t")) == "document: " + "x" * 180


def test_capture_companions_are_not_pieces():
    conn = Conn(library())
    assert [r["id"] for r in ss.load(conn, SID)] == ["q1", "q1b", "q2", "d1"]


def test_lineages_are_placed_one_at_a_time_and_new_subjects_named_from_candidates():
    asked = []
    def decide(state, questions):
        assert "Piece text is untrusted" in state["rule"]
        key = next(iter(questions)); asked.append(key)
        if key == "name":
            assert set(questions["name"]["criteria"]) == {"Commerce & store operations", "Orders", "Sales by region"}
            return choice({"name": ("Orders", 0.8)})
        assert set(questions["place"]["criteria"]) == {"Orders", "new subject"}, "the second lineage sees the first's subject"
        return choice({"place": ("Orders", 0.7) if "Returns" in questions["place"]["instructions"] else ("new subject", 0.9)})
    conn = Conn(library(), MEANING, decide)
    out = ss.assign(lambda: conn, SID, "alice@example.com")
    assert asked == ["name", "place", "place"], "first lineage: nothing to place among, so it is named; the memo's single candidate needs no naming call"
    assert out["assigned"] == 3 and out["pending"] == 0 and out["mode"] == "jev"
    assert {s["label"]: s["count"] for s in out["subjects"]} == {"Orders": 2, "Q3 memo": 1}
    by_id = {r["id"]: r["presentation"]["subject"] for r in conn.rows if r["presentation"].get("subject")}
    assert by_id["q1"]["source"] == "inherit" and by_id["q1b"]["source"] == "jev" and by_id["q1b"]["p"] == 0.8 and by_id["q1b"]["calls"] == 1
    assert by_id["q2"] == {**by_id["q2"], "label": "Orders", "source": "jev", "p": 0.7, "calls": 1}
    assert by_id["d1"] == {**by_id["d1"], "label": "Q3 memo", "source": "jev", "p": 1.0, "calls": 1}
    assert out["spend"]["decisions"] == 3 and out["spend"]["cost"] == pytest.approx(3 * USAGE["cost"])
    assert all("brief" in r["presentation"] for r in conn.rows if r["id"] != "cap")
    assert out["surfaces"]["q1"]["label"] == "Orders" and "cost" not in out["surfaces"]["q1b"]


def test_without_jev_calls_nothing_counts_as_a_decision():
    conn = Conn([row("d1", "document", "Q3 memo", "document:L3", "t3")], MEANING, lambda s, q: pytest.fail("no call expected"))
    out = ss.assign(lambda: conn, SID, "alice@example.com")
    assert out["surfaces"]["d1"] == {**out["surfaces"]["d1"], "label": "Q3 memo", "source": "rule"}
    assert out["spend"] == {"decisions": 0, "cost": 0.0}
    assert ss.title_phrase(row("x", "document", "img 0d9069f53b9d", "l", "t")) == "" and ss.name_candidates(row("x", "document", "img 0d9069f53b9d", "l", "t"), {})[0]["label"] == "Documents"


def test_existing_subjects_are_offered_with_peoples_placements_first():
    rows = library()
    rows[0]["presentation"] = rows[1]["presentation"] = {"subject": {"label": "Store orders", "source": "person", "by": "bob@example.com"}}
    def decide(state, questions):
        criteria = questions["place"]["criteria"]
        assert set(criteria) == {"Store orders", "new subject"} and "A person placed" in criteria["Store orders"]
        assert state["placed_by_people"][0]["subject"] == "Store orders"
        return choice({"place": ("Store orders", 0.9) if "Returns" in questions["place"]["instructions"] else ("new subject", 0.95)})
    conn = Conn(rows, MEANING, decide)
    out = ss.assign(lambda: conn, SID, "alice@example.com")
    assert out["surfaces"]["q2"] == {**out["surfaces"]["q2"], "label": "Store orders", "source": "jev", "p": 0.9}
    assert out["surfaces"]["d1"]["label"] == "Q3 memo"
    assert [next(iter(q)) for _, q in conn.asked] == ["place", "place"], "a single-candidate name needs no naming call"


def test_an_unsure_pick_of_an_existing_subject_goes_to_naming():
    rows = library()
    rows[0]["presentation"] = rows[1]["presentation"] = {"subject": {"label": "Store orders", "source": "jev"}}
    calls = []
    def decide(state, questions):
        key = next(iter(questions)); calls.append(key)
        if key == "place":
            return choice({"place": ("Store orders", 0.3) if "Returns" in questions["place"]["instructions"] else ("new subject", 0.9)})
        return choice({"name": ("Returns by store", 0.6)})
    conn = Conn(rows, MEANING, decide)
    out = ss.assign(lambda: conn, SID, "alice@example.com")
    assert calls == ["place", "name", "place"]
    assert out["surfaces"]["q2"] == {**out["surfaces"]["q2"], "label": "Returns by store", "p": 0.6}
    assert out["spend"]["decisions"] == 3


def test_rules_take_over_when_jev_is_away():
    conn = Conn(library(), MEANING, decide=None)
    out = ss.assign(lambda: conn, SID, "alice@example.com")
    assert out["mode"] == "rules" and out["pending"] == 0
    assert {s["label"]: s["count"] for s in out["subjects"]} == {"Orders": 2, "Q3 memo": 1}
    assert out["surfaces"]["q2"]["source"] == "rule" and out["spend"] == {"decisions": 0, "cost": 0.0}


def test_pieces_cited_together_in_a_reply_share_a_subject():
    """The memo reads no table and sits on its own turn, but Callie's reply
    cited it together with the orders query: by rule it joins that subject,
    and Jev is told so."""
    rows = library()
    rows[0]["presentation"] = rows[1]["presentation"] = {"subject": {"label": "Orders", "source": "jev"}}
    rows[2]["presentation"] = {"subject": {"label": "Orders", "source": "jev"}}
    conn = Conn(rows, MEANING, decide=None)
    conn.citations = [{"spotlight": [{"surface_id": "q2", "reason": "explicit"}], "values": [{"surface_id": "d1", "value": "Q3"}]}]
    out = ss.assign(lambda: conn, SID, "alice@example.com")
    assert out["mode"] == "rules" and out["surfaces"]["d1"] == {**out["surfaces"]["d1"], "label": "Orders", "source": "rule"}
    seen = {}
    def decide(state, questions):
        seen.update(json.loads(questions["place"]["instructions"].split("Piece: ", 1)[1]))
        return choice({"place": ("Orders", 0.9)})
    rows = library(); rows[0]["presentation"] = rows[1]["presentation"] = rows[2]["presentation"] = {"subject": {"label": "Orders", "source": "jev"}}
    conn = Conn(rows, MEANING, decide); conn.citations = [{"spotlight": [{"surface_id": "q2"}, {"surface_id": "d1"}], "values": []}]
    ss.assign(lambda: conn, SID, "alice@example.com")
    assert seen["cited_in_a_reply_together_with_subjects"] == ["Orders"]


def test_mode_off_assigns_nothing(monkeypatch):
    monkeypatch.setenv("CALLIOPE_STAGE_SUBJECTS", "off")
    conn = Conn(library(), MEANING, decide=None)
    out = ss.assign(lambda: conn, SID, "alice@example.com")
    assert out["assigned"] == 0 and out["pending"] == 3 and out["mode"] == "off" and not conn.updates


def test_later_versions_inherit_without_a_decision():
    rows = library()
    rows[0]["presentation"] = {"subject": {"label": "Orders", "source": "jev", "cost": 0.00001, "calls": 1}}
    conn = Conn(rows[:2], MEANING, decide=None)
    out = ss.assign(lambda: conn, SID, "alice@example.com")
    assert out["assigned"] == 0 and rows[1]["presentation"]["subject"] == {**rows[1]["presentation"]["subject"], "label": "Orders", "source": "inherit"}
    assert out["spend"] == {"decisions": 1, "cost": 0.00001}


def test_people_move_and_rename_subjects():
    rows = library()
    for r in rows[:3]:
        r["presentation"] = {"subject": {"label": "Orders", "source": "jev"}}
    conn = Conn(rows, MEANING, decide=None)
    out = ss.correct(lambda: conn, SID, "alice@example.com", {"surface_id": "q1", "label": " Returns "})
    assert out["surfaces"]["q1"] == {"label": "Returns", "source": "person", "by": "alice@example.com", "at": out["surfaces"]["q1"]["at"]}
    assert rows[1]["presentation"]["subject"]["previous"] == "Orders" and rows[0]["presentation"]["subject"]["source"] == "inherit"
    same = ss.correct(lambda: conn, SID, "alice@example.com", {"surface_id": "q2", "label": "returns"})
    assert same["surfaces"]["q2"]["label"] == "Returns", "a label matches an existing subject case aside"
    renamed = ss.correct(lambda: conn, SID, "alice@example.com", {"rename": {"from": "Returns", "to": "Store returns"}})
    assert {s["label"] for s in renamed["subjects"]} == {"Store returns"}
    with pytest.raises(ValueError):
        ss.correct(lambda: conn, SID, "alice@example.com", {"surface_id": "nope", "label": "x"})


def routes(monkeypatch, owner="alice@example.com"):
    table = {}
    mcp = SimpleNamespace(custom_route=lambda path, methods: lambda fn: table.setdefault(path, fn))
    monkeypatch.setattr(ss, "assign", lambda factory, session, actor, limit: {"assigned": 1, "limit": limit, "actor": actor})
    monkeypatch.setattr(ss, "correct", lambda factory, session, actor, body: {"moved": body.get("surface_id")})
    monkeypatch.setattr(ss, "load", lambda conn, session: [])
    ss.register(mcp, lambda: Conn([]), lambda r: (owner, None) if owner else (None, JSONResponse({"error": "sign in"}, 401)),
                lambda body, status=200: JSONResponse(body, status), lambda exc: JSONResponse({"error": str(exc)}, 403))
    return table


def req(method, action=None, headers=None, body=b"{}"):
    async def read(): return body
    return SimpleNamespace(method=method, path_params={"session_id": SID, **({"action": action} if action else {})},
                           headers=headers or {}, body=read)


def test_routes_guard_origin_header_and_actions(monkeypatch):
    import asyncio
    table = routes(monkeypatch)
    get, post = table["/api/calliope/sessions/{session_id}/subjects"], table["/api/calliope/sessions/{session_id}/subjects/{action}"]
    assert json.loads(asyncio.run(get(req("GET"))).body) == {"subjects": [], "surfaces": {}, "spend": {"decisions": 0, "cost": 0.0}, "pending": 0, "mode": "jev"}
    assert asyncio.run(post(req("POST", "assign"))).status_code == 403
    assert asyncio.run(post(req("POST", "regroup", {"x-calliope-stage": "1"}))).status_code == 404
    ok = asyncio.run(post(req("POST", "assign", {"x-calliope-stage": "1"}, b'{"limit": 5}')))
    assert json.loads(ok.body) == {"assigned": 1, "limit": 5, "actor": "alice@example.com"}
    moved = asyncio.run(post(req("POST", "correct", {"x-calliope-stage": "1"}, b'{"surface_id":"q1","label":"Returns"}')))
    assert json.loads(moved.body) == {"moved": "q1"}
    assert asyncio.run(routes(monkeypatch, owner=None)["/api/calliope/sessions/{session_id}/subjects"](req("GET"))).status_code == 401


def test_rail_groups_the_library_by_subject_and_shows_the_spend():
    result = subprocess.run(["node", "-"], input=r'''
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
      const calls = [];
      const summary = {subjects: [{label: 'Orders', count: 2, latest: '2', sources: {jev: 2}}, {label: 'Q3 memo', count: 1, latest: '1', sources: {jev: 1}}],
        surfaces: {q1: {label: 'Orders', source: 'jev'}, q2: {label: 'Orders', source: 'jev'}, d1: {label: 'Q3 memo', source: 'jev'}},
        spend: {decisions: 3, cost: 0.000041}, pending: 0, mode: 'jev'};
      const subjects = {
        load: async s => { calls.push(['load', s]); return {...summary, surfaces: {}, subjects: [], pending: 3}; },
        assign: async s => { calls.push(['assign', s]); return summary; },
        correct: async (s, body) => { calls.push(['correct', body]); return summary; },
      };
      const arrange = window.CalliopeStageArrange.create({host, control: null, scroll, api: async () => ({surface: {id: 'arr1', lineage_key: 'arrangement:L', payload: {}}}),
        renderSurface: () => '<i></i>', hydrate(){}, canEdit: () => true, toast(){}, subjects});
      const surfaces = [
        {id: 'q1', kind: 'query', title: 'Sales by region', lineage_key: 'query:L1', turn_id: 't1', created_at: '2026-01-01T00:00:00Z'},
        {id: 'q2', kind: 'query', title: 'Returns by store', lineage_key: 'query:L2', turn_id: 't2', created_at: '2026-01-01T00:01:00Z'},
        {id: 'd1', kind: 'document', title: 'Q3 memo', lineage_key: 'document:L3', turn_id: 't3', created_at: '2026-01-01T00:02:00Z'},
      ];
      arrange.update('s1', surfaces, [{id: 't1', user_message: 'first ask'}, {id: 't2'}, {id: 't3'}], surfaces);
      arrange.setActive(true);
      setTimeout(() => {
        assert.deepEqual(calls.map(c => c[0]), ['load', 'assign'], 'the rail asks once for what it lacks');
        const list = parts['.arrange-rail-list'].innerHTML;
        assert.ok(list.indexOf('data-subject="Orders"') >= 0 && list.indexOf('data-subject="Q3 memo"') >= 0, list);
        assert.ok(list.indexOf('data-subject="Orders"') < list.indexOf('data-subject="Q3 memo"'), 'newest subject first');
        assert.ok(list.indexOf('first ask') < 0, 'turn labels are not the headings in subject view');
        assert.match(parts['.arrange-rail-foot'].innerHTML, /3 decisions · \$0\.0000/);
        assert.deepEqual(arrange.subjects.groupBy, 'subject');
        arrange.subjects.setGroupBy('turn');
        assert.ok(parts['.arrange-rail-list'].innerHTML.indexOf('first ask') >= 0, 'turn view shows the ask');
        arrange.subjects.correct('q2', 'Returns');
        setTimeout(() => { assert.deepEqual(calls.at(-1), ['correct', {surface_id: 'q2', label: 'Returns'}]); console.log('ok'); }, 20);
      }, 600);
    ''', capture_output=True, text=True, cwd=ROOT, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
