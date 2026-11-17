"""Durable corrections: steer active work, batch anything left at round boundaries.

The thread row owns its inbox. Enqueue and round creation share its row lock;
claimed requests move into the run ledger in the same transaction. No schema
change is needed, and original assignments are never overwritten.
"""
import json
import uuid
from datetime import datetime, timezone

QUEUE = 'pending_followups'
RECEIPT = 'followup_id'


def request_id(thread_id, source_turn_id, message, context):
    body = json.dumps([str(thread_id), str(source_turn_id), message, context or {}],
                      sort_keys=True, default=str, separators=(',', ':'))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'calliope-followup:' + body))


def receipt(item, *, run=None):
    if item.get('bound_run_id'):
        return {'status': item.get('steer_state', 'queued'), 'accepted': True,
                'delivered': item.get('steer_state') == 'delivered',
                'followup_id': item['id'], 'thread_id': item['thread_id'],
                'run_id': item['bound_run_id'], 'followup_started': False, 'error': item.get('delivery_error'),
                'next': 'Finish guidance targets only this run and will never start another round. '
                        'Read or await this exact run_id for the saved answer.'}
    if item.get('steer_run_id'):
        return {'status':item.get('steer_state','steering'),'accepted':True,
                'delivered':item.get('steer_state')=='delivered',
                'followup_id':item['id'],'thread_id':item['thread_id'],
                'run_id':item['steer_run_id'],'error':item.get('delivery_error'),
                'next':('Guidance reached the current round transcript. Inspect the result before claiming it was applied.'
                        if item.get('steer_state')=='delivered' else
                        'Guidance is being delivered during the current round. Do not resend or claim it was applied; inspect the specialist result.')}
    delivered = bool(run and run.get('hermes_run_id'))
    status = str(run['status']) if run else 'queued'
    if status in {'failed', 'cancelled'}:
        next_step = 'The correction round ended without a successful result. Inspect its error and thread before retrying; do not claim it was applied.'
    elif delivered:
        next_step = 'The correction has its own round. Check its result before claiming it was applied.'
    elif run:
        next_step = 'A round is reserved for the correction, but Hermes delivery is not confirmed yet. Do not resend or claim it is applied.'
    else:
        next_step = ('Correction saved for steering at the next tool boundary, or one combined continuation if the round ends first. It has not reached the specialist yet; '
                     'do not claim it is applied or resend it. Its reply will return automatically.')
    return {'status': status, 'accepted': True, 'delivered': delivered,
            'followup_id': item['id'], 'thread_id': item['thread_id'],
            'run_id': str(run['id']) if run else None,
            'error': str(run.get('error') or '')[:2000] if run else None,
            'next': next_step}


def enqueue(conn, thread_id, source, message, context, key, *, bound_run_id=None):
    """Caller holds the thread lock and has authorized this notebook/owner."""
    thread = conn.execute('SELECT context FROM rvbbit.calliope_specialist_threads WHERE id=%s::uuid FOR UPDATE',
                          (thread_id,)).fetchone()
    prior = conn.execute("SELECT id,status,hermes_run_id,error FROM rvbbit.calliope_specialist_runs "
                         "WHERE thread_id=%s::uuid AND (context->>'followup_id'=%s OR context->'followup_ids' ? %s) LIMIT 1",
                         (thread_id, key, key)).fetchone()
    item = {'id': key, 'thread_id': str(thread_id)}
    if prior:
        return receipt(item, run=prior)
    for prior_steer in (thread.get('context') or {}).get('steered_followups', []):
        if prior_steer['id'] == key: return receipt(prior_steer)
    inbox = list((thread.get('context') or {}).get(QUEUE) or [])
    for existing in inbox:
        if existing['id'] == key:
            return receipt(existing)
    if len(inbox) >= 32:
        raise ValueError('This specialist already has 32 queued follow-ups; wait for delivery before adding more')
    item.update(source_turn_id=str(source['turn_id']), message=message, context=context,
                created_at=datetime.now(timezone.utc).isoformat())
    if bound_run_id:
        item['bound_run_id'] = str(bound_run_id)
    inbox.append(item)
    conn.execute("UPDATE rvbbit.calliope_specialist_threads SET context=jsonb_set(context,"
                 "'{pending_followups}',%s::jsonb),updated_at=clock_timestamp() WHERE id=%s::uuid",
                 (json.dumps(inbox, default=str), thread_id))
    return receipt(item)


