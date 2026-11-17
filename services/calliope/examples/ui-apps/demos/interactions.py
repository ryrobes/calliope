"""Exercise shared interaction primitives in the published regression workspace."""
from ui import UI, bind, scroll_area, record_item, reactive_region


def compose(doc):
    fragments=doc['fragments']
    def edit(name):
        ui=UI();ui.nodes=fragments[name]['components']
        return ui,{n['id']:n for n in ui.nodes}
    def item(field):return {'item':'/'+field}
    # Lists use the same selected record as the editor. Whole rows are keyboard
    # accessible buttons; their IDs remain stable when queries refresh.
    for name,label,detail,badge,kind in [('job-row','customer','detail','status','job'),
            ('quote-row','id','customer_id','status','quote'),('po-row','id','detail','branch','po'),
            ('deal-row','address','price_label','stage','deal')]:
        ui=UI();root=record_item(ui,'body',item(label),item(detail),item('id'),bind('/state/selection/'+kind),'select_'+kind,item(badge))
        fragments[name]={'root':root,'components':ui.nodes}

    # Each bounded work list gets its own scroll position. Charts and forms
    # remain outside this boundary; a wheel at the list edge stays in the list.
    lists={'adaptive':['adaptive-jobs'],'hvac':['job-list'],'field':['field-jobs','report-list'],
           'dispatch':['dispatch-jobs'],'receiving':['po-list'],'counter':['quote-list'],
           'dossier':['deal-list'],'briefing':['deal-list']}
    for page,ids in lists.items():
        ui,nodes=edit(page)
        for ident in ids:
            parent=next(n for n in ui.nodes if ident in n.get('children',[]))
            wrapper=scroll_area(ui,ident+'-scroll','Submitted reports' if ident=='report-list' else 'Choose a record',ident)
            parent['children']=[wrapper if child==ident else child for child in parent['children']]
    # Keep phone navigation secondary to the selected record. These are shared
    # disclosure sections, not renderer checks for demo IDs or industry names.
    for page in ('field','hvac','adaptive','dispatch','receiving','counter','dossier'):
        ui,nodes=edit(page)
        selection = '/job/customer' if page in ('field','hvac','adaptive','dispatch') else '/po/id' if page=='receiving' else '/quote/id' if page=='counter' else '/deal/address'
        for node in list(ui.nodes):
            if node['component']=='CSection' and node.get('title') in ('Visits','Visit work list','Visit context','Purchase orders','Quotes','Transactions'):
                node.update(collapse='phone',summary=bind(selection))
        if 'filters' in nodes:
            nodes['filters']['id']='filter-controls'
            ui.nodes.append({'id':'filters','component':'CSection','title':'Find work','summary':'Search and filters','collapse':'phone','child':'filter-controls'})
    # Overrides are contextual and explicit. Navigation or a different visit
    # resumes automatic mode rather than leaking a held result to another page.
    for event in doc['events']:
        if event.startswith('page_') or event=='select_job':
            doc['events'][event].setdefault('set',{})['/state/controls/focus']=['auto']
    doc['events']['hold_decision']={'set':{'/state/controls/focus':[bind('/context/label')]}}
    doc['events']['resume_decision']={'set':{'/state/controls/focus':['auto']}}
    for name,label,event in [('decision-hold','Hold this view','hold_decision'),('decision-resume','Resume automatic','resume_decision')]:
        ui=UI();button=ui.button('toggle',label,event,{'label':bind('/decision/label')})
        root=ui.stack('body',[button],'row','sm')
        fragments[name]={'root':root,'components':ui.nodes}
    for page,container,target in [('adaptive','next-body','adaptive-support'),('case','support-body','case-support')]:
        ui,nodes=edit(page)
        controls=ui.stack(page+'-decision-controls',[])
        doc['switch'][controls]={'path':'/state/controls/focus','cases':{'auto':'decision-hold'},'default':'decision-resume'}
        region=reactive_region(ui,page+'-decision-region','Suggested workspace',target,bind('/decision/status'),bind('/decision/label'))
        nodes[container]['children']=[region,controls]
        ui.nodes[:]=[n for n in ui.nodes if n['id'] not in ('focus','decision-status')]
        if page=='adaptive':
            nodes['purpose']['text']='Choose a visit and describe what you find. Callie adjusts the next workspace as the context changes.'

    # Branches expose different useful work, making the switch meaningful.
    for key,title,tone in [('diagnose','Make the symptom concrete.','cool'),('parts','Bring the right part.','accent'),
            ('comeback','Pick up the whole story.','warm'),('clarify','A little more context.','neutral')]:
        ui=UI();children=[ui.badge('kind',key.upper(),tone),ui.text('heading',title,'title',family='display')]
        if key=='diagnose':
            children += [ui.text('equipment',bind('/job/equipment'),'caption','muted'),
                ui.text('prompt','Capture when the symptom appears and compare temperature and pressure readings.'),
                ui.button('findings','Record field readings','page_field')]
        elif key=='parts':
            children += [ui.text('equipment',bind('/job/equipment'),'caption','muted'),
                ui.panel('readiness',[ui.text('label','Current parts readiness','eyebrow','muted'),ui.text('parts',bind('/job/parts'))],tone,'inset'),
                ui.stack('tools',[ui.button('parts-desk','Open parts desk','page_counter'),ui.button('delivery','Check deliveries','page_receiving')],'row','sm',wrap=True)]
        elif key=='comeback':
            children += [ui.panel('previous',[ui.text('label','Saved visit notes','eyebrow','muted'),ui.text('notes',bind('/job/notes'),'caption')],tone,'inset'),
                ui.text('prompt','Compare the earlier findings with what returned before planning another visit.'),
                ui.button('reports','Review field reports','page_field')]
        else:
            children += [ui.text('prompt','What changed? Is a failed part confirmed, or has the same symptom returned after a repair?'),
                ui.text('help','Add that context to the working notes. The next workspace updates automatically.','caption','muted')]
        root=ui.panel('body',children,tone,'wash')
        fragments['next-'+key]={'root':root,'components':ui.nodes}
