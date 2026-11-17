#!/usr/bin/env python3
"""Check native text entry on a private display and disposable profile; no business writes."""
import sys,time,json,subprocess,os,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from capture_published import isolated_app
from studio import probe,capture
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
with isolated_app('demo-field-notebook',out/'desktop-interaction.log') as s:
 env={**os.environ,'DISPLAY':s['display']}
 def state():return probe(s,{'op':'inspect'})
 def wait(pred=lambda s:True):
  end=time.monotonic()+30
  while time.monotonic()<end:
   v=state()
   if not v.get('busy') and pred(v):return v
   time.sleep(.2)
  (out/'desktop-failure.json').write_text(json.dumps(v,indent=2));raise RuntimeError('State not reached')
 def key(*args):subprocess.run(['xdotool',*args],env=env,check=True)
 v=wait();probe(s,{'op':'press','id':'page.submit'})
 v=wait(lambda v:bool(v['render'].get('field_errors')))
 assert v['pending'] is None and 'page.notes' in v['render']['field_errors']
 capture(s,out/'desktop-validation.png',1440,1000,0)
 b=v['render']['inputs']['page.notes']
 key('mousemove','--window',s['window'],str(int(b['x']+b['width']/2)),str(int(b['y']+b['height']/2)),'click','1')
 key('type','--clearmodifiers','--delay','15','Native input check')
 key('key','Return');key('type','--clearmodifiers','--delay','15','Second line')
 v=wait(lambda v:'Second line' in v['surface']['data']['state']['fieldForm']['notes'])
 expected=v['surface']['data']['state']['fieldForm']['notes'];assert '\n' in expected
 wait(lambda v:not v['render']['field_errors'] and not v['failed'])
 key('key','ctrl+a','ctrl+c','BackSpace')
 wait(lambda v:v['surface']['data']['state']['fieldForm']['notes']=='')
 key('key','ctrl+v')
 wait(lambda v:v['surface']['data']['state']['fieldForm']['notes']==expected)
 key('key','Tab');time.sleep(.3)
 v=state();print('After Tab',v['render']['input_kind'],v['render']['focused_input'],flush=True);assert v['render']['focused_input']=='page.temperature'
 key('key','shift+Tab');time.sleep(.3)
 v=state();print('After Shift Tab',v['render']['input_kind'],v['render']['focused_input'],flush=True)
 assert v['render']['input_kind']=='longText' and v['render']['focused_input']=='page.notes'
 probe(s,{'op':'home'});wait(lambda v:not v['slug'])
 probe(s,{'op':'search','value':'NO SUCH APP 79d6'});v=wait(lambda v:v['matching_apps']==0)
 capture(s,out/'desktop-empty-search.png',1440,1000,0)
 probe(s,{'op':'search','value':'dispatch'});v=wait(lambda v:v['matching_apps']==1)
 assert probe(s,{'op':'open_app','slug':'demo-dispatch'})['ok']
 v=wait(lambda v:bool(v.get('surface',{}).get('nodes')))
 fields=[(n['id'],n['props'].get('kind')) for n in v['surface']['nodes'] if n['component']=='CField'];print(fields,flush=True)
 date_id=next(i for i,k in fields if k=='date');time_id=next(i for i,k in fields if k=='time')
 assert probe(s,{'op':'picker','id':date_id})['ok'];v=wait(lambda v:v['render']['picker']==date_id)
 capture(s,out/'desktop-calendar.png',1440,1000,0)
 key('key','Escape');wait(lambda v:v['render']['picker'] is None)
 assert probe(s,{'op':'picker','id':time_id})['ok'];wait(lambda v:v['render']['picker']==time_id)
 capture(s,out/'desktop-time.png',1440,1000,0)
 key('key','Escape');wait(lambda v:v['render']['picker'] is None)
 result={'invalid_submit_blocked':True,'multiline_keyboard':True,'copy_paste':True,'tab_shift_tab':True,'search':True,'pickers_escape':True}
 (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result),flush=True)
