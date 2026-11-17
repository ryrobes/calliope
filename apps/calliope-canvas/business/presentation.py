"""Three compositions, one small catalog. No view-specific renderer components."""
import datetime as dt
import json
import re
from pathlib import Path

SPEC=json.loads((Path(__file__).parent/'catalog.json').read_text())
VIEWS=('dossier','pipeline','briefing','hvac','counter','receiving','dispatch','field','adaptive','case')
STAGES=('Offer','Due diligence','Closing','Closed')
TONES={'Offer':'cool','Due diligence':'warm','Closing':'accent','Closed':'success'}

def definition(deals, selected, tasks, activity, all_tasks, view):
    c=[]
    def add(id,component,**p):
        c.append({'id':id,'component':component,**p}); return id
    def text(id,value,size='body',tone='normal',family='body',**p):
        return add(id,'CText',text=value,size=size,tone=tone,family=family,**p)
    def stack(id,children,direction='column',gap='md',collapse='never',**p):
        return add(id,'CStack',children=children,direction=direction,gap=gap,collapse=collapse,**p)
    def panel(id,children,tone='neutral',treatment='soft',padding='md',**p):
        return add(id,'CSurface',child=stack(id+'-content',children),tone=tone,treatment=treatment,padding=padding,**p)
    def badge(id,value,tone='neutral'):
        return add(id,'CBadge',text=value,tone=tone)
    def button(id,label,name,context=None,primary=False):
        return add(id,'Button',child=text(id+'-label',label),variant='primary' if primary else 'borderless',action={'event':{'name':name,'context':context or {}}})
    def record_button(deal,prefix):
        return button(prefix+'-open','Open dossier ↗','select_deal',{'id':deal['id'],'view':'dossier'})
    bind=lambda p:{'path':p}
    identity={'id':selected['id'],'version':bind('/draft/version')}
    done=sum(t['done'] for t in tasks)
    selected_tone=TONES[selected['stage']]

    navigation=stack('views',[button('view-'+v,label,'set_view',{'view':v},v==view)
        for v,label in [('dossier','01  Dossier'),('pipeline','02  Pipeline'),('briefing','03  Briefing'),('hvac','04  HVAC'),('counter','05  Parts desk'),('receiving','06 Receiving'),('dispatch','07 Dispatch'),('field','08 Field notebook'),('adaptive','09 Adaptive'),('case','10 Case workspace')]],'row','sm',wrap=True)
    filterbar=add('filters','CFilterBar',label='Your portfolio',value=bind('/filter'),onChange='filter',children=[add('owner-filter','CRecordPicker',label='Portfolio owner',value=bind('/filter/owner'),options=[{'label':x,'value':x} for x in ['Everyone','Alex Morgan','Sam Rivera']])])
    form=panel('working-file',[
        stack('file-heading',[text('file-title','Working file','title',family='display'),badge('version',f"REV {selected['version']:02d}")],'row','md','phone'),
        text('file-note','A shared record. Your edits stay a draft until you save.','caption','muted'),
        stack('form-fields',[add('edit-owner','TextField',label='Owner · Alex Morgan or Sam Rivera',value=bind('/draft/owner'),weight=1),add('edit-closing','TextField',label='Closing date · YYYY-MM-DD',value=bind('/draft/closing_date'),weight=1)],'row','md','phone'),
        add('edit-notes','TextField',label='Working notes',variant='longText',value=bind('/draft/notes')),
        stack('edit-buttons',[button('save','Save changes','save_deal',{**identity,**{k:bind('/draft/'+k) for k in ['owner','closing_date','notes']}},True),button('discard','Discard / reload','discard',{'id':selected['id']})],'row','sm','phone')
    ],treatment='outlined')
    commitments=[text('commitments-heading','The path to closing','title',family='display'),add('completion','CMeter',label='Commitments complete',value=done/max(1,len(tasks)),tone=selected_tone)]
    for i,t in enumerate(tasks):
        p='task-'+t['id']; contents=[stack(p+'-heading',[badge(p+'-number',f'{i+1:02d}','success' if t['done'] else selected_tone),text(p+'-title',t['title'],weight=1)],'row','sm'),text(p+'-due',('Completed' if t['done'] else 'Due '+t['due']),'caption','muted')]
        if not t['done']: contents.append(button(p+'-complete','Mark complete','complete_task',{**identity,'task_id':t['id']}))
        commitments.append(panel(p,contents,'success' if t['done'] else 'neutral','plain','sm'))
    if selected['stage']!='Closed': commitments.append(button('advance','Advance transaction →','advance_deal',identity,True))
    else: commitments.append(badge('closed','Closed · packet retained','success'))
    checklist=panel('checklist',commitments,selected_tone,'wash')
    log=panel('work-log',[text('work-log-title','Work log','title',family='display')]+[
        stack(f'log-{a["id"]}',[text(f'log-{a["id"]}-source',a['source']+' / '+a['created'][:16],'eyebrow','muted'),text(f'log-{a["id"]}-body',a['description'],'caption')],'column','xs') for a in activity],treatment='plain',padding='sm')

    if view=='dossier':
        hero=panel('hero',[
            stack('hero-top',[badge('record-stage',selected['stage'],selected_tone),text('record-id','PROPERTY FILE / '+selected['id'].upper(),'eyebrow','muted')],'row','md','phone'),
            stack('hero-body',[
                stack('hero-copy',[text('address',selected['address'],'display',family='display'),text('area',selected['neighborhood'],'body','muted'),text('buyer','Prepared for '+selected['buyer'],'body'),text('price',f"${selected['price']:,}",'metric',selected_tone, 'mono')],weight=3),
                add('hero-art','CArtwork',asset='neighborhood',size='banner',weight=2)
            ],'row','lg','tablet'),
            stack('hero-facts',[stack('closing-fact',[text('closing-label','TARGET CLOSE','eyebrow','muted'),text('closing-value',selected['closing_date'],'title')],weight=1),stack('owner-fact',[text('owner-label','RELATIONSHIP LEAD','eyebrow','muted'),text('owner-value',selected['owner'],'title')],weight=1),stack('readiness-fact',[text('readiness-label','READINESS','eyebrow','muted'),text('readiness-value',f'{done} of {len(tasks)} complete','title')],weight=1)],'row','md','phone')
        ],selected_tone,'wash','lg')
        folios=[]
        for d in deals:
            p='folio-'+d['id']
            folios.append(panel(p,[badge(p+'-stage',d['stage'],TONES[d['stage']]),button('deal-'+d['id'],d['address'],'select_deal',{'id':d['id']},d['id']==selected['id']),text(p+'-date','Close '+d['closing_date'],'caption','muted')],TONES[d['stage']] if d['id']==selected['id'] else 'neutral','outlined','sm'))
        body=[hero,stack('dossier-columns',[stack('dossier-main',[form,log],weight=3),stack('dossier-side',[checklist],weight=2)],'row','lg','tablet'),text('portfolio-heading','Elsewhere in your portfolio','title',family='display'),stack('folio-grid',folios,'row','sm','tablet')]
    elif view=='pipeline':
        active=[d for d in deals if d['stage']!='Closed']
        intro=stack('pipeline-intro',[stack('pipeline-title',[text('pipeline-kicker','PORTFOLIO / FLOW OF WORK','eyebrow','cool'),text('pipeline-heading','Every deal, moving forward.','display',family='display'),text('pipeline-deck','Follow the handoffs. Open any record to work through its next step.','body','muted')],weight=3),panel('pipeline-total',[text('total-label','ACTIVE PORTFOLIO','eyebrow','muted'),text('total-value',f"${sum(d['price'] for d in active):,}",'metric','cool','mono'),text('total-count',f'{len(active)} active transactions','caption','muted')],'cool','wash',weight=2)],'row','lg','tablet')
        lanes=[]
        for stage in STAGES:
            p='lane-'+stage.lower().replace(' ','-'); members=[d for d in deals if d['stage']==stage]
            cards=[]
            for d in members:
                q='pipeline-'+d['id']; related=[t for t in all_tasks if t['deal_id']==d['id']]; finished=sum(t['done'] for t in related)
                cards.append(panel(q,[text(q+'-area',d['neighborhood'].split(' · ')[0],'eyebrow','muted'),text(q+'-address',d['address'],'title',family='display'),text(q+'-price',f"${d['price']:,}",'body',TONES[stage],'mono'),text(q+'-buyer',d['buyer'],'caption','muted'),add(q+'-progress','CMeter',label='Ready for handoff',value=finished/max(1,len(related)),tone=TONES[stage]),text(q+'-date','Close '+d['closing_date'],'caption'),record_button(d,q)],treatment='outlined',padding='md'))
            if not cards: cards=[text(p+'-empty','Room for the next opportunity.','caption','muted')]
            lanes.append(panel(p,[stack(p+'-head',[badge(p+'-badge',stage,TONES[stage]),text(p+'-count',f'{len(members):02d}','title','muted','mono')],'row','sm')]+cards,TONES[stage],'wash','sm',weight=1))
        body=[intro,stack('pipeline-lanes',lanes,'row','md','tablet'),text('pipeline-note','Stage changes happen in the dossier after its commitments are complete.','caption','muted')]
    else:
        today=dt.date.today(); pending=sorted([t for t in all_tasks if not t['done'] and any(d['id']==t['deal_id'] for d in deals)],key=lambda t:t['due'])
        urgent=[t for t in pending if t['due']<=today.isoformat()]
        intro=panel('briefing-hero',[text('briefing-date',today.strftime('%A / %d %B %Y').upper(),'eyebrow','warm'),text('briefing-title','A little clarity.\nThen, the next move.','display',family='display'),text('briefing-subtitle',f"{len(pending)} open commitments across your portfolio. {len(urgent)} due today or earlier.",'body','muted')],'warm','wash','lg')
        agenda=[]
        for i,t in enumerate(pending[:6]):
            d=next(d for d in deals if d['id']==t['deal_id']); p='agenda-'+t['id']; days=(dt.date.fromisoformat(t['due'])-today).days
            agenda.append(panel(p,[stack(p+'-row',[stack(p+'-when',[text(p+'-day',str(abs(days)) if days else '0','metric','warm','mono'),text(p+'-unit','DAYS OVERDUE' if days<0 else 'DAYS TO GO','eyebrow','muted')],weight=1),stack(p+'-copy',[badge(p+'-stage',d['stage'],TONES[d['stage']]),text(p+'-task',t['title'],'title',family='display'),text(p+'-record',d['address']+' · '+d['owner'],'caption','muted'),record_button(d,p)],weight=4)],'row','md','phone')], 'warm' if days<=0 else 'neutral','outlined'))
        if not agenda: agenda=[panel('clear',[text('clear-title','Everything is in hand.','title',family='display'),text('clear-copy','No open commitments in this portfolio.','body','muted')],'success','wash')]
        nextclose=next((d for d in deals if d['stage']!='Closed'),None)
        aside=[add('briefing-art','CArtwork',asset='neighborhood',size='portrait'),panel('portfolio-pulse',[text('pulse-title','Portfolio pulse','title',family='display'),text('pulse-active',str(sum(d['stage']!='Closed' for d in deals)),'metric','accent','mono'),text('pulse-label','active relationships','caption','muted')],'accent','wash')]
        if nextclose: aside.append(panel('next-close',[badge('next-close-label','NEXT CLOSING','accent'),text('next-close-address',nextclose['address'],'title',family='display'),text('next-close-date',nextclose['closing_date'],'body','muted'),record_button(nextclose,'next-close')],treatment='plain'))
        body=[intro,stack('briefing-columns',[stack('agenda',[text('agenda-title','On the horizon','title',family='display')]+agenda,weight=3),stack('briefing-aside',aside,weight=2)],'row','lg','tablet')]
    stack('root',[navigation,filterbar]+body,gap='lg')
    # Shared form components are only mounted in the dossier. Remove orphans so
    # every emitted node belongs to this composition, including action labels.
    nodes={x['id']:x for x in c}; reachable=set()
    def walk(id):
        if id in reachable: return
        reachable.add(id); n=nodes[id]
        for child in n.get('children',[])+([n['child']] if 'child' in n else []): walk(child)
    walk('root')
    return [n for n in c if n['id'] in reachable]

