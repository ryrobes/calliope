"""Fictional HVAC field-work fixture and SQL-backed chart view models."""
import datetime as dt
import json
import equipment
import receiving
from pathlib import Path

TECHS = ('Everyone', 'Alex Morgan', 'Sam Rivera', 'Jordan Lee')
STATUSES = ('Scheduled', 'Dispatched', 'On site', 'Complete')
TYPES = ('Maintenance', 'Repair', 'Installation')

QUERIES = {
    'status': """SELECT status, COUNT(*) AS jobs FROM hvac_jobs
      WHERE scheduled_date BETWEEN :start AND :end AND (:tech='Everyone' OR technician=:tech)
      GROUP BY status ORDER BY CASE status WHEN 'Scheduled' THEN 0 WHEN 'Dispatched' THEN 1 WHEN 'On site' THEN 2 ELSE 3 END""",
    'revenue': """SELECT date(scheduled_date, '-6 days', 'weekday 1') AS week, date(scheduled_date, '-6 days', 'weekday 1') AS week_label,
      ROUND(SUM(CASE WHEN status='Complete' THEN amount_cents ELSE 0 END)/100.0,2) AS invoice_value
      FROM hvac_jobs WHERE scheduled_date BETWEEN :start AND :end AND (:tech='Everyone' OR technician=:tech)
      GROUP BY week ORDER BY week""",
    'mix': """SELECT technician, job_type, COUNT(*) AS jobs FROM hvac_jobs
      WHERE scheduled_date BETWEEN :start AND :end AND (:tech='Everyone' OR technician=:tech)
      GROUP BY technician,job_type ORDER BY technician,job_type""",
}

