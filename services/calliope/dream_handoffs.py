"""One explicitly requested exploration thread per owner and Dream.

Claims never hold a database transaction across Hermes/network operations.
The existing agent mailbox owns execution, retries, and tool-call receipts.
"""
import json
from uuid import uuid4

DDL = """
CREATE TABLE IF NOT EXISTS rvbbit.calliope_dream_handoffs (
    dream_id uuid NOT NULL REFERENCES rvbbit.calliope_dreams(id) ON DELETE CASCADE,
    owner_email text NOT NULL,
    claim_token uuid NOT NULL,
    claimed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    session_id uuid REFERENCES rvbbit.calliope_sessions(id) ON DELETE CASCADE,
    surface_id uuid REFERENCES rvbbit.calliope_surfaces(id) ON DELETE SET NULL,
    message_id uuid,
    PRIMARY KEY(dream_id,owner_email)
);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_dream_handoff_session_idx
    ON rvbbit.calliope_dream_handoffs(session_id) WHERE session_id IS NOT NULL;

-- Recover provenance for older interactive handoffs. This links existing work;
-- it never starts a turn. When several old threads exist, reuse the newest.
INSERT INTO rvbbit.calliope_dream_handoffs
    (dream_id,owner_email,claim_token,claimed_at,session_id,surface_id)
SELECT DISTINCT ON (d.id,s.owner_email)
    d.id,s.owner_email,gen_random_uuid(),su.created_at,s.id,su.id
FROM rvbbit.calliope_surfaces su
JOIN rvbbit.calliope_sessions s ON s.id=su.session_id
JOIN rvbbit.calliope_dreams d ON d.id::text=su.source->>'dream_id'
WHERE su.kind='dream' AND su.tool_name='calliope_dream_handoff'
    AND s.owner_email IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM rvbbit.calliope_dream_handoffs h
        WHERE h.session_id=s.id OR (h.dream_id=d.id AND h.owner_email=s.owner_email))
ORDER BY d.id,s.owner_email,su.created_at DESC,su.id DESC
ON CONFLICT DO NOTHING;
"""


def claim(conn_factory, owner, dream_id):
    """Caller has checked Dream access. A lease bounds failed preparation."""
    token=str(uuid4())
    with conn_factory() as c:
        row=c.execute("""INSERT INTO rvbbit.calliope_dream_handoffs(dream_id,owner_email,claim_token)
            VALUES(%s::uuid,%s,%s::uuid) ON CONFLICT(dream_id,owner_email) DO UPDATE
            SET claim_token=excluded.claim_token,claimed_at=clock_timestamp()
            WHERE calliope_dream_handoffs.session_id IS NULL
            AND calliope_dream_handoffs.claimed_at<clock_timestamp()-interval '5 minutes'
            RETURNING *""",(dream_id,owner,token)).fetchone()
        if row:return {'claimed':True,**dict(row)}
        row=c.execute('SELECT * FROM rvbbit.calliope_dream_handoffs WHERE dream_id=%s::uuid AND owner_email=%s',(dream_id,owner)).fetchone()
        return {'claimed':False,**dict(row or {})}


def release(conn_factory, owner, dream_id, token):
    with conn_factory() as c:
        c.execute('DELETE FROM rvbbit.calliope_dream_handoffs WHERE dream_id=%s::uuid AND owner_email=%s AND claim_token=%s::uuid AND session_id IS NULL',(dream_id,owner,token))


def launch(conn_factory, owner, dream_id, token, session_id, surface_id, title, prompt):
    """Attach the notebook and queue its first turn atomically; no inbox item."""
    with conn_factory() as c:
        with c.transaction():
            linked=c.execute('''UPDATE rvbbit.calliope_dream_handoffs SET session_id=%s::uuid,surface_id=%s::uuid
                WHERE dream_id=%s::uuid AND owner_email=%s AND claim_token=%s::uuid AND session_id IS NULL RETURNING dream_id''',
                (session_id,surface_id,dream_id,owner,token)).fetchone()
            if not linked:raise RuntimeError('This Dream launch was already claimed; no duplicate work was queued.')
            message=c.execute('''INSERT INTO rvbbit.calliope_agent_messages
                (owner_email,kind,from_kind,from_ref,from_label,to_session_id,subject,body,payload)
                VALUES(%s,'note','system',%s,'Dream from the Floor',%s::uuid,%s,%s,%s::jsonb) RETURNING id''',
                (owner,'dream:'+str(dream_id),session_id,title,prompt,json.dumps({'dream_engage':True,'dream_id':str(dream_id),'surface_id':str(surface_id)}))).fetchone()
            c.execute('UPDATE rvbbit.calliope_dream_handoffs SET message_id=%s::uuid WHERE dream_id=%s::uuid AND owner_email=%s',(message['id'],dream_id,owner))
            return dict(message)
