"""User-selected profiles are the only source of artifact style constraints."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import calliope  # noqa: E402
import server  # noqa: E402


def _profile_row(name="Adaptive Calliope", version=2, owner="calliope@system", **extra):
    row = {
        "id": calliope._ADAPTIVE_DESIGN_PROFILE_VERSION_ID,
        "profile_id": calliope._ADAPTIVE_DESIGN_PROFILE_ID,
        "profile_name": name,
        "profile_description": "A dense editorial dashboard system.",
        "profile_owner": owner,
        "version": version,
        "compiled_prompt": "DESIGN PROFILE — " + name + "\nUse var(--artifact-bg).",
        "tokens": {
            "behavior": {"theme_source": "viewer", "runtime": "rvbbit.adaptive-theme.v1"},
            "palette": {"accent": "#68c7b2"},
        },
    }
    row.update(extra)
    return row


@pytest.fixture
def direct_oauth(monkeypatch):
    """A direct OAuth connector: real subject, non-Hermes client, no envelope."""
    monkeypatch.setattr(server, "_authenticated_caller",
                        lambda: ("ryan@example.com", "16a7448e-oauth-client"))
    monkeypatch.setattr(server, "_hermes_mcp_envelope", lambda ctx=None, authenticated=None: None)
    monkeypatch.setattr(server, "_active_calliope_design_profile", lambda: None)


@pytest.fixture
def hermes_service(monkeypatch):
    """The trusted Hermes service token inside a Calliope turn that deliberately unpinned."""
    monkeypatch.setattr(server, "_authenticated_caller",
                        lambda: ("owner@example.com", server._HERMES_SERVICE_CLIENT_ID))
    monkeypatch.setattr(server, "_hermes_mcp_envelope",
                        lambda ctx=None, authenticated=None: {"source": "hermes", "session_id": "s1"})
    monkeypatch.setattr(server, "_active_authoring_mode", lambda: "html")
    monkeypatch.setattr(server, "_active_calliope_design_profile", lambda: server._DESIGN_PROFILE_UNPINNED)


def test_direct_publisher_has_no_implicit_profile(direct_oauth):
    doc = server._with_active_calliope_design_profile({"semantic_map": {"objects": []}})
    assert doc == {"semantic_map": {"objects": []}}
    assert server._publication_design_profile_summary() == {"source": "none"}


def test_unpinned_hermes_turn_is_left_exactly_alone(hermes_service):
    """A notebook turn that deliberately unpinned publishes without a profile."""
    doc = server._with_active_calliope_design_profile({"semantic_map": {"objects": []}})

    assert "design_profile" not in doc
    assert server._publication_design_profile_summary() == {"source": "calliope_turn_unpinned"}


def test_hermes_session_without_a_notebook_turn_has_no_implicit_profile(monkeypatch):
    monkeypatch.setattr(server, "_authenticated_caller",
                        lambda: ("owner@example.com", server._HERMES_SERVICE_CLIENT_ID))
    monkeypatch.setattr(server, "_hermes_mcp_envelope",
                        lambda ctx=None, authenticated=None: {"source": "hermes", "platform": "google_chat", "session_id": "spaces/x"})
    monkeypatch.setattr(server, "_active_calliope_design_profile", lambda: None)

    doc = server._with_active_calliope_design_profile({})
    assert "design_profile" not in doc
    assert server._publication_design_profile_summary()["source"] == "none"


def test_static_key_with_forwarded_envelope_has_no_implicit_profile(monkeypatch):
    monkeypatch.setattr(server, "_authenticated_caller", lambda: ("ryan@example.com", "static-key"))
    monkeypatch.setattr(server, "_hermes_mcp_envelope",
                        lambda ctx=None, authenticated=None: {"source": "hermes", "session_id": "s9"})
    monkeypatch.setattr(server, "_active_calliope_design_profile", lambda: None)

    assert server._with_active_calliope_design_profile({}) == {}


def test_active_profile_resolver_distinguishes_unpinned_from_no_turn(monkeypatch):
    import contextlib
    monkeypatch.setattr(server, "_hermes_mcp_envelope",
                        lambda ctx=None, authenticated=None: {"source": "hermes", "session_id": "hs-1"})

    class _Conn:
        def __init__(self, row): self.row = row
        def execute(self, sql, params=()): return self
        def fetchone(self): return self.row

    def with_row(row):
        @contextlib.contextmanager
        def _conn(**_kw):
            yield _Conn(row)
        return _conn

    monkeypatch.setattr(server, "_conn", with_row({"design_profile_version_id": None}))
    assert server._active_calliope_design_profile() is server._DESIGN_PROFILE_UNPINNED
    monkeypatch.setattr(server, "_conn", with_row(None))
    assert server._active_calliope_design_profile() is None

    pinned = _profile_row()
    monkeypatch.setattr(server, "_conn", with_row({"design_profile_version_id": pinned["id"]}))
    monkeypatch.setattr(calliope, "_design_profile_version", lambda conn, version: pinned)
    assert server._active_calliope_design_profile() == pinned
    monkeypatch.setattr(calliope, "_design_profile_version", lambda conn, version: None)
    with pytest.raises(ValueError, match="pinned design profile version is unavailable"):
        server._active_calliope_design_profile()

    def unavailable(conn, version):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(calliope, "_design_profile_version", unavailable)
    with pytest.raises(ValueError, match="pinned design profile could not be loaded"):
        server._active_calliope_design_profile()


def test_static_key_without_envelope_is_a_direct_publisher(monkeypatch):
    monkeypatch.setattr(server, "_authenticated_caller", lambda: ("ryan@example.com", "static-key"))
    monkeypatch.setattr(server, "_hermes_mcp_envelope", lambda ctx=None, authenticated=None: None)
    monkeypatch.setattr(server, "_active_calliope_design_profile", lambda: None)

    doc = server._with_active_calliope_design_profile({})
    assert "design_profile" not in doc


def test_pinned_turn_profile_still_wins_for_everyone(monkeypatch):
    pinned = _profile_row(name="House Style", version=4, owner="owner@example.com",
                          id="1b5b8f7c-1111-4222-8333-444455556666",
                          profile_id="1b5b8f7c-aaaa-4bbb-8ccc-dddddddddddd")
    monkeypatch.setattr(server, "_active_calliope_design_profile", lambda: pinned)
    for caller in (("ryan@example.com", "16a7448e-oauth-client"),
                   ("owner@example.com", server._HERMES_SERVICE_CLIENT_ID)):
        monkeypatch.setattr(server, "_authenticated_caller", lambda c=caller: c)
        doc = server._with_active_calliope_design_profile({})
        assert doc["design_profile"]["name"] == "House Style"
        assert doc["design_profile"]["version"] == 4
        assert server._publication_design_profile_summary()["source"] == "calliope_turn"


def test_direct_publisher_can_opt_out_with_an_explicit_null(direct_oauth):
    doc = server._with_active_calliope_design_profile({"design_profile": None, "x": 1})

    assert "design_profile" not in doc
    assert doc["x"] == 1
    assert server._publication_design_profile_summary() == {"source": "opt_out"}


def test_pinned_profile_compilation_failure_never_accepts_an_agent_substitute(monkeypatch):
    monkeypatch.setattr(server, "_active_calliope_design_profile", lambda: _profile_row())
    monkeypatch.setattr(server, "_freeze_design_profile", lambda profile: None)
    with pytest.raises(ValueError, match="pinned design profile could not be compiled"):
        server._with_active_calliope_design_profile({"design_profile": {"name": "Agent suggestion"}})


def test_direct_publisher_may_name_a_visible_profile(direct_oauth, monkeypatch):
    mine = _profile_row(name="Ryan Editorial", version=3, owner="ryan@example.com",
                        id="2b5b8f7c-1111-4222-8333-444455556666",
                        profile_id="2b5b8f7c-aaaa-4bbb-8ccc-dddddddddddd")
    seen = {}

    def resolve(reference, caller):
        seen["reference"], seen["caller"] = reference, caller
        return mine if reference.get("name") == "Ryan Editorial" else None
    monkeypatch.setattr(server, "_resolve_client_design_profile", resolve)

    doc = server._with_active_calliope_design_profile({"design_profile": {"name": "Ryan Editorial"}})
    assert doc["design_profile"]["name"] == "Ryan Editorial"
    assert doc["design_profile"]["version"] == 3
    assert seen == {"reference": {"name": "Ryan Editorial"}, "caller": "ryan@example.com"}
    assert server._publication_design_profile_summary()["source"] == "client"


@pytest.mark.parametrize("reference", [{"name": "Nope"}, {}, "Adaptive Calliope"])
def test_unresolvable_reference_never_substitutes_a_house_style(direct_oauth, monkeypatch, reference):
    monkeypatch.setattr(server, "_resolve_client_design_profile", lambda reference, caller: None)
    with pytest.raises(ValueError, match="did not match a visible profile"):
        server._with_active_calliope_design_profile({"design_profile": reference})


def test_forked_frozen_contract_is_carried_forward_when_its_version_is_gone(direct_oauth, monkeypatch):
    monkeypatch.setattr(server, "_resolve_client_design_profile", lambda reference, caller: None)
    frozen = {
        "schema_version": "rvbbit.artifact-design-profile.v1",
        "profile_id": "9b5b8f7c-aaaa-4bbb-8ccc-dddddddddddd",
        "version_id": "9b5b8f7c-1111-4222-8333-444455556666",
        "name": "Archived Look", "version": 1, "adaptive": True,
        "behavior": {"theme_source": "viewer"},
        "tokens": {"behavior": {"theme_source": "viewer"}, "palette": {"accent": "#123456"}},
    }
    doc = server._with_active_calliope_design_profile({"design_profile": dict(frozen)})
    assert doc["design_profile"] == frozen
    assert server._publication_design_profile_summary()["source"] == "client_frozen"


def test_visibility_is_owner_or_system_only():
    assert server._design_profile_visible_to({"profile_owner": "calliope@system"}, "anyone@x")
    assert server._design_profile_visible_to({"profile_owner": "Ryan@Example.com"}, "ryan@example.com")
    assert not server._design_profile_visible_to({"profile_owner": "other@example.com"}, "ryan@example.com")
    assert not server._design_profile_visible_to({"profile_owner": "other@example.com"}, None)


@pytest.mark.parametrize("tool", [server.tool_dashboard_template, server.tool_live_app_template])
def test_unpinned_templates_supply_no_profile(direct_oauth, tool):
    result = tool()
    assert "design_profile" not in result
    assert "composePayload" in result["template_html"]


def test_dashboard_template_adds_nothing_for_unpinned_hermes(hermes_service):
    assert "design_profile" not in server.tool_dashboard_template()


def test_unpinned_turn_removes_inherited_or_agent_supplied_profiles(hermes_service):
    doc = server._with_active_calliope_design_profile({"design_profile": {"name": "Unrequested"}, "keep": 1})
    assert doc == {"keep": 1}


def test_template_returns_only_the_actual_pinned_contract(direct_oauth, monkeypatch):
    pinned = _profile_row(name="User palette", tokens={"palette": {"accent": "#804020"}})
    monkeypatch.setattr(server, "_active_calliope_design_profile", lambda: pinned)
    package = server.tool_dashboard_template()["design_profile"]
    assert package["name"] == "User palette"
    assert package["attached_by_default"] is False
    assert package["compiled_prompt"] == pinned["compiled_prompt"]
    assert package["tokens"] == pinned["tokens"]
    assert "Superior" not in str(package)
    assert "adaptive_style_block" not in package


def test_main_authoring_prompt_follows_user_when_unpinned():
    note = calliope._instructions([], None)
    assert "No Design Profile is pinned" in note
    assert "Follow the user's visual brief" in note
    assert "Do not suggest or attach" in note
    pinned = calliope._instructions([], None, design_profile=_profile_row())
    assert "CALLIOPE_DESIGN_PROFILE_BEGIN" in pinned
    assert "No Design Profile is pinned" not in pinned


def test_publish_and_update_tools_report_the_profile_decision():
    source = (_HERE / "server.py").read_text(encoding="utf-8")
    assert source.count('"design_profile": _publication_design_profile_summary(),') == 4
    # publish/update dashboards + create/update live apps all freeze through one helper.
    assert source.count("_with_active_calliope_design_profile(") >= 5
