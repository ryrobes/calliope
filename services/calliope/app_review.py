"""Shared request review and private test evidence, on Calliope/Postgres."""
import asyncio
import contextvars
import hashlib
import html
import json
import threading
from pathlib import Path
from urllib.parse import urlencode

from app_actions import ActionError
import app_revisions

_ready = False
_lock = threading.Lock()


def ensure(factory):
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        with factory() as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('app-review-v1',0))")
                conn.execute(Path(__file__).with_suffix('.sql').read_text())
        _ready = True


def decorate(conn, actor, row):
    review = conn.execute('SELECT * FROM rvbbit.app_request_reviews WHERE turn_id=%s', (row['id'],)).fetchone() or {}
    # Same fingerprint for owner/requester despite different artifact visibility.
    releases = conn.execute("SELECT v.dashboard_id,v.version FROM rvbbit.dashboard_versions v "
        "WHERE v.manifest->'change_requests' ? %s ORDER BY v.dashboard_id,v.version", (row['id'],)).fetchall()
    key = hashlib.sha256(json.dumps([str(v['dashboard_id'])+':'+str(v['version']) for v in releases]).encode()).hexdigest() if releases else ''
    confirmation = review.get('requester_status', '') if review.get('release_key') == key else ''
    is_owner = actor.lower() == row['owner_email'].lower()
    is_requester = actor.lower() == row['requested_by'].lower()
    row['review'] = {'revision':review.get('revision',0), 'owner_status':review.get('owner_status','new'),
        'requester_status':confirmation, 'release_key':key,
        'can_triage':is_owner, 'is_requester':is_requester, 'can_confirm':is_requester and bool(row['releases']),
        'updated_at':review.get('updated_at')}
    row['status'] = confirmation or ('published' if row['releases'] else review.get('owner_status','new'))
    row['needs_attention'] = ((is_owner and row['status'] in ('new','reopened')) or
                              (is_requester and row['status'] == 'published'))
    return row


def _requests(conn, actor):
    """Requests on the actor's apps and requests the actor made, newest first."""
    rows = conn.execute("SELECT t.id,t.user_message,t.origin_context,t.created_at,d.slug,d.name AS title,d.owner_email,d.latest_version,"
        "coalesce(t.author_email,s.owner_email) AS requested_by FROM rvbbit.calliope_turns t "
        "JOIN rvbbit.calliope_sessions s ON s.id=t.session_id "
        "JOIN rvbbit.dashboards d ON d.slug=t.origin_context->>'slug' "
        "WHERE t.origin_context->>'kind'='app_workspace' AND t.origin_context->>'intent'='improve' "
        "AND NOT d.archived AND rvbbit.artifact_can_view(d.id,%s,false) "
        "AND (lower(d.owner_email)=lower(%s) OR lower(coalesce(t.author_email,s.owner_email))=lower(%s)) "
        "ORDER BY t.created_at DESC LIMIT 200", (actor,actor,actor)).fetchall()
    return [app_revisions.enrich(conn,actor,row) for row in rows]


def inbox(server, actor):
    if not isinstance(actor,str) or not actor.strip():
        raise ValueError('A signed-in user is required')
    ensure(server._conn)
    with server._conn() as conn:
        result = _requests(conn, actor)
    return {'title':'App improvements', 'requests':result, 'limit':200,
            'attention':sum(r['needs_attention'] for r in result)}


# Requests not yet shipped or set aside; "reopened" means the release missed.
OPEN = ('new', 'acknowledged', 'working', 'reopened')


def open_counts(factory, actor):
    """Open requests on the actor's own apps, by slug, for the Gallery cards."""
    ensure(factory)
    with factory() as conn:
        rows = _requests(conn, actor)
    counts = {}
    for row in rows:
        if row['review']['can_triage'] and row['status'] in OPEN:
            counts[row['slug']] = counts.get(row['slug'], 0) + 1
    return counts


