"""App improvement requests are shared individual turns, not shared conversations.

Published versions reference requests in manifest.change_requests. No parallel
ticket, artifact, or release registry is needed.
"""
import base64
import hashlib
import html
import json
import threading
import time
import uuid
from urllib.parse import urlencode


def ensure(factory):
    import app_review
    app_review.ensure(factory)


def request_id(value):
    return str(uuid.UUID(str(value)))


def enrich(conn, actor, row):
    row = dict(row)
    row['id'] = str(row['id'])
    versions = conn.execute("SELECT d.slug,d.name,v.version,v.notes FROM rvbbit.dashboard_versions v "
        "JOIN rvbbit.dashboards d ON d.id=v.dashboard_id WHERE v.manifest->'change_requests' ? %s "
        "AND NOT d.archived AND rvbbit.artifact_can_view(d.id,%s,false) ORDER BY v.created_at LIMIT 20",
        (row['id'], actor)).fetchall()
    row['releases'] = [dict(v) for v in versions]
    row['status'] = 'published' if versions else 'requested'
    row['url'] = '/apps/' + row['slug'] + '/changes?' + urlencode({'request': row['id']})
    import app_review
    return app_review.decorate(conn, actor, row)


def requests(conn_factory, actor, slug, ident=None):
    if not isinstance(actor,str) or not actor.strip():
        raise ValueError('A signed-in user is required')
    ident = request_id(ident) if ident else None
    ensure(conn_factory)
    with conn_factory() as conn:
        app = conn.execute("SELECT id,name,owner_email,latest_version FROM rvbbit.dashboards "
            "WHERE slug=%s AND NOT archived AND rvbbit.artifact_can_view(id,%s,false)", (slug, actor)).fetchone()
        if not app:
            raise ValueError('App unavailable')
        rows = conn.execute("SELECT t.id,t.user_message,t.origin_context,t.created_at,"
            "coalesce(t.author_email,s.owner_email) AS requested_by FROM rvbbit.calliope_turns t "
            "JOIN rvbbit.calliope_sessions s ON s.id=t.session_id "
            "WHERE t.origin_context->>'kind'='app_workspace' AND t.origin_context->>'intent'='improve' "
            "AND t.origin_context->>'slug'=%s AND (%s::uuid IS NULL OR t.id=%s::uuid) "
            "AND (lower(%s)=lower(%s) OR lower(coalesce(t.author_email,s.owner_email))=lower(%s)) "
            "ORDER BY t.created_at DESC LIMIT 50", (slug, ident, ident, actor, app['owner_email'], actor)).fetchall()
        result = []
        for row in rows:
            result.append(enrich(conn, actor, {**row, 'slug':slug, 'title':app['name'],
                'owner_email':app['owner_email'], 'latest_version':app['latest_version']}))
    return {'slug': slug, 'title': app['name'], 'requests': result,
            'note': 'Only explicitly shared requests and their screen context; no other chat turns. Publication is not proof of user acceptance.'}


def validate_requests(conn, owner, manifest):
    ids = manifest.get('change_requests', [])
    if not isinstance(ids, list) or len(ids) > 20:
        raise ValueError('change_requests must contain at most 20 request UUIDs')
    for value in ids:
        ident = request_id(value)
        row = conn.execute("SELECT t.id FROM rvbbit.calliope_turns t JOIN rvbbit.dashboards d "
            "ON d.slug=t.origin_context->>'slug' WHERE t.id=%s::uuid "
            "AND t.origin_context->>'kind'='app_workspace' AND t.origin_context->>'intent'='improve' "
            "AND lower(d.owner_email)=lower(%s) AND NOT d.archived", (ident, owner)).fetchone()
        if not row:
            raise ValueError('Change request unavailable to this app author')


def release(server, slug, app, row, record=None):
    latest = row
    if app.get('latest_version', row['version']) != row['version']:
        _, latest = server._load_live_app_version(slug)
    return {'version': latest['version'], 'notes': str(latest.get('notes') or '')[:2400],
            'requests': (latest.get('manifest') or {}).get('change_requests', []),
            'compare_url': '/apps/' + slug + '/revisions?' + urlencode({
                'before': row['version'] if row['version'] != latest['version'] else max(1, row['version']-1),
                'after': latest['version'], **({'record':json.dumps(record,separators=(',',':'))} if record else {})})}


_locks = [threading.Lock() for _ in range(32)]


