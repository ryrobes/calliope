"""Explicit, compact analytical answers on the existing Stage ledger."""
import hashlib
import json
from uuid import UUID


def _text(value, name, limit, required=False):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be plain text")
    value = " ".join(value.split())
    if required and not value:
        raise ValueError(f"{name} is required")
    if len(value) > limit:
        raise ValueError(f"{name} must be at most {limit} characters; shorten it without losing essential qualifications")
    return value


def _surface_id(value):
    try:
        return str(UUID(str(value).removeprefix("surface:")))
    except ValueError:
        raise ValueError("Evidence and replacement references must be Stage surface UUIDs") from None


def publish(conn_factory, destination, answer, caveat="", evidence_refs=None, replaces_surface_id=None):
    import calliope

    answer = _text(answer, "answer", 320, required=True)
    caveat = _text(caveat, "caveat", 240)
    refs = [] if evidence_refs is None else evidence_refs
    if not isinstance(refs, list) or len(refs) > 8:
        raise ValueError("evidence_refs must be a list of up to eight Stage surface UUIDs")
    refs = list(dict.fromkeys(_surface_id(ref) for ref in refs))
    parent_id = _surface_id(replaces_surface_id) if replaces_surface_id else None
    session_id, turn_id = destination["session_id"], destination["turn_id"]
    lineage = f"answer:{turn_id}"
    with conn_factory() as conn, conn.transaction():
        # Serialize answer revisions, including calls from different specialists.
        # The shared inserter still owns ordinal allocation and publication.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"stage-answer:{session_id}",))
        if parent_id:
            parent = conn.execute(
                "SELECT lineage_key FROM rvbbit.calliope_surfaces WHERE id=%s::uuid "
                "AND session_id=%s::uuid AND kind='answer'", (parent_id, session_id),
            ).fetchone()
            if not parent:
                raise ValueError("Replacement answer must belong to this chat")
            lineage = parent["lineage_key"]
        latest = conn.execute(
            "SELECT * FROM rvbbit.calliope_surfaces WHERE session_id=%s::uuid "
            "AND lineage_key=%s AND kind='answer' ORDER BY created_at DESC,ordinal DESC,id DESC LIMIT 1",
            (session_id, lineage),
        ).fetchone()
        evidence = []
        for ref in refs:
            row = conn.execute(
                "SELECT id,title,kind FROM rvbbit.calliope_surfaces WHERE id=%s::uuid AND session_id=%s::uuid",
                (ref, session_id),
            ).fetchone()
            if not row or row["kind"] == "answer":
                raise ValueError("Evidence must reference an existing supporting Stage item in this chat")
            evidence.append({"surface_id": str(row["id"]), "title": row["title"], "kind": row["kind"]})
        payload = {"answer": answer, "caveat": caveat, "evidence_refs": evidence}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        identical = latest and (latest.get("payload") or {}).get("content_sha256") == digest
        if parent_id and latest and str(latest["id"]) != parent_id:
            # A transport retry of the just-created revision is harmless. An
            # older parent cannot overwrite an intervening edit.
            if not (identical and str(latest.get("parent_surface_id")) == parent_id):
                raise ValueError(f"Answer was revised; use latest surface {latest['id']}")
        if identical:
            return {"surface_id": str(latest["id"]), "kind": "answer", "reused": True}
        payload["content_sha256"] = digest
        parent_id = str(latest["id"]) if latest else None
        rows = calliope._insert_surfaces(conn_factory, session_id, turn_id, [{
            "kind": "answer", "title": "Answer", "lineage_key": lineage,
            "tool_name": "publish_answer", "tool_call_id": f"answer:{turn_id}:{parent_id or 'root'}:{digest}",
            "parent_surface_id": parent_id, "payload": payload,
            "source": {"origin": "publish_answer"},
            "presentation": {"purpose": "answer", "phase": "published", "size": "full"},
        }])
        if not rows:
            raise ValueError("Answer publication returned no surface")
        return {"surface_id": rows[0]["id"], "kind": "answer", "reused": False,
                "message": "Answer published to the Stage. Use this surface_id to revise it in a later turn."}
