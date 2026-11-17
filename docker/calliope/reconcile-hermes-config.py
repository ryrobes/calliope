#!/usr/bin/env python3
"""Reconcile hosted-model invariants into Hermes' persistent config volume."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

import yaml


# The model block is the operator's per-agent choice (Models & agents in the
# Calliope UI): which provider and which model this agent talks to. There are
# no aliases — Hermes' api_server `model_routes` never sat on Calliope's
# serving path (every request names the api_server model_name), so the
# retired alias table is dropped from any config that still carries it.
#
# "clover" in the UI is Hermes' `custom` provider bound to the appliance's
# Hutch endpoint through env references; every other provider is a native
# Hermes provider whose credentials live in the Hermes credential pool (keys
# and OAuth grants installed through Calliope's provider control), so the
# block carries no base_url/api_key for them.
CLOVER_MODEL_BINDINGS = {
    "base_url": "${env:RVBBIT_CLOVER_OPENAI_BASE_URL}",
    "api_key": "${env:RVBBIT_CLOVER_KEY}",
}
DEFAULT_MODEL_CHOICE = {"default": "openai/gpt-6.1-sol", "provider": "openrouter"}
# Hermes provider ids an agent may be pointed at. Mirrors the provider
# control surface in services/calliope/provider_settings.py (which spells the
# custom/Hutch entry "clover").
# The Claude Pro / Max subscription is the DirectSDK plugin's provider profile
# (login through the Claude Code CLI); like every native provider it carries
# no bindings — only `custom` gets the Hutch base_url/api_key.
HOSTED_MODEL_PROVIDERS = frozenset({
    "custom", "openrouter", "openai-api", "xai", "gemini", "openai-codex", "nous", "xai-oauth",
    "claude-subscription-directsdk-experimental",
})
_MODEL_CHOICE_KEYS = ("default", "provider", "base_url", "api_key")

# Hermes models auxiliary routing per task rather than through one global
# provider key. Calliope presents one quiet appliance-level choice, so it
# installs a marked first fallback on the built-in background tasks below.
# The task still tries its agent's primary model first; hand-authored fallback
# entries remain behind ours and are restored untouched when the UI setting is
# cleared. MoA slots are deliberately excluded because their provider/model is
# part of the MoA preset itself, not background-task policy.
CALLIOPE_AUXILIARY_TASKS = (
    "vision",
    "web_extract",
    "compression",
    "skills_hub",
    "approval",
    "mcp",
    "title_generation",
    "memory_query_rewrite",
    "tts_audio_tags",
    "triage_specifier",
    "kanban_decomposer",
    "profile_describer",
    "goal_judge",
    "curator",
    "monitor",
    "background_review",
)
CALLIOPE_AUXILIARY_MARKER = "calliope_managed"
DEFAULT_AUXILIARY_CHOICE = {"provider": "custom", "model": "calliope-luna"}

# Hermes' stable reasoning-effort scale (agent.reasoning_effort). Empty/unset
# means "auto": Hermes' own default (medium) or the provider's. Hermes clamps
# a requested level downward when the route supports fewer levels, so offering
# the full scale for any model is safe.
REASONING_EFFORT_LEVELS = (
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
)


def normalize_reasoning_effort(value):
    """'' (auto) or one canonical level; raises on anything else."""
    text = str(value or "").strip().lower()
    if text in ("", "auto"):
        return ""
    if text not in REASONING_EFFORT_LEVELS:
        raise ValueError(
            "reasoning_effort must be one of: " + ", ".join(REASONING_EFFORT_LEVELS)
        )
    return text


def apply_reasoning_effort(payload, reasoning_effort):
    """Set or clear agent.reasoning_effort in an agent's config document.
    None leaves the document untouched; '' clears back to auto."""
    if reasoning_effort is None:
        return
    level = normalize_reasoning_effort(reasoning_effort)
    agent = payload.get("agent")
    if not isinstance(agent, dict):
        if not level:
            return
        agent = {}
        payload["agent"] = agent
    if level:
        agent["reasoning_effort"] = level
    else:
        agent.pop("reasoning_effort", None)
        if not agent:
            payload.pop("agent", None)


def carry_reasoning_effort(current, desired):
    """Keep an operator-set reasoning effort across image reconciles (it lives
    in the top-level agent block, so the model-block carry does not cover it).
    Invalid values are dropped rather than carried."""
    agent = current.get("agent") if isinstance(current, dict) else None
    value = agent.get("reasoning_effort") if isinstance(agent, dict) else None
    try:
        level = normalize_reasoning_effort(value)
    except ValueError:
        level = ""
    apply_reasoning_effort(desired, level)
# The working budget the compression ratios hang off. Measured 2026-08-17 on
# Measured on a hosted install: a Calliope turn is ~86k prompt tokens before any conversation
# (system prompt, visible tools, per-turn instructions, memory recall), so the
# earlier 131k budget (0.7 -> 92k threshold) compacted on the first tool
# result of nearly every turn, then reported "ineffective" and ran at
# 100-150k anyway. 262k keeps ~95k of real headroom over that base; the
# Hutch `calliope` route (Grok 4.6 / GPT-5.6 class) accepts far more.
# Override per box with CALLIOPE_MODEL_CONTEXT_LENGTH. Budgets follow the
# CONTEXT_ECONOMY_DEFAULTS rule (see _model_budget_defaults): 131072 was the
# first appliance budget and is moved forward; an operator's own budget (say
# for a 128k model chosen for one specialist) is left alone.
DEFAULT_MODEL_CONTEXT_LENGTH = 262_144
# Per-response output cap. A dashboard write is 30-45k chars of HTML (10-14k
# tokens); at 8192 every such write was cut mid tool-call and re-emitted whole
# on the next call (Gemini 3.7 Flash, 2026-08-18: 4 cuts, ~90k tokens wasted).
# 32768 covers a large document; the model's own output limit caps it below.
DEFAULT_MODEL_MAX_TOKENS = 32_768
MODEL_MAX_TOKENS_FLOOR = 4_096
MEMORY_INVARIANTS = {
    "memory_enabled": True,
    "provider": "hindsight",
}
# Context-economy defaults for a conversational appliance. Hermes ships coding-
# agent defaults (compact at 85% of the window, 50 KB tool outputs); on the
# pilot that meant ~100k cached tokens re-read per API step in long chat
# threads, 93% of it tool results. The first appliance pass (threshold 0.5)
# compacted every ~2 user turns and still ran ~97k per call because a build
# turn outgrows the headroom and the anti-thrash guard then suspends
# compaction; see docker/calliope/hermes-config.yaml for the measurements and
# the rationale behind the proactive prune + 70% backstop below.
# Upstream defaults are treated as "unset" so an operator's own value survives
# while a fresh volume gets ours.
# (values treated as "unset", our value). Earlier appliance defaults are listed
# so a box that received them is moved forward; anything else is the operator's.
# Pinned tool working set for the default agent (2026-08-21 describe audit:
# ~30% of all tool executions were tool_describe round-trips re-fetching the
# same core verbs — run_sql_multi alone was described 182 times in two days,
# because deferred schemas reset with every session). Pinned schemas stay in
# the model-facing array, joining the stable cacheable prompt prefix; the
# deferred catalog still lists them, so tool_search discovery stays complete
# and the long tail keeps flowing through capability_search. Move-forward
# default: a missing/empty list gets ours, an operator's own list survives.
PINNED_TOOLS_DEFAULT = (
    "mcp__Warehouse__run_sql",
    "mcp__Warehouse__run_sql_multi",
    "mcp__Warehouse__recall_calliope_sql",
    "mcp__Warehouse__read_calliope_specialist_result",
    "mcp__Warehouse__search_data",
    # 2026-09-19: find = search_data + lineage/freshness/usage/meaning envelope
    # and the literal-values seam (docs/FIND_PLAN.md). Pinned beside search_data
    # so the coordinator can reach it on the first try.
    "mcp__Warehouse__find",
    "mcp__Warehouse__capability_search",
    "mcp__Warehouse__calliope_work_item",
    "mcp__Warehouse__calliope_observation_context",
    "mcp__Warehouse__calliope_decision_request",
    "mcp__Warehouse__propose_calliope_computer_action",
    # Read-only eyes on the thread's Desktop Lab workbench (2026-09-11). Pinned
    # for the same reason as propose_*: a tool the coordinator must be able to
    # call on the first try cannot live behind the deferred bridge.
    "mcp__Warehouse__look_at_desktop",
    "mcp__Warehouse__read_calliope_sketch",
    "mcp__Warehouse__update_calliope_sketch",
    "mcp__Warehouse__get_live_app",
    "mcp__Warehouse__update_live_app",
    "mcp__Warehouse__capture_live_app",
    "mcp__Warehouse__list_calliope_specialists",
    "mcp__Roundtable__delegate_calliope_specialist",
    "mcp__Roundtable__continue_calliope_specialist",
    "mcp__Roundtable__await_calliope_specialists",
    # Native Hermes image tool — the Calliope image lane calls it by name;
    # agents that can't see it improvise (2026-09-02: one drove the
    # operator's logged-in grok.com through the browser toolset).
    "image_generate",
    # 2026-09-10 pilot histogram of the deferred tool_call wrapper: the metric
    # verbs and describe_table were the head of the discovery tax
    # (get_metric ×155, list_metrics ×83, describe_table ×85, metric ×43,
    # search_business_context ×57 in one day for the coordinator alone).
    "mcp__Warehouse__get_metric",
    "mcp__Warehouse__list_metrics",
    "mcp__Warehouse__metric",
    "mcp__Warehouse__metric_history",
    "mcp__Warehouse__describe_table",
    "mcp__Warehouse__search_business_context",
    # 2026-09-21: the write-back verbs. Every docstring says "only what a person
    # told you", and the rules text tells her to bank taught facts; but the
    # three tools were reachable only through discovery, so they were almost
    # never used. Pinned, teaching lands in canon instead of evaporating.
    "mcp__Warehouse__teach_meaning",
    "mcp__Warehouse__bind_calliope_term",
    "mcp__Warehouse__curate_calliope_memory",
    # 2026-09-22 pilot histogram (14 days, 76k tool calls): the coordinator lane
    # reached these through the deferred bridge in the most SESSIONS —
    # mirror_status 142, begin/finish_calliope_workflow_run 148/141 (every
    # scheduled run pays a describe + a bridge for each), administer_local_sql
    # 140, list_live_apps 48, list_dashboards 42, read_calliope_playbook 41,
    # ask_brain 34, request_calliope_input 33, brain_facets 32,
    # search_calliope_actions 25, describe_cube 19, upload_artifact 19.
    # 19% of all tool calls were bridge indirections; this list is the cure
    # the numbers wrote. Cap raised to 48 in hermes-pinned-tools.patch.
    "mcp__Warehouse__administer_local_sql",
    "mcp__Warehouse__mirror_status",
    "mcp__Warehouse__begin_calliope_workflow_run",
    "mcp__Warehouse__finish_calliope_workflow_run",
    "mcp__Warehouse__ask_brain",
    "mcp__Warehouse__brain_facets",
    "mcp__Warehouse__list_live_apps",
    "mcp__Warehouse__list_dashboards",
    "mcp__Warehouse__read_calliope_playbook",
    "mcp__Warehouse__request_calliope_input",
    "mcp__Warehouse__search_calliope_actions",
    "mcp__Warehouse__describe_cube",
    "mcp__Warehouse__upload_artifact",
    # The Meaning tree is the coordinator's reading of the company's data as
    # nouns; hand-pinned on the pilot on 2026-09-20 (40 calls in 9 sessions
    # since), now standard so a fresh install and the pilot agree.
    "mcp__Warehouse__meaning_tree",
)
_PINNED_TOOLS_2026_09_21_TEACH_VERBS = (
    "mcp__Warehouse__teach_meaning",
    "mcp__Warehouse__bind_calliope_term",
    "mcp__Warehouse__curate_calliope_memory",
)
_PINNED_TOOLS_2026_09_10_METRIC_VERBS = (
    "mcp__Warehouse__get_metric",
    "mcp__Warehouse__list_metrics",
    "mcp__Warehouse__metric",
    "mcp__Warehouse__metric_history",
    "mcp__Warehouse__describe_table",
    "mcp__Warehouse__search_business_context",
)
_PINNED_TOOLS_2026_09_22_BRIDGE_TAX = (
    "mcp__Warehouse__administer_local_sql",
    "mcp__Warehouse__mirror_status",
    "mcp__Warehouse__begin_calliope_workflow_run",
    "mcp__Warehouse__finish_calliope_workflow_run",
    "mcp__Warehouse__ask_brain",
    "mcp__Warehouse__brain_facets",
    "mcp__Warehouse__list_live_apps",
    "mcp__Warehouse__list_dashboards",
    "mcp__Warehouse__read_calliope_playbook",
    "mcp__Warehouse__request_calliope_input",
    "mcp__Warehouse__search_calliope_actions",
    "mcp__Warehouse__describe_cube",
    "mcp__Warehouse__upload_artifact",
)
_PINNED_TOOLS_BEFORE_MEANING_TREE = tuple(
    name for name in PINNED_TOOLS_DEFAULT if name != "mcp__Warehouse__meaning_tree"
)
_PINNED_TOOLS_BEFORE_BRIDGE_TAX = tuple(
    name for name in _PINNED_TOOLS_BEFORE_MEANING_TREE if name not in _PINNED_TOOLS_2026_09_22_BRIDGE_TAX
)
_PINNED_TOOLS_BEFORE_TEACH_VERBS = tuple(
    name for name in _PINNED_TOOLS_BEFORE_BRIDGE_TAX if name not in _PINNED_TOOLS_2026_09_21_TEACH_VERBS
)
_PINNED_TOOLS_BEFORE_FIND = tuple(
    name for name in _PINNED_TOOLS_BEFORE_TEACH_VERBS if name != "mcp__Warehouse__find"
)
_PINNED_TOOLS_BEFORE_LOOK_AT_DESKTOP = tuple(
    name for name in _PINNED_TOOLS_BEFORE_FIND if name != "mcp__Warehouse__look_at_desktop"
)
_PINNED_TOOLS_BEFORE_METRIC_VERBS = tuple(
    name for name in _PINNED_TOOLS_BEFORE_LOOK_AT_DESKTOP
    if name not in _PINNED_TOOLS_2026_09_10_METRIC_VERBS
)
_PINNED_TOOLS_BEFORE_LIVE_ROSTER = tuple(
    name for name in _PINNED_TOOLS_BEFORE_METRIC_VERBS
    if name != "mcp__Warehouse__list_calliope_specialists"
)
PINNED_TOOLS_PREVIOUS_DEFAULTS = (
    _PINNED_TOOLS_BEFORE_MEANING_TREE,
    _PINNED_TOOLS_BEFORE_BRIDGE_TAX,
    _PINNED_TOOLS_BEFORE_TEACH_VERBS,
    _PINNED_TOOLS_BEFORE_FIND,
    _PINNED_TOOLS_BEFORE_LOOK_AT_DESKTOP,
    _PINNED_TOOLS_BEFORE_METRIC_VERBS,
    _PINNED_TOOLS_BEFORE_LIVE_ROSTER,
    tuple(name for name in _PINNED_TOOLS_BEFORE_LIVE_ROSTER if name != "image_generate"),
    tuple(
        name for name in _PINNED_TOOLS_BEFORE_LIVE_ROSTER
        if name not in {
            "mcp__Warehouse__propose_calliope_computer_action",
            "image_generate",
        }
    ),
    tuple(
        name for name in _PINNED_TOOLS_BEFORE_LIVE_ROSTER
        if name not in {
            "mcp__Warehouse__calliope_observation_context",
            "mcp__Warehouse__calliope_decision_request",
            "mcp__Warehouse__propose_calliope_computer_action",
            "image_generate",
        }
    ),
)


# Preserve the historical tuples above so installed defaults remain recognizable.
PINNED_TOOLS_PREVIOUS_DEFAULTS = (*PINNED_TOOLS_PREVIOUS_DEFAULTS, PINNED_TOOLS_DEFAULT)
PINNED_TOOLS_DEFAULT = tuple(
    replacement
    for name in PINNED_TOOLS_DEFAULT
    for replacement in (
        ("mcp__Warehouse__open_desktop", "mcp__Warehouse__desktop_exec", "mcp__Warehouse__finish_desktop")
        if name == "mcp__Warehouse__propose_calliope_computer_action" else (name,)
    )
)

# Recognize every previously shipped default without the two recovery tools,
# while still preserving genuinely operator-customized pin lists.
_RESULT_RECOVERY_PINS = {
    "mcp__Warehouse__recall_calliope_sql", "mcp__Warehouse__read_calliope_specialist_result",
}
PINNED_TOOLS_PREVIOUS_DEFAULTS = (*PINNED_TOOLS_PREVIOUS_DEFAULTS, *(
    tuple(name for name in previous if name not in _RESULT_RECOVERY_PINS)
    for previous in (*PINNED_TOOLS_PREVIOUS_DEFAULTS, PINNED_TOOLS_DEFAULT)
))


def _auth_store_provider_names() -> set[str]:
    """Provider names in the Hermes auth store, across both schemas: the
    original flat mapping keyed by provider, and the current versioned shape
    where credentials live under a ``providers`` dict."""
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    try:
        with open(os.path.join(home, "auth.json"), encoding="utf-8") as handle:
            store = json.load(handle) or {}
    except Exception:
        return set()
    if not isinstance(store, dict):
        return set()
    names = set(store.keys())
    providers = store.get("providers")
    if isinstance(providers, dict):
        names |= set(providers.keys())
    return names


def _auth_store_has_xai_oauth() -> bool:
    """True when the Hermes auth store holds an xAI OAuth credential."""
    return "xai-oauth" in _auth_store_provider_names()


def _has_openrouter_key() -> bool:
    """True when Hermes can reach OpenRouter: API-key providers ride the
    container environment (only OAuth credentials live in auth.json), so the
    env var is the primary signal; the store name covers any future move."""
    if (os.environ.get("OPENROUTER_API_KEY") or "").strip():
        return True
    # The startup reconciler runs before Hermes loads its persistent .env.
    # Inspect that same store without copying credentials into config or the
    # reconciler's process environment. Disable interpolation when reading it.
    from dotenv import dotenv_values

    home = Path(os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"))
    native_env = dotenv_values(home / ".env", interpolate=False)
    if str(native_env.get("OPENROUTER_API_KEY") or "").strip():
        return True
    return "openrouter" in _auth_store_provider_names()

CONTEXT_ECONOMY_DEFAULTS = {
    "compression": {
        "threshold": ((0.85, 0.45, 0.5), 0.7),
        "target_ratio": ((None, 0.2), 0.25),
        # Recent tail kept verbatim through compaction AND the prune. 30 was set
        # when every Calliope turn ran with an empty history (nothing to protect
        # across turns); once /v1/runs loaded the session again (2026-08-18) a
        # 27-message session could not compact at all — 30 protected everything
        # — and turn 2 opened at 368k tokens. 12 keeps the working set of one
        # build step and lets research payloads be summarised behind it.
        "protect_last_n": ((None, 20, 30), 12),
        # Deterministic prune of old, large tool results (upstream default 0 =
        # off): no LLM call, protects the last protect_last_n messages, commits
        # only when it reclaims min_reclaim tokens so cache breaks stay rare.
        # Measured 2026-08-17: a Calliope turn's fixed base (system prompt,
        # visible tools, per-turn instructions, memory recall) is ~86k prompt
        # tokens before any conversation, so a 60k prune line was permanently
        # crossed; 120k sits above the base with room for one working set.
        "proactive_prune_tokens": ((None, 0, 60_000), 120_000),
        "proactive_prune_min_result_chars": ((None, 8_000), 6_000),
        "proactive_prune_min_reclaim_tokens": ((None, 4_096), 8_192),
        # A thread resumed after an hour idle compacts up front (upstream 0 =
        # off) rather than mid-turn; the 240-minute idle reset still follows.
        "idle_compact_after_seconds": ((None, 0), 3_600),
    },
    # Caps terminal / read_file output only (MCP results are never truncated
    # by Hermes). A dashboard HTML read must survive intact (a truncated read
    # is a broken patch), so this stays at upstream's 50 KB; the earlier
    # appliance values are moved back up.
    "tool_output": {"max_bytes": ((24_000, 40_000), 50_000)},
    # A Google Chat thread picked up next morning starts a fresh session; memory
    # (Hindsight + MEMORY.md) carries continuity, the transcript need not.
    "session_reset": {"mode": (("none",), "idle"), "idle_minutes": ((1440,), 240)},
    # Hermes' background skill curator forks the agent after a turn once
    # `creation_nudge_interval` tool ITERATIONS have passed (upstream 10) and
    # replays the whole uncompacted history to ask "should a skill be saved?".
    # A dashboard turn is 10-14 iterations, so on the appliance it fired after
    # nearly every build turn at 215-285k prompt tokens each — 2-3x the turn
    # itself — and could not even write (Calliope's skills are user-owned).
    # 0 disables that trigger; skills are curated on purpose (RVBBIT capability
    # search, `skill_manage` from an explicit review job) rather than by
    # reflex. Memory review (memory.nudge_interval, per user turn) is untouched.
    "skills": {"creation_nudge_interval": ((None, 10), 0)},
}
_FALLBACK_PROFILE_ALLOWLIST = [
    "dashboard-studio",
    "warehouse-steward",
    "semantic-architect",
    "signal-watch",
    "knowledge-gardener",
    "analytical-skeptic",
]


def _manifest_profile_allowlist():
    """The multiplex allowlist derives from the specialists manifest, so a
    newly minted profile (e.g. a domain expert) is servable the moment it
    ships — a profile on disk but off this list 404s as "unconfigured".
    Factory-generated specialists join from the reconciler's durable cache
    (the reconciler writes it before the gateway validates its config).
    Falls back to the static list when the manifest is unreadable."""
    import json as _json
    import os as _os
    from pathlib import Path as _Path
    keys = []
    here = _Path(__file__).resolve().parent
    for candidate in (
        here / "specialists" / "manifest.json",          # image layout
        here / "hermes-specialists" / "manifest.json",   # repo layout
    ):
        try:
            manifest = _json.loads(candidate.read_text(encoding="utf-8"))
            keys = [
                str(profile["key"])
                for profile in manifest.get("profiles", [])
                if profile.get("key")
            ]
            if keys:
                break
        except Exception:  # noqa: BLE001 — fall through to the next candidate
            continue
    if not keys:
        keys = list(_FALLBACK_PROFILE_ALLOWLIST)
    generated_cache = _Path(
        _os.environ.get("HERMES_HOME", "/opt/data")
    ) / ".calliope-seeds" / "generated-specialists.json"
    try:
        generated = _json.loads(generated_cache.read_text(encoding="utf-8"))
        for item in generated.get("specialists", []):
            key = str((item or {}).get("specialist_key") or "")
            if key and key not in keys:
                keys.append(key)
    except Exception:  # noqa: BLE001 — no generated specialists yet
        pass
    return keys


def gateway_invariants():
    """Return gateway invariants using the current generated-profile cache.

    The supervisor imports this policy before first-boot reconciliation has
    necessarily populated the durable generated-specialists cache.  Keeping
    the allowlist in a module-level snapshot made every later validation in
    that supervisor process compare against the six built-in profiles even
    after the reconciler had installed generated specialists into config.yaml.
    """
    return {
        "multiplex_profiles": True,
        "multiplex_profile_allowlist": _manifest_profile_allowlist(),
        # Hermes 0.21.4 starts every multiplexed profile on the event loop (delegation
        # ledgers, durability barriers, config loads); with ~23 profiles that blocks the
        # loop for 2-2.5 minutes on every boot, and upstream's liveness watchdog (30 s
        # probes, 3 strikes ≈ 2 min) then hard-exits 75 and the boot loops. Upstream's
        # own guidance for slow loops is to tune gateway.loop_watchdog_* in config.yaml;
        # 12 strikes ≈ 7 minutes leaves genuine wedges still caught.
        "loop_watchdog_max_strikes": 12,
    }


# Compatibility snapshot for callers that display the shipping invariants.
# Validation and reconciliation deliberately use gateway_invariants() so a
# long-running supervisor observes specialists minted after module import.
GATEWAY_INVARIANTS = gateway_invariants()
API_SERVER_INVARIANTS = {
    "enabled": True,
}
API_SERVER_EXTRA_INVARIANTS = {
    "host": "0.0.0.0",
    "port": 8642,
}
# Composed from the hermes-api-server preset MINUS the toolsets that are
# credential-gated dead ends on the appliance (2026-08-21: sol burned a call
# on the unconfigured Firecrawl web_search and 472 s downloading Playwright).
# `web` is deliberately absent: the governed web search lives in the SQL
# dialect (clover) and is discovered through capability_search — one catalog,
# receipted like everything else. The BFL video generators fall away with the
# preset for the same keyless-trap reason. Native delegation is also excluded
# from the web coordinator: top-level Hermes children force background work
# outside Calliope's turn lifecycle. Use the durable Roundtable tools.
# Specialist profiles retain their independently configured delegation toolset.
API_SERVER_TOOLSET_INVARIANTS = [
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
    "cronjob",
    "homeassistant",
    "Warehouse",
    "Roundtable",
]
WAREHOUSE_MCP_INVARIANTS = {
    "Warehouse": {
        "url": "http://calliope:8765/mcp",
        "headers": {
            "Authorization": "Bearer ${env:WAREHOUSE_HERMES_MCP_KEY}"
        },
        "forward_session_identity": True,
        "supports_parallel_tool_calls": True,
        "timeout": 90,
        # Local SQL has a 120s default / 600s maximum execution budget. Keep
        # the caller alive through rollback and the durable completion receipt.
        "tool_timeouts": {"generate_image": 660, "run_workspace_sql": 660, "administer_local_sql": 660},
        "tools": {
            # Anything that blocks on a specialist run lives on the long lane:
            # a single delegation waits for its child (Iris takes minutes to
            # build a dashboard) and hit the 90 s transport ceiling here on
            # 2026-08-17 — Hermes reported a timeout, Calliope rebuilt the
            # dashboard herself, and the specialist's result was orphaned.
            "exclude": ["delegate_all_calliope_specialists", "delegate_calliope_specialist",
                        "continue_calliope_specialist", "await_calliope_specialists",
                        "ask_calliope"],
            "prompts": False,
            "resources": False,
        },
    },
    "Roundtable": {
        "url": "http://calliope:8765/mcp?lane=roundtable",
        "headers": {
            "Authorization": "Bearer ${env:WAREHOUSE_HERMES_MCP_KEY}"
        },
        "forward_session_identity": True,
        "supports_parallel_tool_calls": True,
        "timeout": 2_100,
        "tool_timeouts": {"await_calliope_specialists": 360},
        "tools": {
            "include": ["delegate_all_calliope_specialists", "delegate_calliope_specialist",
                        "continue_calliope_specialist", "await_calliope_specialists"],
            "prompts": False,
            "resources": False,
        },
    },
    "Specialist": {
        "url": "http://calliope:8765/mcp?lane=specialist",
        "headers": {
            "Authorization": "Bearer ${env:WAREHOUSE_HERMES_MCP_KEY}"
        },
        "forward_session_identity": True,
        "supports_parallel_tool_calls": True,
        "timeout": 90,
        "tool_timeouts": {"generate_image": 660, "run_workspace_sql": 660, "administer_local_sql": 660},
    },
}
_SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:token|secret|password|api_key|private_key)$", re.IGNORECASE
)
_ENV_REFERENCE_RE = re.compile(r"^\$\{env:[A-Za-z_][A-Za-z0-9_]*\}$")
_CREDENTIAL_REFERENCE_RE = re.compile(
    r"^(?:credential|rvbbit-credential)://[A-Za-z0-9][A-Za-z0-9_.:/@-]+$"
)
_AUTHORIZATION_REFERENCE_RE = re.compile(
    r"^(?:Bearer\s+)?(?:\$\{env:[A-Za-z_][A-Za-z0-9_]*\}|"
    r"(?:credential|rvbbit-credential)://[A-Za-z0-9][A-Za-z0-9_.:/@-]+)$",
    re.IGNORECASE,
)
_MODEL_NAME_RE = re.compile(r"^[^\s\x00-\x1f]{1,256}$")


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return value if value > 0 else default


def _model_budget_defaults() -> dict[str, tuple[tuple[Any, ...], int]]:
    """(values treated as unset, ours) for the model working budgets."""
    return {
        "context_length": ((None, 131_072), _positive_int("CALLIOPE_MODEL_CONTEXT_LENGTH", DEFAULT_MODEL_CONTEXT_LENGTH)),
        "max_tokens": ((None, 8_192), _positive_int("CALLIOPE_MODEL_MAX_TOKENS", DEFAULT_MODEL_MAX_TOKENS)),
    }


def model_block_errors(model: Any, path: str = "model") -> list[dict[str, str]]:
    """Validate one agent's model block (default profile or a specialist).

    Operator-chosen: `default` (the model id) and `provider`. Bound by policy:
    a `custom` provider is the appliance's Hutch endpoint and must carry the
    env-reference bindings, every native provider carries none. Budgets must
    be positive integers; their values are the operator's."""
    if not isinstance(model, dict):
        return [{"path": path, "message": "required mapping is missing"}]
    errors: list[dict[str, str]] = []
    name = model.get("default")
    if not isinstance(name, str) or not _MODEL_NAME_RE.fullmatch(name.strip()) or not name.strip():
        errors.append({"path": f"{path}.default", "message": "model must be a non-empty string of at most 256 characters without whitespace or control characters"})
    provider = model.get("provider")
    if not isinstance(provider, str) or provider.strip().lower() not in HOSTED_MODEL_PROVIDERS:
        errors.append({
            "path": f"{path}.provider",
            "message": "provider must be one of the hosted Calliope providers",
            "expected": repr(sorted(HOSTED_MODEL_PROVIDERS)),
            "actual": repr(provider),
        })
    elif provider.strip().lower() == "custom":
        errors.extend(_invariant_errors(model, CLOVER_MODEL_BINDINGS, path))
    else:
        for key in CLOVER_MODEL_BINDINGS:
            if model.get(key) not in (None, ""):
                errors.append({
                    "path": f"{path}.{key}",
                    "message": "native Hermes providers take their endpoint and credentials from the Hermes credential pool; remove this key",
                })
    for key in ("context_length", "max_tokens"):
        value = model.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.append({"path": f"{path}.{key}", "message": "must be a positive integer"})
    return errors


