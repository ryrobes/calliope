"""SQL evidence + editable notes -> a small semantic workspace, on both hosts."""
from ui import UI, bind
from reactive import prepare

CHOICES = {
    'diagnose':'A new fault or intermittent symptom needs investigation; no confirmed failed part yet.',
    'parts':'A specific failed part or needed replacement has been identified. The next step is parts coordination.',
    'comeback':'A previous repair has failed or the same problem has returned after a recent visit.',
    'clarify':'Too little or conflicting information to select an appropriate next workspace.',
}
TREE = {'kind':'choice', 'question':'Which workspace is most useful next? Prioritize the technician working notes over the older job record. A return of the same symptom after repair is a comeback even if a part is mentioned. These are workflow suggestions, not technical diagnosis.',
        'choices':CHOICES, 'routes':{k:{'kind':'leaf','label':k} for k in CHOICES}}


def policy():
    outcomes = {
        'diagnose':('Make the symptom concrete.', 'cool', 'INVESTIGATE', [
            ('Observe','Capture when the symptom occurs and what has already been checked.'),
            ('Compare','Keep equipment history beside the current report before choosing a repair.')]),
        'parts':('Bring the right part.', 'accent', 'PARTS COORDINATION', [
            ('Identify','Verify the equipment model and the exact replacement part.'),
            ('Availability','Open the parts desk to confirm stock and reservations. This suggestion makes no stock claim.'),
            ('Handoff','Keep the supplier reference and visit notes together.')]),
        'comeback':('Pick up the whole story.', 'warm', 'RETURN VISIT', [
            ('Previous repair','Compare the earlier work with what returned; avoid repeating an unsupported conclusion.'),
            ('Customer follow-up','Prepare a clear account of the next visit and assign an owner.')]),
        'clarify':('A little more context.', 'neutral', 'CLARIFY', [
            ('What changed?','Describe the observed symptom, any confirmed failed part, and whether this repeats a recent repair.')]),
    }
    variants={}
    for key,(heading,tone,kicker,cards) in outcomes.items():
        u=UI(); prefix='next.'
        children=[u.badge(prefix+'kicker',kicker,tone)]
        for i,(title,body) in enumerate(cards):
            children.append(u.panel(prefix+key+str(i),[
                u.text(prefix+key+str(i)+'-title',title,'title',family='display'),
                u.text(prefix+key+str(i)+'-copy',body)],tone,'soft'))
        # This field intentionally appears in different compositions with the
        # same ID and binding, exercising native input-entity continuity.
        children.append(u.field('next.handoff','Working handoff','/draft/handoff',kind='longText',help='Stays with this draft when the workspace changes.'))
        root=u.stack('next.body',children,gap='md')
        variants[key]={'value':{'heading':heading}, 'patches':{'adaptive-region':{'tone':tone}}, 'components':u.nodes,'root':root}
    return {'id':'next','watch':{'job':'/job','notes':'/draft/notes'},'tree':TREE,'debounceMs':450,
            'target':'adaptive-slot','initial':'clarify','variants':variants}


def snapshot(store,selected,notice='',_db=None):
    from contextlib import nullcontext
    with (nullcontext(_db) if _db is not None else store.connect()) as db:
        jobs=[dict(r) for r in db.execute('SELECT id,customer,equipment,job_type,notes,version FROM hvac_jobs ORDER BY id LIMIT 6')]
        job=next((j for j in jobs if j['id']==selected),jobs[0])
        revision=store.revision(db)
    data={'job':job,'draft':{'id':job['id'],'version':job['version'],'notes':job['notes'],'handoff':''},'errors':{}}
    u=UI(); nav=u.nav('adaptive')
    heading=u.stack('adaptive-heading',[
        u.text('adaptive-eyebrow','NORTHSTAR / A WORKSPACE THAT LISTENS','eyebrow','muted'),
        u.text('adaptive-title','The next useful step.','display',family='display'),
        u.text('adaptive-copy','Choose a visit. Describe what you have found. The workspace follows the context.','body','muted')])
    visits=u.stack('adaptive-visits',[u.button('adaptive-'+j['id'],j['customer'],'adaptive_select',{'id':j['id']},j['id']==job['id']) for j in jobs],'row','sm',wrap=True)
    editor=u.panel('adaptive-context',[
        u.text('adaptive-customer',job['customer'],'title',family='display'),
        u.text('adaptive-equipment',job['equipment'],'caption','muted'),
        u.field('adaptive-notes','What are you finding?','/draft/notes',kind='longText',help='Only the job and these notes are watched. Handoff edits do not trigger decisions.'),
        u.text('adaptive-fixture','Fictional SQL visit. Notes are a local draft; no service work is changed.','caption','muted'),
        u.button('adaptive-retry','Reconsider','reactive_retry',{'id':'next'}),
        u.button('adaptive-pin','Hold / resume this workspace','reactive_hold',{'id':'next'}),
    ],treatment='outlined',weight=2)
    region=u.panel('adaptive-region',[
        u.text('adaptive-result',bind('/resources/next/value/heading'),'title',family='display'),
        u.text('adaptive-status',bind('/resources/next/message'),'caption','muted'),
        u.stack('adaptive-slot',[]),
    ],'neutral','wash',weight=3)
    u.stack('root',[nav,heading,visits,u.stack('adaptive-columns',[editor,region],'row','lg','tablet')],gap='lg')
    result=prepare({'components':u.nodes,'data':data,'reactive':[policy()]})
    result.update(view='adaptive',selected=job['id'],revision=revision,notice=notice,owner='Everyone',application='calliope-transaction-desk')
    return result
