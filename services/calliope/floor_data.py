"""Bounded Floor pages over the complete authorized live or historical graph."""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from collections import OrderedDict
from datetime import datetime

LANES = ('awaiting_you', 'in_motion', 'standing', 'done', 'noticed', 'archived')


# Retire attention, never the underlying work or its evidence. These rules use
# source identities and terminal state; prose similarity is not a lifecycle.
ATTENTION_RETIREMENTS_SQL = """
WITH owned AS MATERIALIZED (
    SELECT * FROM rvbbit.calliope_work_items
    WHERE lower(owner_email)=lower(%s) AND state IN ('unread','seen')
), reasons AS (
    SELECT id, 'routine_receipt' AS reason FROM owned
    WHERE (source='hermes' AND kind<>'blocked')
       OR (source='calliope_playbook' AND kind='result')
    UNION ALL
    SELECT id, 'expired' AS reason FROM owned
    WHERE expires_at IS NOT NULL AND expires_at<=now()
    UNION ALL
    SELECT wi.id, 'turn_resumed_or_superseded' FROM owned wi
    JOIN rvbbit.calliope_turns t ON t.id::text=wi.source_ref AND t.session_id=wi.session_id
    WHERE wi.source='calliope_turn' AND (
        t.status='complete' OR t.error='Stopped by you.'
        OR coalesce(t.completed_at,t.created_at)<now()-interval '3 days'
        OR EXISTS (SELECT 1 FROM rvbbit.calliope_turns later
            WHERE later.session_id=t.session_id AND later.ordinal>t.ordinal
            AND later.status='complete')
        OR EXISTS (SELECT 1 FROM rvbbit.calliope_work_items newer
            JOIN rvbbit.calliope_turns nt ON nt.id::text=newer.source_ref
            WHERE newer.owner_email=wi.owner_email AND newer.source='calliope_turn'
            AND newer.session_id=wi.session_id AND nt.ordinal>t.ordinal)
        OR EXISTS (SELECT 1 FROM rvbbit.calliope_workflow_runs wr WHERE wr.session_id=t.session_id)
        OR EXISTS (SELECT 1 FROM rvbbit.calliope_work_order_runs wr WHERE wr.session_id=t.session_id)
    )
    UNION ALL
    SELECT wi.id, 'interactive_run_receipt' FROM owned wi
    JOIN rvbbit.calliope_workflow_runs r ON r.id::text=wi.source_ref
    WHERE wi.source='calliope_workflow' AND wi.kind='result' AND r.trigger_kind<>'scheduled'
    UNION ALL
    SELECT wi.id, CASE WHEN w.archived OR NOT w.schedule_enabled
        OR w.scheduled_version IS DISTINCT FROM r.workflow_version
        THEN 'workflow_schedule_inactive' ELSE 'newer_workflow_result' END
    FROM owned wi JOIN rvbbit.calliope_workflow_runs r ON r.id::text=wi.source_ref
    JOIN rvbbit.calliope_workflows w ON w.id=r.workflow_id
    WHERE wi.source='calliope_workflow' AND r.trigger_kind='scheduled' AND (
        w.archived OR NOT w.schedule_enabled OR w.scheduled_version IS DISTINCT FROM r.workflow_version
        OR EXISTS (SELECT 1 FROM rvbbit.calliope_workflow_runs newer
            WHERE newer.workflow_id=r.workflow_id AND newer.owner_email=r.owner_email
            AND newer.trigger_kind='scheduled' AND newer.status<>'running'
            AND (newer.started_at,newer.id)>(r.started_at,r.id))
    )
    UNION ALL
    SELECT wi.id, CASE WHEN w.status IN ('paused','cancelled')
        OR w.definition_version<>r.definition_version THEN 'assignment_inactive'
        ELSE 'newer_assignment_result' END
    FROM owned wi JOIN rvbbit.calliope_work_order_runs r ON r.id::text=wi.source_ref
    JOIN rvbbit.calliope_work_orders w ON w.id=r.work_order_id
    WHERE wi.source='calliope_work_order' AND (
        w.status IN ('paused','cancelled') OR w.definition_version<>r.definition_version
        OR EXISTS (SELECT 1 FROM rvbbit.calliope_work_order_runs newer
            WHERE newer.work_order_id=r.work_order_id AND newer.status<>'running'
            AND (newer.started_at,newer.id)>(r.started_at,r.id))
    )
)
SELECT DISTINCT ON (id) id, reason FROM reasons ORDER BY id,reason
"""