def capture(server, actor, slug, version, viewport, renderer, record=None):
    import ui_apps
    import ui_app_captures
    import ui_app_preview
    import artifact_app_preview
    import html_app_capture
    size = ui_app_preview.viewport(viewport)
    if renderer not in ('web', 'native'):
        raise ValueError('Unknown renderer')
    server._artifact_require_view(slug, caller_override=actor)
    app, row = server._load_live_app_version(slug, version)
    if not app or not row or row['kind'] not in ('a2ui','html','live'):
        raise ValueError('App version unavailable')
    if row['kind'] in ('html','live') and renderer != 'web':
        raise ValueError('HTML dashboards use the web renderer')
    key = hashlib.sha256(json.dumps([actor, slug, version, viewport, renderer, record]).encode()).hexdigest()
    directory = server._live_app_capture_root()/'revisions'/key
    path = directory/'preview.png'
    with _locks[int(key[:4], 16) % len(_locks)]:
        if path.is_file() and time.time()-path.stat().st_mtime < 120:
            return path
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        pending = directory/'pending.png'
        token = server._SESSION_SUB.set(actor)
        try:
            if row['kind'] == 'a2ui':
                document = ui_apps.definition(row['source_files'])
                body = {'mode':'server-read'}
                if record:
                    import copy
                    import ui_records
                    record = ui_records.reference(record, slug)
                    if record['slug'] != slug:
                        raise ValueError('Record belongs to another app')
                    model = copy.deepcopy(document['data'])
                    ui_records.open_record(document, model, record)
                    body['state'] = model['state']
                snapshot = ui_app_preview.request_snapshot(server, document, body, actor)
                if record and ui_apps.resolve(document['records'][record['record']]['id'],
                        snapshot['messages'][2]['updateDataModel']['value']) != record['id']:
                    raise ValueError('Record unavailable to this viewer')
                theme = artifact_app_preview.theme_for(server._conn, actor)
                if renderer == 'web':
                    result = ui_app_captures.web(snapshot, pending, size, theme, server._launch_playwright_chromium, full_page=True)
                else:
                    result = ui_app_captures.native(document, snapshot, theme, [viewport])['captures'][viewport]
                    pending.write_bytes(base64.b64decode(result['data'], validate=True))
                if not result.get('ready'):
                    raise ValueError('Preview did not settle; inspect the SQL bindings and renderer')
            else:
                result = server._capture_html_with_playwright(server._capturable_dashboard_html(slug, row), pending,
                    size['width'], size['height'], False, 800,
                    app_read_runner=lambda target, body: html_app_capture.app_read(server, actor, target, body))
                if not (result.get('readiness') or {}).get('ready') or (result.get('readiness') or {}).get('failed') or any(result.get(k) for k in ('page_errors','runtime_errors','render_issues')):
                    raise ValueError('HTML preview did not settle')
            server._artifact_require_view(slug, caller_override=actor)
            pending.chmod(0o600)
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)
            server._SESSION_SUB.reset(token)
    return path


def register_tools(mcp, server):
    def app_change_requests(slug: str, request_id: str | None = None):
        """Read explicitly shared app/dashboard improvement requests and captured screen context.
        App owners see requests for their artifact; other viewers see only their own.
        Use save_app_scenario(request_id=...) to freeze reported inputs, then
        check_app_revision to test the same case against saved or candidate source.
        Review the request, edit source, test native/web phone/desktop previews, then publish
        with update_live_app notes and manifest.change_requests=[request UUID]. Preserve grants
        and compatibility with old clients/drafts. This does not execute or publish changes.
        """
        return server._logged('app_change_requests', {'slug':slug,'request_id':request_id},
            lambda: requests(server._conn, server._require_application_subject().subject, slug, request_id))
    mcp.tool(name='app_change_requests')(app_change_requests)
    import app_review
    app_review.register_tools(mcp, server)