RESULT_FORMATS={'text','integer','plain','decimal','boolean','time','json'}

def validate_components(components):
    """Bound the custom vocabulary and reject malformed graphs before rendering."""
    if not 0<len(components)<=500: raise ValueError('Invalid component count')
    nodes={n['id']:n for n in components}
    if len(nodes)!=len(components): raise ValueError('Duplicate component ID')
    if sum(n['component']=='CFilterBar' for n in components)>1: raise ValueError('Use one live filter bar per page')
    builtin={'Button','TextField','ChoicePicker','CheckBox'}
    for n in components:
        kind=n['component']
        if kind in builtin: continue
        if kind not in SPEC['components']: raise ValueError('Unknown component: '+kind)
        props=SPEC['components'][kind]
        for key,value in n.items():
            if key in ('id','component'): continue
            if key not in props: raise ValueError('Unknown property: '+key)
            rule=props[key]
            if rule=='filterEvent' and not (isinstance(value,str) and re.fullmatch(r'(filter|[a-z][a-z0-9_]*_filter)',value)): raise ValueError('Live filters require a read-only filter action')
            if rule=='filterDelay' and (type(value) is not int or not 100<=value<=1000): raise ValueError('Invalid filter debounce')
            if rule=='eventName' and not (isinstance(value,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,99}',value)): raise ValueError('Invalid chart action name')
            if isinstance(rule,list) and value not in rule: raise ValueError('Unknown design token: '+str(value))
            if rule=='scrollHeight' and value!='available' and (type(value) is not int or not 160<=value<=1000): raise ValueError('Scroll height must be available or 160..1000 pixels')
            if rule=='scrollMinimum' and (type(value) is not int or not 160<=value<=600): raise ValueError('Minimum scroll height must be 160..600 pixels')
            if rule=='weight' and (type(value) not in (int,float) or not 0<value<=12): raise ValueError('Invalid weight')
            if rule=='fraction' and (type(value) not in (int,float) or not 0<=value<=1): raise ValueError('Invalid progress')
            if rule=='text' and not (isinstance(value,str) or isinstance(value,dict) and set(value)=={'path'} and isinstance(value['path'],str)): raise ValueError('Invalid text binding')
            if rule=='objectBinding' and not (isinstance(value,dict) and set(value)=={'path'} and isinstance(value['path'],str) and value['path'].startswith('/')): raise ValueError('Chart requires an absolute model data binding')
            if rule=='dataBinding' and not (isinstance(value,dict) and set(value)=={'path'} and isinstance(value['path'],str) and value['path'].startswith('/')): raise ValueError('Invalid data binding')
            if rule=='booleanBinding' and not (type(value) is bool or isinstance(value,dict) and set(value)=={'path'} and isinstance(value['path'],str)): raise ValueError('Invalid boolean binding')
            if rule=='literalText' and not isinstance(value,str): raise ValueError('Invalid literal string')
            if rule=='precision' and (type(value) is not int or not 0<=value<=4): raise ValueError('Invalid precision')
            if rule=='maxLength' and (type(value) is not int or not 1<=value<=10000): raise ValueError('Invalid field length')
            if rule=='rowKeys' and not (isinstance(value,list) and len(value)<=100 and all(isinstance(v,str) for v in value) and len(set(value))==len(value)): raise ValueError('Invalid row identities')
            if rule=='resources' and not (isinstance(value,list) and 1<=len(value)<=12 and all(isinstance(x,str) and len(x)<=100 for x in value)): raise ValueError('Invalid resource lanes')
            if rule=='minute' and not (type(value) is int and 0<=value<=1440): raise ValueError('Invalid day range')
            if rule=='positions' and not (isinstance(value,list) and len(value)<=100 and all(isinstance(x,dict) and set(x)=={'lane','start','end'} and all(type(v) is int for v in x.values()) and 0<=x['lane']<12 and 0<=x['start']<x['end']<=1440 for x in value)): raise ValueError('Invalid appointment positions')
            if rule=='boolean' and type(value) is not bool: raise ValueError('Invalid boolean')
            if rule=='columns' and not (isinstance(value,list) and 1<=len(value)<=8 and all(isinstance(v,dict) and set(v)<= {'label','weight'} and isinstance(v.get('label'),str) and type(v.get('weight',1)) in (int,float) and 0<v.get('weight',1)<=12 for v in value)): raise ValueError('Invalid grid columns')
            if rule=='options' and not (isinstance(value,list) and 1<=len(value)<=100 and all(isinstance(v,dict) and set(v)=={'label','value'} and all(isinstance(x,str) for x in v.values()) for v in value) and len({v['value'] for v in value})==len(value)): raise ValueError('Invalid record options')
            if rule=='stringListBinding' and not (isinstance(value,dict) and set(value)=={'path'} and isinstance(value['path'],str) and value['path'].startswith('/')): raise ValueError('Invalid selection binding')
            if rule=='chartHeight' and (type(value) is not int or not 180<=value<=960): raise ValueError('Invalid chart height')
            if rule=='resultColumns' and not (isinstance(value,list) and 1<=len(value)<=256 and all(isinstance(v,dict) and set(v)<={'name','type','format'} and isinstance(v.get('name'),str) and 0<len(v['name'])<=200 and isinstance(v.get('type','text'),str) and v.get('format','text') in RESULT_FORMATS for v in value)): raise ValueError('Invalid result columns')
            if rule=='count' and (type(value) is not int or value<0): raise ValueError('Invalid count')
            if rule=='visibleRows' and (type(value) is not int or not 3<=value<=50): raise ValueError('Visible rows must be 3..50')
            if rule=='child' and not isinstance(value,str): raise ValueError('Invalid child')
            if rule=='children' and not (isinstance(value,list) and all(isinstance(v,str) for v in value)): raise ValueError('Invalid children')
        for key in ('child','children','text','label','value','asset','spec','description','columns','options','onChange','title','rows'):
            if key in props and key not in n: raise ValueError('Missing property: '+key)
        if kind=='CResourceDay' and (not n.get('resources') or len(n.get('positions',[]))!=len(n['children']) or not 0<=n.get('start',360)<n.get('end',1200)<=1440 or any(x['lane']>=len(n['resources']) or x['start']<n.get('start',360) or x['end']>n.get('end',1200) for x in n.get('positions',[]))): raise ValueError('Invalid resource day layout')
        if kind=='CDataGrid' and 'rowKeys' in n and len(n['rowKeys'])!=len(n['children'])//len(n['columns']): raise ValueError('Grid row identities do not match cells')
        if kind=='CDataGrid' and (len(n['children'])>160 or len(n['children'])%len(n['columns'])): raise ValueError('Invalid grid cells')
    def visit(id,parents):
        if id not in nodes or id in parents or len(parents)>24: raise ValueError('Invalid component graph')
        n=nodes[id]
        for child in n.get('children',[])+([n['child']] if 'child' in n else []): visit(child,parents|{id})
    visit('root',set())
