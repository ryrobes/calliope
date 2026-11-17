"""The domain-agent factory: topology-derived experts, Hermes-crafted.

Business Topology's promoted value-system families define which domain
experts should exist (deterministic eligibility, operator floors in
rvbbit.knowledge_loop_policy kind='domain_agent'); the default Hermes
profile — the appliance's smartest configured model — crafts each agent's
persona, SOUL, and routing hint from the domain's governed evidence; and
registration lands atomically in rvbbit.generated_specialists +
rvbbit.calliope_specialists (0374). The Hermes reconciler fetches active
generated specialists over the authed internal endpoint and synthesizes
their profiles at boot.

Baselines track topology: when a family's revision advances past the one a
SOUL was crafted from, the factory re-crafts it. Learned overlays (0370)
compose beneath the regenerated baseline; Hindsight banks and wiki pages
are untouched — accumulated experience survives definition refreshes.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid as uuid_module
from typing import Any, Callable

TICK_SECONDS = int(os.environ.get("DOMAIN_AGENT_FACTORY_TICK_SECONDS", "900"))
CRAFT_TIMEOUT_SECONDS = int(os.environ.get("DOMAIN_AGENT_FACTORY_CRAFT_TIMEOUT", "600"))
MAX_CRAFTS_PER_TICK = 1

_RESERVED_NAMES = {
    "iris", "hestia", "atalanta", "athena", "argus", "demeter", "ariadne",
    "cassandra", "calliope", "hermes",
}

_CRAFT_INSTRUCTIONS = """You are crafting a new persistent domain-expert agent for this appliance's specialist team from governed Business Topology evidence. Return ONLY a JSON object (no markdown fence, no prose) with exactly these keys:

- display_name: a single Greek-mythology-adjacent persona name, one word, capitalized, NOT any of: Iris, Hestia, Atalanta, Athena, Argus, Demeter, Ariadne, Cassandra, Calliope, Hermes, Peitho, Talaria, Amalthea, Danae.
- role_label: the domain's human name (e.g. "Support Inquiries").
- description: one sentence, "<role_label> domain expert — owns <what>", naming the source schema.
- routing_hint: 1-3 sentences telling the lead agent when to prefer this expert. MUST name the source schema/tables so questions about same-topic data in OTHER source systems do not misroute. Start with "Prefer <name> for".
- memory_mission: one sentence starting "Retain durable <role_label> domain knowledge:" listing what should compound.
- capabilities: 3-6 short snake_case tags, first one "domain_expert".
- soul_markdown: the agent's SOUL.md. Structure it EXACTLY as: H1 "<display_name> · <role_label>"; an identity paragraph (full-stack within this domain: analysis, briefs, documents, dashboard substance; Calliope is the lead agent and integrator; other domains have their own experts); a paragraph noting this SOUL was cold-boot synthesized from governed, auto-adopted, provisional topology evidence — every definition is a working assumption to verify and correct; "## Your data" (sources, governed cube + grain, every governed metric with its formula, time semantics, and dimensions — from the evidence, never invented); "## Domain vocabulary" (the key concepts, compressed); "## Known limitations" (EVERY gap assertion, kept sharp — these are load-bearing craft, never soften them); "## Neighbors — hand off, don't guess" (which sibling domain owns what, and which cross-system joins are UNPROVEN and must never be fabricated — derive from the evidence's source systems and the provided sibling list); "## Operating contract" (verify against the governed cube first, guarded SQL fallback, capability_search before assuming absence, wiki_write_page for durable lessons, record_specialist_finding for material findings, corrections outrank inference — say so when a definition proves wrong).

