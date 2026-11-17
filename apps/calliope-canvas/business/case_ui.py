"""One case, stable editors, independently changing supporting information."""
import copy
import counter
import hvac
from ui import UI, bind


def support():
    variants={}
    for key,heading,tone,description in [
      ('equipment','Pick up the whole story.','cool','Equipment and previous customer visits beside the current report.'),
      ('parts','Follow the part.','warm','Reserved quantities and receipt evidence from the linked order.'),
      ('schedule','Make the return visit count.','accent','Readiness and a reviewed appointment, with the handoff kept in view.')]:
        u=UI()
        children=[u.text('support.heading',heading,'title',family='display'),u.text('support.description',description,'caption','muted')]
        if key=='equipment':
            children += [u.text('support.asset',bind('/copy/equipment')),u.text('support.history',bind('/copy/history'),'caption','muted')]
        elif key=='parts':
            children += [u.text('support.stock',bind('/copy/parts')),u.text('support.allocations',bind('/copy/allocation'),'caption','muted'),u.button('support.receipt','Inspect the delivery','case_tool',{'mode':'receipt'})]
        else:
            children += [u.text('support.ready',bind('/status/reason')),u.button('support.schedule','Open appointment review','case_tool',{'mode':'schedule'})]
        root=u.stack('support.body',children)
        variants[key]={'value':{'heading':heading},'patches':{'case-support':{'tone':tone}},'components':u.nodes,'root':root}
    choices={'equipment':'Working notes explicitly request equipment investigation or review of previous service.',
      'parts':'Current SQL facts.readiness is waiting: parts are still backordered.',
      'schedule':'Current SQL facts.readiness is ready, scheduled, released or complete: follow the appointment and handoff. Older notes about waiting for parts are superseded.'}
    resource = {'id':'support','watch':{'facts':'/evidence','notes':'/draft/notes'},'debounceMs':450,
      'tree':{'kind':'choice','question':'Which supporting panel is useful NOW? Current SQL facts.readiness and current_readiness_explanation supersede historical working notes about parts availability. Choose schedule when current readiness is ready or scheduled, even if old notes mention waiting or a failed repair. Choose parts while current readiness is waiting. Only choose equipment instead when notes explicitly request investigation/history, not merely mention a past failure. Role is a tie-breaker. This selects information and never authorizes work.',
      'choices':choices,'routes':{k:{'kind':'leaf','label':k} for k in choices}},'target':'case-support-slot','initial':'parts','variants':variants}
    resource['tree']={'kind':'branch','question':'Do the working notes explicitly REQUEST reviewing or comparing the equipment history or previous service visits? Answer yes only for an explicit request to investigate history. Merely reporting a previous failure or a replacement part is not a request.', 'yes':{'kind':'leaf','label':'equipment'},'no':resource['tree']}
    return resource