# The working budget never exceeds the appliance ceiling; a model with a
# smaller window than the ceiling gets its own window (Hermes' compaction
# threshold is a ratio of this number, so it must be real for the model).
MODEL_CONTEXT_FLOOR = 32_768


def working_context_length(model_window: Any, ceiling: int | None = None) -> int | None:
    """min(appliance budget, the model's advertised window), or None when the
    window is unknown (leave the block's current budget alone)."""
    ceiling = int(ceiling or _positive_int("CALLIOPE_MODEL_CONTEXT_LENGTH", DEFAULT_MODEL_CONTEXT_LENGTH))
    try:
        window = int(model_window)
    except (TypeError, ValueError):
        return None
    if window < MODEL_CONTEXT_FLOOR:
        return None
    return min(ceiling, window)


def working_max_tokens(model_output_limit: Any, ceiling: int | None = None) -> int | None:
    """min(appliance output cap, the model's advertised output limit), or None
    when the limit is unknown (leave the block's current cap alone)."""
    ceiling = int(ceiling or _positive_int("CALLIOPE_MODEL_MAX_TOKENS", DEFAULT_MODEL_MAX_TOKENS))
    try:
        limit = int(model_output_limit)
    except (TypeError, ValueError):
        return None
    if limit < MODEL_MAX_TOKENS_FLOOR:
        return None
    return min(ceiling, limit)


