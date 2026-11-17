"""Read-only starting point for Ops: observed work and its actual evidence.

One observation per accessible conversation. Cross-conversation practices and
business outcome attribution belong to the later discovery pass, not heuristics
that mistake a completed chat or saved dashboard for a completed sale.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging

import calliope_dreams
from starlette.responses import HTMLResponse, RedirectResponse

LIMIT = 60
RESULT_KINDS = ('artifact', 'chart', 'board', 'document', 'image', 'markdown',
                'email', 'sketch', 'metric', 'cube', 'playbook', 'workflow', 'work_order')

SQL = """
WITH recent AS MATERIALIZED (
  SELECT s.id,s.title,s.owner_email,s.updated_at
  FROM rvbbit.calliope_sessions s
  WHERE s.purpose='chat' AND NOT s.archived
    AND s.updated_at >= now() - make_interval(days => %s)
    AND rvbbit.calliope_session_can_view(s.id,%s,false)
    AND EXISTS (SELECT 1 FROM rvbbit.calliope_turns t WHERE t.session_id=s.id)
  ORDER BY s.updated_at DESC,s.id LIMIT %s
)
SELECT s.*,sn.synopsis,sn.through_ordinal,sn.updated_at AS synopsis_at,
  (SELECT max(t.ordinal) FROM rvbbit.calliope_turns t WHERE t.session_id=s.id) AS latest_ordinal,
  EXISTS (SELECT 1 FROM rvbbit.calliope_turns t WHERE t.session_id=s.id AND t.status='running') AS active,
  result.total AS result_count,result.items AS results,
  inputs.total AS open_requests,inputs.items AS requests
FROM recent s
LEFT JOIN rvbbit.calliope_session_synopses sn ON sn.session_id=s.id
LEFT JOIN LATERAL (
  SELECT count(*)::int AS total,
    coalesce(jsonb_agg(jsonb_build_object('id',f.id,'kind',f.kind,'title',f.title,
      'created_at',f.created_at) ORDER BY f.created_at DESC) FILTER (WHERE f.rank<=6),'[]'::jsonb) AS items
  FROM (
    SELECT latest.*,row_number() OVER(ORDER BY created_at DESC,id) AS rank FROM (
      SELECT DISTINCT ON (coalesce(nullif(lineage_key,''),id::text))
        id,kind,title,created_at
      FROM rvbbit.calliope_surfaces WHERE session_id=s.id AND kind=ANY(%s)
      ORDER BY coalesce(nullif(lineage_key,''),id::text),created_at DESC,id DESC
    ) latest
  ) f
) result ON true
LEFT JOIN LATERAL (
  SELECT count(*)::int AS total,
    coalesce(jsonb_agg(jsonb_build_object('id',r.id,'title',r.request->>'title',
      'created_at',r.created_at) ORDER BY r.created_at DESC) FILTER (WHERE r.rank<=6),'[]'::jsonb) AS items
  FROM (SELECT r.*,row_number() OVER(ORDER BY r.created_at DESC,r.id) AS rank
        FROM rvbbit.calliope_input_requests r WHERE r.session_id=s.id AND r.status='open') r
) inputs ON true
ORDER BY s.updated_at DESC,s.id
"""


def snapshot(conn_factory, owner, days=30):
    if days not in (7, 30, 90):
        raise ValueError('Choose 7, 30, or 90 days')
    with conn_factory() as conn:
        # Bound an optional admin read; never leave a slow dashboard query running.
        with conn.transaction():
            conn.execute("SET LOCAL statement_timeout = '10s'")
            rows = conn.execute(SQL, (days, owner, LIMIT + 1, list(RESULT_KINDS))).fetchall()
    observations = []
    for row in rows[:LIMIT]:
        item = dict(row)
        item['id'] = str(item['id'])
        for key in ('updated_at', 'synopsis_at'):
            if item.get(key):
                item[key] = item[key].isoformat()
        item['summary_is_current'] = (bool(item.get('synopsis')) and
                                     (item.get('through_ordinal') or 0) >= (item.get('latest_ordinal') or 0))
        item['url'] = '/calliope?session=' + item['id'] + '&view=zen'
        observations.append(item)
    return {
        'observations': observations, 'period_days': days, 'limit': LIMIT,
        'has_more': len(rows) > LIMIT, 'as_of': datetime.now(timezone.utc).isoformat(),
        'scope': 'Conversations you can access',
        'totals': {'observations': len(observations),
                   'results': sum(x['result_count'] for x in observations),
                   'open_requests': sum(x['open_requests'] for x in observations)},
    }


async def admin_access(request, conn_factory, api_owner, json_response):
    owner, error = api_owner(request)
    if error is not None:
        return None, error
    if not await asyncio.to_thread(calliope_dreams.is_company_admin, conn_factory, owner):
        return None, json_response({'error': {'code': 'ADMIN_REQUIRED', 'message': 'Ops is available to administrators.'}}, 403)
    return owner, None


def register(mcp, conn_factory, api_owner, json_response, canonical_owner,
             browser_asset, auth, warehouse_theme, asset_dir, asset_version):
    @mcp.custom_route('/api/calliope/ops', methods=['GET'])
    async def ops_snapshot(request):
        owner, error = await admin_access(request, conn_factory, api_owner, json_response)
        if error is not None:
            return error
        try:
            days = int(request.query_params.get('days', '30'))
            data = await asyncio.to_thread(snapshot, conn_factory, owner, days)
        except ValueError:
            return json_response({'error': {'code': 'INVALID_PERIOD', 'message': 'Choose 7, 30, or 90 days.'}}, 400)
        except Exception:
            logging.getLogger(__name__).exception('Ops evidence could not be loaded')
            return json_response({'error': {'code': 'OPS_UNAVAILABLE', 'message': 'The work record could not be loaded. Please try again.'}}, 503)
        response = json_response(data)
        response.headers['cache-control'] = 'private, no-store'
        return response

    @mcp.custom_route('/ops', methods=['GET'])
    async def ops_page(request):
        owner, session = canonical_owner(request)
        if not owner:
            return RedirectResponse('/login?next=%2Fops', status_code=302)
        _, error = await admin_access(request, conn_factory, api_owner, json_response)
        if error is not None:
            return error
        html = (asset_dir / 'ops.html').read_text(encoding='utf-8')
        html = html.replace('__CALLIOPE_ASSET_VERSION__', asset_version)
        html = html.replace('__CALLIOPE_BACKGROUND__', auth.background_layer(
            .42, 'linear-gradient(to bottom,rgba(16,13,11,.28),rgba(16,13,11,.72) 84%)', scene_key=owner))
        html = html.replace('__CALLIOPE_WORKSPACE_NAV__', warehouse_theme.workspace_navigation('ops', can_administer=True))
        html = html.replace('__CALLIOPE_ACCOUNT__', warehouse_theme.account_control(session, workspace_menu=True, can_administer=True))
        return HTMLResponse(html, headers={
            'cache-control': 'private, no-store', 'vary': 'Cookie, Authorization',
            'x-content-type-options': 'nosniff',
            'content-security-policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'; form-action 'self'",
        })

    @mcp.custom_route('/ops/ops.js', methods=['GET'])
    @mcp.custom_route('/ops/ops.css', methods=['GET'])
    async def ops_asset(request):
        _, error = await admin_access(request, conn_factory, api_owner, json_response)
        if error is not None:
            return error
        name = request.url.path.rsplit('/', 1)[-1]
        return browser_asset(request, name, 'text/css' if name.endswith('.css') else 'text/javascript')