def definition(data, selection, log):
    c=data['case'];s=data['status'];po=data['purchase_order'];job=s['job'];d=data['draft']
    u=UI();nav=u.nav('case')
    tone='warm' if s['phase']=='waiting' else 'success'
    hero=u.panel('case-hero',[
      u.stack('case-identity',[u.text('case-kicker','NORTHSTAR / SHARED WORKSPACE','eyebrow','muted'),u.badge('case-state',bind('/status/label'),tone)],'row','md','phone'),
      u.text('case-title',c['title'],'display',family='display'),
      u.text('case-location',job['customer']+' · '+job['address']+' · '+c['id'],'caption','muted'),
      u.text('case-why',bind('/status/reason')),
      u.stack('case-actions',[
        u.button('case-pin','Unpin from canvas' if c['pinned'] else 'Pin to canvas','case_pin',{'value':not bool(c['pinned'])}),
        u.button('case-copy','Copy context for Callie','case_context'),
        u.record('case-equipment-link','Inspect equipment ↗','equipment',data['equipment']['id']),
      ],'row','sm',wrap=True)],tone,'plain')
    # These IDs and bindings do not change when evidence or semantic focus changes.
    editor=u.panel('case-working-file',[
      u.field('case-notes','What are you finding?','/draft/notes','longText',maxLength=4000),
      u.field('case-handoff','Handoff for the return visit','/draft/handoff','longText',maxLength=4000),
      u.button('case-save','Save notes & handoff','case_save',{'draft':bind('/draft')},True),
      u.text('case-local','Fictional local case · edits stay on this device until saved.','caption','muted'),
    ],weight=3)
    resource=support();focus=c['focus']
    supporting=[u.stack('case-support-slot',[])]
    if focus=='auto':
        supporting += [u.text('case-decision-status',bind('/resources/support/message'),'caption','muted'),
          u.stack('case-decision-controls',[u.button('case-hold','Hold / resume','reactive_hold',{'id':'support'}),u.button('case-reconsider','Reconsider','reactive_retry',{'id':'support'})],'row','sm',wrap=True)]
    else:
        variant=resource['variants'][focus]
        u.nodes.extend(copy.deepcopy(variant['components']))
        next(n for n in u.nodes if n['id']=='case-support-slot')['children']=[variant['root']]
        supporting.append(u.text('case-decision-status','Your choice · automatic panel changes are paused.','caption','muted'))
    supporting += [u.stack('case-perspective',[
        u.picker('case-focus','Supporting panel','/preferences/focus',[('auto','Follow the work'),('equipment','Equipment & history'),('parts','Parts & receiving'),('schedule','Appointment')],weight=1),
        u.picker('case-role','Working perspective','/preferences/role',[('coordinator','Coordinator'),('technician','Technician'),('counter','Parts counter'),('dispatcher','Dispatcher')],weight=1)],'row','sm','tablet'),
      u.button('case-preferences','Keep these preferences','case_preferences',{'focus':bind('/preferences/focus'),'role':bind('/preferences/role')}),
      u.text('case-perspective-note','Perspective changes emphasis, not permissions.','caption','muted')]
    side=u.panel('case-support',supporting,'neutral' if focus=='auto' else resource['variants'][focus]['patches']['case-support']['tone'],'inset',weight=2)
    tools=u.stack('case-tool-tabs',[u.button('case-overview','Case evidence','case_tool',{'mode':'overview'},selection['mode']=='overview'),u.button('case-delivery','Delivery','case_tool',{'mode':'receipt'},selection['mode']=='receipt'),u.button('case-appointment','Return visit','case_tool',{'mode':'schedule'},selection['mode']=='schedule')],'row','sm',wrap=True)
    if selection['mode']=='receipt':
        tools_body=receipt_editor(u,data)
    elif selection['mode']=='schedule':
        tools_body=schedule_editor(u,data)
    else:
        tools_body=u.stack('case-evidence',[
          u.panel('case-parts-evidence',[u.text('case-parts-title','The part has a paper trail.','title',family='display'),u.text('case-order-id',data['context']['order']['id']+' · North branch','eyebrow','muted'),u.text('case-stock',bind('/copy/parts')),u.text('case-allocation',bind('/copy/allocation'),'caption','muted'),u.record('case-order-link','Inspect order ↗','order',data['context']['order']['id'])],'warm','outlined',weight=1),
          u.panel('case-visit-evidence',[u.text('case-visit-title','The next visit.','title',family='display'),u.text('case-visit-status',s['label']),u.text('case-visit-detail',s['reason'],'caption','muted'),u.record('case-visit-link','Inspect visit ↗','visit',job['id'])],'cool','outlined',weight=1),
        ],'row','md','tablet')
    history_controls=u.button('case-history-toggle','Hide retained history' if c['history'] else 'Keep equipment history visible','case_history',{'value':not bool(c['history'])})
    history=[u.panel('case-retained-history',[u.text('case-history-heading','Equipment & customer history','title',family='display'),u.text('case-equipment-copy',bind('/copy/equipment')),u.text('case-history-copy',bind('/copy/history'),'caption','muted')],'cool','plain')] if c['history'] else []
    worklog=u.panel('case-log',[u.text('case-log-title','What changed','title',family='display')]+[u.text('case-log-'+str(r['id']),r['created'][:16]+' · '+r['description'],'caption','muted') for r in log],treatment='plain')
    u.stack('root',[nav,hero,u.stack('case-columns',[u.section('case-work-section','Working together',[editor],subtitle='Notes and handoff stay here as the surrounding information changes.',tone='accent',weight=3),u.section('case-support-section','Context from Callie',[side],subtitle='Supporting information for the work at hand.',weight=2)],'row','lg','tablet'),u.section('case-tools-section','Evidence & next steps',[tools,tools_body],subtitle='Inspect the record, receive parts, or review a return visit.',tone='cool'),history_controls,*history,worklog],gap='lg')
    return u.nodes,[resource] if focus=='auto' else []


