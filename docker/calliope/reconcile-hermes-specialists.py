#!/usr/bin/env python3
"""Idempotently seed Calliope-managed Hermes specialist profiles.

Named profiles live in the existing /opt/data volume, so their sessions,
Hindsight identity, learned skills, and future cron state survive image
replacements. Only the managed runtime contract is reconciled here.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(os.environ.get("CALLIOPE_HERMES_SEED_ROOT", "/opt/hermes/calliope"))
SPECIALISTS_ROOT = Path(
    os.environ.get("CALLIOPE_HERMES_SPECIALISTS_ROOT", str(ROOT / "specialists"))
)
SKILLS_ROOT = Path(
    os.environ.get("CALLIOPE_HERMES_SKILLS_ROOT", str(ROOT / "skills"))
)
SPECIALIST_SKILLS_ROOT = Path(
    os.environ.get("CALLIOPE_HERMES_SPECIALIST_SKILLS_ROOT", str(ROOT / "specialist-skills"))
)
MANIFEST = SPECIALISTS_ROOT / "manifest.json"
DEFAULT_PROFILE_SOURCE = Path(
    os.environ.get("CALLIOPE_HERMES_PROFILE_SOURCE", str(ROOT / "profile.yaml"))
)
# The hosted config policy (model block rules, budgets) ships next to this
# script both in the repo (docker/calliope/) and in the image
# (/opt/hermes/calliope/); the model helpers are shared so a specialist's
# block is normalised by exactly the rules the default profile is validated by.
POLICY_PATH = Path(__file__).with_name("reconcile-hermes-config.py")
MODEL_OPERATOR_MARKER = "model.operator-managed"
ENV_KEYS = (
    "API_SERVER_KEY",
    "HINDSIGHT_API_URL",
    "HINDSIGHT_TIMEOUT",
    "RVBBIT_CLOVER_KEY",
    "RVBBIT_CLOVER_OPENAI_BASE_URL",
    "WAREHOUSE_HERMES_MCP_KEY",
)


def _load_policy():
    spec = importlib.util.spec_from_file_location("calliope_hermes_config_policy", POLICY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Calliope Hermes config policy is unavailable at {POLICY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICY = _load_policy()


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(entry for entry in path.rglob("*") if entry.is_file()):
        digest.update(str(item.relative_to(path)).encode())
        digest.update(item.read_bytes())
    return digest.hexdigest()


def _atomic_text(path: Path, text: str, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.calliope-tmp")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def _dotenv_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _reconcile_env(path: Path, overrides: dict[str, str] | None = None) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = existing.splitlines()
    positions: dict[str, int] = {}
    for index, line in enumerate(lines):
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match:
            positions[match.group(1)] = index
    for key in ENV_KEYS:
        value = os.environ.get(key)
        if value is None:
            continue
        rendered = f"{key}={_dotenv_quote(value)}"
        if key in positions:
            lines[positions[key]] = rendered
        else:
            lines.append(rendered)
    for key, value in (overrides or {}).items():
        rendered = f"{key}={_dotenv_quote(str(value))}"
        if key in positions:
            lines[positions[key]] = rendered
        else:
            lines.append(rendered)
    _atomic_text(path, "\n".join(lines).rstrip() + "\n")


# The specialist lane's pinned working set (same 2026-08-21 describe audit;
# the Specialist verbs dominated the histogram). See
# reconcile-hermes-config.PINNED_TOOLS_DEFAULT for the mechanism notes.
SPECIALIST_PINNED_TOOLS = (
    "mcp__Specialist__run_sql",
    "mcp__Specialist__run_sql_multi",
    "mcp__Specialist__recall_calliope_sql",
    "mcp__Specialist__validate_sql",
    "mcp__Specialist__describe_table",
    "mcp__Specialist__search_data",
    "mcp__Specialist__capability_search",
    "mcp__Specialist__create_live_app",
    "mcp__Specialist__get_live_app",
    "mcp__Specialist__update_live_app",
    "mcp__Specialist__capture_live_app",
    "mcp__Specialist__upload_artifact",
    "mcp__Specialist__dashboard_template",
    # Delegation carries the session Sketch, so specialists read it as a
    # matter of course — the 2026-08-21 grok run paid 13 describes + 12
    # deferred-bridge calls for this one tool before it was pinned.
    "mcp__Specialist__read_calliope_sketch",
    # 2026-09-10 pilot histogram: specialists reached these through the
    # deferred wrapper more than any other tools (get_metric ×366,
    # list_metrics ×105, metric_history ×59, metric ×56,
    # search_business_context ×46 in a day).
    "mcp__Specialist__get_metric",
    "mcp__Specialist__list_metrics",
    "mcp__Specialist__metric",
    "mcp__Specialist__metric_history",
    "mcp__Specialist__search_business_context",
    # 2026-09-22 pilot histogram (14 days): every specialist role reached these
    # through the deferred bridge or described them first (sessions in
    # parentheses): ask_brain (58), brain_facets (54), describe_cube (48),
    # list_specialist_findings (42), record_specialist_finding (39, described
    # 267×), business_context_changes (39), business_context_status (31),
    # get_business_value_system (30, described 291×), mirror_status (27),
    # list_cubes (27), get_tool_help (26), ask_system_learning (24),
    # administer_local_sql (23 sessions, 1,576 calls), wiki_list_pages (21),
    # wiki_read_page (13); find and meaning_tree were hand-pinned on the pilot
    # on 2026-09-20 and lost at the next reconcile — they belong here; and
    # teach_meaning, which the test box's domain agents bridged 188 times.
    "mcp__Specialist__find",
    "mcp__Specialist__meaning_tree",
    "mcp__Specialist__teach_meaning",
    "mcp__Specialist__get_tool_help",
    "mcp__Specialist__record_specialist_finding",
    "mcp__Specialist__list_specialist_findings",
    "mcp__Specialist__ask_brain",
    "mcp__Specialist__brain_facets",
    "mcp__Specialist__describe_cube",
    "mcp__Specialist__list_cubes",
    "mcp__Specialist__mirror_status",
    "mcp__Specialist__business_context_status",
    "mcp__Specialist__business_context_changes",
    "mcp__Specialist__get_business_value_system",
    "mcp__Specialist__ask_system_learning",
    "mcp__Specialist__administer_local_sql",
    "mcp__Specialist__wiki_list_pages",
    "mcp__Specialist__wiki_read_page",
)

# What each shipping role reached through the bridge beyond the common set
# (same 14-day pilot histogram, keyed by profile). Appended to
# SPECIALIST_PINNED_TOOLS; every list stays under the 48 cap.
SPECIALIST_ROLE_PINNED_TOOLS = {
    # Hestia: 1,360 administer_local_sql bridges, search_calliope_actions 322,
    # get_tool_help 149, search_tools 145, draft_calliope_workflow 118,
    # acceleration_steward_snapshot 113, profile_schema 85, search_hermes_docs 84,
    # workspace_job_status 72, list_resources 53, begin_calliope_workflow_run 52.
    "warehouse-steward": (
        "mcp__Specialist__search_calliope_actions",
        "mcp__Specialist__acceleration_steward_snapshot",
        "mcp__Specialist__profile_schema",
        "mcp__Specialist__workspace_job_status",
        "mcp__Specialist__draft_calliope_workflow",
        "mcp__Specialist__begin_calliope_workflow_run",
        "mcp__Specialist__search_hermes_docs",
        "mcp__Specialist__list_resources",
        "mcp__Specialist__system_learning_status",
    ),
    # Demeter: ask_brain 534, wiki_list_pages 184, brain_browse 122, brain_facets 117,
    # brain_get_doc 83, wiki_write_page (Pheme 41; described 93×).
    "knowledge-gardener": (
        "mcp__Specialist__brain_browse",
        "mcp__Specialist__brain_get_doc",
        "mcp__Specialist__wiki_write_page",
        "mcp__Specialist__brain_context",
    ),
    # Iris: update_live_app 123, list_live_apps 93, artifact_access_get 76,
    # debug_live_app 59, list_dashboards 59, generate_image 52, read_calliope_playbook 49.
    "dashboard-studio": (
        "mcp__Specialist__list_live_apps",
        "mcp__Specialist__artifact_access_get",
        "mcp__Specialist__debug_live_app",
        "mcp__Specialist__list_dashboards",
        "mcp__Specialist__generate_image",
        "mcp__Specialist__live_app_status",
    ),
    # Argus: compare 146, metric_history (common), business_metric_change 65,
    # metric_lineage, breaching_kpis 38, breaching_alerts 34.
    "signal-watch": (
        "mcp__Specialist__compare",
        "mcp__Specialist__business_metric_change",
        "mcp__Specialist__metric_lineage",
        "mcp__Specialist__breaching_kpis",
        "mcp__Specialist__breaching_alerts",
    ),
    # Cassandra: get_metric 146 (now direct), metric_lineage 46, business_context_for_question.
    "analytical-skeptic": (
        "mcp__Specialist__metric_lineage",
        "mcp__Specialist__business_context_for_question",
        "mcp__Specialist__metric_dimensions",
    ),
    # Athena: metric_lineage, business_context_for_question, edit_metric/create_metric were described 81×/75×.
    "semantic-architect": (
        "mcp__Specialist__metric_lineage",
        "mcp__Specialist__business_context_for_question",
        "mcp__Specialist__metric_dimensions",
        "mcp__Specialist__create_metric",
        "mcp__Specialist__edit_metric",
    ),
}

# Nemesis (design-red-team) reviews, and applies presentation edits when
# asked: the picture, the source, the template primitives, the Sketch, the
# two edits=[...] publish paths, and a place to record a finding. No SQL,
# no terminal, no files — the looping we measured on the pilot (2026-09-10)
# lived in exactly those tools. The publish tools MUST be pinned, not
# deferred: on 2026-09-10 a model that found update_live_app through
# tool_search could not reach it through the tool_call bridge and emitted
# its arguments under the pinned capture_live_app name thirteen times.
DESIGN_RED_TEAM_PINNED_TOOLS = (
    "mcp__Specialist__capture_live_app",
    "mcp__Specialist__get_live_app",
    "mcp__Specialist__update_live_app",
    "mcp__Specialist__get_dashboard",
    "mcp__Specialist__update_dashboard",
    "mcp__Specialist__dashboard_template",
    "mcp__Specialist__read_calliope_sketch",
    "mcp__Specialist__record_specialist_finding",
)
DESIGN_RED_TEAM_TOOLSETS = (
    "vision",
    "skills",
    "todo",
    "memory",
    "session_search",
    "Specialist",
)


def specialist_pinned_tools(profile_name: str) -> list[str]:
    """The pinned working set for one profile: the common specialist set plus
    what its role reached through the bridge. Nemesis keeps her own short list."""
    if profile_name == "design-red-team":
        return list(DESIGN_RED_TEAM_PINNED_TOOLS)
    pinned = list(SPECIALIST_PINNED_TOOLS)
    for name in SPECIALIST_ROLE_PINNED_TOOLS.get(profile_name, ()):
        if name not in pinned:
            pinned.append(name)
    return pinned


def _profile_config(profile_name: str) -> dict:
    config = _profile_config_base(profile_name)
    POLICY.apply_managed_media_defaults(config)
    POLICY.apply_managed_approval_defaults(config)
    POLICY.apply_managed_auxiliary_defaults(config)
    config["tools"] = {"tool_search": {"pinned": specialist_pinned_tools(profile_name)}}
    if profile_name == "design-red-team":
        config["platform_toolsets"] = {"api_server": list(DESIGN_RED_TEAM_TOOLSETS)}
    return config


def _profile_config_base(profile_name: str) -> dict:
    # Multiplex profiles share Hermes' global tool registry. The root gateway
    # registers this second MCP lane so every specialist can call Warehouse
    # while the parent's default Warehouse transport remains in flight.
    return {
        "_config_version": 37,
        # New profiles use the release default. Existing profile model choices
        # survive upgrades independently of credential connection/rotation.
        "model": {
            **POLICY.DEFAULT_MODEL_CHOICE,
            "context_length": int(os.environ.get("CALLIOPE_MODEL_CONTEXT_LENGTH", "262144")),
            "max_tokens": int(os.environ.get("CALLIOPE_MODEL_MAX_TOKENS", "32768")),
        },
        # Match the root agent's working-context budget. The former 9k trigger
        # was below an ordinary specialist prompt (including tool schemas): it
        # repeatedly replaced dashboard contracts and proved SQL with one-line
        # placeholders, causing re-discovery instead of implementation. Audit
        # history is not a substitute for evidence in the active model context.
        # Prune only substantial older results under real context pressure;
        # normal model-window compaction still handles smaller context windows.
        "compression": {
            "protect_first_n": 1,
            "protect_last_n": 12,
            "min_tail_user_messages": 1,
            "max_attempts": 5,
            "proactive_prune_tokens": 120000,
            "proactive_prune_min_result_chars": 6000,
            "proactive_prune_min_reclaim_tokens": 8192,
        },
        "tools": {"tool_search": {"pinned": list(SPECIALIST_PINNED_TOOLS)}},
        "memory": {
            "memory_enabled": True,
            "user_profile_enabled": True,
            "write_approval": False,
            "provider": "hindsight",
            "memory_char_limit": 2200,
            "user_char_limit": 1375,
            "nudge_interval": 10,
            "flush_min_turns": 6,
        },
        # No reflex skill curator (a full-history fork after every tool-heavy
        # turn); specialists' skills are managed by the manifest, not learned
        # per turn. See docker/calliope/hermes-config.yaml.
        "skills": {"creation_nudge_interval": 0},
        # Container-wide API_SERVER_* variables belong to the default profile.
        # Upstream explicitly honors this YAML false (via _enabled_explicit)
        # even while the key remains available for multiplexed /p/<profile>/
        # authentication.
        "platforms": {
            "api_server": {"enabled": False},
            # Google Chat's plugin can auto-enable from the root gateway's
            # environment. Only that gateway may consume its subscription;
            # specialists receive delegated work through the multiplex API.
            "google_chat": {"enabled": False},
        },
        # Multiplex profiles share the process-wide MCP registry. Explicitly
        # allow only their independent callback lane so a specialist cannot
        # discover or invoke the parent Calliope transport.
        "platform_toolsets": {
            # Same composed lane as the parent (no credential-gated web/video
            # traps; see reconcile-hermes-config.API_SERVER_TOOLSET_INVARIANTS)
            # plus the specialist Warehouse lane.
            "api_server": [
                "terminal",
                "file",
                "vision",
                "image_gen",
                "skills",
                "browser",
                "todo",
                "memory",
                "session_search",
                "code_execution",
                "delegation",
                "cronjob",
                "homeassistant",
                "Specialist",
            ]
        },
        "mcp_servers": {
            "Specialist": {
                "url": "http://calliope:8765/mcp?lane=specialist",
                "headers": {"Authorization": "Bearer ${env:WAREHOUSE_HERMES_MCP_KEY}"},
                "forward_session_identity": True,
                "supports_parallel_tool_calls": True,
                "timeout": 300,
                "tool_timeouts": {"generate_image": 660, "run_workspace_sql": 660, "administer_local_sql": 660},
            }
        },
    }


def _degenerate_copy(source: Path, target: Path) -> bool:
    """True when the target is a hollow copy of the source: a file the source
    ships with content exists in the target with zero bytes.

    2026-08-17, on a hosted install: every specialist profile's SKILL.md and reference
    files were 0 bytes (since 2026-08-14) while the shipped sources were fine.
    The empty tree hashed to neither the marker nor the source, so the operator-
    modified rule below "preserved" it on every boot and Iris ran without her
    dashboard skill for three days. Empty is never an operator's edit."""
    if source.is_file():
        return target.is_file() and target.stat().st_size == 0 and source.stat().st_size > 0
    if not target.is_dir():
        return False
    for item in source.rglob("*"):
        if not item.is_file() or item.stat().st_size == 0:
            continue
        counterpart = target / item.relative_to(source)
        if counterpart.is_file() and counterpart.stat().st_size == 0:
            return True
    return False


