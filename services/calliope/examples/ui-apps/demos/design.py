"""Author deliberate page compositions using the existing shared catalog.

This runs when generating app.json, never in the client or server renderer.
Open sections, work sheets, tinted context and record cards have distinct jobs.
"""
import copy
from ui import UI, bind


def compose(doc):
    fragments = doc['fragments']

    def edit(name):
        ui = UI()
        ui.nodes = fragments[name]['components']
        return ui, {n['id']: n for n in ui.nodes}

    def sheet(ui, nodes, ident, tone='neutral', treatment='outlined'):
        # Preserve the component ID, bindings and event targets inside the sheet.
        node = nodes[ident]
        title = ui.text(ident+'-caption', node['title'], 'title', tone, family='display')
        children = [title]
        if node.get('subtitle'):
            children.append(ui.text(ident+'-intro', node['subtitle'], 'caption', 'muted'))
        children.append(node['child'])
        node.update(component='CSurface', child=ui.stack(ident+'-sheet', children),
                    tone=tone, treatment=treatment, padding='md')
        node.pop('title', None)
        node.pop('subtitle', None)

    def finish(name, ui, children):
        nodes = {n['id']: n for n in ui.nodes}
        nodes['body']['children'] = children
        seen = set()
        def walk(ident):
            if ident in seen: return
            seen.add(ident)
            node = nodes[ident]
            for child in node.get('children', []) + ([node['child']] if 'child' in node else []): walk(child)
        walk('body')
        fragments[name]['components'] = [n for n in ui.nodes if n['id'] in seen]

    def metric(ui, ident, label, path, tone='accent', weight=1):
        return ui.stack(ident, [ui.text(ident+'-label', label, 'eyebrow', 'muted'),
            ui.text(ident+'-value', bind(path), 'metric', tone, family='mono')], gap='xs', weight=weight)

    # A SQL projection over already-bound datasets: no new tables or app routes.
    doc['data']['overview'] = {'value':'$0', 'active':'0', 'open':'0', 'visits':'0',
        'scheduled':'0', 'complete':'0', 'offer':[], 'diligence':[], 'closing':[], 'closed':[]}
    doc['data']['charts']['status'].setdefault('datasets', {})['visits'] = []
    doc['queries']['overview'] = {'sql': """WITH deals AS (SELECT * FROM jsonb_array_elements($1::jsonb)),
      jobs AS (SELECT * FROM jsonb_array_elements($2::jsonb)), tasks AS (SELECT * FROM jsonb_array_elements($3::jsonb))
      SELECT jsonb_build_object(
        'value', '$'||to_char(coalesce((SELECT sum((value->>'price')::numeric) FROM deals WHERE value->>'stage'<>'Closed'),0),'FM999,999,990'),
        'active', (SELECT count(*)::text FROM deals WHERE value->>'stage'<>'Closed'),
        'open', (SELECT count(*)::text FROM tasks WHERE value->>'done'='0'),
        'visits', (SELECT count(*)::text FROM jobs),
        'scheduled', (SELECT count(*)::text FROM jobs WHERE value->>'status' IN ('Scheduled','Dispatched','On site')),
        'complete', (SELECT count(*)::text FROM jobs WHERE value->>'status'='Complete'),
        'offer', coalesce((SELECT jsonb_agg(value) FROM deals WHERE value->>'stage'='Offer'),'[]'::jsonb),
        'diligence', coalesce((SELECT jsonb_agg(value) FROM deals WHERE value->>'stage'='Due diligence'),'[]'::jsonb),
        'closing', coalesce((SELECT jsonb_agg(value) FROM deals WHERE value->>'stage'='Closing'),'[]'::jsonb),
        'closed', coalesce((SELECT jsonb_agg(value) FROM deals WHERE value->>'stage'='Closed'),'[]'::jsonb)) AS overview""",
        'params':[bind('/deals'),bind('/charts/status/datasets/visits'),bind('/tasks')], 'path':'/overview', 'shape':'value'}

    # Repeated records have edges again; activity stays a quiet ruled list.
    for name in ('deal-row','job-row','quote-row','po-row','report-row','equipment-row','task-row','activity-row'):
        ui, nodes = edit(name)
        root = nodes['body']
        children = root['children']
        if name == 'deal-row':
            children = [ui.stack('top', ['stage','price'], 'row', 'sm'), 'open', 'buyer', 'closing']
        elif name == 'job-row':
            children = [ui.stack('top', ['status','open'], 'row', 'sm'), 'detail']
        elif name == 'task-row':
            children = ['title', ui.stack('due-status', ['due','status'], 'row','sm'), ui.stack('action',['complete'],'row','sm')]
        treatment = 'row' if name in ('task-row','activity-row') else 'outlined'
        content = ui.stack('record-content', children, gap='sm')
        root.clear()
        root.update(id='body', component='CSurface', child=content, tone='neutral', treatment=treatment, padding='sm')

    # Short page identity for working tools. Editorial scale belongs to the file
    # itself or the briefing, not an identical giant headline above every tool.
    for name in ('hvac','counter','receiving','dispatch','field','adaptive','case','charts'):
        ui, nodes = edit(name)
        if 'heading' in nodes:
            nodes['heading'].update(size='metric')
            nodes['eyebrow']['tone'] = 'cool' if name in ('hvac','dispatch') else 'warm'
        if 'work' in nodes and len(nodes['work'].get('children', [])) == 2:
            nodes['work']['collapse'] = 'phone'

    # Real estate: a visual dossier, stage lanes, and an editorial briefing.
    ui, n = edit('dossier')
    sheet(ui,n,'working','accent')
    sheet(ui,n,'commitments','warm','wash')
    n['transactions']['weight'] = 1
    file_header = ui.panel('property', [ui.stack('property-top',[
        ui.stack('property-copy',[ui.badge('stage',bind('/deal/stage'),'cool'),
            ui.text('property-address',bind('/deal/address'),'display',family='display'),
            ui.text('property-client',bind('/deal/buyer'),'body','muted'),
            ui.text('property-price',bind('/deal/price_label'),'metric','accent',family='mono')],weight=3),
        ui.add('property-art','CArtwork',asset='neighborhood',size='banner',weight=2)],'row','lg','phone')], 'cool','wash')
    # Keep the repeated selection list at the side; the file owns the main area.
    main = ui.stack('file-and-commitments',['working','commitments'],'row','md','tablet',weight=3)
    finish('dossier',ui,[file_header,'filters',ui.stack('desktop',['transactions',main],'row','lg','phone')])

    ui,n=edit('pipeline')
    lanes=[]
    for key,title,tone in [('offer','Offer','cool'),('diligence','Due diligence','warm'),('closing','Closing','accent'),('closed','Closed','success')]:
        ident='pipeline-'+key
        rows=ui.stack(ident+'-records',[],gap='sm')
        doc['repeat'][rows]={'path':'/overview/'+key,'fragment':'deal-row','key':'id'}
        lanes.append(ui.panel(ident,[ui.badge(ident+'-title',title,tone),rows],tone,'inset',padding='sm',weight=1))
    sheet(ui,n,'working','accent')
    finish('pipeline',ui,['identity','filters',ui.stack('portfolio-strip',[
        metric(ui,'value','ACTIVE PORTFOLIO','/overview/value','cool',2),
        metric(ui,'active','ACTIVE RELATIONSHIPS','/overview/active'),
        metric(ui,'commitment-count','OPEN ON SELECTED FILE','/overview/open','warm')],'row','lg','phone'),
        ui.stack('stage-lanes',lanes,'row','md','tablet'),ui.stack('selected-file',['working','commitments'],'row','lg','phone')])

    ui,n=edit('briefing')
    n['heading']['text']='A little clarity.\nThen, the next move.'
    sheet(ui,n,'commitments','warm','wash')
    sheet(ui,n,'working','accent')
    intro=ui.panel('briefing-cover',['identity',ui.stack('briefing-stats',[
        metric(ui,'portfolio','ACTIVE PORTFOLIO','/overview/value','warm',2),
        metric(ui,'relationships','RELATIONSHIPS','/overview/active')],'row','md','phone')],'warm','wash',weight=3)
    cover=ui.stack('briefing-front',[intro,ui.add('briefing-art','CArtwork',asset='neighborhood',size='portrait',weight=1)],'row','md','phone')
    finish('briefing',ui,[cover,'filters','overview',ui.stack('briefing-work',['transactions','working'],'row','lg','phone')])

    # Operational pages: contained editors against open queues and analytic bands.
    for name,ident,tone in [('hvac','working','accent'),('counter','quote-editor','accent'),
            ('receiving','delivery','warm'),('dispatch','appointment','accent'),
            ('field','report','accent'),('adaptive','working','accent'),('case','file','accent')]:
        ui,n=edit(name)
        sheet(ui,n,ident,tone)
    for name,ident,tone in [('field','reports','cool'),('adaptive','next','warm'),('case','support','cool')]:
        ui,n=edit(name)
        sheet(ui,n,ident,tone,'wash')
    ui,n=edit('hvac')
    for key,tone in [('status','cool'),('revenue','accent'),('mix','warm')]:
        sheet(ui,n,'chart-'+key,tone,'inset')
    stats=ui.panel('operations-pulse',[ui.stack('pulse-metrics',[
        metric(ui,'visit-count','VISITS IN PERIOD','/overview/visits','cool'),
        metric(ui,'in-progress','IN MOTION','/overview/scheduled','warm'),
        metric(ui,'finished','COMPLETE','/overview/complete','success')],'row','lg','phone')],'cool','wash',padding='sm')
    n['body']['children'].insert(2,stats)
    ui,n=edit('case')
    n['file-body']['children'][-1]=ui.stack('save-actions',[n['file-body']['children'][-1]],'row','sm')
    status=ui.panel('case-status',[ui.stack('case-status-line',['phase','reason'],'row','md','phone')],'warm','wash',padding='sm')
    finish('case',ui,['identity',status,'work','tools','activity-section'])
    ui,n=edit('case-overview')
    for ident,tone in [('parts','warm'),('visit','cool')]:
        sheet(ui,n,ident,tone,'inset')
        n[ident]['weight']=1

    # Navigation is a quiet tab strip, with the current page distinguished using
    # the standard button variant. Each page receives its own fragment at build.
    template = doc['components']
    for page in ('dossier','pipeline','briefing','hvac','counter','receiving','dispatch','field','adaptive','case','charts'):
        nodes=copy.deepcopy([n for n in template if n['id']=='navigation' or n['id'].startswith('page-')])
        for node in nodes:
            if node['id']=='navigation':node['id']='links'
            if node['id']=='page-'+page:node['variant']='primary'
        nodes.append({'id':'tabs','component':'CSection','title':'Apps and pages','collapse':'phone','summary':next(n['text'] for n in nodes if n['id']=='page-'+page+'-label'),'child':'links'})
        fragments['nav-'+page]={'root':'tabs','components':nodes}
    doc['components']=[n for n in template if n['id']!='navigation' and not n['id'].startswith('page-')]
    doc['components'].append({'id':'navigation','component':'CStack','children':[],'gap':'none'})
    doc['switch']['navigation']={'path':'/state/page','cases':{p:'nav-'+p for p in ('dossier','pipeline','briefing','hvac','counter','receiving','dispatch','field','adaptive','case','charts')}}
    next(n for n in doc['components'] if n['id']=='root')['gap']='md'

    # Remove obsolete slots after replacing compositions; published definitions
    # contain only reachable component graphs and their own composition rules.
    ids={n['id'] for n in doc['components']} | {n['id'] for f in fragments.values() for n in f['components']}
    for key in ('repeat','switch'):
        doc[key]={ident:rule for ident,rule in doc[key].items() if ident in ids}
