"""Durable human questions and transactional conversation continuation.

The surface insert trigger captures questions even when no browser is attached.
Answers and their mailbox delivery commit together; retries cannot post twice.
"""
from __future__ import annotations

import json
from pathlib import Path
import uuid


class InputConflict(Exception):
    pass


def ensure(conn):
    ready = conn.execute("SELECT to_regclass('rvbbit.calliope_input_requests') AS relation").fetchone()
    if ready and ready['relation']:
        return
    candidates = [Path(__file__).resolve().parent / '../../crates/pg_rvbbit/sql/migrations/0411_calliope_input_requests.sql',
                  Path('/app/sql/0411_calliope_input_requests.sql')]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        raise RuntimeError('Missing canonical input request migration')
    conn.execute(path.read_text().split('-- Work Graph projection:')[0])


def _id(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Invalid question ID') from None


def _row(conn, owner, request_id, *, lock=False):
    row = conn.execute(
        "SELECT r.*,s.channel,s.archived,m.status AS delivery_status,m.delivered_turn_id, "
        "rvbbit.calliope_session_can_participate(s.id,%s) AS may_answer "
        "FROM rvbbit.calliope_input_requests r "
        "JOIN rvbbit.calliope_sessions s ON s.id=r.session_id "
        "LEFT JOIN rvbbit.calliope_agent_messages m ON m.id=r.delivery_message_id "
        "WHERE r.id=%s::uuid AND rvbbit.calliope_session_can_view(s.id,%s,false) "
        + ('FOR UPDATE OF r' if lock else ''),
        (owner.lower(), _id(request_id), owner.lower()),
    ).fetchone()
    if not row:
        raise LookupError('Question not found')
    return dict(row)


def _public(row):
    result = json.loads(json.dumps(row, default=str))
    result['can_answer'] = (row.get('may_answer', True) and row['status'] == 'open' and not row['archived']
                            and (row.get('channel') or 'web') == 'web')
    return result


def get(conn_factory, owner, request_id):
    with conn_factory() as conn:
        return _public(_row(conn, owner, request_id))


def resolve_work_items(conn, request):
    """Resolve only alerts bound to this exact, owner-scoped question."""
    if request['status'] not in ('answered', 'cancelled'):
        return
    conn.execute(
        "UPDATE rvbbit.calliope_work_items SET state=%s,updated_at=clock_timestamp(),"
        "seen_at=coalesce(seen_at,clock_timestamp()),resolved_at=clock_timestamp() "
        "WHERE lower(owner_email)=%s AND session_id=%s::uuid "
        "AND context->>'input_request_id'=%s AND state IN ('unread','seen')",
        ('done' if request['status'] == 'answered' else 'dismissed',
         request['owner_email'].lower(), str(request['session_id']), str(request['id'])),
    )


def work_item_request(conn, session, context, *, dedupe_key=None, source='hermes'):
    """Bind a blocker to the one question in the current turn, or an explicit ID.

    An explicit null opts out for an unrelated blocker. Never guess between
    multiple questions or attach an old alert merely because it shares a chat.
    Lock the question before the alert, matching the answer transaction.
    """
    explicit = 'input_request_id' in context
    request_id = context.get('input_request_id')
    if explicit and not request_id:
        return None
    if not explicit and dedupe_key:
        previous = conn.execute(
            "SELECT context->>'input_request_id' AS request_id "
            "FROM rvbbit.calliope_work_items WHERE owner_email=%s "
            "AND session_id=%s::uuid AND source=%s AND dedupe_key=%s",
            (session['owner_email'], str(session['id']), source, dedupe_key),
        ).fetchone()
        request_id = (previous or {}).get('request_id')
    if not explicit and not request_id:
        candidates = conn.execute(
            "SELECT r.id FROM rvbbit.calliope_input_requests r "
            "WHERE r.session_id=%s::uuid AND lower(r.owner_email)=%s "
            "AND r.request->'blocking' IS DISTINCT FROM 'false'::jsonb "
            "AND r.turn_id=(SELECT t.id FROM rvbbit.calliope_turns t "
            "WHERE t.session_id=r.session_id AND t.status NOT IN ('queued','merged') "
            "AND t.turn_kind NOT IN ('note','knock') ORDER BY t.ordinal DESC LIMIT 1) "
            "AND EXISTS (SELECT 1 FROM rvbbit.calliope_turns t WHERE t.id=r.turn_id AND t.status='running') "
            "ORDER BY r.created_at,r.id LIMIT 2",
            (str(session['id']), session['owner_email'].lower()),
        ).fetchall()
        if len(candidates) == 1:
            request_id = candidates[0]['id']
    if not request_id:
        return None
    request = conn.execute(
        "SELECT * FROM rvbbit.calliope_input_requests WHERE id=%s::uuid "
        "AND session_id=%s::uuid AND lower(owner_email)=%s FOR UPDATE",
        (_id(request_id), str(session['id']), session['owner_email'].lower()),
    ).fetchone()
    if not request:
        raise ValueError('The linked question must belong to this work item session')
    context['input_request_id'] = str(request['id'])
    return dict(request)


def link_work_items(conn, request_id):
    """A question can explicitly name alerts that were published before it."""
    request = conn.execute(
        "SELECT * FROM rvbbit.calliope_input_requests WHERE id=%s::uuid FOR UPDATE",
        (_id(request_id),),
    ).fetchone()
    if not request:
        return
    for item_id in request['request'].get('work_item_ids') or []:
        row = conn.execute(
            "UPDATE rvbbit.calliope_work_items SET context=jsonb_set(context,"
            "'{input_request_id}',to_jsonb(%s::text)),updated_at=clock_timestamp() "
            "WHERE id=%s::uuid AND lower(owner_email)=%s AND session_id=%s::uuid "
            "AND state IN ('unread','seen') RETURNING id",
            (str(request['id']), _id(item_id), request['owner_email'].lower(), str(request['session_id'])),
        ).fetchone()
        if not row:
            raise ValueError('Linked work items must be open and belong to this question session')


def blocking_request(conn, owner, session_id, client_turn_id=None, poster=None):
    """Read the canonical gate, including questions created without a browser.

    Call under the session lock before inserting a new human turn. Optional
    requests opt out explicitly; missing flags on existing open requests block.
    A knock card (kind join_request) blocks only the owner, who answers it:
    the rest of the room keeps talking.
    """
    row = conn.execute(
        "SELECT r.id FROM rvbbit.calliope_input_requests r "
        "WHERE session_id=%s::uuid AND lower(owner_email)=%s AND status='open' "
        "AND request->'blocking' IS DISTINCT FROM 'false'::jsonb "
        "AND (r.request->>'kind' IS DISTINCT FROM 'join_request' OR %s::text IS NULL "
        " OR lower(%s::text)=lower(r.owner_email)) "
        "AND NOT EXISTS (SELECT 1 FROM rvbbit.calliope_turns t WHERE t.session_id=r.session_id "
        "AND t.client_turn_id=%s::uuid) ORDER BY created_at,id LIMIT 1",
        (_id(session_id), owner.lower(), poster, poster, _id(client_turn_id) if client_turn_id else None),
    ).fetchone()
    return str(row['id']) if row else None


def validate_answers(request, answers):
    if not isinstance(answers, dict):
        raise ValueError('Answers must be an object')
    fields = request.get('fields') or []
    known = {f['key'] for f in fields}
    if set(answers) - known:
        raise ValueError('Unknown question field')
    cleaned = {}
    for field in fields:
        key, kind = field['key'], field.get('type', 'text')
        value = answers.get(key)
        if value is None or value == '':
            if field.get('required'):
                raise ValueError(f"{field['label']} is required")
            continue
        if kind == 'boolean':
            if not isinstance(value, bool):
                raise ValueError(f"{field['label']} must be yes or no")
        elif not isinstance(value, str):
            raise ValueError(f"{field['label']} must be text")
        elif kind in ('choice', 'select'):
            options = [o.get('value') if isinstance(o, dict) else o for o in field.get('options', [])]
            if value not in options:
                raise ValueError(f"Choose a listed option for {field['label']}")
        elif len(value) > 4000:
            raise ValueError(f"{field['label']} is too long")
        cleaned[key] = value
    if not cleaned:
        raise ValueError('Provide at least one answer')
    return cleaned


def answer_text(request, answers):
    lines = []
    for f in request['fields']:
        if f['key'] in answers:
            value = answers[f['key']]
            if isinstance(value, bool):
                value = 'Yes' if value else 'No'
            lines.append(f"- {f['label']}: {value}")
    text = f"Here are my answers for “{request.get('title', 'Calliope question')}”:\n" + '\n'.join(lines)
    if len(text) > 6000:
        raise ValueError('Combined answers are too long (maximum 6,000 characters)')
    return text


def change(conn_factory, owner, request_id, action, body):
    if action not in ('answer', 'cancel'):
        raise ValueError('Unknown action')
    revision = body.get('revision')
    if type(revision) is not int or revision < 1:
        raise ValueError('A question revision is required')
    with conn_factory() as conn:
        with conn.transaction():
            row = _row(conn, owner, request_id, lock=True)
            if row['archived'] or (row.get('channel') or 'web') != 'web':
                raise InputConflict('Continue this conversation in its original channel before answering.')
            if not row.get('may_answer'):
                raise PermissionError('Participation access is required to answer this question')
            join_request = (row['request'] or {}).get('kind') == 'join_request'
            if join_request and owner.lower() != str(row['owner_email']).lower() and conn.execute(
                "SELECT rvbbit.calliope_session_role(%s::uuid,%s) AS role", (row['session_id'], owner)
            ).fetchone()['role'] != 'manager':
                raise PermissionError("Only the chat's owner or a manager can let people in")
            answers = validate_answers(row['request'], body.get('answers')) if action == 'answer' else {}
            target = 'answered' if action == 'answer' else 'cancelled'
            # An identical retry receives the original receipt, including its
            # original delivery ID, even after another tab has refreshed.
            if row['status'] == target and (action == 'cancel' or row['answers'] == answers):
                resolve_work_items(conn, row)
                return _public(row)
            if row['status'] != 'open' or row['revision'] != revision:
                raise InputConflict('This question has changed or is already closed. Refresh to see its answer.')
            delivery = None
            if action == 'answer' and (row['archived'] or (row.get('channel') or 'web') != 'web'):
                raise InputConflict('Continue this conversation in its original channel before answering.')
            # A knock is decided here, not handed to Callie.
            if join_request:
                import calliope_rooms
                calliope_rooms.apply_knock_decision(conn, row, owner, answers, action)
            # Closing a required ask must tell the waiting lead what happened.
            # This shares the answer outbox and its idempotent transaction.
            elif not row['archived'] and (row.get('channel') or 'web') == 'web':
                text = answer_text(row['request'], answers) if action == 'answer' else (
                    f"I cancelled the request “{row['request'].get('title', 'Calliope question')}”. "
                    "No answer or approval was provided. Do not guess the missing answers or "
                    "perform work that depended on them. Acknowledge the cancellation."
                )
                delivery = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO rvbbit.calliope_agent_messages "
                    "(id,owner_email,kind,from_kind,from_ref,from_label,to_session_id,subject,body,payload) "
                    "VALUES (%s::uuid,%s,'note','system',%s,%s,%s::uuid,%s,%s,%s::jsonb)",
                    (delivery, owner.lower(), 'input_' + action + ':' + str(row['id']), owner.lower(),
                     str(row['session_id']), row['request'].get('title', 'Question resolved'), text,
                     json.dumps({'input_request_id': str(row['id']), 'action': action})),
                )
            conn.execute(
                "UPDATE rvbbit.calliope_input_requests SET status=%s,answers=%s::jsonb,revision=revision+1,"
                "answered_by=%s,answered_at=CASE WHEN %s='answered' THEN clock_timestamp() END,"
                "closed_by=%s,closed_at=clock_timestamp(),updated_at=clock_timestamp(),delivery_message_id=%s::uuid "
                "WHERE id=%s::uuid",
                (target, json.dumps(answers), owner.lower() if action == 'answer' else None, target,
                 owner.lower(), delivery, str(row['id'])),
            )
            resolved = _row(conn, owner, request_id)
            resolve_work_items(conn, resolved)
            # Room members were told too; once anyone answers, it's answered for all.
            import calliope_rooms
            calliope_rooms.resolve_notifications(conn, f"room-question:{resolved['id']}")
            return _public(resolved)


def delivery(conn_factory, message_id):
    """Only a persisted resolution's own outbox message can become a human turn."""
    with conn_factory() as conn:
        row = conn.execute(
            "SELECT r.id,coalesce(r.answered_by,r.closed_by) AS answered_by,r.status,m.body FROM rvbbit.calliope_input_requests r "
            "JOIN rvbbit.calliope_agent_messages m ON m.id=r.delivery_message_id "
            "JOIN rvbbit.calliope_sessions s ON s.id=r.session_id "
            "WHERE m.id=%s::uuid AND r.status IN ('answered','cancelled') AND m.to_session_id=r.session_id "
            "AND lower(s.owner_email)=lower(r.owner_email) AND lower(m.owner_email)=lower(coalesce(r.answered_by,r.closed_by))",
            (_id(message_id),),
        ).fetchone()
        return dict(row) if row else None