def _managed_copy(source: Path, target: Path, marker: Path) -> str:
    source_hash = _tree_hash(source) if source.is_dir() else _hash_file(source)
    prior_hash = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    if source.is_dir():
        current_hash = _tree_hash(target) if target.is_dir() else ""
    else:
        current_hash = _hash_file(target) if target.is_file() else ""
    degenerate = _degenerate_copy(source, target)
    if not target.exists() or current_hash == prior_hash or current_hash == source_hash or degenerate:
        if target.exists() and source.is_dir():
            shutil.rmtree(target)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
        marker.parent.mkdir(parents=True, exist_ok=True)
        _atomic_text(marker, source_hash + "\n")
        if degenerate:
            return "repaired"
        return "seeded" if not current_hash else "updated"
    return "preserved"


def _profile_identity(source: Path, key: str) -> dict:
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"invalid profile identity for {key}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"profile identity for {key} must be a YAML object")
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    if not name or not description:
        raise RuntimeError(f"profile identity for {key} requires name and description")
    return payload


def _reconcile_profile_identity(source: Path, target: Path, marker: Path) -> str:
    """Seed identity metadata while upgrading the legacy generated stub.

    Older Calliope images wrote only description/description_auto on every
    start. That shape was never user-owned identity configuration, so it is
    safe to replace once. Any richer document is handled by _managed_copy and
    therefore survives image upgrades.
    """
    if target.is_file() and not marker.exists():
        try:
            payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            payload = {}
        if isinstance(payload, dict) and set(payload) <= {"description", "description_auto"}:
            shutil.copy2(source, target)
            marker.parent.mkdir(parents=True, exist_ok=True)
            _atomic_text(marker, _hash_file(source) + "\n")
            return "upgraded-legacy"
    return _managed_copy(source, target, marker)