Ground every claim in the provided evidence. Where the evidence is silent, say less rather than inventing. Keep soul_markdown under 6000 characters."""


def _hermes_config() -> dict[str, str] | None:
    url = (os.environ.get("WAREHOUSE_HERMES_URL") or "").rstrip("/")
    key = os.environ.get("WAREHOUSE_HERMES_API_KEY") or ""
    if not url or not key:
        return None
    return {"url": url, "key": key}


def _hermes_json(config: dict[str, str], method: str, path: str,
                 payload: dict | None = None, timeout: int = 60) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        config["url"] + path, data=data, method=method,
        headers={
            "Authorization": f"Bearer {config['key']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def _domain_evidence(conn: Any, domain_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT jsonb_build_object(
          'domain_key', summary.domain_key,
          'name', summary.name,
          'summary', summary.summary,
          'cube', summary.cube_name,
          'grain', summary.materialization_grain,
          'sources', summary.source_relations,
          'family_revision', summary.family_revision,
          'family_id', summary.family_id,
          'metrics', (SELECT jsonb_agg(jsonb_build_object(
              'key', m->>'metric_key', 'name', m->>'name', 'formula', m->>'formula',
              'grain', m->>'grain', 'time', m->>'time_semantics',
              'dims', (SELECT jsonb_agg(d->>'key')
                       FROM jsonb_array_elements(coalesce(m->'dimensions','[]'::jsonb)) d)))
            FROM jsonb_array_elements(summary.graph->'metrics') m),
          'assertions', (SELECT jsonb_agg(jsonb_build_object(
              'kind', a.assertion_kind, 'subject', a.subject_key,
              'predicate', a.predicate, 'value', a.object_value))
            FROM (SELECT * FROM rvbbit.business_semantic_assertions a2
                   WHERE a2.family_id=summary.family_id
                     AND a2.assertion_kind IN ('business_domain','concept','gap',
                                               'analytical_question','semantic_relationship')
                   ORDER BY a2.confidence DESC LIMIT 40) a)
        ) AS evidence
        FROM rvbbit.business_value_graph_summary summary
        WHERE summary.domain_key=%s AND summary.status='promoted'
        LIMIT 1
        """,
        (domain_key,),
    ).fetchone()
    return row["evidence"] if row else None