def _notice(row, role, who):
    text = ' '.join(str(row.get('user_message') or '').split())
    quoted = '“' + (text[:280] + '…' if len(text) > 280 else text) + '”'
    title, version = row['title'] or row['slug'], (row['releases'][-1]['version'] if row['releases'] else None)
    if role == 'owner' and row['status'] == 'reopened':
        heading = f"{who} says {title} v{version} didn't fix their request"
        ask = f"{who} reopened their request on the {title} app ({row['slug']}) after v{version}: {quoted}."
    elif role == 'owner':
        heading = f"{who} asked for a change to {title}"
        ask = f"{who} asked for a change to the {title} app ({row['slug']}): {quoted}."
    else:
        heading = f"Your request for {title} shipped in v{version}"
        ask = f"My request for the {title} app shipped in v{version}: {quoted}."
    prompt = (ask + " Read the request and its screen context with app_improvements, then draft a revised "
              f"version for my review. List request {row['id']} in the new version's change_requests so "
              f"{who} hears when it ships.") if role == 'owner' else (
              ask + " Help me check whether the new version does what I asked.")
    summary = quoted if role == 'owner' else quoted + " Open it to confirm it's fixed or reopen it."
    return {'kind': 'suggestion' if role == 'owner' else 'result', 'title': heading[:240], 'summary': summary,
            'prompt': prompt, 'days': 30 if role == 'owner' else 14,
            'context': {'open_url': row['url'], 'slug': row['slug'], 'request_id': row['id'], 'role': role,
                        'version': version, 'app_title': title}}


def sync_notices(factory, actor):
    """Mirror request events into the actor's Work Inbox, which the Daily Brief also reads.

    One notice per event: a new or reopened request for the app owner, a shipped
    version for the requester. Done/Dismiss close the notice only; the request's
    own state lives on the app's requests page. A notice whose request no longer
    needs this person closes itself.
    """
    if not isinstance(actor, str) or not actor.strip():
        return 0
    ensure(factory)
    with factory() as conn:
        rows, wanted = _requests(conn, actor), {}
        for row in rows:
            review, owner, requester = row['review'], row['owner_email'], row['requested_by']
            if review['can_triage'] and row['status'] in ('new', 'reopened') and requester.lower() != actor.lower():
                role = 'owner'
            elif review['is_requester'] and row['status'] == 'published' and owner.lower() != actor.lower():
                role = 'requester'
            else:
                continue
            key = f"{row['id']}:{role}:{row['status']}:{review['release_key'][:16]}"
            wanted[key] = (row, role)
        names = {}
        if wanted:
            people = {row['requested_by'].lower() for row, role in wanted.values() if role == 'owner'}
            for person in conn.execute("SELECT lower(email) AS email,display_name FROM rvbbit.application_principals "
                                       "WHERE lower(email)=ANY(%s)", (sorted(people),)).fetchall():
                names[person['email']] = person.get('display_name') or ''
        for key, (row, role) in wanted.items():
            requester = row['requested_by']
            who = names.get(requester.lower()) or requester.split('@')[0]
            notice = _notice(row, role, who if role == 'owner' else 'you')
            conn.execute("INSERT INTO rvbbit.calliope_work_items (id,owner_email,session_id,kind,source,source_ref,"
                "dedupe_key,title,summary,urgency,state,context,action_prompt,expires_at) "
                "VALUES (gen_random_uuid(),%s,NULL,%s,'app_request',%s,%s,%s,%s,'normal','unread',%s::jsonb,%s,"
                "now()+make_interval(days=>%s)) ON CONFLICT (owner_email,source,dedupe_key) DO NOTHING",
                (actor, notice['kind'], row['id'], key, notice['title'], notice['summary'],
                 json.dumps(notice['context'], default=str), notice['prompt'], notice['days']))
        conn.execute("UPDATE rvbbit.calliope_work_items SET state='done',resolved_at=now(),updated_at=now() "
            "WHERE owner_email=%s AND source='app_request' AND state IN ('unread','seen') "
            "AND NOT (dedupe_key=ANY(%s::text[]))", (actor, list(wanted)))
    return len(wanted)


