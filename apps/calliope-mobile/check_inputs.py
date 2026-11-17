#!/usr/bin/env python3
"""Exercise Android physical-key entry without submitting business data. Requires an unlocked phone with no drafts in Field notebook."""
import sys,time,json,subprocess,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from probe import Device
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--serial',required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();p=Device(args.serial);out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
def adb(*cmd):return subprocess.check_output(p.adb+list(cmd),text=True)
def state():return p.probe({'op':'inspect'})
def wait(pred=lambda s:True):
 end=time.monotonic()+35;v={}
 while time.monotonic()<end:
  try:v=state()
  except (subprocess.CalledProcessError,subprocess.TimeoutExpired):time.sleep(.25);continue
  if not v.get('busy') and pred(v):return v
  time.sleep(.25)
 (out/'phone-failure.json').write_text(json.dumps(v,indent=2));raise RuntimeError('State not reached')
def openapp(slug):
 for _ in range(10):
  wait()
  if p.probe({'op':'open_app','slug':slug}).get('ok'):return wait(lambda s:s.get('slug')==slug and bool(s.get('surface',{}).get('nodes')))
  time.sleep(.3)
 raise RuntimeError('Open rejected')
def form(s):return s['surface']['data']['state']['fieldForm']
v=wait()
if v.get('account'):p.probe({'op':'press','id':'cancel'});v=wait()
if v.get('slug')!='demo-field-notebook':v=openapp('demo-field-notebook')
assert not v.get('pending') and not v['drafts'], 'Preserve existing unsent work'
original=form(v).copy()
assert original['notes']=='' and original['temperature']=='', original
result={}
try:
 p.probe({'op':'press','id':'page.submit'})
 v=wait(lambda s:'page.notes' in s['render']['field_errors'] and s['render']['focused_input']=='page.notes')
 assert not v['pending'];result['invalid_submit_focus']=True
 time.sleep(1)
 adb('shell','input','text','Phone%snative%sentry')
 adb('shell','input','keyevent','KEYCODE_ENTER')
 adb('shell','input','text','Second%sline')
 v=wait(lambda s:'Second line' in form(s)['notes']);expected=form(v)['notes'];assert expected=='Phone native entry\nSecond line',expected
 wait(lambda s:not s['render']['field_errors'] and not s['failed'])
 result['multiline']=True;print('Multiline passed',flush=True)
 adb('shell','input','keycombination','KEYCODE_CTRL_LEFT','KEYCODE_A')
 adb('shell','input','keycombination','KEYCODE_CTRL_LEFT','KEYCODE_C')
 adb('shell','input','keyevent','KEYCODE_DEL')
 wait(lambda s:form(s)['notes']=='')
 adb('shell','input','keycombination','KEYCODE_CTRL_LEFT','KEYCODE_V')
 wait(lambda s:form(s)['notes']==expected);result['select_copy_paste']=True;print('Clipboard passed',flush=True)
 adb('shell','input','keyevent','KEYCODE_TAB')
 wait(lambda s:s['render']['focused_input']=='page.temperature' and s['render']['input_kind']=='quantity')
 ime=adb('shell','dumpsys','input_method')
 assert 'inputType=0x3002' in ime;result['numeric_ime']=True;print('Numeric IME passed',flush=True)
 adb('shell','input','text','72.5')
 wait(lambda s:form(s)['temperature']=='72.5')
 p.capture(out/'phone-number-final.png')
 adb('shell','input','keycombination','KEYCODE_SHIFT_LEFT','KEYCODE_TAB')
 wait(lambda s:s['render']['focused_input']=='page.notes');result['tab_shift_tab']=True
finally:
 v=state()
 if v.get('slug')=='demo-field-notebook':
  for field,k in [('page.notes','notes'),('page.temperature','temperature')]:
   p.probe({'op':'fill','id':field,'value':original[k]})
  wait(lambda s:form(s)['notes']==original['notes'] and form(s)['temperature']==original['temperature'])
  adb('shell','input','keyevent','KEYCODE_BACK')
print(json.dumps(result),flush=True)
(out/'phone-input-results.json').write_text(json.dumps(result,indent=2)+'\n')
