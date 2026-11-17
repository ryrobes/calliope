#!/usr/bin/env python3
"""Read-only native regression of the published Demo workspace, off-desktop."""
import json
from pathlib import Path
import sys
import time
from capture_published import isolated_app
from studio import probe, capture

PAGES = ('dossier','pipeline','briefing','hvac','counter','receiving','dispatch',
         'field','adaptive','case','charts')


def settle(session, predicate=lambda s: True):
    deadline = time.monotonic()+40
    while time.monotonic()<deadline:
        state=probe(session, {'op':'inspect'})
        if state.get('failed'): raise RuntimeError(state['notice'])
        if not state['busy'] and predicate(state): return state
        time.sleep(.15)
    raise RuntimeError('Native update did not settle')


def fill(session, state, path, value):
    node=next(n for n in state['surface']['nodes'] if n['bindings'].get('value')=={'path':path})
    return probe(session, {'op':'fill','id':node['id'],'value':value})


def main(output):
    output.mkdir(parents=True,exist_ok=True)
    receipts=[]
    with isolated_app('demo-service-operations',output/'native-check.log') as session:
        for page in PAGES:
            state=settle(session)
            button=next(n for n in state['surface']['nodes'] if n['props'].get('action',{}).get('event',{}).get('name')=='page_'+page)
            probe(session,{'op':'press','id':button['id']})
            state=settle(session,lambda s:s['surface']['data']['state']['page']==page)
            receipt=capture(session,output/(page+'-native.png'),1440,1100,0)
            assert receipt['ready'],receipt
            receipts.append({'page':page,**receipt})
            print(page,'ready',flush=True)
        for family in ('facet','repeat','concat','heatmap','geoshape','regression'):
            state=settle(session)
            fill(session,state,'/state/controls/chart',[family])
            state=settle(session,lambda s:any(n['component']=='CVegaChart' and n['bindings'].get('spec')=={'path':'/charts/'+family} for n in s['surface']['nodes']))
            receipt=capture(session,output/(family+'-native-narrow.png'),950,1000,3 if family=='facet' else 0)
            assert receipt['ready'],receipt
            receipts.append({'family':family,**receipt})
            print(family,'native responsive ready',flush=True)
        # SQL filters refresh automatically without an Apply event.
        state=settle(session)
        fill(session,state,'/state/filter/technician',['Sam Rivera'])
        def filtered(s):
            rows=s['surface']['data']['charts']['facet']['datasets']['visits']
            return bool(rows) and all(r['technician']=='Sam Rivera' for r in rows)
        state=settle(session,filtered)
        receipts.append({'auto_filter':True,'rows':len(state['surface']['data']['charts']['facet']['datasets']['visits'])})
        # A watched note changes a semantic region without replacing the editor.
        button=next(n for n in state['surface']['nodes'] if n['props'].get('action',{}).get('event',{}).get('name')=='page_adaptive')
        probe(session,{'op':'press','id':button['id']})
        state=settle(session,lambda s:s['surface']['data']['state']['page']=='adaptive')
        note='The same symptom returned after the repair. This needs a comeback review.'
        fill(session,state,'/state/jobForm/notes',note)
        state=settle(session,lambda s:s['surface']['data']['decision'].get('label')=='comeback')
        assert state['surface']['data']['state']['jobForm']['notes']==note
        receipts.append({'watched_note_preserved':True,'decision':'comeback'})
    (output/'native-verification.json').write_text(json.dumps(receipts,indent=2))


if __name__=='__main__': main(Path(sys.argv[1]))
