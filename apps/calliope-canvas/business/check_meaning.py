#!/usr/bin/env python3
"""Read published Meaning in an isolated native window, verify personal pins.

No fixtures, model calls, business writes, or input on the user's display.
Temporary pins are removed unless --keep-pin is given. --ask also creates and
archives a test conversation, without sending a conversational turn.
"""
import argparse
import json
import os
import time
import subprocess
import urllib.request
from pathlib import Path

from capture_published import isolated_app
from studio import probe


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--area',default='subject:hvac_inventory_packages')
    parser.add_argument('--keep-pin',action='store_true')
    parser.add_argument('--ask',action='store_true',help='Prepare an unsent contextual chat, then archive the empty test chat')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with isolated_app('',args.output/'native.log',mode='meaning') as session:
        def call(op,**kw):return probe(session,{'op':op,**kw})
        def wait(f):
            end=time.monotonic()+50
            while time.monotonic()<end:
                v=call('inspect')
                if f(v):return v
                time.sleep(.2)
            raise RuntimeError(str(v.get('notice') or 'Native Meaning did not settle'))
        def mouse(*arguments):
            subprocess.run(['xdotool',*map(str,arguments)], env={**os.environ,'DISPLAY':session['display']},check=True)
            time.sleep(.25)
        def capture(name):
            time.sleep(.3)
            subprocess.run(['magick','import','-display',session['display'],'-window',session['window'],str(args.output/name)],check=True)
        initial=wait(lambda v:not v['busy'] and not v['pins_busy'])
        assert initial['graph']['nodes']
        capture('areas.png')
        area=args.area
        if not any(n['area']==area for n in initial['graph']['nodes']):area=initial['graph']['nodes'][0]['area']
        call('area',area=area)
        graph=wait(lambda v:not v['busy'] and v['graph'].get('area')==area)
        assert graph['graph']['edges']
        node=next((n for n in graph['graph']['nodes'] if n['kind']=='metric'),graph['graph']['nodes'][0])
        call('select',id=node['id']);time.sleep(.4);call('fit');time.sleep(.3)
        context=call('context');assert context['selected']['reference']==node['reference']
        # Exercise real pointer dispatch on this private X server.
        state=call('inspect');layout=state['layout'];viewport=state['viewport']
        original=layout['positions'][node['id']];zoom=layout['zoom']
        x=round(viewport['x']+layout['pan'][0]+(original[0]+55)*zoom)
        y=round(viewport['y']+layout['pan'][1]+(original[1]+18)*zoom)
        mouse('mousemove','--window',session['window'],x,y,'mousedown',1)
        mouse('mousemove','--window',session['window'],x+40,y+22)
        mouse('mouseup',1)
        moved=call('inspect')['layout']['positions'][node['id']]
        assert abs(moved[0]-original[0]-40/zoom)<2, (original,moved)
        assert abs(moved[1]-original[1]-22/zoom)<2, (original,moved)
        mouse('mousemove','--window',session['window'],700,400,'click',4)
        assert call('inspect')['layout']['zoom']>zoom
        # Find empty background after zoom; panning must not select a node.
        state=call('inspect');old_pan=state['layout']['pan']
        layout=state['layout'];z=layout['zoom'];pan=layout['pan']
        boxes=[(pan[0]+p[0]*z,pan[1]+p[1]*z,300*z,170*z) for k,p in layout['positions'].items() if k in state['visible']]
        bx,by=next((x,y) for y in range(60,int(viewport['height'])-30,30) for x in range(60,int(viewport['width'])-30,30)
                   if not any(a-6<x<a+w+6 and b-6<y<b+h+6 for a,b,w,h in boxes))
        bx+=round(viewport['x']);by+=round(viewport['y'])
        mouse('mousemove','--window',session['window'],bx,by,'mousedown',1)
        mouse('mousemove','--window',session['window'],bx-35,by-20)
        mouse('mouseup',1)
        new_pan=call('inspect')['layout']['pan']
        assert abs(new_pan[0]-old_pan[0]+35)<2, (old_pan,new_pan)
        call('fit');time.sleep(.2)
        capture('neighborhood.png')
        mouse('windowsize',session['window'],1000,700);time.sleep(.4);call('fit');time.sleep(.3)
        capture('compact.png')
        assert call('inspect')['viewport']['width']==1000
        mouse('windowsize',session['window'],1440,1000);time.sleep(.4);call('fit');time.sleep(.3)
        stable=call('inspect')['layout']
        prior=next((p for p in initial['pins'] if p['reference']==node['reference']),None)
        pin=None;topic=None
        connection=json.loads((Path(session['probe']).parent/'config/calliope-canvas/connection.json').read_text())
        def api(path,method='GET',body=None):
            request=urllib.request.Request(connection['base_url'].rstrip('/')+path,method=method,
                data=json.dumps(body).encode() if body is not None else None,
                headers={'Content-Type':'application/json','Cookie':'wh_session='+connection['session_cookie']})
            with urllib.request.urlopen(request,timeout=45) as r:return json.load(r)
        try:
            call('pin')
            saved=wait(lambda v:not v['changing'] and not v['pins_busy'] and any(p['reference']==node['reference'] for p in v['pins']))
            pin=next(p for p in saved['pins'] if p['reference']==node['reference'])
            call('pin')
            again=wait(lambda v:not v['changing'] and not v['pins_busy'])
            assert len([p for p in again['pins'] if p['reference']==node['reference']])==1
            call('area',area='');wait(lambda v:not v['busy'] and v['area']=='')
            call('area',area=area);reopened=wait(lambda v:not v['busy'] and v['area']==area)
            assert {k:v for k,v in reopened['layout'].items() if k!='shown'}=={k:v for k,v in stable.items() if k!='shown'}
            assert set(reopened['layout'].get('shown') or [])==set(stable.get('shown') or [])
            # Open an actual published A2UI artifact through the graph handoff.
            artifact=next((n for n in graph['graph']['nodes'] if n['kind']=='artifact' and n.get('runtime')=='a2ui'),None)
            opened=None
            if artifact:
                call('select',id=artifact['id']);call('open')
                app_session={**session,'probe':session['probe'].removesuffix('.meaning')}
                end=time.monotonic()+50
                while time.monotonic()<end:
                    try: app=probe(app_session,{'op':'inspect'})
                    except (OSError,TimeoutError): time.sleep(.2);continue
                    if not app['busy'] and app.get('surface',{}).get('nodes'):
                        assert app['slug']==artifact['slug'], app.get('notice')
                        assert not app.get('failed'), app.get('notice')
                        opened=app['slug'];break
                    time.sleep(.2)
                assert opened, 'Published app handoff did not settle'
                capture('opened-app.png')
                mouse('mousemove','--window',session['window'],60,27,'click',1)
            # Show a live personal pin on Home, without sharing local geometry.
            mouse('mousemove','--window',session['window'],267,38,'click',1)
            capture('home.png')
            if args.ask:
                mouse('mousemove','--window',session['window'],450,38,'click',1)
                call('select',id=node['id']);call('ask')
                home_path=Path(session['probe']).parent/'data/calliope-canvas/home.json'
                end=time.monotonic()+50
                while time.monotonic()<end:
                    home=json.loads(home_path.read_text()) if home_path.exists() else {}
                    matches=[v for k,v in home.get('meaning_topics',{}).items() if json.loads(k)==node['reference']]
                    if matches: topic=matches[0];break
                    time.sleep(.2)
                assert topic, 'Contextual chat did not open'
                detail=api('/api/calliope/sessions/'+topic)
                assert all(t.get('turn_kind')=='sketch_bootstrap' for t in detail.get('turns',[])), 'Ask must prepare context without sending a conversational turn'
                capture('ask-callie.png')
            report={'areas':len(initial['graph']['nodes']),'area':area,'nodes':len(graph['graph']['nodes']),
                    'edges':len(graph['graph']['edges']),'pin':pin,'context_reference':context['selected']['reference'],
                    'stable_positions':True,'deduplicated':True,'pointer_drag_zoom_pan':True,'opened_app':opened,'unsent_context_chat':bool(topic)}
            (args.output/'result.json').write_text(json.dumps(report,indent=2))
            print(json.dumps({k:v for k,v in report.items() if k!='pin'}))
        finally:
            if topic: api('/api/calliope/sessions/'+topic,'PATCH',{'archived':True,'title':'Meaning UI verification (empty)'})
            if pin and not prior and not args.keep_pin:
                call('remove_pin',id=pin['id']);wait(lambda v:not v['changing'] and not v['pins_busy'] and not any(p['id']==pin['id'] for p in v['pins']))


if __name__=='__main__':main()