def update_review(server, actor, slug, ident, body):
    if not isinstance(body,dict) or set(body)-{'revision','owner_status','requester_status','release_key'}:
        raise ValueError('Use revision and owner_status or requester_status/release_key')
    if type(body.get('revision')) is not int or sum(k in body for k in ('owner_status','requester_status')) != 1:
        raise ValueError('Specify the read revision and one status change')
    data = app_revisions.requests(server._conn,actor,slug,ident)
    if not data['requests']:
        raise ValueError('Shared request unavailable')
    row = data['requests'][0]
    if 'owner_status' in body:
        if not row['review']['can_triage']:
            raise ActionError('FORBIDDEN','Only the app owner can triage this request',403)
        if body['owner_status'] not in ('acknowledged','working','deferred'):
            raise ValueError('Use acknowledged, working or deferred')
    else:
        if not row['review']['can_confirm']:
            raise ActionError('FORBIDDEN','Only the requester can confirm a published update',403)
        if body['requester_status'] not in ('fixed','reopened'):
            raise ValueError('Use fixed or reopened')
        if body.get('release_key') != row['review']['release_key']:
            raise ActionError('CONFLICT','A newer release is available; refresh before confirming',409)
    with server._conn() as conn:
        with conn.transaction():
            conn.execute('INSERT INTO rvbbit.app_request_reviews(turn_id,updated_by) VALUES(%s,%s) ON CONFLICT DO NOTHING',(ident,actor))
            current = conn.execute('SELECT * FROM rvbbit.app_request_reviews WHERE turn_id=%s FOR UPDATE',(ident,)).fetchone()
            if current['revision'] != body['revision']:
                raise ActionError('CONFLICT','This request changed; refresh before updating',409)
            conn.execute('UPDATE rvbbit.app_request_reviews SET revision=revision+1, owner_status=%s,'
                'requester_status=%s,release_key=%s,updated_by=%s,updated_at=now() WHERE turn_id=%s',
                (body.get('owner_status',current['owner_status']),body.get('requester_status',current['requester_status']),
                 row['review']['release_key'] if 'requester_status' in body else current['release_key'],actor,ident))
    return app_revisions.requests(server._conn,actor,slug,ident)


def scenarios(server,actor,slug):
    import app_validation
    ensure(server._conn)
    app_validation.load_version(server,actor,slug)
    with server._conn() as conn:
        rows = conn.execute('SELECT s.id,s.name,s.request_id,s.version,s.definition_sha256,s.provenance,s.created_at,'
            '(SELECT jsonb_build_object(\'id\',c.id,\'ready\',c.result->\'ready\',\'version\',c.result->\'version\') '
            'FROM rvbbit.app_preview_checks c WHERE c.scenario_id=s.id AND lower(c.owner_email)=lower(s.owner_email) '
            'ORDER BY c.created_at DESC LIMIT 1) AS last_check '
            'FROM rvbbit.app_preview_scenarios s WHERE s.slug=%s AND lower(s.owner_email)=lower(%s) ORDER BY s.created_at DESC LIMIT 100',
            (slug,actor)).fetchall()
    return {'scenarios':[dict(r) for r in rows]}


def page(data, actor, *, session=None, factory=None):
    import auth
    import calliope_dreams
    import warehouse_theme
    from starlette.responses import HTMLResponse
    assets = Path(__file__).parent / 'calliope'
    version = hashlib.sha256(b''.join((assets / name).read_bytes() for name in
        ('app-review.html', 'app-review.css', 'app-review.js'))).hexdigest()[:16]
    can_administer = bool(session and factory and calliope_dreams.is_company_admin(factory, actor))
    boot = json.dumps({'slug':data.get('slug'), 'request':data.get('selected')},default=str).replace('<','\\u003c')
    document = (assets / 'app-review.html').read_text().replace('__REVIEW_VERSION__', version)
    document = document.replace('__CALLIOPE_HEADER__', warehouse_theme.workspace_header(
        session, active='gallery', can_administer=can_administer))
    document = document.replace('__CALLIOPE_BACKGROUND__', auth.background_layer(
        .74, "linear-gradient(to bottom,rgba(16,13,11,.10),rgba(16,13,11,.40) 86%)", scene_key=actor))
    return HTMLResponse(document.replace('__REVIEW_BOOT__', boot))


