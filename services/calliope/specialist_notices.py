"""Notify a busy parent about saved answers without consuming mailbox delivery.

Hermes accepts idempotent steering at tool boundaries. Acceptance is not proof
of reading: only the result reader/await acknowledges the original reply. If
the parent exits before reading, the normal idle mailbox remains the fallback.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote


def notice_text(row):
    return (
        '[INTERNAL AGENT RESULT NOTICE — not a human request]\n'
        f"Specialist round {row['round_no']} has ended. Its saved answer is available now.\n"
        f"run_id={row['run_id']} thread_id={row['thread_id']}\n"
        'Read read_calliope_specialist_result with that exact run_id (follow next_offset for all pages), '
        'or await_calliope_specialists with run_ids. Do not ask the specialist to repeat or '
        'finish this round; it is already finished. Continue only after reviewing the answer.'
    )


async def notify_active_parents(conn_factory, request, limit=4):
    """Bounded, serialized notification pass; request returns (status, JSON)."""
    with conn_factory() as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(hashtext('calliope.specialist_notices')) AS ok").fetchone()['ok']:
            return 0
        try:
            rows = conn.execute("""
                SELECT m.id,m.payload,m.run_id,r.thread_id,r.round_no,
                       t.hermes_run_id AS parent_run_id,p.profile_name
                FROM rvbbit.calliope_agent_messages m
                JOIN rvbbit.calliope_specialist_runs r ON r.id=m.run_id
                JOIN rvbbit.calliope_sessions s ON s.id=m.to_session_id
                JOIN rvbbit.calliope_turns t ON t.session_id=s.id AND t.status='running'
                LEFT JOIN rvbbit.calliope_specialists p ON p.specialist_key=s.specialist_key
                WHERE m.status='queued' AND m.kind IN ('reply','failed') AND m.from_kind='specialist'
                  AND m.not_before<=clock_timestamp() AND NOT s.archived
                  AND r.source_session_id=s.id AND r.delivery='mailbox'
                  AND r.status IN ('complete','failed','cancelled')
                  AND lower(m.owner_email)=lower(s.owner_email)
                  AND lower(r.owner_email)=lower(s.owner_email)
                  AND lower(t.author_email)=lower(s.owner_email)
                  AND t.hermes_run_id IS NOT NULL
                  AND (s.specialist_key IS NULL OR p.profile_name IS NOT NULL)
                  AND (m.payload->'active_notice'->>'parent_run_id' IS DISTINCT FROM t.hermes_run_id
                       OR coalesce(m.payload->'active_notice'->>'accepted','false')<>'true')
                ORDER BY m.created_at LIMIT 64
            """).fetchall()
            processed = 0
            for row in rows:
                previous = (row.get('payload') or {}).get('active_notice') or {}
                if previous.get('parent_run_id') == row['parent_run_id']:
                    if previous.get('accepted'):
                        continue
                    if previous.get('retry_after') and datetime.fromisoformat(previous['retry_after']) > datetime.now(timezone.utc):
                        continue
                if processed >= limit:
                    break
                processed += 1
                key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"calliope-result:{row['id']}:{row['parent_run_id']}"))
                profile = row.get('profile_name')
                path = (f"/p/{quote(profile, safe='')}" if profile else '')
                path += f"/v1/runs/{quote(row['parent_run_id'], safe='')}/steer"
                receipt = {'parent_run_id': row['parent_run_id'], 'request_id': key,
                           'accepted': False, 'attempted_at': datetime.now(timezone.utc).isoformat()}
                try:
                    code, result = await request('POST', path, {'request_id': key, 'input': notice_text(row)})
                    receipt['accepted'] = code == 200 and result.get('accepted') is True
                    receipt['http_status'] = code
                except Exception:
                    receipt['error'] = 'Notification transport unconfirmed; same request ID will be retried'
                receipt['retry_after'] = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
                # Merge only our metadata; never overwrite a simultaneous reader ack.
                conn.execute("UPDATE rvbbit.calliope_agent_messages SET payload=jsonb_set(payload,"
                             "'{active_notice}',%s::jsonb) WHERE id=%s AND status='queued'",
                             (json.dumps(receipt), row['id']))
            return processed
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext('calliope.specialist_notices'))")
