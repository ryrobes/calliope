#!/usr/bin/env python3
"""Read-only UX regression: selection, independent wheel scrolling, real decisions.

Uses the private display/probe. It never saves notes or posts business actions.
A working SQL decision provider is required for the semantic assertions.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from capture_published import isolated_app
from check_published_native import settle, fill
from studio import capture, probe


def event(session, name):
    state=settle(session)
    node=next(n for n in state['surface']['nodes'] if n['props'].get('action',{}).get('event',{}).get('name')==name)
    return probe(session,{'op':'press','id':node['id']})


def main(output):
    output.mkdir(parents=True,exist_ok=True)
    evidence={}
    with isolated_app('demo-adaptive-workspace',output/'interaction.log') as session:
        state=settle(session)
        assert not any(n['component']=='CRecordPicker' and n['bindings'].get('value')=={'path':'/state/controls/focus'} for n in state['surface']['nodes'])
        capture(session,output/'adaptive-before.png',1440,1100,0)
        state=probe(session,{'op':'inspect'})
        ident,area=next(iter(state['render']['scroll_areas'].items()))
        assert 0<area['height']<1100
        env={**os.environ,'DISPLAY':session['display']}
        def wheel(button,count):
            subprocess.run(['xdotool','mousemove','--window',session['window'],str(int(area['x']+80)),str(int(area['y']+70)),
                'click','--repeat',str(count),'--delay','12',str(button)],env=env,check=True,timeout=10)
            time.sleep(.25)
            return probe(session,{'op':'inspect'})['render']['scroll_areas'][ident]
        moved=wheel(5,16)
        assert moved['offset_y']<area['offset_y'],(area,moved)
        assert abs(moved['y']-area['y'])<1,(area,moved)
        edge=wheel(5,80)
        assert abs(edge['y']-area['y'])<1,(area,edge)
        wheel(4,100)
        evidence['scroll']={'before':area,'after':moved,'edge':edge,'outer_page_stationary':True}
        state=settle(session)
        record=next(row['id'] for row in state['surface']['data']['jobs'] if row['id']!=state['surface']['data']['job']['id'])
        item=next(n for n in state['surface']['nodes'] if n['component']=='CRecordItem' and n['props']['recordId']==record)
        probe(session,{'op':'press','id':item['id']})
        state=settle(session,lambda s:s['surface']['data']['job']['id']==record)
        def assert_selected(state):
            selected=[n for n in state['surface']['nodes'] if n['component']=='CRecordItem' and n['props']['recordId']==n['props']['selectedId']]
            assert len(selected)==1 and selected[0]['props']['recordId']==record
        assert_selected(state)
        assert state['surface']['data']['state']['controls']['focus']==['auto']
        evidence['selection']={'record':record,'one_selected_row':True}
        part_note='The ECM blower motor has failed. The exact model and rotation are verified. Arrange a replacement motor.'
        fill(session,state,'/state/jobForm/notes',part_note)
        pending=False
        for _ in range(12):
            time.sleep(.08)
            s=probe(session,{'op':'inspect'})
            if s['render']['refresh_pending']:pending=True;break
        assert pending,'No visible pending phase'
        state=settle(session,lambda s:s['surface']['data']['decision'].get('label')=='parts' and not s['render']['refresh_pending'])
        assert state['surface']['data']['state']['jobForm']['notes']==part_note
        assert state['surface']['data']['decision']['status']=='complete'
        assert_selected(state)
        capture(session,output/'adaptive-parts.png',1440,1100,0)
        event(session,'hold_decision')
        state=settle(session,lambda s:s['surface']['data']['decision'].get('status')=='held')
        assert state['surface']['data']['state']['controls']['focus']==['parts']
        comeback_note='The same cooling problem returned one day after the previous repair. This is a repeat visit and failed repair.'
        fill(session,state,'/state/jobForm/notes',comeback_note)
        # Wait through debounce and the held read; no semantic switch is allowed.
        time.sleep(1)
        state=settle(session,lambda s:not s['render']['refresh_pending'])
        assert state['surface']['data']['decision']['label']=='parts'
        event(session,'resume_decision')
        state=settle(session,lambda s:s['surface']['data']['decision'].get('label')=='comeback' and not s['render']['refresh_pending'])
        assert state['surface']['data']['state']['jobForm']['notes']==comeback_note
        assert state['surface']['data']['decision']['status']=='complete'
        assert_selected(state)
        capture(session,output/'adaptive-comeback.png',1440,1100,0)
        evidence['decisions']={'pending_visible':True,'parts_to_comeback':True,'draft_preserved':True,'hold_resume':True}
        event(session,'hold_decision');settle(session,lambda s:s['surface']['data']['decision'].get('status')=='held')
        event(session,'page_case')
        state=settle(session,lambda s:s['surface']['data']['state']['page']=='case')
        assert state['surface']['data']['state']['controls']['focus']==['auto']
        assert state['surface']['data']['decision']['label'] in ('equipment','parts','schedule')
        evidence['page_scope']={'hold_does_not_leak':True}
        event(session,'page_adaptive');settle(session,lambda s:s['surface']['data']['state']['page']=='adaptive')
        for width in (950,600):
            receipt=capture(session,output/f'adaptive-{width}.png',width,1100,0)
            assert receipt['ready'],receipt
    (output/'verification.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(evidence,indent=2))


if __name__=='__main__':main(Path(sys.argv[1]))