def register_tools(mcp,server):
    def app_improvements():
        """List explicitly shared requests for apps you own, plus your own requests.
        Includes needs_attention, release links and requester confirmation. No other chat turns.
        """
        return server._logged('app_improvements',{},lambda:inbox(server,server._require_application_subject().subject))

    def app_preview_scenarios(slug: str):
        """List your private reusable app scenarios. Fixtures are not real-user acceptance."""
        return server._logged('app_preview_scenarios',{'slug':slug},lambda:scenarios(server,server._require_application_subject().subject,slug))

    def save_app_scenario(slug: str, name: str, request_id: str | None = None, version: int | None = None,
                          data: dict | None = None, checks: list | None = None):
        """Save a private reusable fixture for an existing app. Provide a shared request_id
        to replay its captured version, visible bindings and local edits without SQL. Older
        requests are PARTIAL evidence over definition defaults; unmapped fields are reported.
        Or provide a complete data model labelled as an author-supplied fixture. No impersonation.
        Checks: {path,equals}, {component,property,equals}, or {event,context,equals} for
        simulated action inputs. Use check_app_revision to compare a candidate with this case.
        """
        import app_validation
        return server._logged('save_app_scenario',{'slug':slug,'request_id':request_id},lambda:
            app_validation.save_scenario(server,server._require_application_subject().subject,slug,name,request_id,version,data,checks))

    async def check_app_revision(slug: str, version: int | None = None, scenario_id: str | None = None,
                                 document: dict | None = None, html_source: str | None = None,
                                 captures: bool = True, return_image: bool = True):
        """Check one exact app revision using a saved scenario or current authorized reads.
        Supply document (native JSON) or html_source for an unpublished candidate, otherwise
        use the saved version. Returns hash, structural assertions, readiness and durable
        evidence receipt; optional Web/GPUI phone+desktop images. HTML uses the isolated
        read-only capture path, not your interactive computer/browser. No business actions.
        Supplied/request fixtures are distinct from real account or device acceptance.
        Inspect images even when ready=true. Use the returned source hash when publishing.
        """
        import app_validation
        from mcp.server.fastmcp import Image
        actor = server._require_application_subject().subject
        def run():
            result = server._logged('check_app_revision',{'slug':slug,'version':version,'scenario_id':scenario_id},lambda:
                app_validation.check_revision(server,actor,slug,version,scenario_id,document,html_source,captures))
            if not return_image or not isinstance(result,dict) or not result.get('id'):
                return result
            directory = server._live_app_capture_root()/'checks'/result['id']
            paths = sorted(directory.glob('*.png'))
            result['image_order'] = [p.stem for p in paths]
            return [result,*[Image(data=p.read_bytes(),format='png') for p in paths]]
        return await asyncio.to_thread(run)

    for fn in (app_improvements,app_preview_scenarios,save_app_scenario,check_app_revision):
        mcp.tool(name=fn.__name__)(fn)


