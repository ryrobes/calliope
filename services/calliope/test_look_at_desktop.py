"""Current computer tool wiring and read access to historical screenshots."""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("WAREHOUSE_DSN", "postgresql://x")

import calliope  # noqa: E402

SERVER_SRC = (HERE / "server.py").read_text(encoding="utf-8")
CALLIOPE_SRC = (HERE / "calliope.py").read_text(encoding="utf-8")
RECONCILER_SRC = (
    HERE.parent.parent / "docker" / "calliope" / "reconcile-hermes-config.py"
).read_text(encoding="utf-8")


def test_look_at_desktop_is_registered_and_returns_native_image():
    assert 'mcp.tool(name="look_at_desktop")(_mcp_look_at_desktop)' in SERVER_SRC
    body = SERVER_SRC.split("def _mcp_look_at_desktop(", 1)[1].split("\n\n\n", 1)[0]
    assert '_computer_operation("look_at_desktop", session_id)' in body
    assert "Image(path=res[\"path\"])" in body
    assert "fresh image" in body


def test_look_never_takes_a_lease_and_fetches_remote_evidence():
    source = (HERE / "desktop_tools.py").read_text()
    body = source.split("def look_at_desktop(", 1)[1].split("\ndef ", 1)[0]
    assert "acquire_lease" not in body
    assert '"observe"' in body
    assert '"observation.captured"' in body
    assert "runtime.download(key, path, shot.get(\"sha256\"))" in body


def test_screenshot_route_serves_look_events():
    route = CALLIOPE_SRC.split('/screenshots/{request_event_id}"', 1)[1].split("@mcp.custom_route", 1)[0]
    assert 'item.get("event_type") == "look.captured"' in route


def test_instructions_carry_the_workbench_note_only_when_given():
    base = calliope._instructions([], None)
    noted = calliope._instructions([], None, workbench_note="\n\nDESKTOP LAB: open")
    assert "DESKTOP LAB" not in base
    assert noted.endswith("DESKTOP LAB: open") or "DESKTOP LAB: open" in noted


def test_computer_note_is_installation_scoped_and_names_the_session(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(calliope.CalliopeConfig, "from_env", lambda: SimpleNamespace(desktop_enabled=True))
    note = calliope._desktop_workbench_note(None, "22222222-2222-4222-8222-222222222222")
    assert "CALLIE'S COMPUTER" in note
    assert 'open_desktop(session_id="22222222-2222-4222-8222-222222222222"' in note
    assert "desktop_exec" in note
    assert "finish_desktop" in note
    monkeypatch.setattr(calliope.CalliopeConfig, "from_env", lambda: SimpleNamespace(desktop_enabled=False))
    assert calliope._desktop_workbench_note(None, "22222222-2222-4222-8222-222222222222") == ""


def test_reconciler_pins_look_at_desktop_with_an_upgrade_path():
    assert '"mcp__Warehouse__look_at_desktop",' in RECONCILER_SRC
    assert "_PINNED_TOOLS_BEFORE_LOOK_AT_DESKTOP," in RECONCILER_SRC.split(
        "PINNED_TOOLS_PREVIOUS_DEFAULTS = (", 1
    )[1]
