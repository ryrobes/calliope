"""The default page recipe returned to authors with the component catalog."""
from ui import UI

PAGE_PATTERN = {
    'name': 'Calliope workspace',
    'order': ['page identity', 'live filters', 'named work sections', 'supporting context'],
    'surfaces': {
        'CSection': 'Major work areas: a title, short purpose and breathing room.',
        'row': 'Quiet activity and checklist rows. Selectable records can instead use outlined cards.',
        'inset': 'Supporting context or charts inside a section.',
        'outlined': 'A focused editor or actionable business object.',
        'wash': 'A file cover, status band or contextual highlight; contrast with open work areas.',
    },
    'filters': 'One CFilterBar watches a separate object; 250 ms debounce, 350 ms for search. Bind its children beneath that path. onChange names a read-only filter handler. Keep drafts outside it.',
    'commands': 'Save, post, approve and other business changes remain explicit actions.',
    'layout': 'Vary composition by task: dossier cover, stage lanes, editorial briefing or operational queue/editor. Use collapse=phone for two-column desks and tablet for three columns. Keep stable record IDs; check 1440, 950 and narrow layouts. This starter demonstrates wiring, not a mandatory page shape.',
}


def workspace_template():
    u=UI()
    hero=u.stack('identity',[
        u.text('eyebrow','SERVICE / WORKSPACE','eyebrow','muted'),
        u.text('title','Today’s work','metric',family='display'),
        u.text('purpose','Find the next job, work through it, and keep the useful context close.','body','muted'),
    ])
    filters=u.filters('filters','Find work','/filters','work_filter',[
        u.field('search','Search','/filters/search',maxLength=100),
        u.picker('status','Status','/filters/status',[('open','Open work'),('all','All work')]),
    ],debounce=350)
    queue=u.section('work-list','Work to pick up',[
        u.row('job-visit-104',[
            u.badge('job-status','Ready','success'),
            u.text('job-title','Harbor · planned maintenance','title'),
            u.text('job-detail','Visit 104 · Alex Morgan · 10:30','caption','muted'),
            u.button('open-job','Open visit','open_visit',{'id':'visit-104'}),
        ]),
    ],'A short list of records that need attention.',weight=2)
    editor=u.section('working-area','Working together',[
        u.panel('editor',[
            u.field('notes','Working notes','/draft/notes',kind='longText'),
            u.button('save','Save notes','save_notes',{'id':'visit-104','notes':{'path':'/draft/notes'}},True),
        ]),
    ],'Your edits stay a draft until you save.',tone='accent',weight=3)
    context=u.section('context','Useful context',[
        u.panel('next-step',[
            u.text('context-title','Before you head out','title'),
            u.text('context-copy','Confirm the replacement filter is in the van.'),
        ],treatment='inset'),
    ],'Evidence and suggestions for the current record.',weight=2)
    u.stack('root',[hero,filters,u.stack('work-columns',[queue,editor,context],'row','lg','tablet')],gap='xl')
    return {'title':'Calliope workspace starter','components':u.nodes,'data':{'filters':{'search':'','status':['open']},'draft':{'notes':''}}}