FAVORITES_DDL = """
CREATE TABLE IF NOT EXISTS rvbbit.calliope_session_favorites (
    owner_email text NOT NULL,
    session_id uuid NOT NULL REFERENCES rvbbit.calliope_sessions(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_email, session_id)
);
"""


def favorite_sessions(conn_factory, owner):
    with conn_factory() as conn:
        return [str(r['session_id']) for r in conn.execute(
            'SELECT session_id FROM rvbbit.calliope_session_favorites WHERE owner_email=lower(%s)',
            (owner,)).fetchall()]


def set_favorite(conn_factory, owner, session_id, favorite):
    import calliope_access
    if type(favorite) is not bool:
        raise ValueError('Favorite must be true or false.')
    with conn_factory() as conn, conn.transaction():
        session = calliope_access.require_view(conn, session_id, owner)
        if favorite:
            conn.execute('INSERT INTO rvbbit.calliope_session_favorites(owner_email,session_id) '
                         'VALUES(lower(%s),%s::uuid) ON CONFLICT DO NOTHING', (owner,str(session['id'])))
        else:
            conn.execute('DELETE FROM rvbbit.calliope_session_favorites '
                         'WHERE owner_email=lower(%s) AND session_id=%s::uuid', (owner,str(session['id'])))
    return {'favorite': favorite}


def reconcile_attention(conn, owner):
    """Bounded owner-scoped cleanup on reads; no inference or background poller."""
    return conn.execute("""
        WITH retire AS (""" + ATTENTION_RETIREMENTS_SQL + """)
        UPDATE rvbbit.calliope_work_items wi
        SET state='dismissed',resolved_at=coalesce(wi.resolved_at,now()),updated_at=now(),
            engaged_session_id=NULL,engaged_at=NULL,
            context=coalesce(wi.context,'{}'::jsonb)||jsonb_build_object(
                'attention_retired',jsonb_build_object('reason',retire.reason,'at',now()))
        FROM retire WHERE wi.id=retire.id AND wi.state IN ('unread','seen')
        RETURNING wi.id,retire.reason
    """, (owner,)).fetchall()


# Generated labels are presentation, not durable history. Remember labels this
# viewer has actually received so copying a visible label into search works.
# Canonical stored titles/summaries remain searchable across the entire graph.
_presentations = OrderedDict()
_presentation_lock = threading.Lock()


def remember_presentations(owner, titles):
    with _presentation_lock:
        _presentations[owner] = (time.monotonic(), list(titles)[:50])
        _presentations.move_to_end(owner)
        while len(_presentations) > 128:
            _presentations.popitem(last=False)


def cached_presentations(owner):
    with _presentation_lock:
        stamp, titles = _presentations.get(owner, (0, []))
        return titles if time.monotonic() - stamp < 3600 else []


