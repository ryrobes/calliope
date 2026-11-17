#!/usr/bin/env python3
import sys,json,time,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from capture_published import isolated_app
from studio import probe,capture
parser=argparse.ArgumentParser(description='Exercise a real contextual Callie reply in private Xvfb. Creates a chat turn and local drafts; no business submissions.')
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--live-agent',action='store_true',help='Explicitly allow one live Calliope turn and its usage')
args=parser.parse_args()
if not args.live_agent:parser.error('--live-agent is required')
out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
with isolated_app('demo-field-notebook',out/'race-desktop.log') as session:
 def call(op,**kw):return probe(session,dict(op=op,**kw))
 def settle(record=None):
  end=time.monotonic()+45
  while time.monotonic()<end:
   s=call('inspect')
   if not s['busy'] and (record is None or s['surface']['data']['state']['fieldForm']['id']==record):return s
   time.sleep(.3)
  raise RuntimeError(s.get('notice'))
 def select(record):
  s=call('inspect');n=next(n for n in s['surface']['nodes'] if n['component']=='CRecordItem' and n['props'].get('recordId')==record)
  call('press',id=n['id']);return settle(record)
 s=select('HV-CASE-01');print('Harbor form',s['surface']['data']['state']['fieldForm'],flush=True)
 call('assistant_open')
 for _ in range(100):
  a=call('assistant_inspect')
  if not a['loading']:break
  time.sleep(.3)
 assert not a['error'],a['error']
 call('assistant_send',message='For this Harbor Dental visit, what is holding it up? Use only the attached context, do not run tools or change business records. Prepare my field report notes from these findings: inspected the unit, filter clean, waiting for the ordered replacement part. Offer the notes as a native draft suggestion, not a submission.')
 call('assistant_close');select('HV-1001');call('assistant_open')
 end=time.monotonic()+180
 while time.monotonic()<end:
  a=call('assistant_inspect')
  if not a['running']:break
  time.sleep(.6)
 (out/'race-reply.json').write_text(json.dumps(a,indent=2));print('Answer',a['error'],a['turns'][-1]['answer'],flush=True)
 assert not a['error'],a['error'];index=len(a['turns'])-1
 try:call('assistant_apply',index=index);raise AssertionError('Applied to wrong record')
 except RuntimeError as e:print('Wrong record rejected',str(e),flush=True)
 call('assistant_close');select('HV-CASE-01');call('assistant_open')
 call('fill',id='page.notes',value='My intervening edit')
 try:call('assistant_apply',index=index);raise AssertionError('Overwrote intervening edit')
 except RuntimeError as e:print('Intervening edit rejected',str(e),flush=True)
 call('fill',id='page.notes',value='')
 print('Apply',call('assistant_apply',index=index),flush=True)
 result=settle();assert 'filter' in result['surface']['data']['state']['fieldForm']['notes'].lower()
 capture(session,out/'desktop-harbor.png',1440,1000,0)
 call('assistant_close');capture(session,out/'desktop-harbor-form.png',1440,1000,0)
 capture(session,out/'phone-layout.png',420,900,0)
 (out/'race-result.json').write_text(json.dumps({'wrong_record_rejected':True,'intervening_edit_rejected':True,'applied_to_draft':True,'submitted':False,'session':a['session'],'turn':a['turns'][-1]['id']},indent=2))
 print('PASS',flush=True)