# The MCP lanes were renamed on 2026-09-23 (WarehouseSpecialist → Specialist,
# WarehouseRoundtable → Roundtable), so tool names are mcp__Specialist__* /
# mcp__Roundtable__*. Profile configs written before that still carry the old
# names in their pins, mcp_servers keys and platform toolset lists; those
# names now resolve to nothing. Normalize them in place, before any converge
# logic, so an operator-managed document keeps its choices under the new names.
LEGACY_LANE_NAMES = {
    "WarehouseSpecialist": "Specialist",
    "WarehouseRoundtable": "Roundtable",
}


def _rename_lane(value: str) -> str:
    for legacy, current in LEGACY_LANE_NAMES.items():
        if value == legacy:
            return current
        prefix = f"mcp__{legacy}__"
        if value.startswith(prefix):
            return f"mcp__{current}__" + value[len(prefix):]
    return value


def _normalize_lane_names_in_list(values: list) -> tuple[list, bool]:
    """Rewrite legacy lane names (bare toolset entries and mcp__<lane>__ tool
    prefixes) preserving order. A rewrite that lands on a name the list
    already carries is dropped so the existing entry keeps its position;
    entries that need no rewrite are left exactly as they are."""
    changed = False
    originals = {value for value in values if isinstance(value, str)}
    emitted: set = set()
    result: list = []
    for value in values:
        item = _rename_lane(value) if isinstance(value, str) else value
        if item != value:
            changed = True
            if item in originals or item in emitted:
                continue
        if isinstance(item, str):
            emitted.add(item)
        result.append(item)
    return result, changed


