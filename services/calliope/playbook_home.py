"""A Playbook's home: the thread where its manager and Calliope shape it.

Save as Playbook opens a new home next to the chat it came from. Calliope reads
that whole chat (a bounded transcript built here from the database, not from her
own possibly compressed memory), drafts the method, and talks it through in the
home. Revising, copying and reviewing a Playbook all happen in its home too, so
its versions and the conversation about them stay in one place.
"""
from __future__ import annotations

import re
from typing import Any

TRANSCRIPT_BUDGET = 48_000
# Each pass shortens long answers further until the whole chat fits.
_ANSWER_CAPS = (6_000, 2_500, 1_000, 400, 160)
_ASK_CAPS = (3_000, 1_500, 700, 300, 160)
_SQL_CAP = 600


def _clip(text: Any, cap: int) -> str:
    value = str(text or "").strip()
    if len(value) <= cap:
        return value
    head = value[: max(0, cap - 120)].rstrip()
    tail = value[-100:].lstrip() if cap >= 400 else ""
    return f"{head} […]" + (f" {tail}" if tail else "")


def _tools(receipt: Any) -> str:
    tools = receipt.get("tools") if isinstance(receipt, dict) else None
    names = []
    for tool in tools if isinstance(tools, list) else []:
        if isinstance(tool, dict) and tool.get("name"):
            count = int(tool.get("count") or 1)
            names.append(f"{tool['name']}" + (f" ×{count}" if count > 1 else ""))
    return ", ".join(names[:12])


def _stage_lines(surfaces: list[dict[str, Any]]) -> list[str]:
    lines = []
    for surface in surfaces[:8]:
        title = re.sub(r"\s+", " ", str(surface.get("title") or "untitled")).strip()[:160]
        source = surface.get("source") if isinstance(surface.get("source"), dict) else {}
        sql = re.sub(r"\s+", " ", str(source.get("sql") or "")).strip()
        lines.append(f"- [{surface.get('kind') or 'item'}] {title}"
                     + (f" — SQL: {_clip(sql, _SQL_CAP)}" if sql else ""))
    return lines


def transcript(conn: Any, source_session_id: str, focus_turn_id: str | None = None,
               budget: int = TRANSCRIPT_BUDGET) -> tuple[str, int | None]:
    """The saved chat as readable text, bounded; returns (text, focus ordinal)."""
    session = conn.execute(
        "SELECT title FROM rvbbit.calliope_sessions WHERE id=%s::uuid", (source_session_id,)
    ).fetchone()
    turns = [dict(row) for row in conn.execute(
        "SELECT id::text,ordinal,turn_kind,status,user_message,assistant_message,"
        "author_display_name,author_email,response_receipt FROM rvbbit.calliope_turns "
        "WHERE session_id=%s::uuid AND turn_kind IN ('chat','note') ORDER BY ordinal",
        (source_session_id,),
    ).fetchall()]
    by_turn: dict[str, list[dict[str, Any]]] = {}
    for row in conn.execute(
        "SELECT turn_id::text,kind,title,source FROM rvbbit.calliope_surfaces "
        "WHERE session_id=%s::uuid ORDER BY created_at LIMIT 400",
        (source_session_id,),
    ).fetchall():
        by_turn.setdefault(str(row["turn_id"]), []).append(dict(row))
    focus = next((t for t in turns if t["id"] == str(focus_turn_id or "")), None)
    focus_ordinal = int(focus["ordinal"]) if focus else None
    title = str((session or {}).get("title") or "Untitled chat")

    def render(ask_cap: int, answer_cap: int, keep: set[int] | None = None) -> str:
        parts = [f"# Chat: {title}"]
        skipped = 0
        for turn in turns:
            ordinal = int(turn["ordinal"])
            if keep is not None and ordinal not in keep:
                skipped += 1
                continue
            if skipped:
                parts.append(f"[… {skipped} earlier turns left out to fit …]")
                skipped = 0
            pointed = ordinal == focus_ordinal
            caps = (_ASK_CAPS[0], _ANSWER_CAPS[0]) if pointed else (ask_cap, answer_cap)
            who = str(turn.get("author_display_name") or turn.get("author_email") or "The human")
            heading = f"## Turn {ordinal}" + (" — the reply they pointed at" if pointed else "")
            lines = [heading]
            if turn["turn_kind"] == "note":
                lines.append(f"{who} (note to the room): {_clip(turn.get('user_message'), caps[0])}")
            else:
                lines.append(f"{who} asked: {_clip(turn.get('user_message'), caps[0])}")
                answer = str(turn.get("assistant_message") or "").strip()
                if answer:
                    lines.append(f"Calliope answered: {_clip(answer, caps[1])}")
                elif turn.get("status") not in ("complete", "partial"):
                    lines.append(f"(This turn ended {turn.get('status')}.)")
                tools = _tools(turn.get("response_receipt"))
                if tools:
                    lines.append(f"Tools used: {tools}")
                stage = _stage_lines(by_turn.get(turn["id"], []))
                if stage:
                    lines.append("Put on the Stage:\n" + "\n".join(stage))
            parts.append("\n".join(lines))
        return "\n\n".join(parts)

    for ask_cap, answer_cap in zip(_ASK_CAPS, _ANSWER_CAPS):
        text = render(ask_cap, answer_cap)
        if len(text) <= budget:
            return text, focus_ordinal
    # Still too long: keep the start, the end and the pointed-at reply.
    ordinals = [int(t["ordinal"]) for t in turns]
    for edge in (12, 8, 5, 3):
        keep = set(ordinals[:3]) | set(ordinals[-edge:]) | ({focus_ordinal} if focus_ordinal else set())
        text = render(_ASK_CAPS[-1], _ANSWER_CAPS[-1], keep)
        if len(text) <= budget:
            return text, focus_ordinal
    return text[:budget], focus_ordinal