def register(m,server,actor_for,respond):
    from starlette.responses import FileResponse,RedirectResponse,HTMLResponse
    import os
    import artifact_access
    import app_validation

    async def run(request,callback,write=False,with_session=False):
        actor,session,error = actor_for(request)
        if error:
            if not session and request.url.path.startswith('/apps/'):
                return RedirectResponse('/login?'+urlencode({'next':str(request.url.path)}),status_code=303)
            return error
        body = None
        if write:
            allowed = {str(request.base_url).rstrip('/'),os.environ.get('WAREHOUSE_PUBLIC_URL','').rstrip('/'),os.environ.get('WAREHOUSE_NATIVE_PUBLIC_URL','').rstrip('/')}
            origin = request.headers.get('origin')
            if request.headers.get('sec-fetch-site') == 'cross-site' or origin and origin not in allowed or request.headers.get('content-type','').split(';')[0] != 'application/json':
                return respond({'error':{'message':'Same-origin JSON request required'}},403)
            raw = await request.body()
            if len(raw)>600000:
                return respond({'error':{'message':'Request too large'}},413)
            try:
                body = json.loads(raw)
                if not isinstance(body,dict): raise ValueError()
            except ValueError:
                return respond({'error':{'message':'Expected a JSON object'}},400)
        def work():
            try:
                result = callback(actor,body,session) if with_session else callback(actor,body)
                result.headers['Cache-Control']='private, no-store'
                return result
            except (ActionError,artifact_access.ArtifactAccessError) as exc:
                return respond({'error':{'code':exc.code,'message':str(exc)}},exc.status)
            except (ValueError,KeyError,TypeError) as exc:
                return respond({'error':{'message':str(exc)}},400)
        return await asyncio.get_running_loop().run_in_executor(server._CONTROL_EXECUTOR,contextvars.copy_context().run,work)

    @m.custom_route('/api/apps/review.js',methods=['GET'])
    async def script(request):
        return FileResponse(Path(__file__).parent/'calliope/app-review.js',media_type='text/javascript')

    @m.custom_route('/api/apps/review.css',methods=['GET'])
    async def style(request):
        return FileResponse(Path(__file__).parent/'calliope/app-review.css',media_type='text/css')

    @m.custom_route('/apps/improvements',methods=['GET'])
    async def improvements(request):
        return await run(request,lambda actor,body,session:page({},actor,session=session,factory=server._conn),with_session=True)

    @m.custom_route('/api/app-improvements',methods=['GET'])
    async def all_requests(request):
        return await run(request,lambda actor,body:respond(inbox(server,actor)))

    @m.custom_route('/api/apps/{slug}/changes/{ident}/review',methods=['POST'])
    async def review(request):
        return await run(request,lambda actor,body:respond(update_review(server,actor,request.path_params['slug'],request.path_params['ident'],body)),True)

    @m.custom_route('/api/apps/{slug}/changes/{ident}/replay',methods=['GET'])
    async def replay(request):
        def work(actor,body):
            _,snapshot,_,provenance = app_validation.request_replay(server,actor,request.path_params['slug'],request.path_params['ident'],
                int(request.query_params['version']) if request.query_params.get('version') else None)
            import ui_apps
            if request.query_params.get('format') == 'json':
                return respond(snapshot)
            boot = json.dumps(snapshot).replace('<','\\u003c')
            source = ui_apps.shell().replace('<div id="root">','<script type="application/json" id="calliope-preview-snapshot">'+boot+'</script><div id="root">')
            source = source.replace('<body>','<body><aside style="padding:12px;font:14px sans-serif" role="note">'+html.escape(provenance['notice'])+'</aside>')
            return HTMLResponse(source)
        return await run(request,work)

    @m.custom_route('/api/apps/{slug}/scenarios',methods=['GET','POST'])
    async def saved_scenarios(request):
        def work(actor,body):
            slug=request.path_params['slug']
            if body is None:
                return respond(scenarios(server,actor,slug))
            if set(body)-{'name','request_id','version','data','checks'}: raise ValueError('Unknown scenario property')
            return respond(app_validation.save_scenario(server,actor,slug,**body))
        return await run(request,work,request.method=='POST')

    @m.custom_route('/api/apps/{slug}/checks',methods=['POST'])
    async def check(request):
        def work(actor,body):
            if set(body)-{'version','scenario_id','document','html_source','captures'}: raise ValueError('Unknown check property')
            return respond(app_validation.check_revision(server,actor,request.path_params['slug'],**body))
        return await run(request,work,True)

    @m.custom_route('/api/app-improvements/checks/{ident}/{file}',methods=['GET'])
    async def evidence(request):
        def work(actor,body):
            ensure(server._conn)
            ident=app_revisions.request_id(request.path_params['ident'])
            with server._conn() as conn:
                row=conn.execute('SELECT * FROM rvbbit.app_preview_checks WHERE id=%s AND lower(owner_email)=lower(%s)',(ident,actor)).fetchone()
            if not row: raise ValueError('Private check unavailable')
            app_validation.load_version(server,actor,row['slug'])
            if row['scenario_id']: app_validation.scenario(server,actor,row['slug'],str(row['scenario_id']))
            name=request.path_params['file']
            if name=='evidence.json': return respond(row['result'])
            if name not in ('web-phone.png','web-desktop.png','native-phone.png','native-desktop.png'): raise ValueError('Unknown capture')
            path=server._live_app_capture_root()/'checks'/ident/name
            if not path.is_file(): raise ValueError('Image unavailable; rerun this scenario')
            return FileResponse(path,media_type='image/png')
        return await run(request,work)