def request_options(params):
    """Reject malformed navigation rather than silently switching its scope."""
    as_of = params.get('as_of') or None
    if as_of:
        stamp = datetime.fromisoformat(as_of.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('A timezone-aware AS-OF timestamp is required.')
        as_of = stamp.isoformat()
    lane = params.get('lane') or None
    if lane and lane not in LANES:
        raise ValueError('Unknown Floor lane.')
    queries = json.loads(params.get('lane_queries') or '{}')
    if not isinstance(queries, dict) or any(k not in LANES or not isinstance(v, str) for k, v in queries.items()):
        raise ValueError('Invalid lane searches.')
    freshness = params.get('freshness', 'all')
    if freshness not in ('all', 'live', 'day', 'week', 'older'):
        raise ValueError('Unknown freshness filter.')
    return dict(as_of=as_of, lane=lane, q=str(params.get('q') or '')[:300].strip().lower(),
                person=str(params.get('person') or 'all')[:300],
                kind=str(params.get('kind') or 'all')[:100], freshness=freshness,
                hide_notices=str(params.get('hide_notices') or '') in ('1', 'true'),
                lane_queries={k: v.strip().lower()[:300] for k, v in queries.items()},
                limit=max(1, min(int(params.get('limit') or 30), 100)),
                cursor=params.get('cursor') or None,
                # Board textures and grid previews reuse existing artifact captures.
                thumbs=str(params.get('thumbs') or '') in ('1', 'true'))


def read_page(conn_factory, projection, owner, options, presentations=(), favorites=()):
    """Projection already enforces ACLs. All counts/search precede page limits.

    The AS-OF GUC, projection, counts and page read share one transaction. Live
    pages use a stable tie-breaker; clients deduplicate items moved by live writes.
    """
    scope = hashlib.sha256(json.dumps([owner, {k: v for k, v in options.items()
        if k not in ('cursor', 'limit', 'lane')}, sorted(favorites)], sort_keys=True).encode()).hexdigest()
    lane = options['lane']
    after = None
    if options['cursor']:
        try:
            token = json.loads(base64.urlsafe_b64decode(options['cursor'].encode()))
            if token['scope'] != scope or token['lane'] != lane or not lane:
                raise ValueError()
            after = (bool(token['favorite']), float(token['rank']), str(token['at']), str(token['id']))
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise ValueError('This cursor belongs to a different Floor view.') from exc
    filters = ["(%s = '' OR strpos(search_text, %s) > 0)",
               "(%s = 'all' OR lower(owner_email) = lower(%s) OR %s = ANY(agents))",
               "(%s = 'all' OR item_kind = %s)"]
    aliases = [] if options['as_of'] else list(presentations)
    args = [owner, owner, json.dumps(aliases), ['chat:'+str(s) for s in favorites], options['q'], options['q'], options['person'], options['person'],
            options['person'], options['kind'], options['kind']]
    fresh = options['freshness']
    if options.get('hide_notices'):
        filters.append("column_state <> 'noticed'")
    if fresh != 'all':
        filters.append({'live': 'active_now', 'day': 'idle_seconds <= 86400',
                        'week': 'idle_seconds <= 604800', 'older': 'idle_seconds > 604800'}[fresh])
    for name, query in options['lane_queries'].items():
        if query:
            filters.append('(column_state <> %s OR strpos(search_text, %s) > 0)')
            args.extend((name, query))
    page_filters = []
    if lane:
        page_filters.append('column_state = %s')
        args.append(lane)
    if after:
        page_filters.append('(favorite, sort_rank, sort_at, item_id) < (%s::boolean, %s::numeric, %s::timestamptz, %s::text)')
        args.extend(after)
    args.append(options['limit'] + 1)
    sql = f"""
        WITH visible AS MATERIALIZED ({projection}),
        aliases AS (SELECT * FROM jsonb_to_recordset(%s::jsonb)
            AS a(item_id text, semantic_title text, semantic_status text)),
        corpus AS MATERIALIZED (
            SELECT visible.*, a.semantic_title, a.semantic_status,
              item_id=ANY(%s::text[]) AS favorite,
              lower(concat_ws(' ', display_title, summary, state_reason, item_kind,
                    owner_email, array_to_string(agents, ' '), action_prompt, a.semantic_title, a.semantic_status)) AS search_text,
              CASE WHEN column_state IN ('done','archived','noticed') THEN 0
                   ELSE coalesce(attention_rank,0) END AS sort_rank,
              coalesce(last_activity_at, '-infinity'::timestamptz) AS sort_at
            FROM visible LEFT JOIN aliases a USING (item_id)

        ), matches AS MATERIALIZED (
            SELECT * FROM corpus WHERE {' AND '.join(filters)}
        ), ranked AS (
            SELECT *,row_number() OVER (PARTITION BY column_state
                ORDER BY favorite DESC,sort_rank DESC,sort_at DESC,item_id DESC) AS page_row
            FROM matches {'WHERE ' + ' AND '.join(page_filters) if page_filters else ''}
        ), page AS (
            SELECT * FROM ranked WHERE page_row <= %s
        ) SELECT jsonb_build_object(
            'total', (SELECT count(*) FROM corpus),
            'matched_total', (SELECT count(*) FROM matches),
            'lanes', (SELECT coalesce(jsonb_agg(to_jsonb(l)), '[]'::jsonb) FROM (
                SELECT column_state,count(*) AS pieces,
                    count(*) FILTER (WHERE active_now) AS active_now,
                    count(*) FILTER (WHERE default_hidden) AS hidden_by_default,
                    (SELECT count(*) FROM matches m WHERE m.column_state=c.column_state) AS matched
                FROM corpus c GROUP BY column_state) l),
            'facets', jsonb_build_object(
                'people', (SELECT coalesce(jsonb_agg(p ORDER BY p), '[]'::jsonb) FROM (
                    SELECT DISTINCT unnest(array_prepend(owner_email,coalesce(agents,'{{}}'::text[]))) AS p
                    FROM corpus) people WHERE p IS NOT NULL AND p <> ''),
                'kinds', (SELECT coalesce(jsonb_agg(k ORDER BY k), '[]'::jsonb)
                          FROM (SELECT DISTINCT item_kind AS k FROM corpus) kinds)),
            'cards', (SELECT coalesce(jsonb_agg(to_jsonb(page) - 'search_text'
                ORDER BY column_state,favorite DESC,sort_rank DESC,sort_at DESC,item_id DESC), '[]'::jsonb) FROM page)
        ) AS result
    """
    with conn_factory() as conn:
        with conn.transaction():
            if options['as_of']:
                conn.execute("SELECT set_config('rvbbit.as_of_timestamp', %s, true)", (options['as_of'],))
            row = conn.execute(sql, tuple(args)).fetchone()
    result = row['result'] if hasattr(row, 'keys') else row[0]
    result['as_of'] = options['as_of']
    result['pages'] = {}
    cards = []
    for name in ([lane] if lane else LANES):
        group = [card for card in result['cards'] if card['column_state'] == name]
        kept = group[:options['limit']]
        cursor = None
        if len(group) > options['limit']:
            last = kept[-1]
            cursor = base64.urlsafe_b64encode(json.dumps(dict(scope=scope, lane=name,
                favorite=last['favorite'], rank=last['sort_rank'], at=last['sort_at'], id=last['item_id'])).encode()).decode()
        result['pages'][name] = {'next_cursor': cursor, 'has_more': bool(cursor)}
        for card in kept:
            cards.append({k: v for k, v in card.items() if k not in ('sort_rank', 'sort_at', 'page_row')})
    result['cards'] = cards
    if options.get('thumbs'):
        attach_artifact_thumbnails(conn_factory, cards)
    return result


def attach_artifact_thumbnails(conn_factory, cards):
    """Give each piece the thumbnail of the newest published artifact in its
    thread — the same /thumbs/ capture the Gallery shows — for board textures
    and grid previews. One indexed query per page; pieces without a published
    artifact simply get no thumbnail."""
    session_ids = sorted({str(card.get('session_id')) for card in cards if card.get('session_id')})
    if not session_ids:
        return
    try:
        with conn_factory() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ON (su.session_id) su.session_id::text AS session_id,"
                " su.artifact_slug, d.app_kind, su.created_at "
                "FROM rvbbit.calliope_surfaces su "
                "JOIN rvbbit.dashboards d ON d.slug = su.artifact_slug "
                "WHERE su.session_id = ANY(%s::uuid[]) AND su.kind = 'artifact' "
                "ORDER BY su.session_id, su.created_at DESC",
                (session_ids,),
            ).fetchall()
    except Exception:  # noqa: BLE001 — thumbnails are decoration, never a page failure
        return
    by_session = {}
    for row in rows:
        row = dict(row)
        slug = str(row.get('artifact_slug') or '').strip()
        if not slug:
            continue
        kind = 'dashboard' if str(row.get('app_kind') or '') == 'dashboard' else 'app'
        by_session[row['session_id']] = f"/thumbs/{kind}/{slug}.png"
    for card in cards:
        url = by_session.get(str(card.get('session_id') or ''))
        if url:
            card['thumbnail_url'] = url