def _normalize_legacy_lane_names(config: dict) -> bool:
    """Apply the lane rename to one profile config document in place.

    pins (tools.tool_search.pinned): prefix rewrite preserving order;
    mcp_servers: rename the key in place, dropping the legacy entry when the
    new key already exists; platform_toolsets lists: entry rewrite. Returns
    whether anything changed.
    """
    changed = False
    tools = config.get("tools")
    tool_search = tools.get("tool_search") if isinstance(tools, dict) else None
    if isinstance(tool_search, dict) and isinstance(tool_search.get("pinned"), list):
        pinned, pinned_changed = _normalize_lane_names_in_list(tool_search["pinned"])
        if pinned_changed:
            tool_search["pinned"] = pinned
            changed = True
    servers = config.get("mcp_servers")
    if isinstance(servers, dict) and any(legacy in servers for legacy in LEGACY_LANE_NAMES):
        renamed: dict = {}
        for name, value in servers.items():
            current = LEGACY_LANE_NAMES.get(name, name)
            if current != name and (current in servers or current in renamed):
                continue  # the new key already exists: drop the legacy twin
            renamed[current] = value
        servers.clear()
        servers.update(renamed)
        changed = True
    toolsets = config.get("platform_toolsets")
    if isinstance(toolsets, dict):
        for platform, entries in toolsets.items():
            if isinstance(entries, list):
                normalized, entries_changed = _normalize_lane_names_in_list(entries)
                if entries_changed:
                    toolsets[platform] = normalized
                    changed = True
    return changed


def _reconcile_config(profile: Path, desired: dict, markers: Path) -> str:
    """Reconcile shipped config without erasing profile-owned choices.

    A simple skill toggle remains a managed config: only the native `skills`
    subtree is carried forward while new appliance defaults continue to land.
    The same goes for the operator's provider/model choice (Models & agents
    writes `model.operator-managed`): the `model` block travels with the
    profile while everything around it keeps receiving appliance defaults.
    Saving YAML from DataRabbit writes `config.user-managed`, at which point
    the entire document belongs to the operator and image upgrades preserve it
    (the one exception is the lane rename, applied to that document in place).
    """
    path = profile / "config.yaml"
    current: dict = {}
    if path.exists():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            current = loaded if isinstance(loaded, dict) else {}
        except (OSError, yaml.YAMLError):
            current = {}
    lanes_renamed = _normalize_legacy_lane_names(current)
    if (markers / "config.user-managed").exists() and path.exists():
        if lanes_renamed:
            _atomic_text(path, yaml.safe_dump(current, sort_keys=False, allow_unicode=True), 0o660)
            return "normalized-user-managed"
        return "preserved-user-managed"
    if isinstance(current.get("model"), dict) and not POLICY.model_block_errors(current["model"]):
        desired["model"] = POLICY.carry_model_choice(current["model"], dict(desired.get("model") or {}))
        # The appliance-wide fallback and the reasoning effort travel with the
        # model choice (top-level keys; the model-block carry does not cover them).
        POLICY.carry_fallback_model(current, desired)
        POLICY.carry_reasoning_effort(current, desired)
    # Background routing can be chosen independently of the main model. An
    # empty section is an explicit automatic-routing choice, not an omission.
    POLICY.carry_auxiliary_settings(current, desired)
    # Explicit approval policies survive upgrades; absent policies inherit the
    # unattended appliance default, independently of the model marker.
    if isinstance(current.get("approvals"), dict):
        desired["approvals"] = {**desired.get("approvals", {}), **current["approvals"]}
    # Media choices can be saved independently of the main model marker.
    for capability in ("image_gen", "stt"):
        if isinstance(current.get(capability), dict) and current[capability].get("provider"):
            desired[capability] = current[capability]
    if isinstance(current.get("skills"), dict):
        # The profile owns its skill toggles (`disabled`, …); appliance
        # defaults for other skill keys still land when the profile has not
        # set them, and Hermes' upstream curator interval (10) counts as unset.
        merged = dict(current["skills"])
        for key, value in (desired.get("skills") or {}).items():
            if key not in merged or (key == "creation_nudge_interval" and merged.get(key) in (None, 10)):
                merged[key] = value
        desired["skills"] = merged
    rendered = yaml.safe_dump(desired, sort_keys=False, allow_unicode=True)
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return "current"
    _atomic_text(path, rendered, 0o660)
    return "seeded" if not current else "updated"


def _profile_model_pin(profile: Path) -> tuple[str | None, str | None]:
    """The profile's own provider/model, to pin its cron job.

    Hermes' drift guard (#44585) silently skips any UNPINNED job whenever
    the global inference config changes since job creation — sane for ad-hoc
    jobs, fatal on an appliance where operators swap root models freely:
    observed 2026-09-03, three specialists' nightly duty cycles had been
    drift-skipped 13 nights straight with the alert delivered to a local
    file. Pinned to the profile's model the job runs exactly what the
    specialist itself runs, and the reconciler refreshes the pin every pass
    so operator model changes follow at the next reconcile.
    """
    try:
        import yaml

        cfg = yaml.safe_load((profile / "config.yaml").read_text()) or {}
        model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
        provider = str(model_cfg.get("provider") or "").strip() or None
        model = str(model_cfg.get("default") or "").strip() or None
        return provider, model
    except Exception:  # noqa: BLE001 — an unreadable config just leaves the job unpinned
        return None, None


