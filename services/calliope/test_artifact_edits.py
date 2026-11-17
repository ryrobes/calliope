"""Revisions as edits, not rewrites.

Bikeshare session, 2026-08-17: every revision retyped the whole document from a
fresh read, and a two-line change ("raise the limit, dark basemap") silently
dropped the Attention-queue section. Exact-match edits can only touch what they
name and fail loudly when the caller's picture of the document is stale; the
structure comparison is the seatbelt for the full-rewrite path that remains.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import server  # noqa: E402

V2 = """<!doctype html><html><head><title>Bikeshare Station Pulse</title></head><body>
<section id="kpis"><h2>Where availability is tight</h2><p>lowest bikes</p></section>
<section id="posture"><h2>Service posture</h2></section>
<section id="attention"><h2>Attention queue</h2><ul><li>x</li></ul></section>
<section id="map"><h2>Fleet availability map</h2><div id="mapped"></div></section>
<section id="watch"><h2>Station watchlist</h2><template><h3>${x.name}</h3></template></section>
<script>const LIMIT = 1000; rvbbitQuery(sql, {limit: LIMIT});</script>
</body></html>"""


def test_structure_inventory_lists_static_headings_and_ids():
    s = server._artifact_structure(V2)
    assert s["headings"] == [
        "Where availability is tight", "Service posture", "Attention queue",
        "Fleet availability map", "Station watchlist", "${x.name}",
    ]
    assert s["ids"] == ["kpis", "posture", "attention", "map", "mapped", "watch"]
    assert s["bytes"] == len(V2)


def test_apply_edits_is_exact_unique_and_all_or_nothing():
    out, applied, err = server._apply_edits(V2, [
        {"find": "const LIMIT = 1000;", "replace": "const LIMIT = 3000;"},
        {"find": "<h2>Service posture</h2>", "replace": "<h2>Service posture</h2><p>Live</p>"},
    ])
    assert err is None
    assert "const LIMIT = 3000;" in out and "<p>Live</p>" in out
    assert [a["replaced"] for a in applied] == [1, 1]
    assert applied[0]["delta_bytes"] == 0 and applied[1]["delta_bytes"] == len("<p>Live</p>")

    # a stale find changes nothing and says so
    out2, applied2, err2 = server._apply_edits(V2, [
        {"find": "const LIMIT = 3000;", "replace": "x"},
        {"find": "<h2>Service posture</h2>", "replace": "y"},
    ])
    assert out2 == V2 and applied2 == []
    assert err2["error"]["code"] == "EDIT_NOT_FOUND" and err2["error"]["edit_index"] == 0
    assert "part='html'" in err2["error"]["message"]

    # ambiguity is refused unless explicitly all=true
    out3, _, err3 = server._apply_edits(V2, [{"find": "<section id=", "replace": "<div id="}])
    assert out3 == V2 and err3["error"]["code"] == "EDIT_AMBIGUOUS" and err3["error"]["matches"] == 5
    out4, applied4, err4 = server._apply_edits(V2, [{"find": "<h2>", "replace": "<h2 class=\"t\">", "all": True}])
    assert err4 is None and applied4[0]["replaced"] == 5 and "<h2 class=\"t\">Attention queue" in out4

    # shape validation
    for bad in ([], "nope", [{"replace": "x"}], [{"find": ""}], [{"find": 1, "replace": ""}]):
        _, _, e = server._apply_edits(V2, bad)
        assert e and e["error"]["code"] == "INVALID_ARGUMENT", bad
    assert server._coerce_edits('[{"find":"a","replace":"b"}]') == [{"find": "a", "replace": "b"}]
    assert server._coerce_edits({"find": "a", "replace": "b"}) == [{"find": "a", "replace": "b"}]


def test_direct_artifact_text_replacement_accepts_only_one_exact_plain_element():
    source = '<main><h2 class="section-title">Quarterly &amp; annual</h2><p>Keep me</p></main>'
    edit = {
        "tag": "h2",
        "before": "Quarterly & annual",
        "after": "This quarter & next",
        "before_html": "Quarterly &amp; annual",
        "after_html": "This quarter &amp; next",
        "before_outer_html": '<h2 class="section-title">Quarterly &amp; annual</h2>',
        "after_outer_html": '<h2 class="section-title">This quarter &amp; next</h2>',
    }
    out, applied, refusal = server._direct_artifact_text_replacement(source, edit)
    assert refusal is None
    assert out == '<main><h2 class="section-title">This quarter &amp; next</h2><p>Keep me</p></main>'
    assert applied["replaced"] == 1


def test_direct_artifact_text_replacement_punts_ambiguous_or_non_text_edits():
    clean = {
        "tag": "h2",
        "before": "Quarterly revenue",
        "after": "Revenue this quarter",
        "before_html": "Quarterly revenue",
        "after_html": "Revenue this quarter",
        "before_outer_html": "<h2>Quarterly revenue</h2>",
        "after_outer_html": "<h2>Revenue this quarter</h2>",
    }

    repeated = "<main><h2>Quarterly revenue</h2><h2>Quarterly revenue</h2></main>"
    out, applied, refusal = server._direct_artifact_text_replacement(repeated, clean)
    assert out == repeated and applied is None
    assert refusal["code"] == "DIRECT_EDIT_SOURCE_EDIT_AMBIGUOUS"

    structural = {
        **clean,
        "before_html": "<strong>Quarterly</strong> revenue",
        "before_outer_html": "<h2><strong>Quarterly</strong> revenue</h2>",
    }
    _, applied, refusal = server._direct_artifact_text_replacement(
        structural["before_outer_html"], structural
    )
    assert applied is None and refusal["code"] == "DIRECT_EDIT_STRUCTURAL"

    changed_attribute = {
        **clean,
        "after_outer_html": '<h2 class="edited">Revenue this quarter</h2>',
    }
    _, applied, refusal = server._direct_artifact_text_replacement(
        clean["before_outer_html"], changed_attribute
    )
    assert applied is None and refusal["code"] == "DIRECT_EDIT_ELEMENT_CHANGED"

    dynamic = "<main><div id=\"root\"></div><script>render()</script></main>"
    _, applied, refusal = server._direct_artifact_text_replacement(dynamic, clean)
    assert applied is None and refusal["code"] == "DIRECT_EDIT_SOURCE_EDIT_NOT_FOUND"


def test_revision_advisories_name_what_a_rewrite_dropped():
    v3 = V2.replace('<section id="attention"><h2>Attention queue</h2><ul><li>x</li></ul></section>\n', "")
    v3 = v3.replace("<template><h3>${x.name}</h3></template>", "")
    notes = server._revision_advisories(V2, v3, notes="raise the map limit; dark basemap")
    codes = [n["code"] for n in notes]
    assert "SECTIONS_REMOVED" in codes
    removed = next(n for n in notes if n["code"] == "SECTIONS_REMOVED")
    assert removed["removed_headings"] == ["Attention queue"]     # ${x.name} templates are not counted
    assert removed["removed_ids"] == ["attention"]
    assert "restore them" in removed["message"]

    # naming the removal in notes is an intentional change, not an advisory
    assert server._revision_advisories(V2, v3, notes="Removed the Attention queue section per request") == []

    # a big shrink is called out even when nothing named vanished
    big = V2 + "<!-- " + ("filler " * 900) + "-->"
    shrunk = server._revision_advisories(big, V2, notes=None)
    assert [n["code"] for n in shrunk] == ["DOCUMENT_SHRANK"]

    # identical structure → nothing to say
    assert server._revision_advisories(V2, V2.replace("1000", "3000"), None) == []


def test_read_projection_returns_plain_html_or_structure():
    doc = {"slug": "s", "name": "S", "url": "/d/s", "version": {"version": 2, "html": V2, "manifest": {}}}
    assert server._artifact_read_projection(doc, "html") == V2          # plain text, no JSON escaping
    structure = server._artifact_read_projection(doc, "structure")
    assert structure["slug"] == "s" and structure["version"] == 2
    assert structure["structure"]["headings"][2] == "Attention queue"
    assert server._artifact_read_part(None) == "full"
    try:
        server._artifact_read_part("everything")
    except ValueError as exc:
        assert "part must be one of" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("bad part accepted")


def test_update_tools_and_guidance_advertise_edits():
    import inspect
    for fn in (server._mcp_update_live_app, server._mcp_update_dashboard):
        assert "edits" in inspect.signature(fn).parameters
        assert "EDIT_NOT_FOUND" in (fn.__doc__ or "")
    for fn in (server._mcp_get_live_app, server._mcp_get_dashboard):
        assert "part" in inspect.signature(fn).parameters
        assert "part='html'" in (fn.__doc__ or "")
    template_md = (_HERE / "DASHBOARD_TEMPLATE.md").read_text(encoding="utf-8")
    assert "edits=[{find, replace}]" in template_md
    calliope_src = (_HERE / "calliope.py").read_text(encoding="utf-8")
    assert "REVISIONS: to change a published artifact" in calliope_src
    assert "full_page=true,return_image=false" in calliope_src
    skill = (_HERE.parent.parent / "docker" / "calliope" / "hermes-skills" / "calliope-live-dashboards" / "SKILL.md").read_text(encoding="utf-8")
    assert "## Revising an existing artifact" in skill and "EDIT_NOT_FOUND" in skill
    # Edit discipline (2026-08-21 audit: five stale-context patch misses in
    # one run, with sed one-liners as the silent-failure fallback): re-read
    # then re-patch, never sed, never a whole-file rewrite; and page checks
    # use the shipped harness, not a downloaded browser.
    assert "misses its `old_string` twice" in skill
    assert "re-read that exact section" in skill
    assert "never fall back to sed/regex one-liners" in skill
    assert "do not attempt to download Playwright" in skill
    # calliope-web-check is a shell command; naming it without saying so sent
    # grok to the tool catalog for it every build iteration (12 identical
    # tool_search calls in the 2026-08-21 run).
    assert "never `tool_search` for it" in skill