def include_presence(result, shells, options):
    """Count only the existing bounded, title-free private-presence projection.

    Search never treats a private shell as content. Its identity and activity
    can still participate in the same people/kind/freshness filters as before.
    """
    if not shells:
        return
    lane = next((r for r in result['lanes'] if r['column_state'] == 'in_motion'), None)
    if lane is None:
        lane = dict(column_state='in_motion', pieces=0, matched=0, active_now=0, hidden_by_default=0)
        result['lanes'].append(lane)
    result['total'] += len(shells)
    lane['pieces'] += len(shells)
    lane['active_now'] += sum(bool(c.get('active_now')) for c in shells)
    matched = []
    for card in shells:
        if options['q'] or options['lane_queries'].get('in_motion'):
            continue
        if options['kind'] not in ('all', 'chat'):
            continue
        if options['person'] != 'all' and options['person'] not in [card.get('owner_email'), *(card.get('agents') or [])]:
            continue
        fresh, idle = options['freshness'], float(card.get('idle_seconds') or 0)
        if (fresh == 'live' and not card.get('active_now')) or (fresh == 'day' and idle > 86400) or (fresh == 'week' and idle > 604800) or (fresh == 'older' and idle <= 604800):
            continue
        matched.append(card)
    result['matched_total'] += len(matched)
    lane['matched'] += len(matched)
    result['facets']['people'] = sorted(set(result['facets']['people']) | {
        person for card in shells for person in [card.get('owner_email'), *(card.get('agents') or [])] if person})
    result['facets']['kinds'] = sorted(set(result['facets']['kinds']) | {'chat'})
    if not options['cursor'] and options['lane'] in (None, 'in_motion'):
        result['cards'].extend(matched)