def claim(conn, runner_id, limit=4):
    """Atomically batch pending corrections on an idle thread into one run."""
    claimed = []
    with conn.transaction():
        threads = conn.execute("""
            SELECT t.* FROM rvbbit.calliope_specialist_threads t
            JOIN rvbbit.calliope_sessions n ON n.id=t.source_session_id
            JOIN rvbbit.calliope_specialists s ON s.id=t.specialist_id
            WHERE t.status='open' AND s.status='active' AND NOT n.archived
              AND rvbbit.calliope_session_can_participate(n.id,t.owner_email)
              AND jsonb_array_length(coalesce(t.context->'pending_followups','[]'::jsonb))>0
              AND EXISTS (SELECT 1 FROM rvbbit.calliope_turns source
                          WHERE source.id::text=t.context->'pending_followups'->0->>'source_turn_id'
                            AND source.session_id=t.source_session_id AND source.status<>'cancelled')
              AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(t.context->'pending_followups') f WHERE f ? 'steer_run_id')
              AND NOT EXISTS (SELECT 1 FROM rvbbit.calliope_specialist_runs r
                              WHERE r.thread_id=t.id AND r.status IN ('queued','running'))
            ORDER BY t.updated_at LIMIT %s FOR UPDATE OF t SKIP LOCKED
        """, (limit,)).fetchall()
        for thread in threads:
            # Finish-current-round guidance must never become a continuation.
            inbox = [x for x in thread['context'][QUEUE] if not x.get('bound_run_id')]
            if not inbox:
                continue
            item = inbox[0]
            source = conn.execute("SELECT id FROM rvbbit.calliope_turns WHERE id=%s::uuid AND session_id=%s::uuid AND status<>'cancelled'",
                                  (item['source_turn_id'], thread['source_session_id'])).fetchone()
            if not source:
                continue  # Keep an undeliverable request visible; never discard it.
            rid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'calliope-followup-run:' + item['id']))
            valid=[]
            for candidate in inbox:
                if conn.execute("SELECT id FROM rvbbit.calliope_turns WHERE id=%s::uuid AND session_id=%s::uuid AND status<>'cancelled'",
                                (candidate['source_turn_id'],thread['source_session_id'])).fetchone(): valid.append(candidate)
            context = {**item['context'], RECEIPT: item['id'], 'followup_ids':[x['id'] for x in valid]}
            message = item['message'] if len(valid)==1 else batch_text(valid)
            if len(valid)>1: context['followup_contexts']=[{'id':x['id'],'source_turn_id':x['source_turn_id'],
                'provided':x['context'].get('provided')} for x in valid]
            remaining=[x for x in thread['context'][QUEUE] if x not in valid]
            round_no = int(thread['round_count']) + 1
            conn.execute("""
                INSERT INTO rvbbit.calliope_specialist_runs
                  (id,specialist_id,owner_email,source_session_id,source_turn_id,trigger_kind,
                   hermes_session_id,status,task,context,started_at,runner_id,runner_attempt,
                   heartbeat_at,lease_expires_at,thread_id,round_no,delivery)
                VALUES (%s,%s,%s,%s,%s,'delegated',%s,'running',%s,%s::jsonb,
                        clock_timestamp(),%s,1,clock_timestamp(),clock_timestamp()+interval '60 seconds',%s,%s,'mailbox')
            """, (rid, thread['specialist_id'], thread['owner_email'], thread['source_session_id'],
                  item['source_turn_id'], thread['hermes_session_id'], message,
                  json.dumps(context, default=str), runner_id, thread['id'], round_no))
            conn.execute("INSERT INTO rvbbit.calliope_specialist_messages (run_id,ordinal,role,kind,body,payload) "
                         "VALUES (%s,1,'calliope','handoff',%s,%s::jsonb)",
                         (rid, message, json.dumps({'followup_id': item['id'], 'context': context}, default=str)))
            conn.execute("UPDATE rvbbit.calliope_specialist_threads SET round_count=%s,last_run_id=%s,"
                         "context=jsonb_set(context,'{pending_followups}',%s::jsonb),"
                         "updated_at=clock_timestamp() WHERE id=%s",
                         (round_no, rid, json.dumps(remaining, default=str), thread['id']))
            claimed.append({'id': rid, 'runner_id': runner_id})
    return claimed


