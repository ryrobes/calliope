"""Cached Floor labels plus one bounded background refresh per server process."""
import logging
import threading
import time

log = logging.getLogger(__name__)
CACHED_SQL = """
SELECT c.item_id,p.semantic_title,p.semantic_status
FROM rvbbit.floor_presentation_candidates(%s) c
JOIN rvbbit.floor_presentations p USING(item_id)
WHERE p.source_hash=c.source_hash AND p.semantic_title IS NOT NULL
"""
PENDING_SQL = """
SELECT c.item_id,c.source_text,c.source_hash
FROM rvbbit.floor_presentation_candidates(%s) c
LEFT JOIN rvbbit.floor_presentations p USING(item_id)
WHERE p.source_hash IS DISTINCT FROM c.source_hash
   OR (p.semantic_title IS NULL AND coalesce(p.retry_after,'-infinity')<=now())
ORDER BY CASE c.column_state WHEN 'awaiting_you' THEN 0 WHEN 'in_motion' THEN 1 ELSE 2 END,
         c.lane_rank,c.item_id LIMIT 50
"""


def read_thread(conn_factory, session_id, owner=None):
    """Read saved labels for an already-authorized chat; never start inference.

    Keep the previous saved wording while its replacement is being generated.
    This is deliberately a point lookup, not a scan of the Floor work graph.
    """
    try:
        with conn_factory() as conn:
            conn.execute("SET statement_timeout = 750")
            row = conn.execute("""
                SELECT CASE WHEN s.title_source = 'manual' THEN s.title
                       ELSE coalesce(nullif(btrim(p.semantic_title), ''), s.title) END AS title,
                       coalesce(left(nullif(btrim(sn.synopsis), ''), 700),
                                nullif(btrim(p.semantic_status), ''), '') AS summary
                FROM rvbbit.calliope_sessions s
                LEFT JOIN rvbbit.floor_presentations p ON p.item_id = 'chat:' || s.id::text
                LEFT JOIN rvbbit.calliope_session_synopses sn ON sn.session_id = s.id
                WHERE s.id = %s::uuid
            """, (str(session_id),)).fetchone()
            if row and owner is not None:
                row = dict(row)
                row['favorite'] = conn.execute(
                    'SELECT EXISTS(SELECT 1 FROM rvbbit.calliope_session_favorites '
                    'WHERE owner_email=lower(%s) AND session_id=%s::uuid) AS favorite',
                    (owner,str(session_id))).fetchone()['favorite']
            return dict(row) if row else None
    except Exception:
        # Optional decoration must not keep the conversation from loading.
        log.debug('Chat presentation cache unavailable', exc_info=True)
        return None


class Refresher:
    def __init__(self, conn_factory):
        self.conn_factory = conn_factory
        self.lock = threading.Lock()
        self.retry_at = 0

    def read(self, owner):
        """Return promptly; inference only runs in the daemon or pg_cron."""
        try:
            with self.conn_factory() as conn:
                conn.execute("SET statement_timeout = 5000")
                titles = [dict(r) for r in conn.execute(CACHED_SQL, (owner,)).fetchall()]
                pending = [dict(r) for r in conn.execute(PENDING_SQL, (owner,)).fetchall()]
        except Exception:
            log.warning('Floor presentation cache unavailable', exc_info=True)
            return {'titles': [], 'pending': False}
        if pending and time.monotonic() >= self.retry_at and self.lock.acquire(blocking=False):
            try:
                threading.Thread(target=self._refresh, args=(pending,),
                                 name='floor-presentations', daemon=True).start()
            except Exception:
                self.lock.release()
                raise
        return {'titles': titles, 'pending': bool(pending) and time.monotonic() >= self.retry_at}

    def _refresh(self, candidates):
        started = time.monotonic()
        try:
            for row in candidates:
                # Separate transactions preserve progress and release all SQL locks
                # after each item. Shared SQL locks deduplicate the cron warmer.
                with self.conn_factory() as conn:
                    conn.execute('SET statement_timeout = 650000')
                    conn.execute('SET lock_timeout = 1000')
                    conn.execute('SELECT rvbbit.floor_presentation_refresh(%s,%s,%s)',
                                 (row['item_id'], row['source_text'], row['source_hash']))
                if time.monotonic() - started >= 300:
                    break
        except Exception:
            self.retry_at = time.monotonic() + 300
            log.warning('Floor presentation refresh deferred after failure', exc_info=True)
        finally:
            self.lock.release()


# Also applied during service bootstrap so server-only upgrades cover existing DBs.
NOTIFICATION_LABEL_POLICY_SQL = r"""
-- Inbox rows and run receipts already carry authored titles. Never spend
-- inference rewriting them, even when an old worker queued one before upgrade.
DO $policy$
DECLARE candidate_def text; refresh_def text;
BEGIN
    IF to_regprocedure('rvbbit.floor_presentation_candidates(text)') IS NOT NULL THEN
        candidate_def := pg_get_functiondef('rvbbit.floor_presentation_candidates(text)'::regprocedure);
        IF position('floor-authored-labels' in candidate_def)=0 THEN
            candidate_def := replace(candidate_def, 'FROM rvbbit.work_graph w',
                'FROM (SELECT * FROM rvbbit.work_graph WHERE item_kind NOT IN (''work_item'',''dream'',''workflow_run'')) w /* floor-authored-labels */');
            IF position('floor-authored-labels' in candidate_def)=0 THEN
                RAISE EXCEPTION 'Unrecognized Floor presentation candidate query';
            END IF;
            EXECUTE candidate_def;
        END IF;
    END IF;
    IF to_regprocedure('rvbbit.floor_presentation_refresh(text,text,text)') IS NOT NULL THEN
        refresh_def := pg_get_functiondef('rvbbit.floor_presentation_refresh(text,text,text)'::regprocedure);
        IF position('floor-authored-labels' in refresh_def)=0 THEN
            refresh_def := replace(refresh_def, E'BEGIN\n', E'BEGIN\n    -- floor-authored-labels: reject stale browser/cron queue entries too.\n    IF split_part(p_item_id,'':'',1) IN (''work_item'',''dream'',''curated_dream'',''workflow'',''workflow_run'') THEN RETURN false; END IF;\n');
            IF position('floor-authored-labels' in refresh_def)=0 THEN
                RAISE EXCEPTION 'Unrecognized Floor presentation refresh function';
            END IF;
            EXECUTE refresh_def;
        END IF;
    END IF;
END $policy$;
"""