# Use the actual personal Dream portfolio, not the observation graph. Casting
# through the view's composite type keeps its column order/types authoritative.
CURATED_DREAM_GRAPH_SQL = """
SELECT (jsonb_populate_record(NULL::rvbbit.work_graph, jsonb_build_object(
    'item_id','curated_dream:'||d.id, 'item_kind','curated_dream',
    'display_title',d.title, 'summary',d.thesis,
    'column_state','noticed', 'detail_state',d.status,
    'owner_email',d.owner_email, 'agents',jsonb_build_array('calliope'),
    'state_reason',CASE WHEN d.portfolio_state='backlog' THEN 'On deck — an idea to consider' WHEN d.status='exploring' THEN 'A Dream you are exploring' ELSE 'An idea worth exploring' END,
    'active_now',false, 'default_hidden',false,
    'last_activity_at',d.updated_at,
    'idle_seconds',extract(epoch FROM now()-d.updated_at)::bigint,
    'child_thread_count',0,'active_child_count',0,'related_signal_count',0,'surface_count',0,
    'attention_rank',greatest(1,100-coalesce(d.portfolio_rank,99)),
    'route','dream:'||d.id, 'ref',jsonb_build_object('kind','dream','dream',jsonb_build_object(
        'portfolio_state',d.portfolio_state,'status',d.status,'impact',d.impact,
        'confidence',d.confidence,'effort',d.effort,'novelty',d.novelty,
        'rank_score',d.rank_score,'portfolio_rank',d.portfolio_rank))
))).*
FROM rvbbit.calliope_dreams d
LEFT JOIN LATERAL (
    SELECT e.event_kind,e.payload FROM rvbbit.calliope_dream_events e
    WHERE e.dream_id=d.id AND e.actor_email=d.owner_email
    ORDER BY e.created_at DESC,e.event_id DESC LIMIT 1
) viewer ON true
WHERE d.scope_kind='personal' AND d.portfolio_state IN ('promoted','backlog')
  AND d.status IN ('proposed','exploring')
  -- Once started, the ordinary notebook is the only Floor piece.
  AND NOT EXISTS (SELECT 1 FROM rvbbit.calliope_dream_handoffs h
      WHERE h.dream_id=d.id AND h.owner_email=d.owner_email AND h.session_id IS NOT NULL)
  AND viewer.event_kind IS DISTINCT FROM 'dismissed'
  AND (viewer.event_kind IS DISTINCT FROM 'sleeping'
       OR nullif(viewer.payload->>'wake_at','')::timestamptz<=now())
"""