def batch_text(items):
    return ("Corrections to the current assignment, in order. Later guidance supersedes earlier guidance "
            "where they conflict. Keep completed work that remains valid; update only what these corrections require.\n\n" +
            "\n\n".join(steer_text(x) for x in items))


def steer_text(item):
    # The marker makes terminal pending_steer evidence unambiguous. The new
    # guidance carries its explicit context, not another copy of the parent history.
    text=f"[calliope-followup:{item['id']}]\n{item['message']}"
    provided=(item.get('context') or {}).get('provided')
    if provided: text+='\nContext: '+json.dumps(provided,default=str)
    assignment=(item.get('context') or {}).get('assignment')
    if assignment is not None: text+='\nAssignment boundaries: '+json.dumps(assignment,default=str)
    return text


def _save_delivery(conn, thread_id, item, *, delivered=False, ended=False):
    with conn.transaction():
        row=conn.execute('SELECT context FROM rvbbit.calliope_specialist_threads WHERE id=%s FOR UPDATE',(thread_id,)).fetchone()
        context=dict(row['context']);inbox=context.get(QUEUE,[])
        if not any(x['id']==item['id'] for x in inbox): return
        context[QUEUE]=[x if x['id']!=item['id'] else item for x in inbox] if not (delivered or ended) else [x for x in inbox if x['id']!=item['id']]
        if delivered or ended:
            item={**item,'steer_state':'delivered' if delivered else 'round_ended',
                  'settled_at':datetime.now(timezone.utc).isoformat()}
            if delivered: item['delivered_at'] = item['settled_at']
            context.setdefault('steered_followups',[]).append(item)
        conn.execute('UPDATE rvbbit.calliope_specialist_threads SET context=%s::jsonb,updated_at=clock_timestamp() WHERE id=%s',
                     (json.dumps(context,default=str),thread_id))


async def steer_pending(conn_factory, request, limit=32):
    """Deliver to the exact owned native run; keep uncertain deliveries visible.

    `request(method,path,body)` returns (HTTP status, decoded JSON). A session
    advisory lock serializes workers across HTTP without holding a row lock.
    The attempt is committed before transport. Native request IDs deduplicate
    retries including a lost HTTP reply. Terminal undelivered text is replayed
    only when the native run proves it was not consumed.
    """
    from urllib.parse import quote
    with conn_factory() as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(hashtext('calliope.specialist_steering')) AS ok").fetchone()['ok']: return 0
        try:
            threads=conn.execute("""SELECT t.*,s.profile_name FROM rvbbit.calliope_specialist_threads t
              JOIN rvbbit.calliope_sessions n ON n.id=t.source_session_id
              JOIN rvbbit.calliope_specialists s ON s.id=t.specialist_id
              WHERE t.status='open' AND s.status='active' AND NOT n.archived
              AND rvbbit.calliope_session_can_participate(n.id,t.owner_email)
              AND jsonb_array_length(coalesce(t.context->'pending_followups','[]'::jsonb))>0
              ORDER BY t.updated_at LIMIT 16""").fetchall()
            processed=0
            for thread in threads:
                for original in thread['context'][QUEUE]:
                    if processed>=limit: return processed
                    item=dict(original)
                    source=conn.execute("SELECT id FROM rvbbit.calliope_turns WHERE id=%s::uuid AND session_id=%s AND status<>'cancelled'",
                        (item['source_turn_id'],thread['source_session_id'])).fetchone()
                    if not source: continue
                    if item.get('bound_run_id') or item.get('steer_run_id'):
                        run=conn.execute('SELECT * FROM rvbbit.calliope_specialist_runs WHERE id=%s AND thread_id=%s',
                            (item.get('bound_run_id') or item['steer_run_id'],thread['id'])).fetchone()
                    else:
                        run=conn.execute("SELECT * FROM rvbbit.calliope_specialist_runs WHERE thread_id=%s AND status='running' AND hermes_run_id IS NOT NULL ORDER BY round_no DESC LIMIT 1",
                            (thread['id'],)).fetchone()
                    if not run: continue
                    if item.get('bound_run_id') and run['status'] in ('complete', 'failed', 'cancelled'):
                        _save_delivery(conn, thread['id'], item, ended=True)
                        continue
                    if not run.get('hermes_run_id'): continue
                    path=f"/p/{quote(thread['profile_name'],safe='')}/v1/runs/{quote(run['hermes_run_id'],safe='')}"
                    processed+=1
                    try:
                        code,state=await request('GET',path,None)
                        if code!=200: raise RuntimeError('Native run status unavailable')
                        accepted=item['id'] in (state.get('steer_receipts') or {})
                        if state.get('status') in ('completed','failed','cancelled','interrupted'):
                            if accepted and state.get('status')=='completed' and f"[calliope-followup:{item['id']}]" not in str(state.get('pending_steer') or ''):
                                _save_delivery(conn,thread['id'],item,delivered=True)
                            elif not accepted or f"[calliope-followup:{item['id']}]" in str(state.get('pending_steer') or ''):
                                for key in ('steer_run_id','steer_state','delivery_error'): item.pop(key,None)
                                _save_delivery(conn,thread['id'],item,ended=bool(item.get('bound_run_id')))
                            else:
                                item['delivery_error']='Native run interrupted after accepting guidance; delivery needs review before replay'
                                _save_delivery(conn,thread['id'],item)
                            continue
                        if accepted:
                            item.update(steer_run_id=str(run['id']),steer_state='steering')
                            item.pop('delivery_error',None);_save_delivery(conn,thread['id'],item)
                            continue
                        if state.get('status')!='running': continue
                        item.update(steer_run_id=str(run['id']),steer_state='sending')
                        _save_delivery(conn,thread['id'],item)  # Commit before transport.
                        code,result=await request('POST',path+'/steer',{'request_id':item['id'],'input':steer_text(item)})
                        if code==200 and result.get('accepted'):
                            item['steer_state']='steering';item.pop('delivery_error',None)
                        elif code==409 and (result.get('error') or {}).get('code') in ('run_not_accepting_steer','steer_not_accepted','steer_receipt_limit'):
                            for key in ('steer_run_id','steer_state','delivery_error'): item.pop(key,None)
                        else: item['delivery_error']='Steering transport unconfirmed; retrying the same request ID'
                        _save_delivery(conn,thread['id'],item)
                    except Exception:
                        item['delivery_error']='Native steering temporarily unavailable; guidance retained for retry'
                        _save_delivery(conn,thread['id'],item)
            return processed
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext('calliope.specialist_steering'))")