def apply_model_choice(
    payload: dict[str, Any], provider: str, model_name: str, context_length: Any = None,
    max_tokens: Any = None, reasoning_effort: Any = None,
) -> dict[str, Any]:
    """Point an agent's config at provider/model in place; returns the block.

    Everything else in the model block (budgets, api_mode, …) is preserved
    unless `context_length` is given (the caller resolved the model's window
    through `working_context_length`). `custom` gets the Hutch bindings,
    native providers lose any bindings."""
    if not isinstance(payload, dict):
        raise ValueError("Hermes config must be a mapping")
    provider = str(provider or "").strip().lower()
    model_name = str(model_name or "").strip()
    if provider not in HOSTED_MODEL_PROVIDERS:
        raise ValueError(f"unsupported model provider: {provider or '<empty>'}")
    if not _MODEL_NAME_RE.fullmatch(model_name):
        raise ValueError("model must be a non-empty string of at most 256 characters")
    block = payload.get("model")
    if not isinstance(block, dict):
        block = {}
        payload["model"] = block
    block["default"] = model_name
    block["provider"] = provider
    if context_length is not None:
        try:
            budget = int(context_length)
        except (TypeError, ValueError):
            raise ValueError("context_length must be a positive integer") from None
        if budget < MODEL_CONTEXT_FLOOR:
            raise ValueError(f"context_length must be at least {MODEL_CONTEXT_FLOOR}")
        block["context_length"] = budget
    if max_tokens is not None:
        try:
            cap = int(max_tokens)
        except (TypeError, ValueError):
            raise ValueError("max_tokens must be a positive integer") from None
        if cap < MODEL_MAX_TOKENS_FLOOR:
            raise ValueError(f"max_tokens must be at least {MODEL_MAX_TOKENS_FLOOR}")
        block["max_tokens"] = cap
    if provider == "custom":
        block.update(CLOVER_MODEL_BINDINGS)
    else:
        for key in CLOVER_MODEL_BINDINGS:
            block.pop(key, None)
    # Hermes' session API turns the advertised API-server model into a
    # request-level override.  It therefore has to be the provider-routable
    # model id, not a durable Calliope alias.  Keep it synchronized here as
    # well as in reconcile_payload so direct model-choice helpers are safe.
    platforms = payload.get("platforms")
    if isinstance(platforms, dict):
        api_server = platforms.get("api_server")
        if isinstance(api_server, dict):
            extra = api_server.get("extra")
            if isinstance(extra, dict):
                extra["model_name"] = model_name
    apply_reasoning_effort(payload, reasoning_effort)
    return block