def seed(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS hvac_jobs (
      id TEXT PRIMARY KEY, customer TEXT NOT NULL, address TEXT NOT NULL,
      technician TEXT NOT NULL, job_type TEXT NOT NULL, scheduled_date TEXT NOT NULL,
      slot TEXT NOT NULL, status TEXT NOT NULL, amount_cents INTEGER NOT NULL,
      equipment TEXT NOT NULL, notes TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
      CREATE TABLE IF NOT EXISTS hvac_activity (
      id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, description TEXT NOT NULL,
      source TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);''')
    if db.execute('SELECT 1 FROM hvac_jobs LIMIT 1').fetchone(): return
    today = dt.date.today()
    customers = [('Cedar House','142 Cedar Lane'),('Harbor Dental','8 Harbor View'),('Oak Apartments','306 Oak Street'),('Willow Market','71 Willow Court'),('Station Bakery','19 Station Road'),('Maple Workshop','24 Maple Avenue')]
    for i in range(90):
        name,address = customers[i % len(customers)]
        day = today if i < 6 else today-dt.timedelta(days=1+(i*7)%55)
        status = STATUSES[min(i,3)] if i<6 else 'Complete'
        kind_index = i % 3 if i < 6 else (i // 3) % 3
        kind = TYPES[kind_index]
        amount = [18900,46500,825000][kind_index] + (i%5)*1500
        db.execute('INSERT INTO hvac_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,1)',
          (f'HV-{1001+i}', name, address, TECHS[1+(i%3)], kind, day.isoformat(),
           ['08:00–10:00','10:00–12:00','13:00–15:00'][i%3], status, amount,
           ['Heat pump · 3 ton · installed 2021','Rooftop unit · 7.5 ton · installed 2018','Split system · 2 ton · installed 2023'][i%3],
           ['Verify condensate drain and filter condition.','Diagnose intermittent cooling. Access through side gate.','Commission replacement system; record final readings.'][i%3]))
    db.execute("INSERT INTO hvac_activity(job_id,description,source) VALUES ('HV-1001','Fictional HVAC workspace initialized','Demo')")

def filters(state):
    state = state if isinstance(state,dict) else {}
    tech, days = state.get('technician','Everyone'), state.get('days',28)
    if tech not in TECHS or type(days) is not int or days not in (7,28,56):
        raise ValueError('Choose a listed technician and a 7, 28, or 56 day period.')
    result = {'technician':tech,'days':days}
    selection = state.get('chart_selection')
    if selection is not None:
        if not isinstance(selection,dict): raise ValueError('Invalid chart selection.')
        chart, values = selection.get('chart'), selection.get('values')
        fields = {'status':('status',),'revenue':('week_label',),'mix':('technician','job_type')}.get(chart,()) if isinstance(chart,str) else ()
        if not fields or not isinstance(values,dict) or set(values)!=set(fields): raise ValueError('Invalid chart selection.')
        if chart=='status' and values['status'] not in STATUSES: raise ValueError('Invalid visit status.')
        if chart=='mix' and (values['technician'] not in TECHS[1:] or values['job_type'] not in TYPES): raise ValueError('Invalid visit mix.')
        if chart=='revenue':
            week=dt.date.fromisoformat(values['week_label'])
            if week.weekday()!=0 or week.isoformat()!=values['week_label']: raise ValueError('Invalid reporting week.')
        result['chart_selection']={'chart':chart,'values':values}
    return result


def select_chart(db, state, context):
    chart = {'chart-status':'status','chart-revenue':'revenue','chart-mix':'mix'}.get(context.get('componentId'))
    fields = {'status':('status',),'revenue':('week_label',),'mix':('technician','job_type')}.get(chart,())
    datum = context.get('datum')
    if not fields or not isinstance(datum,dict): raise ValueError('Invalid chart selection.')
    selection={'chart':chart,'values':{k:datum.get(k) for k in fields}}
    selected=filters({**state,'chart_selection':selection})
    _, rows, _=chart_models(db,state)
    if not any(all(row.get(k)==v for k,v in selection['values'].items()) for row in rows[chart]):
        raise ValueError('That chart value is no longer in this reporting period. Refresh and try again.')
    return selected

def chart_models(db, state, theme=None):
    today = dt.date.today()
    params = {'tech':state['technician'],'start':(today-dt.timedelta(days=state['days']-1)).isoformat(),'end':today.isoformat()}
    rows = {name:[dict(r) for r in db.execute(sql,params)] for name,sql in QUERIES.items()}
    t = theme or {}
    accent=t.get('accent','#92bcae'); cool=t.get('cool','#36a8eb'); muted=t.get('muted','#989ca0'); line=t.get('line','#27323d')
    def base(data):
        return {'$schema':'https://vega.github.io/schema/vega-lite/v6.json',
          'data':{'values':data}, 'width':600,'height':260,'autosize':{'type':'fit','contains':'padding'},
          'padding':12,'background':'transparent',
          'config':{'font':'LT Superior','view':{'stroke':None},
            'axis':{'labelColor':muted,'titleColor':muted,'gridColor':line,'domain':False,'tickColor':line,'labelFontSize':11,'titleFontSize':11,'titlePadding':12},
            'legend':{'labelColor':muted,'titleColor':muted}}}
    status={**base(rows['status']), 'mark':{'type':'bar','cornerRadiusEnd':5},
      'encoding':{'x':{'field':'status','type':'nominal','sort':list(STATUSES),'title':None,'axis':{'labelAngle':0,'labelLimit':90}},
        'y':{'field':'jobs','type':'quantitative','title':'Visits','axis':{'tickMinStep':1}},
        'color':{'field':'status','type':'nominal','scale':{'domain':list(STATUSES),'range':[cool,t.get('warm','#ecb317'),accent,t.get('success','#53bf5c')]},'legend':None},
        'tooltip':[{'field':'status','title':'Status'},{'field':'jobs','title':'Visits','type':'quantitative'}]}}
    revenue={**base(rows['revenue']), 'mark':{'type':'line','point':{'filled':True,'size':65},'strokeWidth':3,'color':accent},
      'encoding':{'x':{'field':'week','type':'temporal','scale':{'type':'utc'},'title':None,'axis':{'format':'%b %d','tickCount':4,'labelOverlap':True}},
        'y':{'field':'invoice_value','type':'quantitative','title':'Completed visit value','axis':{'format':'$~s'}},
        'tooltip':[{'field':'week_label','type':'nominal','title':'Week'},{'field':'invoice_value','type':'quantitative','title':'Value','format':'$,.2f'}]}}
    mix={**base(rows['mix']), 'mark':{'type':'rect','cornerRadius':4},
      'encoding':{'x':{'field':'job_type','type':'nominal','title':None,'axis':{'labelAngle':0,'labelLimit':100}},
        'y':{'field':'technician','type':'nominal','title':None,'axis':{'labelLimit':100}},
        'color':{'field':'jobs','type':'quantitative','scale':{'range':[line,cool]},'legend':None},
        'tooltip':[{'field':'technician','title':'Technician'},{'field':'job_type','title':'Work'},{'field':'jobs','type':'quantitative','title':'Visits'}]}}
    mix.pop('mark')
    mix['layer']=[{'mark':{'type':'rect','cornerRadius':4}}, {'mark':{'type':'text','fontSize':12},'encoding':{'text':{'field':'jobs','type':'quantitative'},'color':{'value':t.get('text','#e6eceb')}}}]
    return {'status':status,'revenue':revenue,'mix':mix}, rows, params

def snapshot(store,selected,state,notice='',_db=None):
    from contextlib import nullcontext
    from presentation import validate_components,SPEC
    state=filters(state)
    with (nullcontext(_db) if _db is not None else store.connect()) as db:
        selection=state.get('chart_selection')
        params={'tech':state['technician'],'today':dt.date.today().isoformat()}
        where="scheduled_date>=:today AND (:tech='Everyone' OR technician=:tech)"
        if selection:
            params.update(start=(dt.date.today()-dt.timedelta(days=state['days']-1)).isoformat(), **selection['values'])
            where="scheduled_date BETWEEN :start AND :today AND (:tech='Everyone' OR technician=:tech)"
            where += {'status':' AND status=:status','mix':' AND technician=:technician AND job_type=:job_type','revenue':" AND date(scheduled_date,'-6 days','weekday 1')=:week_label AND status='Complete'"}[selection['chart']]
        order='scheduled_date DESC,slot,id' if selection else 'scheduled_date,slot,id'
        jobs=[dict(r) for r in db.execute('SELECT * FROM hvac_jobs WHERE '+where+' ORDER BY '+order+' LIMIT 30',params)]
        job=next((j for j in jobs if j['id']==selected),jobs[0] if jobs else None)
        if not selection and selected and selected.startswith('HV-'):
            exact=db.execute("SELECT * FROM hvac_jobs WHERE id=? AND (?='Everyone' OR technician=?)",(selected,state['technician'],state['technician'])).fetchone()
            if exact: job=dict(exact)
        linked=db.execute('SELECT quote_id FROM counter_orders WHERE visit_id=?',(job['id'],)).fetchone() if job else None
        linked_quote=linked['quote_id'] if linked else None
        asset=equipment.for_visit(db,job['id']) if job else None
        parts=receiving.readiness(db,job['id']) if job else None
        theme_path=store.path.parent/'business-theme.json'
        try: theme=json.loads(theme_path.read_text())
        except (OSError,ValueError): theme={}
        charts,rows,params=chart_models(db,state,theme)
        revision=store.revision(db)
        activity=[dict(r) for r in db.execute('SELECT * FROM hvac_activity WHERE job_id=? ORDER BY id DESC LIMIT 3',(job['id'],))] if job else []
    data={'draft':{k:job[k] for k in ('id','version','notes')} if job else {},
      'hvac_filter':{'technician':[state['technician']],'days':[str(state['days'])]},'charts':charts,
      'chart_rows':rows,'query_params':params,'job':job,'jobs':jobs,'equipment':asset,'parts_readiness':parts}
    components=definition(jobs,job,activity,state,rows,linked_quote,asset,parts)
    validate_components(components)
    return {'messages':[
      {'version':'v0.9','createSurface':{'surfaceId':'transaction-desk','catalogId':SPEC['id']}},
      {'version':'v0.9','updateComponents':{'surfaceId':'transaction-desk','components':components}},
      {'version':'v0.9','updateDataModel':{'surfaceId':'transaction-desk','path':'/','value':data}}],
      'selected':job['id'] if job else 'HV-1001','owner':'Everyone','state':state,'revision':revision,
      'draft':data['draft'],'view':'hvac','notice':notice,'application':'calliope-transaction-desk'}

def definition(jobs,job,activity,state,rows,linked_quote=None,asset=None,parts=None):
    from ui import UI
    u=UI();c=u.nodes
    def add(id,component,**p): c.append({'id':id,'component':component,**p}); return id
    def text(id,value,size='body',tone='normal',family='body',**p): return add(id,'CText',text=value,size=size,tone=tone,family=family,**p)
    def stack(id,children,direction='column',gap='md',collapse='never',**p): return add(id,'CStack',children=children,direction=direction,gap=gap,collapse=collapse,**p)
    def panel(id,children,tone='neutral',treatment='outlined',**p): return add(id,'CSurface',child=stack(id+'-body',children),tone=tone,treatment=treatment,padding='md',**p)
    def button(id,label,name,context=None,primary=False): return add(id,'Button',child=text(id+'-text',label),variant='primary' if primary else 'borderless',action={'event':{'name':name,'context':context or {}}})
    def badge(id,label,tone='neutral'): return add(id,'CBadge',text=label,tone=tone)
    bind=lambda path:{'path':path}
    nav=stack('views',[button('view-'+v,label,'set_view',{'view':v},v=='hvac') for v,label in [('dossier','01 Dossier'),('pipeline','02 Pipeline'),('briefing','03 Briefing'),('hvac','04 HVAC'),('counter','05 Parts desk'),('receiving','06 Receiving'),('dispatch','07 Dispatch'),('field','08 Field notebook'),('adaptive','09 Adaptive')]],'row','sm',wrap=True)
    controls=u.filters('hvac-filters','Filter visits','/hvac_filter','hvac_filter',[
      u.picker('tech-filter','Technician','/hvac_filter/technician',[(v,v) for v in TECHS]),
      u.picker('period-filter','Reporting period','/hvac_filter/days',[(str(d),f'{d} days') for d in (7,28,56)])])
    total=sum(r['invoice_value'] for r in rows['revenue'])
    header=panel('hvac-hero',[text('hvac-kicker','NORTHSTAR SERVICE / OPERATIONS','eyebrow','cool'),text('hvac-heading','A clearer day in the field.','display',family='display'),text('hvac-deck','Your visit queue, customer context, and the work behind the numbers.','body','muted'),stack('hvac-facts',[
      stack('today-fact',[text('today-count',str(len(jobs)),'metric','cool','mono'),text('today-label','visits in the work list','caption','muted')],weight=1),
      stack('period-fact',[text('period-value',f'${total:,.0f}','metric','accent','mono'),text('period-label',f'completed visit value · last {state["days"]} days','caption','muted')],weight=2)],'row','lg','phone')],'cool','plain')
    cards=[]
    for j in jobs:
      p='job-'+j['id']; cards.append(panel(p,[stack(p+'-top',[badge(p+'-status',j['status'],'success' if j['status']=='Complete' else 'cool'),text(p+'-slot',j['scheduled_date']+' · '+j['slot'],'caption','muted')],'row','sm','phone'),button(p+'-open',j['customer'],'hvac_select',{'id':j['id']},job and j['id']==job['id']),text(p+'-type',j['job_type']+' · '+j['technician'],'caption','muted')],treatment='row'))
    detail=[]
    if job:
      identity={'id':job['id'],'version':bind('/draft/version')}
      detail=[panel('visit-detail',[badge('job-status',job['status'],'cool'),text('job-heading',job['customer'],'title',family='display'),text('job-address',job['address'],'body','muted'),text('job-schedule',job['scheduled_date']+' · '+job['slot'],'body'),text('job-equipment',job['equipment'],'caption','muted'),
        text('job-price',f'${job["amount_cents"]/100:,.2f}','metric','accent','mono'),
        add('hvac-notes','TextField',label='Visit notes',value=bind('/draft/notes'),variant='longText'),
        stack('hvac-edit',[button('hvac-save','Save notes','save_deal',{**identity,'notes':bind('/draft/notes')},True),button('hvac-discard','Discard / reload','discard',identity)],'row','sm','phone'),
        *([button('hvac-advance','Next: '+STATUSES[STATUSES.index(job['status'])+1],'hvac_advance',identity,True)] if job['status']!='Complete' else [badge('visit-done','Visit complete','success')]),
        text('job-operation-note','Status changes update the charts. This local sample does not send messages or create invoices.','caption','muted')])]
      if parts: detail.insert(0,panel('visit-parts-ready',[badge('visit-ready-badge','All parts reserved' if not parts['shortage'] else 'Waiting on backordered parts','success' if not parts['shortage'] else 'warm'),button('visit-inspect-order','Inspect '+parts['id'],'open_record',{'kind':'order','recordId':parts['id']})]))
      if asset: detail.append(panel('field-equipment',[text('field-equipment-title','Equipment file','title',family='display'),badge('field-equipment-id',asset['id'],'cool'),text('field-equipment-name',asset['name']),text('field-equipment-location',asset['location'],'caption','muted'),text('field-equipment-identity',asset['model']+' · '+asset['serial'],'caption',family='mono'),text('field-equipment-notes',asset['service_notes'],'caption','muted')]))
      if linked_quote: detail.append(button('visit-parts','Open linked parts order ↗','set_view',{'view':'counter','id':linked_quote}))
      if activity: detail.append(panel('hvac-log',[text('hvac-log-heading','Work log','title',family='display')]+[text('hvac-log-'+str(a['id']),a['source']+' · '+a['description'],'caption','muted') for a in activity],treatment='plain'))
    selection=state.get('chart_selection')
    work_heading=[]
    if selection:
      work_heading += [text('chart-selection-label',' / '.join(selection['values'].values())+' · up to 30 matching visits','caption','cool'),button('chart-clear','Clear chart selection','hvac_chart_clear')]
    work=stack('hvac-work',[u.section('hvac-list','Visit work list',work_heading+cards,subtitle='Select a visit to work through its details.',weight=2),u.section('hvac-detail','Visit workspace',detail,tone='accent',weight=3)],'row','lg','tablet')
    charts=[]
    for key,title,subtitle in [('status','Work in motion','Visits by status'),('revenue','The shape of the month','Completed visit value by week'),('mix','Where the work lands','Visit mix by technician')]:
      charts.append(panel('chart-panel-'+key,[text('chart-title-'+key,title,'title',family='display'),text('chart-subtitle-'+key,subtitle,'caption','muted'),add('chart-'+key,'CVegaChart',spec=bind('/charts/'+key),height=260,description=subtitle,onSelect='hvac_chart_select'),text('chart-count-'+key,f'{len(rows[key])} grouped rows · click a mark to inspect visits','caption','muted')],treatment='inset',weight=1))
    chartrow=stack('hvac-charts',charts,'row','md','tablet')
    stack('root',[nav,header,controls,u.section('hvac-overview','Service overview',[chartrow],subtitle='Select a chart mark to explore the visits behind the numbers.',tone='cool'),work],gap='lg')
    return c