def receipt_editor(u,data):
    po=data['purchase_order'];review=data['receipt_review'];items=data['receiving_lines'];contents=[
      u.text('case-receiving-title','Receive the replacement.','title',family='display'),
      u.text('case-po',po['id']+' · '+po['supplier']+' · expected '+po['expected_date'],'caption','muted'),
      u.stack('case-receiving-when',[u.field('receipt-date','Received date','/draft/receipt/received_date','date',weight=1),u.field('receipt-time','Time','/draft/receipt/received_time','time',weight=1)],'row','sm','phone'),
      u.field('receipt-reference','Delivery reference','/draft/receipt/reference',maxLength=100)]
    for r in items:
        contents.append(u.field('receive-'+r['id'],r['name']+' · '+counter.quantity(r['expected']-r['received'])+' remaining','/draft/receipt/lines/'+r['id']+'/quantity','quantity',minimum='0',maximum=counter.quantity(r['expected']-r['received']),precision=0 if r['unit']=='ea' else 2))
    contents += [u.field('receipt-notes','Delivery notes','/draft/receipt/notes','longText',maxLength=4000),u.button('case-receipt-preview','Review delivery','case_receipt_preview',{'draft':bind('/draft/receipt')},True)]
    review_nodes=[u.text('case-receipt-review-title','Before stock changes.','title',family='display'),u.text('case-receipt-review-copy','Posting records an immutable receipt and allocates usable stock to waiting orders in oldest-order order. Case notes and the appointment draft stay in place.','body','muted')]
    if review:
        rd=review['draft']
        review_nodes += [u.text('case-receipt-review-ref',rd['reference']),u.text('case-receipt-review-date',rd['received_date']+' · '+rd['received_time'])]
        for r in items:review_nodes.append(u.text('case-reviewed-'+r['id'],r['name']+' · '+str(rd['lines'][r['id']]['quantity'])+' '+r['unit']))
        review_nodes.append(u.button('case-receipt-confirm','Confirm & post delivery','case_receive_confirm',{'review':review['id'],'draft':bind('/draft/receipt')},True) if review['valid'] else u.text('case-receipt-expired','Delivery or inputs changed. Review again.','body','warm'))
    return u.stack('case-receiving-work',[u.panel('case-receiving-editor',contents,weight=1),u.panel('case-receiving-review',review_nodes,'warm','wash',weight=1)],'row','lg','tablet')


def schedule_editor(u,data):
    command=data['command'];status=data['status'];job=status['job']
    # Always keep the editor mounted until the user changes tools; receipt arrival
    # changes availability, never steals focus or removes the scheduling draft.
    fields=[u.text('case-scheduling-title','Make room for a return.','title',family='display'),
      u.text('case-scheduling-ready',bind('/status/reason'),'caption','muted'),
      u.field('dispatch-date','Visit date','/draft/appointment/date','date'),
      u.picker('dispatch-tech','Technician','/draft/appointment/technician',[(x,x) for x in hvac.TECHS[1:]]),
      u.stack('case-scheduling-times',[u.field('dispatch-start','Starts','/draft/appointment/start','time',weight=1),u.field('dispatch-end','Ends','/draft/appointment/end','time',weight=1)],'row','sm'),
      u.field('dispatch-reason','Reason / visit plan','/draft/appointment/reason',maxLength=500),
      u.button('case-schedule-preview','Review return visit','case_schedule_preview',{'draft':bind('/draft/appointment')},True)]
    review=[u.text('case-schedule-review-title','A deliberate handoff.','title',family='display'),u.text('case-schedule-review-copy','Save a draft while parts are pending. Confirmation requires reserved parts, a current visit version and an available technician.','body','muted')]
    if command:
        for key,title in [('before','Currently saved'),('after','After confirmation')]:
            v=command[key]
            review.append(u.panel('case-schedule-'+key,[u.text('case-schedule-'+key+'-label',title,'eyebrow','muted'),u.text('case-schedule-'+key+'-date',v['scheduled_date']),u.text('case-schedule-'+key+'-tech',v['technician']),u.text('case-schedule-'+key+'-slot',v['slot'])],treatment='plain'))
        review.append(u.button('case-schedule-confirm','Confirm return visit','case_schedule_confirm',{'command':command['id'],'draft':bind('/draft/appointment')},True) if command['valid'] else u.text('case-schedule-expired','Appointment review expired or changed. Review again.','body','warm'))
    review.append(u.text('case-schedule-effects','Saves the local appointment. Customer and technician notifications are separate.','caption','muted'))
    return u.stack('case-scheduling-work',[u.panel('case-scheduling-editor',fields,weight=1),u.panel('case-scheduling-review',review,'accent','wash',weight=1)],'row','lg','tablet')