def _reconcile_cron(profile: Path, spec: dict) -> str:
    cron = spec.get("cron") if isinstance(spec.get("cron"), dict) else None
    if not cron:
        return "none"
    # The reconciler lives under /opt/hermes/calliope while Hermes' cron
    # package lives one directory above it. Keep this import local so source-
    # tree tests can still exercise profile reconciliation without vendoring
    # the upstream runtime.
    hermes_root = ROOT.parent
    if str(hermes_root) not in sys.path:
        sys.path.insert(0, str(hermes_root))
    try:
        from cron.jobs import create_job, list_jobs, update_job, use_cron_store
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    except ImportError as exc:
        return f"unavailable:{type(exc).__name__}"

    name = str(cron["name"])
    provider, model = _profile_model_pin(profile)
    # Rung 2 of thread-with-a-heartbeat (0397): every managed duty cycle
    # checks the Floor's operator notes before working, honors them as
    # standing guidance, and marks them read — "leave a note for the night
    # shift" without any new transport.
    notes_clause = (
        "\n\nOPERATOR NOTES: before starting, run this SQL —\n"
        "SELECT id, note, author_email, created_at FROM rvbbit.duty_cycle_notes "
        f"WHERE profile_key='{profile.name}' AND lower(job_name)=lower('{name}') "
        "AND consumed_at IS NULL ORDER BY created_at;\n"
        "Treat any rows as standing guidance from the named operator for this "
        "run and honor them. Then mark them read: UPDATE rvbbit.duty_cycle_notes "
        "SET consumed_at=now() WHERE id IN (<those ids>). Mention in your "
        "summary which notes you applied. If there are none, proceed normally."
    )
    desired = {
        "prompt": str(cron["prompt"]).rstrip() + notes_clause,
        "schedule": str(cron["schedule"]),
        "skills": [str(cron["skill"])],
        "deliver": "local",
        "attach_to_session": False,
    }
    if provider and model:
        desired["provider"] = provider
        desired["model"] = model
    token = set_hermes_home_override(str(profile))
    try:
        with use_cron_store(profile):
            matches = [
                job for job in list_jobs(include_disabled=True)
                if str(job.get("name") or "").casefold() == name.casefold()
            ]
            if not matches:
                create_job(
                    prompt=desired["prompt"],
                    schedule=desired["schedule"],
                    name=name,
                    skills=desired["skills"],
                    deliver=desired["deliver"],
                    attach_to_session=False,
                    provider=provider,
                    model=model,
                )
                return "seeded"
            # A profile refresh must not undo an operator's pause. New jobs
            # default to enabled in create_job; updates preserve lifecycle state.
            update_job(matches[0]["id"], desired)
            return "updated"
    finally:
        reset_hermes_home_override(token)