def settle_from_transcript(conn, run_id, messages):
    """A persisted native user row proves delivery before publishing the reply."""
    row=conn.execute('SELECT thread_id FROM rvbbit.calliope_specialist_runs WHERE id=%s',(run_id,)).fetchone()
    if not row or not row['thread_id']: return
    thread=conn.execute('SELECT context FROM rvbbit.calliope_specialist_threads WHERE id=%s',(row['thread_id'],)).fetchone()
    texts=[str(m.get('content') or '') for m in messages if m.get('role')=='user']
    for item in (thread['context'] or {}).get(QUEUE,[]):
        if item.get('steer_run_id')==str(run_id) and any(f"[calliope-followup:{item['id']}]" in text for text in texts):
            _save_delivery(conn,row['thread_id'],item,delivered=True)


UNREAD_RESULT_SQL = """r.delivery='mailbox' AND r.status IN ('complete','failed','cancelled')
    AND NOT (coalesce(r.context,'{}'::jsonb) ? 'awaited_delivery')
    AND NOT EXISTS (SELECT 1 FROM rvbbit.calliope_agent_messages m
                    WHERE m.run_id=r.id AND m.kind IN ('reply','failed')
                      AND m.status IN ('delivered','delivering','cancelled'))"""


def unread_result(conn, thread_id):
    """Caller already holds the owned thread lock. Publication may still be pending."""
    return conn.execute(
        "SELECT r.* FROM rvbbit.calliope_specialist_runs r WHERE r.thread_id=%s::uuid AND "
        + UNREAD_RESULT_SQL + " ORDER BY r.round_no LIMIT 1", (thread_id,)
    ).fetchone()


def saved_result(run):
    return {'status': run['status'], 'run_id': str(run['id']),
            'thread_id': str(run['thread_id']), 'round': run['round_no'],
            'response': str(run.get('result') or ''), 'error': run.get('error'),
            'followup_started': False, 'needs_result_read': True,
            'next': 'This round has already ended. Read its saved answer with '
                    'read_calliope_specialist_result(run_id), including any remaining pages. '
                    'No new work was started. After reading, send a new continuation only if still needed.'}