def api_server_extra_invariants(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return static API-server invariants plus its live model contract.

    ``model_name`` is not an alias: Hermes applies it to session/chat calls as
    the requested provider model.  Tying it to ``model.default`` lets Dreams,
    topology judgment, and other headless sessions use exactly the model the
    operator selected for Calliope.
    """
    model = payload.get("model") if isinstance(payload, Mapping) else None
    model_name = str(model.get("default") or "").strip() if isinstance(model, Mapping) else ""
    if not model_name:
        model_name = DEFAULT_MODEL_CHOICE["default"]
    return {**API_SERVER_EXTRA_INVARIANTS, "model_name": model_name}


def reconcile_model_block(payload: dict[str, Any]) -> bool:
    """Seed / normalise the model block without overriding the operator's
    provider+model choice. Returns True when anything changed."""
    model = payload.setdefault("model", {})
    if not isinstance(model, dict):
        raise ValueError("Hermes model config must be a mapping")
    changed = False
    provider = model.get("provider")
    provider = provider.strip().lower() if isinstance(provider, str) else ""
    name = model.get("default")
    if provider not in HOSTED_MODEL_PROVIDERS or not isinstance(name, str) or not name.strip():
        # Fresh volume, or a hand-edited block the appliance cannot serve:
        # seed the release default when no valid provider/model is configured.
        for key, value in DEFAULT_MODEL_CHOICE.items():
            if model.get(key) != value:
                model[key] = value
                changed = True
        provider = DEFAULT_MODEL_CHOICE["provider"]
    if provider == "custom":
        for key, value in CLOVER_MODEL_BINDINGS.items():
            if model.get(key) != value:
                model[key] = value
                changed = True
    else:
        for key in CLOVER_MODEL_BINDINGS:
            if key in model:
                model.pop(key, None)
                changed = True
    for key, (replaceable, ours) in _model_budget_defaults().items():
        current = model.get(key)
        if current is None or current in replaceable or not isinstance(current, int) or isinstance(current, bool) or current <= 0:
            if current != ours:
                model[key] = ours
                changed = True
    return changed


def fallback_model_errors(fallback: Any) -> list[dict[str, str]]:
    """Validate a single global fallback entry (we do not manage chains)."""
    if fallback is None:
        return []
    if not isinstance(fallback, dict):
        return [{"path": "fallback_model", "message": "fallback_model must be a mapping"}]
    provider = str(fallback.get("provider") or "")
    model = str(fallback.get("model") or "")
    errors = []
    if provider not in HOSTED_MODEL_PROVIDERS or provider == "custom":
        errors.append({"path": "fallback_model.provider", "message": f"unsupported fallback provider: {provider!r}"})
    if not model:
        errors.append({"path": "fallback_model.model", "message": "fallback_model needs a model"})
    return errors


def apply_fallback_model(payload: dict[str, Any], provider: Any, model: Any) -> bool:
    """Set or clear the appliance-wide fallback entry on one config payload.
    Hermes reads top-level ``fallback_model`` ({provider, model}) when the
    primary cannot produce a client at init or exhausts at runtime."""
    if provider in (None, "") and model in (None, ""):
        return payload.pop("fallback_model", None) is not None
    entry = {"provider": str(provider or "").strip().lower(), "model": str(model or "").strip()}
    errors = fallback_model_errors(entry)
    if errors:
        raise ValueError(errors[0]["message"])
    changed = payload.get("fallback_model") != entry
    payload["fallback_model"] = entry
    return changed


def carry_fallback_model(current: Any, desired: dict[str, Any]) -> None:
    """Keep an operator-set fallback across image reconciles (top-level key,
    so the model-block carry does not cover it). Invalid entries are dropped
    rather than carried."""
    fallback = current.get("fallback_model") if isinstance(current, dict) else None
    if isinstance(fallback, dict) and not fallback_model_errors(fallback):
        desired["fallback_model"] = dict(fallback)
    else:
        desired.pop("fallback_model", None)


def apply_auxiliary_settings(payload: dict[str, Any], provider: Any, model: Any) -> bool:
    """Set or clear Calliope's provider/model fallback for background tasks.

    Hermes owns auxiliary routing per task. Each managed task remains in
    ``provider: auto`` mode (therefore using the agent's own model first), with
    Calliope's selected provider/model prepended to its ``fallback_chain``.
    Only entries carrying our marker are replaced or removed; manually authored
    fallback entries and every other task option survive. The former
    OpenRouter-only operator keys are retired when this control is used."""
    provider = str(provider or "").strip().lower()
    model = str(model or "").strip()
    clearing = not provider and not model
    if bool(provider) != bool(model):
        raise ValueError("auxiliary provider and model must be set together")
    if not clearing and provider not in HOSTED_MODEL_PROVIDERS:
        raise ValueError("auxiliary provider is unsupported")
    if model and not _MODEL_NAME_RE.fullmatch(model):
        raise ValueError("auxiliary model id is invalid")

    before = payload.get("auxiliary") if isinstance(payload.get("auxiliary"), dict) else None
    # YAML configuration is JSON-shaped; a round-trip gives us a stable deep
    # snapshot for idempotence without sharing nested task dictionaries.
    aux = json.loads(json.dumps(before or {}))
    managed_entry = None
    if not clearing:
        managed_entry = {
            "provider": provider,
            "model": model,
            CALLIOPE_AUXILIARY_MARKER: True,
        }
        if provider == "custom":
            managed_entry.update(CLOVER_MODEL_BINDINGS)

    for task in CALLIOPE_AUXILIARY_TASKS:
        current = aux.get(task)
        task_config = dict(current) if isinstance(current, dict) else {}
        raw_chain = task_config.get("fallback_chain")
        chain = [
            entry for entry in (raw_chain if isinstance(raw_chain, list) else [])
            if not (isinstance(entry, dict) and entry.get(CALLIOPE_AUXILIARY_MARKER) is True)
        ]
        if managed_entry is not None:
            chain.insert(0, dict(managed_entry))
        if chain:
            task_config["fallback_chain"] = chain
        else:
            task_config.pop("fallback_chain", None)
        if task_config:
            aux[task] = task_config
        else:
            aux.pop(task, None)

    # Retire the old UI's OpenRouter-only fallback override. Hermes retains its
    # own automatic OpenRouter/default discovery after the managed chain.
    aux.pop("openrouter_model", None)
    aux.pop("free_only", None)
    # An empty section records an explicit "Use automatic" choice. Absence
    # means the appliance has never seeded its background-task fallback.
    payload["auxiliary"] = aux
    return aux != before


def apply_managed_auxiliary_defaults(payload: dict[str, Any]) -> bool:
    """Seed the Hutch fallback once; preserve every explicit auxiliary choice.

    No per-task provider/model is set: Hermes still tries the agent's main
    model first. An existing section, including {}, belongs to the operator.
    """
    if isinstance(payload.get("auxiliary"), dict):
        return False
    if payload.get("auxiliary") is not None:
        raise ValueError("Hermes auxiliary config must be a mapping")
    return apply_auxiliary_settings(payload, **DEFAULT_AUXILIARY_CHOICE)


def carry_auxiliary_settings(current: Any, desired: dict[str, Any]) -> None:
    """Keep an operator's auxiliary block (background-task routing: the
    managed fallback lane, legacy OpenRouter keys, and per-task overrides)
    across image reconciles — a top-level section the model-block carry does
    not cover."""
    aux = current.get("auxiliary") if isinstance(current, dict) else None
    if isinstance(aux, dict):
        desired["auxiliary"] = json.loads(json.dumps(aux))


def carry_model_choice(source: Any, target: dict[str, Any]) -> dict[str, Any]:
    """Copy the operator's provider/model (and budgets) from `source` onto a
    freshly generated `target` model block, then normalise the bindings."""
    if isinstance(source, dict):
        for key in (*_MODEL_CHOICE_KEYS, "context_length", "max_tokens"):
            if key in source:
                target[key] = source[key]
    reconcile_model_block({"model": target})
    return target


def _mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Hermes {key} config must be a mapping")
    return value


def _invariant_errors(
    actual: dict[str, Any], expected: dict[str, Any], prefix: str
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for key, expected_value in expected.items():
        path = f"{prefix}.{key}" if prefix else key
        if actual.get(key) != expected_value:
            errors.append({
                "path": path,
                "message": "hosted Calliope invariant would change",
                "expected": repr(expected_value),
                "actual": repr(actual.get(key)),
            })
    return errors


def _plaintext_secret_errors(value: Any, path: str = "") -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}" if path else key
            if _SECRET_KEY_RE.search(key) and isinstance(child, str) and child:
                if not _ENV_REFERENCE_RE.fullmatch(child) and not _CREDENTIAL_REFERENCE_RE.fullmatch(child):
                    errors.append({
                        "path": child_path,
                        "message": "plaintext secret is not allowed; use an environment or canonical credential reference",
                    })
            if key.lower() in {"authorization", "proxy-authorization"} and isinstance(child, str) and child:
                if not _AUTHORIZATION_REFERENCE_RE.fullmatch(child.strip()):
                    errors.append({
                        "path": child_path,
                        "message": "plaintext authorization header is not allowed; use an environment or canonical credential reference",
                    })
            errors.extend(_plaintext_secret_errors(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_plaintext_secret_errors(child, f"{path}[{index}]"))
    return errors


def validate_hosted_payload(payload: Any, *, native: bool = False) -> dict[str, Any]:
    """Validate the raw candidate without mutating or expanding secret refs."""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not isinstance(payload, dict):
        return {
            "valid": False,
            "errors": [{"path": "", "message": "Hermes config must be a mapping"}],
            "warnings": [],
            "additional_mcp_servers": [],
            "native_validation": "not_run",
        }
    required_sections = (
        "model",
        "memory",
        "gateway",
        "platforms",
        "platform_toolsets",
        "mcp_servers",
    )
    for section in required_sections:
        if not isinstance(payload.get(section), dict):
            errors.append({"path": section, "message": "required mapping is missing"})
    if not errors:
        errors.extend(model_block_errors(payload["model"], "model"))
        errors.extend(_invariant_errors(payload["memory"], MEMORY_INVARIANTS, "memory"))
        errors.extend(_invariant_errors(payload["gateway"], gateway_invariants(), "gateway"))
        api_server = payload["platforms"].get("api_server")
        if not isinstance(api_server, dict):
            errors.append({"path": "platforms.api_server", "message": "required mapping is missing"})
        else:
            errors.extend(_invariant_errors(api_server, API_SERVER_INVARIANTS, "platforms.api_server"))
            extra = api_server.get("extra")
            if not isinstance(extra, dict):
                errors.append({"path": "platforms.api_server.extra", "message": "required mapping is missing"})
            else:
                errors.extend(_invariant_errors(
                    extra, api_server_extra_invariants(payload), "platforms.api_server.extra"
                ))
                if "model_routes" in extra:
                    errors.append({
                        "path": "platforms.api_server.extra.model_routes",
                        "message": "model aliases were retired; each agent names its provider and model directly (Models & agents) — reconcile the config to drop this block",
                    })
        if payload["platform_toolsets"].get("api_server") != API_SERVER_TOOLSET_INVARIANTS:
            errors.append({
                "path": "platform_toolsets.api_server",
                "message": "hosted Calliope invariant would change",
                "expected": repr(API_SERVER_TOOLSET_INVARIANTS),
                "actual": repr(payload["platform_toolsets"].get("api_server")),
            })
        for name, expected in WAREHOUSE_MCP_INVARIANTS.items():
            actual = payload["mcp_servers"].get(name)
            if not isinstance(actual, dict):
                errors.append({"path": f"mcp_servers.{name}", "message": "required first-party bridge is missing"})
            else:
                errors.extend(_invariant_errors(actual, expected, f"mcp_servers.{name}"))
    errors.extend(_plaintext_secret_errors(payload))
    mcp_servers = payload.get("mcp_servers") if isinstance(payload.get("mcp_servers"), dict) else {}
    additional = sorted(set(mcp_servers) - set(WAREHOUSE_MCP_INVARIANTS))
    for name in additional:
        errors.append({
            "path": f"mcp_servers.{name}",
            "message": "customer MCP servers belong in the RVBBIT Warehouse registry, not Hermes; migrate this server before applying the config",
        })

    native_state = "not_run"
    if native:
        try:
            from hermes_cli.config import validate_config_structure

            native_state = "passed"
            for issue in validate_config_structure(payload):
                target = errors if str(issue.severity).lower() == "error" else warnings
                target.append({
                    "path": "",
                    "message": f"Hermes: {issue.message}",
                    "hint": issue.hint,
                })
                if target is errors:
                    native_state = "failed"
        except ImportError as exc:
            errors.append({
                "path": "",
                "message": f"pinned Hermes validator is unavailable: {exc}",
            })
            native_state = "unavailable"

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "additional_mcp_servers": additional,
        "native_validation": native_state,
    }


# 2026-09-23: the two long MCP lane keys were renamed (WarehouseSpecialist → Specialist,
# WarehouseRoundtable → Roundtable) because the Claude subscription plugin refuses any
# advertised tool name over 50 characters and Hermes names MCP tools mcp__<server>__<tool>.
# Configs already on a volume still carry the old keys and pins; rename them in place so an
# operator's own pin list keeps its choices under the new names and no stale key survives.
_LEGACY_MCP_SERVER_KEYS = {"WarehouseSpecialist": "Specialist", "WarehouseRoundtable": "Roundtable"}
_LEGACY_LANE_PREFIXES = {"mcp__WarehouseSpecialist__": "mcp__Specialist__", "mcp__WarehouseRoundtable__": "mcp__Roundtable__"}


def normalize_legacy_lane_names(payload):
    """Rewrite legacy lane keys/prefixes in a parsed config; returns True when anything changed."""
    changed = False
    if not isinstance(payload, dict):
        return False
    servers = payload.get("mcp_servers")
    if isinstance(servers, dict):
        for old, new in _LEGACY_MCP_SERVER_KEYS.items():
            if old in servers:
                value = servers.pop(old)
                if new not in servers:
                    servers[new] = value
                changed = True

    def _rename(name):
        for old, new in _LEGACY_LANE_PREFIXES.items():
            if isinstance(name, str) and name.startswith(old):
                return new + name[len(old):]
        return name

    tools = payload.get("tools")
    tool_search = tools.get("tool_search") if isinstance(tools, dict) else None
    if isinstance(tool_search, dict) and isinstance(tool_search.get("pinned"), list):
        renamed, seen = [], set()
        for name in tool_search["pinned"]:
            new = _rename(name)
            if new not in seen:
                renamed.append(new)
                seen.add(new)
        if renamed != tool_search["pinned"]:
            tool_search["pinned"] = renamed
            changed = True
    toolsets = payload.get("platform_toolsets")
    if isinstance(toolsets, dict):
        for platform, entries in toolsets.items():
            if isinstance(entries, list):
                renamed = [_LEGACY_MCP_SERVER_KEYS.get(e, e) if isinstance(e, str) else e for e in entries]
                if renamed != entries:
                    toolsets[platform] = renamed
                    changed = True
    return changed


def apply_managed_media_defaults(payload: dict) -> bool:
    changed = False
    # Managed appliance capabilities use stable aliases; explicit selections still win.
    if os.environ.get("RVBBIT_CLOVER_KEY"):
        image = payload.get("image_gen") or {}
        if not image.get("provider"):
            payload["image_gen"] = {"provider":"openai", "model":"calliope-image",
                "openai":{"base_url":os.environ.get("RVBBIT_CLOVER_OPENAI_BASE_URL", "http://clover:8090/v1"),
                          "key_env":"RVBBIT_CLOVER_KEY", "model":"calliope-image"}}
            changed = True
        stt = payload.get("stt") or {}
        if not stt.get("provider"):
            payload["stt"] = {**stt, "provider":"openai", "openai":{"model":"calliope-stt",
                "base_url":os.environ.get("RVBBIT_CLOVER_OPENAI_BASE_URL", "http://clover:8090/v1"),
                "key_env":"RVBBIT_CLOVER_KEY"}}
            changed = True

    return changed


def apply_managed_approval_defaults(payload: dict) -> bool:
    """Default the appliance sandbox to unattended execution; explicit policy wins."""
    approvals = payload.get("approvals")
    if approvals is None:
        payload["approvals"] = {"mode": "off"}
        return True
    if not isinstance(approvals, dict):
        raise ValueError("Hermes approvals config must be a mapping")
    if "mode" not in approvals:
        approvals["mode"] = "off"
        return True
    return False


def reconcile_payload(payload: Any) -> bool:
    """Reconcile hosted invariants into an already-parsed config mapping."""
    if not isinstance(payload, dict):
        raise ValueError("Hermes config must be a mapping")
    changed = normalize_legacy_lane_names(payload)
    changed = reconcile_model_block(payload) or changed
    changed = apply_managed_approval_defaults(payload) or changed
    changed = apply_managed_auxiliary_defaults(payload) or changed

    # cli-config.yaml.example only seeds a brand-new Hermes volume. Hosted
    # appliances keep /opt/data across image upgrades, so profiles introduced
    # after first boot must also be reconciled into the durable config. Without
    # this block Hermes accepts /p/<profile>/ as an ignored compatibility
    # prefix and silently runs every specialist in the default profile (and,
    # consequently, the default Hindsight bank).
    gateway = payload.setdefault("gateway", {})
    if not isinstance(gateway, dict):
        raise ValueError("Hermes gateway config must be a mapping")
    desired_gateway = gateway_invariants()
    for key, value in desired_gateway.items():
        if gateway.get(key) != value:
            gateway[key] = value
            changed = True

    servers = payload.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError("Hermes MCP server config must be a mapping")
    for name, desired in WAREHOUSE_MCP_INVARIANTS.items():
        if servers.get(name) != desired:
            servers[name] = desired
            changed = True

    memory = payload.setdefault("memory", {})
    if not isinstance(memory, dict):
        raise ValueError("Hermes memory config must be a mapping")
    for key, value in MEMORY_INVARIANTS.items():
        if memory.get(key) != value:
            memory[key] = value
            changed = True

    for section, keys in CONTEXT_ECONOMY_DEFAULTS.items():
        block = payload.get(section)
        if not isinstance(block, dict):
            block = {}
            payload[section] = block
        for key, (replaceable, ours) in keys.items():
            current = block.get(key)
            if current is None or current in replaceable:
                if current != ours:
                    block[key] = ours
                    changed = True

    tools_block = payload.setdefault("tools", {})
    if not isinstance(tools_block, dict):
        raise ValueError("Hermes tools config must be a mapping")
    tool_search = tools_block.get("tool_search")
    if not isinstance(tool_search, dict):
        # Legacy bool shape (tools.tool_search: true/false) keeps its intent
        # as the `enabled` field while gaining the dict shape the pin needs.
        tool_search = {"enabled": bool(tool_search)} if isinstance(tool_search, bool) else {}
        tools_block["tool_search"] = tool_search
        changed = True
    pinned = tool_search.get("pinned")
    if (
        not isinstance(pinned, list)
        or not pinned
        or tuple(pinned) in PINNED_TOOLS_PREVIOUS_DEFAULTS
    ):
        tool_search["pinned"] = list(PINNED_TOOLS_DEFAULT)
        changed = True

    # Even a customized list must not retain the retired proposal-only tool.
    # Preserve all other choices and put the replacement at its existing slot.
    if "mcp__Warehouse__propose_calliope_computer_action" in tool_search.get("pinned", []):
        replacements = []
        for name in tool_search["pinned"]:
            for replacement in (
                ("mcp__Warehouse__open_desktop", "mcp__Warehouse__desktop_exec", "mcp__Warehouse__finish_desktop")
                if name == "mcp__Warehouse__propose_calliope_computer_action" else (name,)
            ):
                if replacement not in replacements:
                    replacements.append(replacement)
        tool_search["pinned"] = replacements
        changed = True

    changed = apply_managed_media_defaults(payload) or changed

    # Image generation backend selection. Nobody runs the `hermes tools`
    # picker on an appliance, and with no selection stored image_generate
    # deregisters entirely (its legacy autodetect only knows FAL). Mirror
    # what the picker would store: when the operator never selected a
    # backend and the auth store holds xAI OAuth, select the xai backend —
    # it rides the same subscription token as chat.
    image_gen = payload.get("image_gen")
    if (not isinstance(image_gen, dict)
            or not str(image_gen.get("provider") or "").strip()):
        # xAI OAuth first (rides the same subscription token as chat), then
        # an OpenRouter key — without this arm, OpenRouter-keyed installs
        # never grow the image_generate tool and Calliope's portrait lane
        # falls back to hand-typed endpoint + key env config.
        auto_provider = None
        if _auth_store_has_xai_oauth():
            auto_provider = "xai"
        elif _has_openrouter_key():
            auto_provider = "openrouter"
        if auto_provider:
            merged = dict(image_gen) if isinstance(image_gen, dict) else {}
            merged["provider"] = auto_provider
            payload["image_gen"] = merged
            changed = True

    platforms = payload.setdefault("platforms", {})
    if not isinstance(platforms, dict):
        raise ValueError("Hermes platforms config must be a mapping")
    api_server = platforms.setdefault("api_server", {})
    if not isinstance(api_server, dict):
        api_server = {}
        platforms["api_server"] = api_server
        changed = True
    for key, value in API_SERVER_INVARIANTS.items():
        if api_server.get(key) != value:
            api_server[key] = value
            changed = True
    extra = api_server.setdefault("extra", {})
    if not isinstance(extra, dict):
        extra = {}
        api_server["extra"] = extra
        changed = True
    for key, value in api_server_extra_invariants(payload).items():
        if extra.get(key) != value:
            extra[key] = value
            changed = True
    if "model_routes" in extra:
        # Retired alias table (see the model block notes at the top).
        extra.pop("model_routes", None)
        changed = True

    # The specialist transport remains registered process-wide so nested
    # agents have a second Streamable HTTP session, but exposing it through
    # the default API agent leaks a privileged/internal lane and duplicates
    # every Warehouse capability in tool_search. Hermes treats explicit MCP
    # names here as an allowlist. Admit the normal parent bridge plus the
    # one-tool, long-timeout roundtable bridge; specialists remain hidden.
    platform_toolsets = payload.setdefault("platform_toolsets", {})
    if not isinstance(platform_toolsets, dict):
        raise ValueError("Hermes platform toolsets config must be a mapping")
    if platform_toolsets.get("api_server") != API_SERVER_TOOLSET_INVARIANTS:
        platform_toolsets["api_server"] = list(API_SERVER_TOOLSET_INVARIANTS)
        changed = True
    return changed


def reconcile(path: Path) -> bool:
    if not path.is_file():
        return False
    original = path.stat()
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not reconcile_payload(payload):
        return False
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)
        temporary = Path(handle.name)
    os.chmod(temporary, original.st_mode)
    temporary_stat = temporary.stat()
    if (temporary_stat.st_uid, temporary_stat.st_gid) != (
        original.st_uid,
        original.st_gid,
    ):
        os.chown(temporary, original.st_uid, original.st_gid)
    os.replace(temporary, path)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config_path", type=Path)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    if args.validate:
        try:
            payload = yaml.safe_load(args.config_path.read_text(encoding="utf-8")) or {}
            result = validate_hosted_payload(payload, native=args.native)
        except Exception as exc:
            result = {
                "valid": False,
                "errors": [{"path": "", "message": f"YAML parse failed: {exc}"}],
                "warnings": [],
                "additional_mcp_servers": [],
                "native_validation": "not_run",
            }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["valid"] else 1
    changed = reconcile(args.config_path)
    if changed:
        print("[calliope] reconciled hosted model and specialist Warehouse lane")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