def _drop_shadowed_grants(home: Path, profile: Path) -> list[str]:
    """Remove profile-local credential-pool rows that duplicate a root row.

    Before hermes-shared-oauth-root-store.patch a profile that refreshed a
    grant borrowed from the root persisted the rotated pair into its own
    auth.json; that shadow copy then diverged from the root on the next
    rotation and one side got "revoked". The root is authoritative for
    every row it holds: a profile copy of the same entry id is a stale
    shadow, never an operator's own credential. Returns the providers cleaned."""
    root_path = home / "auth.json"
    profile_path = profile / "auth.json"
    if not root_path.is_file() or not profile_path.is_file():
        return []
    try:
        root_pool = (json.loads(root_path.read_text(encoding="utf-8")) or {}).get("credential_pool") or {}
        store = json.loads(profile_path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return []
    pool = store.get("credential_pool")
    if not isinstance(pool, dict) or not isinstance(root_pool, dict):
        return []
    cleaned: list[str] = []
    for provider, entries in list(pool.items()):
        root_ids = {
            str(item.get("id")) for item in (root_pool.get(provider) or [])
            if isinstance(item, dict) and item.get("id")
        }
        if not root_ids or not isinstance(entries, list):
            continue
        kept = [item for item in entries if not (isinstance(item, dict) and str(item.get("id")) in root_ids)]
        if len(kept) != len(entries):
            cleaned.append(provider)
            if kept:
                pool[provider] = kept
            else:
                pool.pop(provider, None)
    if cleaned:
        _atomic_text(profile_path, json.dumps(store, indent=2) + "\n", 0o600)
    return cleaned


OVERLAYS_URL = os.environ.get(
    "CALLIOPE_AGENT_OVERLAYS_URL",
    "http://calliope:8765/api/calliope/agent-overlays",
)
OVERLAY_BEGIN = "<!-- BEGIN LEARNED OVERLAYS — composed from rvbbit.agent_overlays; propose/adopt via overlay tools, never edit here -->"
OVERLAY_END = "<!-- END LEARNED OVERLAYS -->"


def _fetch_agent_overlays(home: Path) -> dict[tuple[str, str, str], list[dict]]:
    """Fetch adopted learned overlays from the Calliope service.

    Fail-open with a durable cache: if Calliope is unreachable at boot the
    last successfully fetched overlays still compose, so a start-ordering
    race never strips learned content from a profile.
    """
    import urllib.request

    cache = home / ".calliope-seeds" / "agent-overlays.json"
    raw = None
    key = os.environ.get("WAREHOUSE_HERMES_MCP_KEY", "")
    if key:
        try:
            request = urllib.request.Request(
                OVERLAYS_URL, headers={"Authorization": f"Bearer {key}"}
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                raw = json.loads(response.read().decode("utf-8"))
            cache.parent.mkdir(parents=True, exist_ok=True)
            _atomic_text(cache, json.dumps(raw, indent=1) + "\n")
        except Exception:
            raw = None
    if raw is None and cache.is_file():
        try:
            raw = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            raw = None
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for item in (raw or {}).get("overlays", []):
        if not isinstance(item, dict):
            continue
        group = (
            str(item.get("agent_key") or ""),
            str(item.get("target_kind") or ""),
            str(item.get("target_key") or ""),
        )
        grouped.setdefault(group, []).append(item)
    return grouped


# Standing rules for Calliope collaboration threads. This used to travel as
# ~2.6K chars of identical user-message freight on EVERY round of every
# specialist thread (and was eligible for compaction, unlike a workspace
# document). As SOUL doctrine it is paid once from prompt cache and survives
# context compression. The per-round handoff keeps only a two-line hard-rail
# reminder, so even a hand-edited SOUL cannot strand a specialist entirely.
COLLABORATION_CONTRACT = """

## Collaboration contract (Calliope threads — appliance-managed)

When a user message opens with "Calliope is speaking with ..." you are in one
round of a persistent collaboration thread with Calliope, the warehouse lead.

- Work only on the delegated step. Settled decisions and supplied evidence are
  inputs to use; investigate only missing implementation facts. A concrete
  contradiction must be reported to Calliope with the exact decision and evidence
  before changing it. Do not redo the overall task or take over its narrative.
- Respond as an autonomous collaborator, not a one-shot tool. You may
  disagree, challenge Calliope's assumptions, expose counterevidence, or ask
  a precise follow-up question. The conversation continues in later rounds,
  so never force a premature terminal answer. Work independently until the
  round has a useful reply or is concretely blocked.
- You inherit Calliope's full Warehouse capability library: capability_search,
  Company Brain and Business Context retrieval, metrics, cubes, SQL,
  administration, artifact, dashboard, and connector tools. The tools you use
  most are already pinned in front of you — call them directly. Ask
  capability_search only for an ability you cannot see (a SQL operator, an
  installed MCP server, a pack, a technical skill), never for a table, a
  column or a business term — that is find's job — and never as an opening
  ritual; do not fall back to blind information_schema excavation.
- Any run_sql or run_sql_multi result you produce is automatically projected
  onto the originating parent turn's Stage. Set its presentation/default_view
  as needed and use the returned surface receipt; never search for a separate
  Stage persistence tool or create a work item, action, upload, or dashboard
  merely to preserve query evidence.
- After compression, use recall_calliope_sql to recover exact earlier SQL
  (including workspace transformations) from this chat's durable receipts.
  Check the receipt outcome before treating a write as applied. Do not infer
  lost CASE expressions or recreate a transformation from its output rows.
  Read oversized saved results with read_tool_result; no code execution is needed.
- Transport: this multiplex profile has a dedicated Warehouse lane. Use only
  tool names beginning with mcp__Specialist__; the default
  mcp__Warehouse__ prefix belongs to the parent Calliope transport and cannot
  be re-entered during your speaking turn.
- Never call delegate_calliope_specialist, delegate_all_calliope_specialists,
  continue_calliope_specialist, list_calliope_specialist_threads, or
  calliope_work_item; those are parent orchestration tools, not a reply
  channel. Do not delegate recursively.
- Calliope owns Playbook selection and execution. Do not read, run, or adopt
  coordinator Playbooks. Follow your scoped assignment and technical skill
  references; the dashboard_template is technical runtime documentation and
  remains appropriate. Do not turn the parent conversation into a solo task.
  Ask Calliope when a missing scope decision blocks your assigned work.
- Rounds may include a CURRENT-TURN CONTEXT envelope with parent_conversation:
  a bounded recent slice of the originating Calliope chat plus the human's
  current message. Use it to resolve references such as "that", "the earlier
  result", or "take another look"; the explicit assignment and current-turn
  pinned objects remain authoritative.
- When a round says it is asynchronous, Calliope has moved on and receives
  your final reply as an agent message in her conversation. If a decision that
  belongs to Calliope or the human blocks you, call ask_calliope once with the
  precise question (and options), then end the round with a short status.
- Your final response automatically returns to Calliope and is the only
  correct way to answer: state your position plainly, include proof, and make
  any requested next exchange explicit.
"""


def _overlay_block(items: list[dict]) -> str:
    if not items:
        return ""
    parts = ["", "", OVERLAY_BEGIN]
    for item in items:
        parts.append("")
        parts.append(f"## Learned: {item.get('section') or 'note'}")
        parts.append(str(item.get("body") or "").strip())
    parts.append("")
    parts.append(OVERLAY_END)
    parts.append("")
    return "\n".join(parts)


def _managed_compose(source: Path, target: Path, marker: Path, block: str) -> str:
    """_managed_copy semantics with a learned-overlay block composed in.

    The marker records the hash of what WE last wrote (post-composition), so
    hand-edited targets stay preserved while baseline or overlay changes
    still propagate. Directory sources compose into the tree's SKILL.md.
    """
    if not block:
        return _managed_copy(source, target, marker)
    prior_hash = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    if source.is_dir():
        current_hash = _tree_hash(target) if target.is_dir() else ""
    else:
        current_hash = _hash_file(target) if target.is_file() else ""
    degenerate = _degenerate_copy(source, target)
    if not target.exists() or current_hash == prior_hash or degenerate:
        if target.exists() and source.is_dir():
            shutil.rmtree(target)
        if source.is_dir():
            shutil.copytree(source, target)
            skill_doc = target / "SKILL.md"
            if skill_doc.is_file():
                _atomic_text(
                    skill_doc,
                    skill_doc.read_text(encoding="utf-8").rstrip("\n") + "\n" + block,
                )
            written_hash = _tree_hash(target)
        else:
            composed = source.read_text(encoding="utf-8").rstrip("\n") + "\n" + block
            _atomic_text(target, composed)
            written_hash = _hash_file(target)
        marker.parent.mkdir(parents=True, exist_ok=True)
        _atomic_text(marker, written_hash + "\n")
        if degenerate:
            return "repaired"
        return "seeded" if not current_hash else "updated"
    return "preserved"


GENERATED_URL = os.environ.get(
    "CALLIOPE_GENERATED_SPECIALISTS_URL",
    "http://calliope:8765/api/calliope/generated-specialists",
)


GENERATED_KEY_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,62}")
# Retired generated profiles leave profiles/ (Hermes 0.21.4 serves every
# directory there and each one costs boot time) for a sibling archive on the
# same volume: profiles-archive/<key>-<UTC stamp>/. Nothing is deleted.
PROFILES_ARCHIVE_DIRNAME = "profiles-archive"


def _fetch_generated_roster(home: Path) -> tuple[dict, bool]:
    """The generated-specialist roster as the Calliope service reports it:
    `specialists` (active rows, full definitions) and `retired` (keys the
    table knows but no longer serves).

    Same fail-open durable-cache contract as overlays: a boot-ordering race
    never strips a generated profile that previously reconciled. Returns
    the payload plus whether it came from a live fetch this run; the cache
    is only ever written from a successful fetch, so a cached `retired`
    list is a real past answer, never an inference from a failed call.
    """
    import urllib.request

    cache = home / ".calliope-seeds" / "generated-specialists.json"
    raw = None
    live = False
    key = os.environ.get("WAREHOUSE_HERMES_MCP_KEY", "")
    if key:
        try:
            request = urllib.request.Request(
                GENERATED_URL, headers={"Authorization": f"Bearer {key}"}
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                raw = json.loads(response.read().decode("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("generated-specialists payload must be an object")
            cache.parent.mkdir(parents=True, exist_ok=True)
            _atomic_text(cache, json.dumps(raw, indent=1) + "\n")
            live = True
        except Exception:
            raw = None
            live = False
    if raw is None and cache.is_file():
        try:
            raw = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            raw = None
    return (raw if isinstance(raw, dict) else {}), live


def _generated_active_items(roster: dict) -> list[dict]:
    specialists = []
    for item in roster.get("specialists", []) or []:
        if isinstance(item, dict) and GENERATED_KEY_RE.fullmatch(
            str(item.get("specialist_key") or "")
        ):
            specialists.append(item)
    return specialists


def _generated_retired_keys(roster: dict) -> list[str]:
    keys: list[str] = []
    for value in roster.get("retired", []) or []:
        key = str(value.get("specialist_key") if isinstance(value, dict) else value or "")
        if GENERATED_KEY_RE.fullmatch(key) and key not in keys:
            keys.append(key)
    return keys


def _fetch_generated_specialists(home: Path) -> list[dict]:
    """Factory-generated domain experts that should be served (active rows).

    Kept as the supervisor's contract (hermes-gateway-supervisor.py calls
    this by name from its live reconcile loop). Baked manifest keys always
    win over generated ones.
    """
    roster, _live = _fetch_generated_roster(home)
    return _generated_active_items(roster)


def _archive_generated_profile(home: Path, key: str) -> Path | None:
    """Move profiles/<key> out of the served tree into profiles-archive/.

    Same volume, single atomic rename, nothing deleted: sessions, Hindsight
    state and any hand edits ride along and an operator can rename the
    directory back. Returns the archive path, or None when there was no
    profile directory to move.
    """
    from datetime import datetime, timezone

    source = home / "profiles" / key
    if not source.is_dir():
        return None
    archive_root = home / PROFILES_ARCHIVE_DIRNAME
    archive_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = archive_root / f"{key}-{stamp}"
    suffix = 1
    while target.exists():
        suffix += 1
        target = archive_root / f"{key}-{stamp}-{suffix}"
    os.rename(source, target)
    return target


def _generated_manifest_entry(item: dict) -> dict:
    key = str(item["specialist_key"])
    skills = item.get("skills")
    if not isinstance(skills, list) or not skills:
        skills = ["calliope-domain-expert", "calliope-hosted-operations"]
    return {
        "key": key,
        "memory_bank": f"calliope-{key}",
        "memory_mission": str(item.get("memory_mission") or (
            f"Retain durable {item.get('role_label') or key} domain knowledge."
        )),
        "skills": [str(s) for s in skills],
    }


def _normalize_runtime_ownership(home: Path, profiles_root: Path) -> None:
    """Make root-written runtime inputs readable by the Hermes supervisor."""
    if os.geteuid() != 0:
        return  # Runtime-created files already belong to the supervisor.
    try:
        seeds = home / ".calliope-seeds"
        if seeds.exists():
            shutil.chown(seeds, user="hermes", group="hermes")
            for path in seeds.rglob("*"):
                shutil.chown(path, user="hermes", group="hermes")
        shutil.chown(home / ".env", user="hermes", group="hermes")
        if (home / "profile.yaml").exists():
            shutil.chown(home / "profile.yaml", user="hermes", group="hermes")
        generated_cache = home / ".calliope-seeds" / "generated-specialists.json"
        if generated_cache.exists():
            # The hosted-policy validator runs as hermes.  A root:root 0600
            # cache makes it silently fall back to the baked six-profile list
            # even though the reconciled config contains generated profiles.
            shutil.chown(generated_cache, user="hermes", group="hermes")
        shutil.chown(profiles_root, user="hermes", group="hermes")
        for path in profiles_root.rglob("*"):
            shutil.chown(path, user="hermes", group="hermes")
        # The live reconcile loop runs as hermes and must be able to rename
        # a retired profile into the archive root the boot pass created.
        archive_root = home / PROFILES_ARCHIVE_DIRNAME
        if archive_root.is_dir():
            shutil.chown(archive_root, user="hermes", group="hermes")
    except LookupError:
        pass


# Google Fonts family names, not URL-encoded query strings. A choice becomes
# part of the profile identity and is reused on every subsequent reconciliation.
GENERATED_SIGNATURE_FONTS = (
    "Parisienne", "My Soul", "Eagle Lake", "Splash", "Condiment", "Molle",
    "Charmonman", "Cinzel Decorative", "Uncial Antiqua", "Caesar Dressing",
    "Bokor", "Jersey 25", "UnifrakturCook", "Germania One", "Marcellus SC",
    "Sedgwick Ave Display", "Permanent Marker", "Rye",
)


def _generated_signature_font(profile: Path | None = None) -> str:
    if profile is not None and profile.is_file():
        identity = yaml.safe_load(profile.read_text(encoding="utf-8")) or {}
        font = str(identity.get("signature_font") or "").strip()
        if font and font != "Italiana":
            return font
    return secrets.choice(GENERATED_SIGNATURE_FONTS)


def _backfill_generated_signature_font(source: Path, target: Path, marker: Path) -> None:
    """Upgrade only the old generated default, even on customized profiles."""
    identity = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if str(identity.get("signature_font") or "").strip() not in ("", "Italiana"):
        return
    managed = marker.is_file() and marker.read_text().strip() == _hash_file(target)
    identity["signature_font"] = yaml.safe_load(source.read_text())["signature_font"]
    stat = target.stat()
    _atomic_text(target, yaml.safe_dump(identity, sort_keys=False), stat.st_mode & 0o777)
    os.chown(target, stat.st_uid, stat.st_gid)
    if managed:
        _atomic_text(marker, _hash_file(target) + "\n")


def _stage_generated_profile(item: dict, profile: Path | None = None) -> Path:
    """Materialize a generated specialist's SOUL and identity into a staging
    directory shaped exactly like a baked SPECIALISTS_ROOT/<key> entry, so
    the normal reconcile loop treats it identically."""
    key = str(item["specialist_key"])
    # Boot reconciliation runs as root; live reconciliation runs as hermes.
    # Never reuse root's staging directory from the unprivileged supervisor.
    staging_root = Path(tempfile.gettempdir()) / f"calliope-generated-specialists-{os.getuid()}"
    staging_root.mkdir(mode=0o700, exist_ok=True)
    staged = staging_root / key
    staged.mkdir(parents=True, exist_ok=True)
    (staged / "SOUL.md").write_text(
        str(item.get("soul_markdown") or "").rstrip("\n") + "\n", encoding="utf-8"
    )
    identity = {
        "name": str(item.get("display_name") or key),
        "role_label": str(item.get("role_label") or key),
        "description": str(item.get("description") or ""),
        "signature_font": _generated_signature_font(profile),
        "voice_id": ((yaml.safe_load(profile.read_text()) or {}).get("voice_id") if profile and profile.exists() else None),
        "voice_preview_text": ((yaml.safe_load(profile.read_text()) or {}).get("voice_preview_text") if profile and profile.exists() else None),
        "speech_persona": ((yaml.safe_load(profile.read_text()) or {}).get("speech_persona") if profile and profile.exists() else None),
        "description_auto": False,
    }
    if item.get("avatar_file"):
        identity["avatar_file"] = str(item["avatar_file"])
    (staged / "profile.yaml").write_text(
        yaml.safe_dump(identity, sort_keys=False),
        encoding="utf-8",
    )
    return staged


def reconcile(home: Path) -> list[str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    actions: list[str] = []
    overlays = _fetch_agent_overlays(home)
    baked_keys = {str(spec.get("key")) for spec in manifest.get("profiles", [])}
    generated_roots: dict[str, Path] = {}
    roster, roster_is_live = _fetch_generated_roster(home)
    for item in _generated_active_items(roster):
        key = str(item["specialist_key"])
        if key in baked_keys:
            continue  # baked definitions always win
        manifest.setdefault("profiles", []).append(_generated_manifest_entry(item))
        generated_roots[key] = _stage_generated_profile(item, home / "profiles" / key / "profile.yaml")
        actions.append(f"{key}:generated:staged")
    profiles_root = home / "profiles"
    profiles_root.mkdir(parents=True, exist_ok=True)
    # Archive rule: a profile directory leaves profiles/ only when the live
    # roster names its key as retired (the table knows it and no longer
    # serves it). Baked manifest keys are never archived, a directory the
    # table does not name is an operator's own and is never touched, and a
    # failed fetch (cache fallback) never archives anything — fail open.
    # Retired keys are absent from `specialists`, so neither the cache nor
    # the manifest path can re-stage an archived profile on a later boot.
    if roster_is_live:
        for key in _generated_retired_keys(roster):
            if key in baked_keys or key in generated_roots:
                continue
            if _archive_generated_profile(home, key) is not None:
                actions.append(f"{key}:generated:archived")
    default_profile_source = DEFAULT_PROFILE_SOURCE
    if default_profile_source.is_file():
        default_profile_action = _managed_copy(
            default_profile_source,
            home / "profile.yaml",
            home / ".calliope-seeds" / "profile.sha256",
        )
        actions.append(f"default:profile:{default_profile_action}")
    # Multiplex mode scopes even the default profile's credential lookups to
    # its persisted .env. Deployment secrets arrive as container environment
    # values, so materialize only the bounded runtime keys Hermes needs.
    _reconcile_env(home / ".env")
    for spec in manifest.get("profiles", []):
        key = str(spec["key"])
        profile = profiles_root / key
        markers = profile / ".calliope-managed"
        spec_root = generated_roots.get(key, SPECIALISTS_ROOT / key)
        profile_source = spec_root / "profile.yaml"
        identity = _profile_identity(profile_source, key)
        (profile / "hindsight").mkdir(parents=True, exist_ok=True)
        (profile / "skills" / "productivity").mkdir(parents=True, exist_ok=True)
        config_action = _reconcile_config(profile, _profile_config(key), markers)
        actions.append(f"{key}:config:{config_action}")
        # The default profile owns container-wide listeners. Secondary
        # profiles are reached through the default API server's /p/<profile>/
        # prefix; explicitly shadow the container-wide listener flag so
        # upstream does not reject them as competing port binders.
        _reconcile_env(profile / ".env", {"API_SERVER_ENABLED": "false"})
        profile_action = _reconcile_profile_identity(
            profile_source,
            profile / "profile.yaml",
            markers / "profile.sha256",
        )
        if key in generated_roots:
            _backfill_generated_signature_font(
                profile_source, profile / "profile.yaml", markers / "profile.sha256",
            )
        _atomic_text(
            profile / "hindsight" / "config.json",
            json.dumps({
                "mode": "local_external",
                "bank_id": spec["memory_bank"],
                "bank_mission": spec["memory_mission"],
                "recall_budget": "mid",
                "recall_types": "observation",
                "recall_prefetch_method": "recall",
                "auto_recall": True,
                "auto_retain": True,
                "retain_async": True,
                "retain_context": (
                    f"Persistent working memory for the Calliope {identity['name']} "
                    f"specialist. {identity['description']}"
                ),
                "retain_tags": ["calliope-specialist", f"profile:{key}"],
                "observation_scopes": [f"calliope-specialist:{key}"],
                "memory_mode": "hybrid",
            }, indent=2) + "\n",
        )
        soul_source = spec_root / "SOUL.md"
        soul_action = _managed_compose(
            soul_source, profile / "SOUL.md", markers / "SOUL.sha256",
            COLLABORATION_CONTRACT
            + _overlay_block(overlays.get((key, "soul", ""), [])),
        )
        for skill in spec.get("skills", []):
            source = SPECIALIST_SKILLS_ROOT / skill
            if not source.is_dir():
                source = SKILLS_ROOT / skill
            target = profile / "skills" / "productivity" / skill
            skill_action = _managed_compose(
                source, target, markers / f"skill-{skill}.sha256",
                _overlay_block(overlays.get((key, "skill", skill), [])),
            )
            actions.append(f"{key}:skill:{skill}:{skill_action}")
        cron_action = _reconcile_cron(profile, spec)
        if cron_action != "none":
            actions.append(f"{key}:cron:{cron_action}")
        for provider in _drop_shadowed_grants(home, profile):
            actions.append(f"{key}:grant:{provider}:root-owned")
        actions.append(f"{key}:profile:{profile_action}")
        actions.append(f"{key}:soul:{soul_action}")
    # Upstream's multiplex runtime and Calliope's config supervisor run as the
    # hermes account. The init hook is root, so normalize ownership after all
    # atomic replacements.
    _normalize_runtime_ownership(home, profiles_root)
    return actions


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: reconcile-hermes-specialists.py HERMES_HOME", file=sys.stderr)
        return 2
    for action in reconcile(Path(sys.argv[1])):
        print(f"[calliope] specialist {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
