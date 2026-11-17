import sys,time,json,subprocess,os,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from capture_published import isolated_app
from studio import probe,capture
parser=argparse.ArgumentParser(description='Verify live record pins on an isolated desktop. Creates a personal demo pin; submits no business forms.')
parser.add_argument('output',type=Path)
parser.add_argument('--keep-pin',action='store_true')
args=parser.parse_args()
out=args.output;out.mkdir(parents=True,exist_ok=True)
ref={'slug':'demo-field-notebook','record':'visit','id':'HV-CASE-01'}
with isolated_app(ref['slug'],out/'native.log') as session:
 def call(op,**kw):return probe(session,dict(op=op,**kw))
 def wait(predicate,op='inspect',seconds=40):
  end=time.monotonic()+seconds
  while time.monotonic()<end:
   v=call(op)
   if predicate(v):return v
   time.sleep(.2)
  raise RuntimeError(json.dumps(v)[:1500])
 def selected(r):return wait(lambda s:not s.get('busy') and s.get('surface',{}).get('data',{}).get('job',{}).get('id')==r)
 initial=wait(lambda p:not p['busy'],op='pins')
 existed=any(p['reference']==ref for p in initial['pins'])
 print('open',call('open_record',reference=ref),flush=True)
 s=selected(ref['id']);assert not s['failed'],s['notice']
 fields=[n for n in s['surface']['nodes'] if n['component']=='CField' and n.get('bindings',{}).get('value',{}).get('path')=='/state/fieldForm/notes']
 assert fields
 print('field',fields[0]['id'],flush=True)
 call('fill',id=fields[0]['id'],value='Pin navigation draft recovery check — do not submit.')
 wait(lambda s:s['draft_saved'])
 call('pin_record')
 p=wait(lambda p:not p['busy'] and not p['changing'] and any(i['reference']==ref and i.get('summary') for i in p['pins']),op='pins')
 (out/'native-pins.json').write_text(json.dumps(p,indent=2))
 print('pin saved',[(i['id'],i['summary']['version']) for i in p['pins']],flush=True)
 call('open_record',reference={**ref,'id':'HV-1001'});selected('HV-1001')
 call('open_record',reference=ref,ask=True);s=selected(ref['id'])
 assert s['surface']['data']['state']['fieldForm']['notes'].startswith('Pin navigation draft recovery check')
 a=wait(lambda a:a['open'] and not a['loading'],op='assistant_inspect')
 assert a['context']['record_links'][0]['reference']==ref
 print('draft and Ask Callie context retained',flush=True)
 call('assistant_close');capture(session,out/'desktop-record.png',1440,1000,0)
 call('home');wait(lambda s:not s['busy'] and not s['slug'])
 capture(session,out/'desktop-pins.png',1440,1000,0)
 env={**os.environ,'DISPLAY':session['display']}
 subprocess.run(['xdotool','mousemove','--window',session['window'],'60','24','click','1'],env=env,check=True)
 time.sleep(.6)
 subprocess.run(['magick','import','-display',session['display'],'-window',session['window'],str(out/'desktop-canvas.png')],check=True)
 print('canvas screenshot saved',flush=True)
 if not existed and not args.keep_pin:
  import urllib.request
  config=json.loads((Path.home()/'.config/calliope-canvas/connection.json').read_text())
  pin=next(i for i in p['pins'] if i['reference']==ref)
  request=urllib.request.Request(config['base_url'].rstrip('/')+'/api/calliope/app-pins',data=json.dumps({'operation':'remove','id':pin['id']}).encode(),headers={'Content-Type':'application/json','Cookie':'wh_session='+config['session_cookie']})
  with urllib.request.urlopen(request,timeout=30) as response:assert response.status==200
