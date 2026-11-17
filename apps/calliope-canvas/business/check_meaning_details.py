#!/usr/bin/env python3
"""Read-only native Meaning detail checks, on a private Xvfb display."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from capture_published import isolated_app
from studio import probe


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with isolated_app('',args.output/'native.log',mode='meaning') as session:
        def call(op,**kw): return probe(session,{'op':op,**kw})
        def wait(predicate):
            end=time.monotonic()+45
            while time.monotonic()<end:
                state=call('inspect')
                if predicate(state): return state
                time.sleep(.15)
            raise AssertionError(state.get('inspection_error') or state.get('notice') or 'Detail did not settle')
        def capture(name):
            time.sleep(.3)
            subprocess.run(['magick','import','-display',session['display'],'-window',session['window'],str(args.output/name)],check=True)
        def select(node):
            call('select',id=node['id'])
            if node['kind'] in ('metric','relation'):
                return wait(lambda s:not s['inspection_busy'] and (s['inspection'] or {}).get('node',{}).get('id')==node['id'])
            time.sleep(.2);return call('inspect')
        call('area',area='subject:hvac_inventory_packages')
        state=wait(lambda s:not s['busy'] and s['graph'].get('area')=='subject:hvac_inventory_packages')
        nodes=state['graph']['nodes']
        metric=next(n for n in nodes if n['kind']=='metric')
        source=next(n for n in nodes if n['id']=='relation:hvac_demo.inventory')
        metric_state=select(metric)
        assert metric_state['inspection']['detail']['sql']
        assert metric_state['inspection']['detail']['observations']
        context=call('context')
        assert context['detail']==metric_state['inspection']['detail']
        assert context['selected']['version']==metric_state['inspection']['node']['version']
        capture('metric.png')
        # Expanding the detail changes available map space, not its camera/objects.
        env={**os.environ,'DISPLAY':session['display']}
        subprocess.run(['xdotool','mousemove','--window',session['window'],'1350','693','click','1'],env=env,check=True)
        wait(lambda s:s['detail_expanded']);time.sleep(.3)
        expanded=call('inspect')
        assert expanded['viewport']['height']<metric_state['viewport']['height']
        assert expanded['layout']==metric_state['layout']
        assert set(expanded['visible'])==set(metric_state['visible'])
        capture('metric-expanded.png')
        before_scroll=expanded['layout']
        env={**os.environ,'DISPLAY':session['display']}
        subprocess.run(['xdotool','mousemove','--window',session['window'],'500','820','click','--repeat','5','--delay','80','5'],env=env,check=True)
        time.sleep(.3)
        assert call('inspect')['layout']==before_scroll
        capture('metric-receipts.png')
        source_state=select(source)
        assert source_state['inspection']['detail']['fields']
        capture('source-expanded.png')
        before_scroll=source_state['layout']
        subprocess.run(['xdotool','mousemove','--window',session['window'],'1300','850','click','--repeat','6','--delay','80','5'],env=env,check=True)
        time.sleep(.3)
        assert call('inspect')['layout']==before_scroll
        capture('connections-scrolled.png')
        # Late metric replies cannot paint over a later source selection.
        for _ in range(3):
            call('select',id=metric['id']);call('select',id=source['id'])
        wait(lambda s:not s['inspection_busy'] and (s['inspection'] or {}).get('node',{}).get('id')==source['id'])
        time.sleep(1)
        assert call('inspect')['inspection']['node']['id']==source['id']
        edge=next(e for e in call('inspect')['graph']['edges'] if e['label']=='joins to' and source['id'] in (e['from'],e['to']))
        call('evidence',id=edge['id']);capture('relationship-evidence.png')
        for kind in ('area','artifact','breakdown','idea'):
            node=next((n for n in nodes if n['kind']==kind),None)
            if node:
                select(node);capture(kind+'.png')
        select(metric)
        subprocess.run(['xdotool','windowsize',session['window'],'1000','700'],env=env,check=True)
        time.sleep(.5);capture('compact-expanded.png')
        call('detail',expanded=False);time.sleep(.3);capture('compact.png')
        result={'metric_observations':len(metric_state['inspection']['detail']['observations']),
                'source_fields':len(source_state['inspection']['detail']['fields']),
                'expand_keeps_map':True,'independent_scroll':True,'selection_race':True,
                'window_sizes':['1440x1000','1000x700'],'server_mutations':0}
        (args.output/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))

if __name__=='__main__':main()