def register(m, server, actor_for, respond):
    import asyncio
    import contextvars
    import auth
    import artifact_access
    import app_actions
    from pathlib import Path
    from starlette.responses import FileResponse, RedirectResponse
    import app_review
    app_review.register(m, server, actor_for, respond)

    async def run(request, callback, with_session=False):
        actor, session, error = actor_for(request)
        if error:
            if not session and request.url.path.startswith('/apps/'):
                return RedirectResponse('/login?'+urlencode({'next':request.url.path+('?' + request.url.query if request.url.query else '')}), status_code=303)
            return error
        def work():
            try:
                response = callback(actor, session) if with_session else callback(actor)
                response.headers['Cache-Control'] = 'private, no-store'
                return response
            except (artifact_access.ArtifactAccessError, app_actions.ActionError) as exc:
                return respond({'error': {'code': exc.code, 'message': str(exc)}}, exc.status)
            except (ValueError, KeyError) as exc:
                return respond({'error': {'message': str(exc)}}, 400)
        return await asyncio.get_running_loop().run_in_executor(server._CONTROL_EXECUTOR, contextvars.copy_context().run, work)

    @m.custom_route('/api/apps/feedback.js', methods=['GET'])
    async def feedback_script(request):
        return FileResponse(Path(__file__).parent/'calliope'/'app-feedback.js', media_type='text/javascript', headers={'Cache-Control':'no-cache'})

    @m.custom_route('/api/apps/{slug}/changes', methods=['GET'])
    async def changes(request):
        return await run(request, lambda actor: respond(requests(server._conn, actor,
            request.path_params['slug'], request.query_params.get('request'))))

    @m.custom_route('/apps/{slug}/changes', methods=['GET'])
    async def change_page(request):
        def page(actor, session):
            slug = request.path_params['slug']
            data = requests(server._conn, actor, slug, request.query_params.get('request'))
            return app_review.page({**data, 'selected': request.query_params.get('request')}, actor, session=session, factory=server._conn)
        return await run(request, page, with_session=True)

    @m.custom_route('/api/apps/{slug}/revisions/{version}/image', methods=['GET'])
    async def revision_image(request):
        def image(actor):
            path = capture(server, actor, request.path_params['slug'], int(request.path_params['version']),
                request.query_params.get('viewport','desktop'), request.query_params.get('renderer','web'),
                json.loads(request.query_params['record']) if 'record' in request.query_params else None)
            return FileResponse(path, media_type='image/png', headers={'Cache-Control':'private, no-store'})
        return await run(request, image)

    @m.custom_route('/apps/{slug}/revisions', methods=['GET'])
    async def comparison(request):
        def page(actor):
            slug = request.path_params['slug']
            server._artifact_require_view(slug, caller_override=actor)
            app, latest = server._load_live_app_version(slug)
            if not app or not latest:
                raise ValueError('App unavailable')
            after = int(request.query_params.get('after', latest['version']))
            before = int(request.query_params.get('before', max(1, after-1)))
            rows = [server._load_live_app_version(slug,v)[1] for v in (before,after)]
            if min(before,after)<1 or not all(rows):
                raise ValueError('Version unavailable')
            mode = request.query_params.get('renderer','web')
            size = request.query_params.get('viewport','desktop')
            if mode not in ('web','native') or size not in ('phone','desktop'):
                raise ValueError('Unknown preview mode')
            if app['runtime_kind'] == 'html' and mode == 'native':
                raise ValueError('HTML dashboards use the web renderer')
            record = request.query_params.get('record')
            extra = {'record':record} if record else {}
            if record:
                import ui_records
                ui_records.reference(json.loads(record), slug)
            e = html.escape
            body = '<h1>'+e(app['name'])+' · revision comparison</h1><p>Exact saved definitions, read-only previews using current authorized data. These are not historical data snapshots.</p><nav class="revision-nav">'
            for label, changes in [('Desktop',{'viewport':'desktop'}),('Phone',{'viewport':'phone'}),('Web',{'renderer':'web'})] + ([('Native',{'renderer':'native'})] if app['runtime_kind']=='a2ui' else []):
                body += '<a href="?'+e(urlencode({'before':before,'after':after,'renderer':mode,'viewport':size,**extra,**changes}),quote=True)+'">'+label+'</a>'
            body += '</nav><div class="revision-pair">'
            for row in rows:
                src = '/api/apps/'+slug+'/revisions/'+str(row['version'])+'/image?'+urlencode({'viewport':size,'renderer':mode,**extra})
                target = '/apps/'+slug+'/versions/'+str(row['version'])+('#'+urlencode({'record':record}) if record else '')
                body += '<section><h2>v'+str(row['version'])+' · <a href="'+e(target,quote=True)+'" target="_blank" rel="noopener">Open this version</a></h2><p>'+e(row.get('notes') or 'No release notes')+'</p><div class="revision-preview"><p class="loading">Rendering '+mode+' '+size+' preview…</p><img src="'+e(src,quote=True)+'" alt="Version '+str(row['version'])+' preview" onload="this.previousElementSibling.hidden=true" onerror="this.previousElementSibling.textContent=\'Preview unavailable. Check the render or SQL binding errors.\'"></div></section>'
            body += '</div>'
            if mode == 'native':
                body += '<p>Native images show one viewport. Open a version to explore the complete page in the web renderer.</p>'
            body = ('<style>.revision-pair img{max-width:390px}</style>' if size=='phone' else '')+body
            return auth._page('<style>.card{max-width:none;width:96%;margin:24px auto}.revision-nav{display:flex;gap:18px;flex-wrap:wrap;background:transparent;padding:12px 0}.revision-pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 24px}.revision-pair img{display:block;width:100%;height:auto;margin:auto;border:1px solid var(--line)}.revision-pair section{min-width:0;display:grid;grid-template-rows:subgrid;grid-row:span 3}.revision-pair h2,.revision-pair section>p{margin:0}.revision-preview{align-self:start;min-height:300px}@media(max-width:760px){.revision-pair{grid-template-columns:1fr}}</style>'+body)
        return await run(request, page)
