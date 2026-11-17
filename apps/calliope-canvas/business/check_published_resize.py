#!/usr/bin/env python3
"""Exercise live geometry changes without touching the user's display or saving data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from capture_published import isolated_app
from check_published_native import settle, fill
from studio import capture, probe


def main(output):
    output.mkdir(parents=True, exist_ok=True)
    evidence={}
    with isolated_app('demo-adaptive-workspace', output/'resize.log') as session:
        state=settle(session)
        record=state['surface']['data']['job']['id']
        note='The exact replacement blower motor is confirmed. Arrange the part before the next visit.'
        fill(session,state,'/state/jobForm/notes',note)
        settle(session,lambda s:not s['render']['refresh_pending'] and s['surface']['data']['decision']['label']=='parts')

        def geometry():
            state=probe(session,{'op':'inspect'})
            assert state['surface']['data']['job']['id']==record
            assert state['surface']['data']['state']['jobForm']['notes']==note
            return next(iter(state['render']['scroll_areas'].values()))

        def resize(width,height):
            receipt=capture(session,output/f'adaptive-{width}x{height}.png',width,height,0)
            assert receipt['ready'],receipt
            area=geometry()
            evidence[f'{width}x{height}']=area
            return area

        def wheel(x,y,button,count):
            subprocess.run(['xdotool','mousemove','--window',session['window'],str(int(x)),str(int(y)),
                'click','--repeat',str(count),'--delay','12',str(button)],
                env={**os.environ,'DISPLAY':session['display']},check=True,timeout=10)
            time.sleep(.3)
            return geometry()

        tall=resize(1440,1100)
        assert tall['height']>480,tall
        moved=wheel(tall['x']+60,tall['y']+60,5,4)
        assert moved['offset_y']<0
        short=resize(1440,800)
        assert abs((tall['height']-short['height'])-300)<2,(tall,short)
        assert abs(short['offset_y']-moved['offset_y'])<1
        taller=resize(1440,1200)
        assert abs((taller['height']-short['height'])-400)<2,(short,taller)
        row=resize(950,1000)
        column=resize(900,1000)
        assert column['width']>row['width']*2,(row,column)
        phone=resize(600,850)
        # Page scrolling must not expand an inner list or move its scroll edge.
        scrolled=wheel(590,700,5,5)
        assert scrolled['y']<phone['y'],(phone,scrolled)
        assert abs(scrolled['height']-phone['height'])<1,(phone,scrolled)
        evidence['page_scroll']=scrolled
        evidence['preserved']={'record':record,'draft':True,'scroll_offset':True}
    (output/'verification.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(evidence,indent=2))


if __name__=='__main__':main(Path(sys.argv[1]))
