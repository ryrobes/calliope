#!/usr/bin/env python3
"""Check complete Meaning groups, cube lineage and snapshots in a private GPUI display."""
import argparse
import json
import subprocess
import time
from pathlib import Path
from capture_published import isolated_app
from studio import probe


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    with isolated_app('',args.output/'native.log',mode='meaning') as session:
        def call(op,**kw):return probe(session,dict(op=op,**kw))
        def wait(test):
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                state=call('inspect')
                if test(state):return state
                time.sleep(.15)
            raise AssertionError(state.get('notice') or 'Meaning did not settle')
        def capture(name):
            time.sleep(.3)
            subprocess.run(['magick','import','-display',session['display'],'-window',session['window'],str(args.output/name)],check=True)
        call('area',area='subject:hvac_inventory_packages')
        state=wait(lambda s:not s['busy'] and s['graph'].get('complete'))
        assert sum(c['loaded'] for c in state['graph']['catalog'])+1==len(state['graph']['nodes'])
        groups=[n for n in state['display_nodes'] if n['kind']=='group']
        assert groups and len(state['visible'])+sum(len(g['members']) for g in groups)==len(state['graph']['nodes'])
        call('fit');time.sleep(.3)
        capture('grouped.png')
        group=next(g for g in groups if g['group_kind']=='metric')
        call('show',id=group['id']);expanded=call('inspect')
        assert set(group['members'])<=set(expanded['visible'])
        call('reset_view')
        cube=next(n for n in state['graph']['nodes'] if n['kind']=='cube')
        call('show',id=cube['id'])
        detail=wait(lambda s:not s['inspection_busy'] and (s['inspection'] or {}).get('node',{}).get('id')==cube['id'])
        assert detail['inspection']['detail']['sql']
        call('detail',expanded=True);capture('cube.png');call('detail',expanded=False)
        call('expand');expanded=call('inspect')
        assert not expanded['hidden_neighbors']
        metric=next(n for n in state['graph']['nodes'] if n['kind']=='metric' and n.get('preview',{}).get('label')=='Saved daily snapshot')
        call('show',id=metric['id'])
        detail=wait(lambda s:not s['inspection_busy'] and (s['inspection'] or {}).get('node',{}).get('id')==metric['id'])
        assert detail['inspection']['detail']['observation_scope']==metric['defaults']
        call('detail',expanded=True);capture('metric.png')
        result={'objects':len(state['graph']['nodes']),'relationships':len(state['graph']['edges']),
                'collapsed_groups':{g['group_kind']:len(g['members']) for g in groups},
                'cube':cube['id'],'metric':metric['id'],'group_expansion':True,'complete_neighbors':True,'snapshot_defaults':True}
        (args.output/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))


if __name__=='__main__':main()