def _sibling_domains(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.specialist_key, s.display_name, g.domain_key,
               (SELECT summary.source_relations
                  FROM rvbbit.business_value_graph_summary summary
                 WHERE summary.domain_key=g.domain_key
                   AND summary.status='promoted' LIMIT 1) AS sources
          FROM rvbbit.calliope_specialists s
          LEFT JOIN rvbbit.generated_specialists g
            ON g.specialist_key=s.specialist_key
         WHERE s.status='active'
           AND (g.specialist_key IS NOT NULL
                OR s.capabilities @> '["domain_expert"]'::jsonb)
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _parse_crafted(raw: Any) -> dict[str, Any]:
    # Response shape matches the design-profile generator's:
    # {"message": {"content": <str | [{type:"text",text},...]>}}.
    if isinstance(raw, dict):
        content = (
            (raw.get("message") or {}).get("content")
            if isinstance(raw.get("message"), dict)
            else raw.get("content") or raw.get("response") or raw.get("text")
        )
    else:
        content = raw
    if isinstance(content, list):
        content = "\n".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    text = str(content or "")
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|```\s*$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in crafted response")
    return json.loads(text[start:end + 1])


def _validate_crafted(crafted: dict[str, Any], conn: Any) -> dict[str, Any]:
    name = str(crafted.get("display_name") or "").strip()
    if not re.fullmatch(r"[A-Z][a-z]{2,19}", name):
        raise ValueError(f"bad display_name {name!r}")
    if name.lower() in _RESERVED_NAMES:
        raise ValueError(f"display_name {name!r} is reserved")
    taken = conn.execute(
        "SELECT 1 FROM rvbbit.calliope_specialists WHERE lower(display_name)=%s AND status IN ('active','provisioning')",
        (name.lower(),),
    ).fetchone()
    if taken:
        raise ValueError(f"display_name {name!r} already on the roster")
    soul = str(crafted.get("soul_markdown") or "")
    for section in ("## Your data", "## Known limitations",
                    "## Neighbors", "## Operating contract"):
        if section not in soul:
            raise ValueError(f"soul_markdown missing section {section!r}")
    hint = str(crafted.get("routing_hint") or "").strip()
    if not hint.lower().startswith("prefer"):
        raise ValueError("routing_hint must start with 'Prefer'")
    capabilities = crafted.get("capabilities")
    if not isinstance(capabilities, list) or "domain_expert" not in capabilities:
        raise ValueError("capabilities must be a list containing 'domain_expert'")
    return {
        "display_name": name,
        "role_label": str(crafted.get("role_label") or "").strip()[:120],
        "description": str(crafted.get("description") or "").strip()[:500],
        "routing_hint": hint[:800],
        "memory_mission": str(crafted.get("memory_mission") or "").strip()[:500],
        "capabilities": [str(c)[:60] for c in capabilities[:6]],
        "soul_markdown": soul[:20000],
    }


def craft_one(conn_factory: Callable[..., Any], domain_key: str) -> dict[str, Any]:
    """Craft and register one domain agent. Raises on any validation failure."""
    config = _hermes_config()
    if not config:
        raise RuntimeError("Hermes API is not configured (WAREHOUSE_HERMES_URL/API_KEY)")
    with conn_factory() as conn:
        evidence = _domain_evidence(conn, domain_key)
        if not evidence:
            raise RuntimeError(f"no promoted evidence for domain {domain_key!r}")
        siblings = _sibling_domains(conn)

    session_id = f"domain_factory_{uuid_module.uuid4().hex[:12]}"
    _hermes_json(config, "POST", "/api/sessions",
                 {"id": session_id, "source": "api_server"})
    try:
        result = _hermes_json(
            config, "POST", f"/api/sessions/{session_id}/chat",
            {
                "message": (
                    "DOMAIN EVIDENCE (governed, from Business Topology):\n"
                    + json.dumps(evidence, default=str)[:24000]
                    + "\n\nEXISTING SIBLING DOMAIN EXPERTS (for the Neighbors "
                      "section; note their source systems):\n"
                    + json.dumps(siblings, default=str)[:4000]
                ),
                "instructions": _CRAFT_INSTRUCTIONS,
            },
            timeout=CRAFT_TIMEOUT_SECONDS,
        )
    finally:
        try:
            _hermes_json(config, "DELETE", f"/api/sessions/{session_id}")
        except Exception:  # noqa: BLE001
            pass

    with conn_factory() as conn:
        crafted = _validate_crafted(_parse_crafted(result), conn)
        specialist_key = domain_key.replace("_", "-")
        registered = conn.execute(
            "SELECT rvbbit.register_generated_specialist("
            "%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s) AS result",
            (
                specialist_key, str(evidence["family_id"]), domain_key,
                crafted["display_name"], crafted["role_label"],
                crafted["description"], crafted["soul_markdown"],
                crafted["routing_hint"], crafted["memory_mission"],
                json.dumps(crafted["capabilities"]),
                int(evidence.get("family_revision") or 1),
            ),
        ).fetchone()
    return {"specialist_key": specialist_key, **(registered["result"] or {})}


def reconcile_readiness(conn_factory: Callable[..., Any]) -> dict[str, Any]:
    """Publish generated agents only after the running profile API accepts them.

    A transport failure is not proof a working profile vanished. A confirmed
    missing profile is quarantined; explicit operator pauses stay untouched.
    """
    config = _hermes_config()
    if not config:
        return {"status": "unconfigured"}
    with conn_factory() as conn:
        installed = conn.execute("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='rvbbit' AND table_name='generated_specialists' AND column_name='profile_ready') AS ready").fetchone()
        if not installed['ready']:
            return {"status": "migration_required"}
        rows = conn.execute("SELECT g.specialist_key,s.profile_name FROM rvbbit.generated_specialists g JOIN rvbbit.calliope_specialists s USING(specialist_key) WHERE g.status='active' AND s.status IN ('active','provisioning')").fetchall()
    ready, pending = [], []
    for row in rows:
        key = row['specialist_key']
        try:
            _hermes_json(config, "GET", "/p/" + urllib.parse.quote(row['profile_name'], safe='') + "/api/sessions", timeout=8)
            available, error = True, None
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode('utf-8', errors='replace')
            if exc.code != 404 or 'Unknown or unconfigured profile' not in detail:
                continue
            available, error = False, 'Hermes profile is not installed or served (404)'
        except Exception:
            continue
        with conn_factory() as conn:
            conn.execute("UPDATE rvbbit.generated_specialists SET profile_ready=%s,profile_checked_at=now(),profile_error=%s WHERE specialist_key=%s AND status='active'", (available, error, key))
            conn.execute("UPDATE rvbbit.calliope_specialists SET status=%s,updated_at=now() WHERE specialist_key=%s AND status IN ('active','provisioning')", ('active' if available else 'provisioning', key))
        (ready if available else pending).append(key)
    return {"ready": ready, "pending": pending}


def tick(conn_factory: Callable[..., Any]) -> dict[str, Any]:
    """One factory pass: retire agents of demoted domains, re-craft stale
    baselines, mint at most MAX_CRAFTS_PER_TICK new agents under the cap."""
    readiness = reconcile_readiness(conn_factory)
    if readiness.get("status") == "migration_required":
        return readiness
    minted, recrafted, retired, errors = [], [], [], []
    with conn_factory() as conn:
        policy = conn.execute(
            "SELECT agent_min_assertions, agent_max_count "
            "FROM rvbbit.knowledge_loop_policy WHERE kind='domain_agent'"
        ).fetchone()
        if not policy:
            return {"status": "no_policy"}
        min_assertions = int(policy["agent_min_assertions"])
        max_count = int(policy["agent_max_count"])

        for row in conn.execute(
            "SELECT g.specialist_key FROM rvbbit.generated_specialists g "
            "WHERE g.status='active' AND NOT EXISTS ("
            " SELECT 1 FROM rvbbit.business_value_graph_summary summary"
            " WHERE summary.domain_key=g.domain_key AND summary.status='promoted')"
        ).fetchall():
            conn.execute(
                "SELECT rvbbit.retire_generated_specialist(%s, %s)",
                (row["specialist_key"], "domain no longer promoted"),
            )
            retired.append(row["specialist_key"])

        stale = [dict(r) for r in conn.execute(
            "SELECT g.specialist_key, g.domain_key FROM rvbbit.generated_specialists g "
            "JOIN rvbbit.business_value_graph_summary summary"
            " ON summary.domain_key=g.domain_key AND summary.status='promoted' "
            "WHERE g.status='active' AND EXISTS (SELECT 1 FROM rvbbit.calliope_specialists s WHERE s.specialist_key=g.specialist_key AND s.status IN ('active','provisioning'))"
            " AND summary.family_revision > g.source_family_revision LIMIT 2"
        ).fetchall()]

        agent_count = conn.execute(
            "SELECT count(*) AS n FROM rvbbit.calliope_specialists "
            "WHERE status IN ('active','provisioning') AND capabilities @> '[\"domain_expert\"]'::jsonb"
        ).fetchone()["n"]

        candidates = [dict(r) for r in conn.execute(
            "SELECT domain_key, assertions FROM rvbbit.domain_agent_candidates "
            "WHERE assertions >= %s AND NOT EXISTS (SELECT 1 FROM rvbbit.generated_specialists g WHERE g.domain_key=domain_agent_candidates.domain_key) ORDER BY assertions DESC LIMIT 5",
            (min_assertions,),
        ).fetchall()]

    for item in stale:
        try:
            craft_one(conn_factory, item["domain_key"])
            recrafted.append(item["specialist_key"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"recraft {item['domain_key']}: {exc}")

    crafts = 0
    for candidate in candidates:
        if crafts >= MAX_CRAFTS_PER_TICK or agent_count + crafts >= max_count:
            break
        try:
            result = craft_one(conn_factory, candidate["domain_key"])
            minted.append(result.get("specialist_key"))
            crafts += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"mint {candidate['domain_key']}: {exc}")

    # A face for anyone missing one — silently off until the operator
    # configures the expert_portraits lane; never blocks the factory.
    try:
        import expert_portraits
        expert_portraits.backfill_tick(conn_factory)
    except Exception:  # noqa: BLE001
        pass

    return {
        "status": "complete", "minted": minted, "recrafted": recrafted,
        "retired": retired, "errors": errors, "readiness": readiness,
        "domain_agents": agent_count + crafts,
    }


_WORKER_STARTED = False


def start_worker(conn_factory: Callable[..., Any]) -> bool:
    """Background factory loop; silent no-op when Hermes is unconfigured."""
    global _WORKER_STARTED
    if _WORKER_STARTED or not _hermes_config():
        return False

    def _loop() -> None:
        time.sleep(120)  # let the appliance finish booting first
        next_craft = 0.0
        while True:
            try:
                if time.monotonic() < next_craft:
                    reconcile_readiness(conn_factory)
                    time.sleep(30)
                    continue
                next_craft = time.monotonic() + TICK_SECONDS
                result = tick(conn_factory)
                if (result.get("minted") or result.get("recrafted")
                        or result.get("retired") or result.get("errors")):
                    print(f"domain-agent factory: {json.dumps(result)[:600]}",
                          file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"domain-agent factory tick failed: {exc}", file=sys.stderr)
            time.sleep(30)

    thread = threading.Thread(target=_loop, name="domain-agent-factory", daemon=True)
    thread.start()
    _WORKER_STARTED = True
    return True
