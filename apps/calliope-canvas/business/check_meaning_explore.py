#!/usr/bin/env python3
"""Exercise native Meaning browsing without changing business data or personal pins."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from capture_published import isolated_app
from studio import probe


def overlaps(a,b,pad=0):
    return a[0]<b[0]+b[2]+pad and a[0]+a[2]+pad>b[0] and a[1]<b[1]+b[3]+pad and a[1]+a[3]+pad>b[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--area',default='subject:hvac_inventory_packages')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with isolated_app('',args.output/'native.log',mode='meaning') as session:
        def call(op,**kw):return probe(session,{'op':op,**kw})
        def wait(predicate):
            end=time.monotonic()+45
            while time.monotonic()<end:
                state=call('inspect')
                if predicate(state):return state
                time.sleep(.15)
            raise AssertionError(state.get('notice') or 'Meaning did not settle')
        def capture(name):
            time.sleep(.3)
            subprocess.run(['magick','import','-display',session['display'],'-window',session['window'],str(args.output/name)],check=True)
        def labels_safe(state):
            l=state['layout'];z=l['zoom'];pan=l['pan'];labels=state['edge_labels']
            cards=[[pan[0]+p[0]*z,pan[1]+p[1]*z,300*z,170*z] for ident,p in l['positions'].items() if ident in state['visible']]
            for i,label in enumerate(labels):
                assert all(not overlaps(label['rect'],card,5) for card in cards),label
                assert all(not overlaps(label['rect'],other['rect'],5) for other in labels[:i]),label
            return len(labels)
        call('area',area=args.area)
        wait(lambda s:not s['busy'] and s['graph'].get('area')==args.area)
        time.sleep(.5);call('fit');time.sleep(.3)
        first=call('inspect')
        metrics=[n for n in first['graph']['nodes'] if n['kind']=='metric' and n['id'] in first['visible']]
        groups=[n.get('display_group',n['id']) for n in metrics]
        assert len(groups)==len(set(groups))
        initial_labels=labels_safe(first)
        # A taller detail panel can fit the map tightly enough that no labels
        # have safe space. Verify both that state and a real zoomed-in view.
        if not initial_labels:
            env={**os.environ,'DISPLAY':session['display']}
            subprocess.run(['xdotool','mousemove','--window',session['window'],'700','330','click','--repeat','3','--delay','80','4'],env=env,check=True)
            time.sleep(.3)
            assert labels_safe(call('inspect'))>0
            call('fit');time.sleep(.3)
        capture('starting-view.png')
        call('catalog',open=True,kind='metric');time.sleep(.4)
        before=call('inspect')
        assert before['catalog_matches'] and all(n['kind']=='metric' for n in before['catalog_matches'])
        capture('metric-browser.png')
        call('catalog',open=True,kind='relation')
        call('load_catalog')
        loaded=wait(lambda s:not s['busy'])
        assert set(loaded['visible'])==set(before['visible']), 'Fetching must not add cards'
        for key in ('pan','zoom','positions'):
            assert loaded['layout'][key]==before['layout'][key], 'Fetching must not rearrange the map'
        assert len(loaded['graph']['nodes'])>=len(before['graph']['nodes'])
        candidate=next(n for n in loaded['catalog_matches'] if n['id'] not in loaded['visible'])
        call('show',id=candidate['id']);shown=call('inspect')
        assert set(shown['visible'])-set(loaded['visible'])=={candidate['id']}
        call('hide',id=candidate['id']);hidden=call('inspect')
        assert candidate['id'] not in hidden['visible']
        # A deliberate hide and the larger catalog survive navigation, rather
        # than returning to twelve records or silently repopulating the canvas.
        call('area',area='');wait(lambda s:not s['busy'] and s['area']=='')
        call('area',area=args.area);reopened=wait(lambda s:not s['busy'] and s['area']==args.area)
        assert set(reopened['visible'])==set(hidden['visible'])
        assert reopened['layout']['catalog_limit']==hidden['layout']['catalog_limit']
        initial_set=set(reopened['visible']);neighbors=reopened['hidden_neighbors']
        call('expand');time.sleep(.3);expanded=call('inspect')
        assert set(expanded['visible'])-initial_set==set(neighbors[:6])
        labels_safe(expanded)
        capture('connected-view.png')
        call('reset_view');time.sleep(.3);reset=call('inspect')
        assert len(reset['visible'])<=11
        call('catalog',open=True,kind='relation');time.sleep(.3)
        capture('source-browser.png')
        # Private-display search: one input changes only the list, not the map.
        env={**os.environ,'DISPLAY':session['display']}
        subprocess.run(['xdotool','mousemove','--window',session['window'],'1230','250','click','1','type','--clearmodifiers','inventory'],env=env,check=True)
        time.sleep(.4)
        filtered=call('inspect')
        capture('source-search.png')
        assert filtered['catalog_matches'] and len(filtered['catalog_matches'])<len([n for n in filtered['graph']['nodes'] if n['kind']=='relation'])
        assert set(filtered['visible'])==set(reset['visible'])
        # Wheel input over the independently scrolling catalog must not zoom/pan the map.
        before_scroll=call('inspect')['layout']
        subprocess.run(['xdotool','mousemove','--window',session['window'],'1240','600','click','--repeat','4','--delay','100','5'],env=env,check=True)
        time.sleep(.3);after_scroll=call('inspect')['layout']
        assert before_scroll==after_scroll
        capture('catalog-scrolled.png')
        report={'area':args.area,'initial_metrics':[n['title'] for n in metrics],
                'initial_labels':initial_labels,'labels_clear_of_tiles':True,
                'loaded_before':len(before['graph']['nodes']),'loaded_after':len(loaded['graph']['nodes']),
                'loading_kept_map':True,'add_hide_persisted':True,'bounded_expansion':True,'search_kept_map':True,'independent_scroll':True}
        (args.output/'result.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report))


if __name__=='__main__':main()
