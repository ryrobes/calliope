#!/usr/bin/env python3
import sys,json,time,uuid,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from capture_published import isolated_app
from studio import probe,capture
parser=argparse.ArgumentParser(description='Check record drafts and Back in an isolated GPUI display. No business writes.')
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
with isolated_app('demo-field-notebook',out/'desktop.log') as session:
 def state():return probe(session,{'op':'inspect'})
 def wait(predicate=lambda s:True):
  end=time.monotonic()+45
  while time.monotonic()<end:
   s=state()
   if not s.get('busy') and s.get('draft_saved') and predicate(s):return s
   time.sleep(.2)
  (out/'desktop-failure.json').write_text(json.dumps(s,indent=2));raise RuntimeError({k:s.get(k) for k in ['notice','draft_error','busy','drafts']})
 def fill(value):
  probe(session,{'op':'fill','id':'page.notes','value':value});return wait(lambda s:s['surface']['data']['state']['fieldForm']['notes']==value)
 def select(ident):
  s=state();node=next(n for n in s['surface']['nodes'] if n['component']=='CRecordItem' and n['props'].get('recordId')==ident)
  probe(session,{'op':'press','id':node['id']});return wait(lambda s:s['surface']['data']['state']['fieldForm']['id']==ident)
 s=wait();a=s['surface']['data']['state']['fieldForm']['id'];b=next(n['props']['recordId'] for n in s['surface']['nodes'] if n['component']=='CRecordItem' and n['props'].get('recordId')!=a and n['props'].get('onSelect')=='select_job')
 marker='Workspace check '+str(uuid.uuid4())
 fill(marker+' A');select(b);fill(marker+' B')
 probe(session,{'op':'back'});s=wait(lambda s:s['surface']['data']['state']['fieldForm']['id']==a)
 assert s['surface']['data']['state']['fieldForm']['notes']==marker+' A',(s['surface']['data']['state']['fieldForm'],s['drafts'])
 key=next(d['key'] for d in s['drafts'] if d['record']==b and d['form']=='/state/fieldForm')
 probe(session,{'op':'resume_draft','key':key});s=wait(lambda s:s['surface']['data']['state']['fieldForm']['id']==b)
 assert s['surface']['data']['state']['fieldForm']['notes']==marker+' B'
 probe(session,{'op':'discard_draft','key':key});assert state()['discard_confirmation']==key
 probe(session,{'op':'back'});assert state()['discard_confirmation'] is None
 assert state()['surface']['data']['state']['fieldForm']['notes']==marker+' B'
 probe(session,{'op':'discard_draft','key':key});assert state()['discard_confirmation']==key
 probe(session,{'op':'confirm_discard'});s=wait(lambda s:s['surface']['data']['state']['fieldForm']['notes']=='')
 probe(session,{'op':'back'}) # Close the Drafts drawer before navigating history.
 probe(session,{'op':'back'});s=wait(lambda s:s['surface']['data']['state']['fieldForm']['id']==a)
 assert s['surface']['data']['state']['fieldForm']['notes']==marker+' A'
 select(b);s=wait();assert s['surface']['data']['state']['fieldForm']['notes']==''
 capture(session,out/'desktop.png',1440,1000,0)
 (out/'desktop-result.json').write_text(json.dumps({'a':a,'b':b,'per_record_edits':True,'back_restores_draft':True,'discard_is_scoped':True,'history_depth':s['history_depth']},indent=2))
 print((out/'desktop-result.json').read_text())