def first_draft_context(home_session_id: str, chat: str, focus_ordinal: int | None,
                        resend: bool) -> str:
    """Instructions for the first turn in a home made by Save as Playbook."""
    focus = (f" They pointed at turn {focus_ordinal}; weigh the work through that reply most."
             if focus_ordinal else "")
    body = (
        "[CALLIOPE PLAYBOOK HOME — internal]\n"
        "This thread is where the human and you turn one of their chats into a Playbook: a "
        "reusable method, not a replay of tool calls." + focus + " Read the whole chat below. "
        "If it covers several unrelated kinds of work, ask which one to keep before drafting. "
        f"Otherwise call draft_calliope_playbook once with session_id={home_session_id} (this "
        "thread); Warehouse links the saved chat as the source and pins its evidence itself. "
        "Keep outcomes, when it applies, judgment, guardrails, how to check the result, fallbacks "
        "and capability needs; leave out one-off values, IDs and accidental tool order. It stays "
        "a private draft: never approve or share it unless the human asks. Then, briefly: say what "
        "you understood the method to be, what will change from one run to the next, and ask at "
        "most three questions whose answers would change the method. They approve with the "
        "Approve button on the draft, or by asking you.\n"
    )
    if resend:
        body += "\nTHE SAVED CHAT (read-only transcript)\n" + chat + "\n"
    else:
        body += "\nThe saved chat's transcript is in the first message of this thread.\n"
    return body + "[/CALLIOPE PLAYBOOK HOME]"


def fork_context(home_session_id: str, playbook: dict[str, Any]) -> str:
    """Instructions for a home where someone makes their own copy of a shared Playbook."""
    return (
        "[CALLIOPE PLAYBOOK HOME — internal]\n"
        f"The human wants their own copy of the “{playbook.get('title')}” Playbook "
        f"(playbook_ref={playbook.get('id')}, approved v{playbook.get('approved_version')}). "
        "Read it with read_calliope_playbook, ask what they want to change if they have not said, "
        f"then call draft_calliope_playbook once with session_id={home_session_id} and no "
        "playbook_ref, so it becomes a new private Playbook of theirs; say in change_summary "
        "which Playbook and version it was copied from. Never approve or share it unless they ask.\n"
        "[/CALLIOPE PLAYBOOK HOME]"
    )


def home_context(home_session_id: str, playbook: dict[str, Any]) -> str:
    """Standing instructions for every later turn in a Playbook's home."""
    approved = playbook.get("approved_version")
    state = (f"approved v{approved}" + (f", newer draft v{playbook.get('latest_version')}"
             if playbook.get("has_newer_draft") else "")) if approved else "not approved yet"
    built_in = (" It is a built-in Playbook; as an Admin the human manages it for everyone."
                if playbook.get("built_in") else "")
    return (
        "[CALLIOPE PLAYBOOK HOME — internal]\n"
        f"This thread is the home of the “{playbook.get('title')}” Playbook "
        f"(playbook_ref={playbook.get('id')}, latest v{playbook.get('latest_version')}, {state})."
        + built_in + " A change the human asks for becomes a new version: read the latest "
        "version first, then call draft_calliope_playbook with playbook_ref, "
        f"expected_version=<latest>, session_id={home_session_id} and a short change_summary. "
        "The approved version keeps working until a human approves the new one; call "
        "approve_calliope_playbook only when they explicitly ask. Archiving withdraws it from "
        "discovery and stops its schedules.\n"
        "[/CALLIOPE PLAYBOOK HOME]"
    )
