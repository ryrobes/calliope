#!/usr/bin/env python3
"""Callie UI studio: disposable SQL fixtures, semantic actions and real GPUI screenshots.

No browser, coordinate matching or arbitrary-code execution protocol required.
Native fill/press use the running renderer; structural mode uses the same definition.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parent
NATIVE_BINARY=Path(os.environ.get('CALLIOPE_CANVAS_BINARY', ROOT.parent/'target/release/calliope-canvas'))

def read_json(path):return json.loads(path.read_text())
def process_stamp(pid):
    try:return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]
    except OSError:return None
def write_json(path,value):
    if path.name=='session.json':
        marks=value.setdefault('process_stamps',{})
        for pid in value['pids']:
            if str(pid) not in marks:marks[str(pid)]=process_stamp(pid)
    path.write_text(json.dumps(value,indent=2));path.chmod(0o600)
def stop_owned(session,pid):
    stamp=session.get('process_stamps',{}).get(str(pid))
    if stamp and stamp==process_stamp(pid):
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:pass
def http(url,body=None):
    request=urllib.request.Request(url,json.dumps(body).encode() if body is not None else None,{'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=12) as r:return json.load(r)
    except urllib.error.HTTPError as e:raise RuntimeError(e.read().decode()) from e

def resolve(value,data):
    if isinstance(value,dict) and set(value)=={'path'}:
        node=data
        for key in value['path'].strip('/').split('/'):
            try:node=node[int(key)] if isinstance(node,list) else node[key.replace('~1','/').replace('~0','~')]
            except (KeyError,TypeError,ValueError,IndexError):return None
        return copy.deepcopy(node)
    if isinstance(value,dict):return {k:resolve(v,data) for k,v in value.items()}
    if isinstance(value,list):return [resolve(v,data) for v in value]
    return value

def nodes(snapshot):
    c=next(m['updateComponents']['components'] for m in snapshot['messages'] if 'updateComponents' in m)
    data=next(m['updateDataModel']['value'] for m in snapshot['messages'] if 'updateDataModel' in m)
    return c,data

def inspect(snapshot):
    from presentation import validate_components
    c,data=nodes(snapshot);validate_components(c)
    return {'view':snapshot['view'],'selected':snapshot['selected'],'revision':snapshot['revision'],'notice':snapshot.get('notice',''),'surface':{'nodes':[{'id':n['id'],'component':n['component'],'props':resolve({k:v for k,v in n.items() if k not in ('id','component')},data),'bindings':{k:v for k,v in n.items() if k not in ('id','component')}} for n in c],'data':data},'validation':'passed',**({'preview':snapshot['preview']} if snapshot.get('preview') else {})}

def probe(session,command):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(8);client.connect(session['probe']);client.sendall(json.dumps(command).encode()+b'\n');response=b''
        while b'\n' not in response:
            part=client.recv(65536)
            if not part:break
            response+=part
        value=json.loads(response)
    if value.get('error') and isinstance(value['error'],str):raise RuntimeError(value['error'])
    return value

def settle(session):
    time.sleep(.3)
    for _ in range(100):
        state=probe(session,{'op':'inspect'})
        if not state.get('busy') and state.get('surface',{}).get('nodes'):return state
        time.sleep(.15)
    raise RuntimeError('Native UI did not settle')

def start(directory,native):
    if (directory/'session.json').exists():raise RuntimeError('Use a new session directory; existing sessions must be stopped explicitly.')
    directory.mkdir(parents=True,exist_ok=True,mode=0o700);directory.chmod(0o700)
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    url=f'http://127.0.0.1:{port}';log=open(directory/'service.log','ab')
    server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port',str(port),'--db',str(directory/'fixture.sqlite')],stdout=log,stderr=log,start_new_session=True)
    session={'url':url,'server':server.pid,'pids':[server.pid],'directory':str(directory),'native':False}
    write_json(directory/'session.json',session)
    for _ in range(60):
        try:http(url+'/api/health');break
        except Exception:time.sleep(.1)
    else:raise RuntimeError('Fixture service failed to start')
    write_json(directory/'snapshot.json',http(url+'/api/action',{'name':'set_view','context':{'view':'dispatch'}}))
    if native:
        for tool in ['Xvfb','xdotool']:
            if not shutil.which(tool):raise RuntimeError('Native screenshot mode requires '+tool)
        if not (shutil.which('magick') or shutil.which('import')):raise RuntimeError('Native screenshot mode requires ImageMagick')
        read,write=os.pipe();log=open(directory/'native.log','ab')
        display=subprocess.Popen(['Xvfb','-displayfd',str(write),'-screen','0','1920x1200x24','-nolisten','tcp'],pass_fds=[write],stdout=log,stderr=log,start_new_session=True)
        session['pids'].append(display.pid);write_json(directory/'session.json',session)
        os.close(write)
        with os.fdopen(read) as pipe:number=pipe.readline().strip()
        env=os.environ.copy();env.pop('WAYLAND_DISPLAY',None);env.update(DISPLAY=':'+number,XDG_SESSION_TYPE='x11',XDG_DATA_HOME=str(directory/'data'),CALLIOPE_BUSINESS_URL=url,CALLIOPE_UI_PROBE=str(directory/'probe.sock'))
        icds=sorted(Path('/usr/share/vulkan/icd.d').glob('lvp_icd*.json'))
        if icds:env['VK_ICD_FILENAMES']=str(icds[0])
        app=subprocess.Popen([str(NATIVE_BINARY),'--business'],env=env,stdout=log,stderr=log,start_new_session=True)
        session.update(native=True,display=':'+number,probe=str(directory/'probe.sock'),app=app.pid,pids=[server.pid,display.pid,app.pid]);write_json(directory/'session.json',session)
        for _ in range(80):
            if Path(session['probe']).exists():break
            time.sleep(.1)
        ids=subprocess.check_output(['xdotool','search','--pid',str(app.pid)],env=env,text=True).strip().splitlines()
        if not ids:raise RuntimeError('Native window not found')
        session['window']=ids[-1];write_json(directory/'session.json',session)
        subprocess.run(['xdotool','windowmove',session['window'],'0','0','windowsize',session['window'],'1500','1100','windowfocus',session['window'],'mousemove','--window',session['window'],'600','300'],env=env,check=True)
        settle(session)
        probe(session,{'op':'press','id':'view-dispatch'});settle(session)
    return session

def filter_event(components, data, path):
    """Structural fill represents a completed edit, so settle without a timer."""
    from forms import validate
    group=next((n for n in components if n['component']=='CFilterBar' and path.startswith(n['value']['path']+'/')),None)
    if not group:return None
    for node in components:
        if not node.get('value',{}).get('path','').startswith(group['value']['path']+'/'):continue
        p=resolve(node,data)
        if node['component']=='CField':
            rules={key:p[key] for key in ('kind','required','minimum','maximum','precision') if key in p}
            rules['max_length']=p.get('maxLength',4000)
            if validate(p['value'],**rules):return None
        if node['component'] in ('ChoicePicker','CRecordPicker') and not p['value']:return None
    return {'name':group['onChange'],'context':resolve(group['value'],data)}


def operate(directory,session,op,id=None,value=None):
    path = directory/'snapshot.json'
    if op in ('inspect', 'fill', 'press') and path.is_file():
        loaded = read_json(path)
        if loaded.get('previewDefinition'):
            return operate_preview(directory, session, loaded, op, id, value)
    if op=='fixture_event':
        if id!='case_delivery':raise RuntimeError('Unknown fixture event')
        # Fixed fixture event through reviewed application commands. No arbitrary
        # SQL, filesystem access or action names cross the Studio protocol.
        import uuid
        def call(name,context):return http(session['url']+'/api/action',{'view':'case','selected':'CASE-HARBOR','name':name,'context':context,'requestId':str(uuid.uuid4()),'source':'Studio'})
        before=call('refresh',{});draft=before['draft']['receipt']
        draft['reference']='STUDIO-DELIVERY'
        from counter import quantity
        for item in nodes(before)[1]['receiving_lines']:draft['lines'][item['id']]['quantity']=quantity(item['expected']-item['received'])
        preview=call('case_receipt_preview',{'draft':draft})
        event=call('case_receive_confirm',{'draft':draft,'review':preview['state']['receipt_review']})
        if session['native']:
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                result=probe(session,{'op':'inspect'})
                if result.get('revision')==event['revision'] and not result.get('busy'):return result
                time.sleep(.2)
            raise RuntimeError('Native host did not observe the external delivery')
        previous=read_json(directory/'snapshot.json')
        fresh=http(session['url']+'/api/action',{'name':'refresh','context':{},'view':previous['view'],'selected':previous['selected'],'state':previous.get('state'),'panes':previous.get('panes',[])})
        preserve_inputs(previous,fresh,[])
        write_json(directory/'snapshot.json',fresh);return inspect(fresh)
    if op=='load':
        from studio_document import normalize
        snapshot=normalize(value)
        if 'previewTheme' in value:
            snapshot['previewTheme'] = value['previewTheme']
        if session['native']:
            settle(session);probe(session,{'op':'load','value':snapshot})
        write_json(directory/'snapshot.json',snapshot)
        return settle(session) if session['native'] else inspect(snapshot)
    if session['native']:
        if op=='inspect':return settle(session)
        # A background health poll may start after settle and before dispatch.
        # Only an explicit pre-dispatch busy rejection is safe to retry.
        for attempt in range(60):
            try:
                probe(session,{'op':op,'id':id,'value':value});break
            except RuntimeError as exc:
                if str(exc)!='UI is busy' or attempt==59:raise
                time.sleep(.15)
        state=settle(session);write_json(directory/'native-state.json',state);return state
    snapshot=read_json(directory/'snapshot.json');c,data=nodes(snapshot)
    if op=='decision':
        from reactive import compose
        resource=next((r for r in snapshot.get('reactive',[]) if r['id']==id),None)
        if not resource or value not in resource['variants']:raise RuntimeError('Unknown resource or variant')
        data['resources'][id]={'label':value,'value':resource['variants'][value].get('value',{}),'status':'held','message':'Studio forced outcome · inference bypassed','receipt':{'source':'studio-forced','label':value}}
        for m in snapshot['messages']:
            if 'updateComponents' in m:m['updateComponents']['components']=compose(snapshot['baseComponents'],snapshot['reactive'],data['resources'])
        write_json(directory/'snapshot.json',snapshot);return inspect(snapshot)
    if op=='inspect':return {**inspect(snapshot),'events':snapshot.get('events',[])}
    node=next((n for n in c if n['id']==id),None)
    if not node:raise RuntimeError('Unknown component ID: '+str(id))
    event=None
    if op=='fill':
        props=resolve(node,data)
        if node['component'] not in ('CField','TextField','ChoicePicker','CRecordPicker','CheckBox') or props.get('readOnly') or props.get('visible') is False:raise RuntimeError('Not an editable input')
        if node['component'] in ('CField','TextField') and not isinstance(value,str):raise RuntimeError('Text inputs require a string')
        if node['component']=='CheckBox' and type(value) is not bool:raise RuntimeError('Checkboxes require a boolean')
        if node['component'] in ('CRecordPicker','ChoicePicker') and (not isinstance(value,list) or not all(v in [o['value'] for o in props['options']] for v in value)):raise RuntimeError('Choose an offered value')
        path=node.get('value',{}).get('path')
        if not path:raise RuntimeError('No binding')
        target=data;keys=[k.replace('~1','/').replace('~0','~') for k in path.strip('/').split('/')]
        for k in keys[:-1]:target=target[k]
        previous=target[keys[-1]]
        target[keys[-1]]=value
        if previous!=value:event=filter_event(c,data,path)
    elif op=='press':
        if node['component']=='CRecordItem':
            props=resolve(node,data);event={'name':props['onSelect'],'context':{'id':props['recordId']}}
        elif node['component']=='Button':event=resolve(node['action']['event'],data)
        else:raise RuntimeError('Not an actionable component')
    else:raise RuntimeError('Unsupported operation')
    if event:
        if snapshot['view']=='studio':
            snapshot.setdefault('events',[])
            if len(snapshot['events'])>=100:raise RuntimeError('Preview event limit reached')
            snapshot['events'].append(event)
            write_json(directory/'snapshot.json',snapshot)
            return {**inspect(snapshot),'events':snapshot['events']}
        if event['name']=='field_queue':raise RuntimeError('Device queue requires a native/web host; structural mode tests its server contract separately.')
        import uuid
        body={**event,'view':snapshot['view'],'selected':snapshot['selected'],'state':snapshot.get('state'),'panes':snapshot.get('panes',[]),'source':'Studio','requestId':str(uuid.uuid4())}
        next_snapshot=http(session['url']+'/api/action',body)
        # Replay the case's partial acknowledgement and independent-data merge.
        if snapshot['view']==next_snapshot['view'] and snapshot['selected']==next_snapshot['selected'] and (snapshot['view']=='case' or op=='fill'):
            preserve_inputs(snapshot,next_snapshot,next_snapshot.get('acknowledgedDraftPaths',[]))
        elif event['name']=='dispatch_preview':nodes(next_snapshot)[1]['draft']=data['draft']
        snapshot=next_snapshot
    write_json(directory/'snapshot.json',snapshot);return inspect(snapshot)


def operate_preview(directory, session, snapshot, op, ident, value):
    """Drive the real control, then materialize with the publication contract."""
    from studio_contract import studio_snapshot
    c, data = nodes(snapshot)
    events = snapshot.get('events', [])
    if op == 'inspect':
        result = settle(session) if session['native'] else inspect(snapshot)
        return {**result, 'preview': snapshot['preview'], 'events': events}
    node = next((n for n in c if n['id'] == ident), None)
    if node is None:
        raise ValueError('Unknown component ID: ' + str(ident))
    props = resolve(node, data)
    event = None
    if op == 'press' and node['component'] in {'CLink', 'CRecordLink'}:
        if node['component'] == 'CRecordLink':
            from ui_records import reference
            target = reference(props.get('reference'))
            event = {'name':'open_record','context':{'componentId':ident,'reference':target},'simulated':True}
        else:
            from ui_apps import validate_link
            validate_link(props.get('href'))
            if not props.get('href'):raise ValueError('Link unavailable')
            event = {'name':'open_link','context':{'componentId':ident,'href':props['href']},'simulated':True}
        if session['native']:
            settle(session)
            probe(session, {'op': 'press', 'id': ident})
            settle(session)
        snapshot['events'] = [*events, event][-100:]
        write_json(directory/'snapshot.json', snapshot)
        return {**inspect(snapshot), 'preview':snapshot['preview'], 'events':snapshot['events']}
    if op == 'press' and node['component'] == 'CSection' and session['native']:
        settle(session)
        probe(session, {'op': 'press', 'id': ident})
        return {**settle(session), 'preview': snapshot['preview'], 'events': events}
    if op == 'fill':
        kind = node['component']
        if kind not in ('CField', 'TextField', 'ChoicePicker', 'CRecordPicker', 'CheckBox') or props.get('readOnly') or props.get('visible') is False:
            raise ValueError('Not an editable input: ' + ident)
        if kind in ('CField', 'TextField') and not isinstance(value, str):
            raise ValueError('Text inputs require a string')
        if kind == 'CheckBox' and type(value) is not bool:
            raise ValueError('Checkboxes require a boolean')
        if kind in ('ChoicePicker', 'CRecordPicker') and (not isinstance(value, list) or not all(v in [o['value'] for o in props['options']] for v in value)):
            raise ValueError('Choose an offered value')
        path = node.get('value', {}).get('path', '')
        if not path.startswith('/state/'):
            raise ValueError('Preview inputs must bind transient state')
        from ui_apps import put
        put(data, path, value)
        event = filter_event(c, data, path)
    elif node['component'] == 'Button':
        event = resolve(node['action']['event'], data)
    elif node['component'] == 'CRecordItem':
        event = {'name': props['onSelect'], 'context': {'id': props['recordId']}}
    else:
        raise ValueError('Not an actionable component: ' + ident)
    if session['native']:
        settle(session)
        probe(session, {'op': op, 'id': ident, 'value': value})
        actual = settle(session)
        # Read the rendered model after its real edit/debounce, not a fabricated
        # UI success. Contract navigation is evaluated outside the renderer.
        actual_data = actual['surface']['data']
        data = {k: actual_data[k] for k in snapshot['previewDefinition']['data']}
    next_snapshot = studio_snapshot(snapshot['previewDefinition'], data=data, state=data['state'],
                                    event=event['name'] if event else None,
                                    context=event.get('context', {}) if event else None,
                                    expected_hash=snapshot['preview']['definition_sha256'])
    if next_snapshot['preview_event']:
        events = [*events, next_snapshot['preview_event']][-100:]
    next_snapshot['events'] = events
    write_json(directory/'snapshot.json', next_snapshot)
    if session['native']:
        probe(session, {'op': 'load', 'value': next_snapshot})
        result = settle(session)
    else:
        result = inspect(next_snapshot)
    return {**result, 'preview': next_snapshot['preview'], 'events': events}


def preserve_inputs(previous,fresh,ack):
    base=previous['draft'];local=nodes(previous)[1]['draft'];remote=fresh['draft']
    def merge(b,l,r,path=''):
        if path in ack or path in ('/id','/version') or l==b:return copy.deepcopy(r)
        if all(isinstance(v,dict) for v in (b,l,r)):
            return {k:merge(b.get(k),l.get(k),r.get(k),path+'/'+k) for k in r}
        # Conflicting inputs are retained for inspection; actual host conflict
        # resolution and physical focus are verified separately on web/native.
        return copy.deepcopy(l)
    nodes(fresh)[1]['draft']=merge(base,local,remote)

def render_receipt(state):
    charts=state.get('render',{}).get('charts',{})
    errors={k:v['error'] for k,v in charts.items() if v.get('error')}
    pending=[k for k,v in charts.items() if v.get('pending') or (v.get('width',0)>0 and not v.get('ready') and not v.get('error'))]
    return {'ready':not state.get('busy') and not errors and not pending,
            'pending_charts':pending,'chart_errors':errors,
            'unmeasured_charts':[k for k,v in charts.items() if not v.get('width')]}

def capture(session,output,width,height,scroll):
    if not session['native']:raise RuntimeError('Start with --native for real GPUI screenshots')
    if not 390<=width<=1920 or not 400<=height<=1200 or abs(scroll)>30:raise RuntimeError('Capture bounds: width 390..1920, height 400..1200, scroll -30..30')
    env={**os.environ,'DISPLAY':session['display']};window=session['window']
    subprocess.run(['xdotool','windowsize',window,str(width),str(height),'mousemove','--window',window,str(width//2),str(height//2)],env=env,check=True)
    if scroll:subprocess.run(['xdotool','click','--repeat',str(abs(scroll)),'--delay','70','5' if scroll>0 else '4'],env=env,check=True)
    # Resizing schedules measured chart work. Wait for renderer state across two
    # frames; a fixed sleep can capture a successful-but-incomplete placeholder.
    time.sleep(.3);deadline=time.monotonic()+20;stable=0;receipt={}
    while time.monotonic()<deadline:
        receipt=render_receipt(probe(session,{'op':'inspect'}))
        stable=stable+1 if receipt['ready'] or receipt['chart_errors'] else 0
        if stable>=2:break
        time.sleep(.2)
    command=['magick','import'] if shutil.which('magick') else ['import']
    subprocess.run(command+['-window',window,str(output)],env=env,check=True,timeout=15)
    return {'screenshot':str(output.resolve()),'renderer':'GPUI','width':width,'height':height,**receipt}

def main():
    os.umask(0o077);p=argparse.ArgumentParser(description=__doc__);p.add_argument('--session',type=Path,required=True);p.add_argument('--compact',action='store_true')
    sub=p.add_subparsers(dest='op',required=True);s=sub.add_parser('start');s.add_argument('--native',action='store_true')
    s=sub.add_parser('check');s.add_argument('file',type=Path)
    s=sub.add_parser('load');s.add_argument('file',type=Path)
    sub.add_parser('inspect');sub.add_parser('stop');sub.add_parser('offline');sub.add_parser('online');sub.add_parser('restart-native')
    s=sub.add_parser('fill');s.add_argument('id');s.add_argument('value',help='JSON value, e.g. \'"09:00"\' or \'["Sam Rivera"]\'')
    s=sub.add_parser('decision');s.add_argument('id');s.add_argument('value',help='Force and hold a declared variant; bypass inference')
    s=sub.add_parser('press');s.add_argument('id')
    s=sub.add_parser('fixture_event');s.add_argument('id',choices=['case_delivery'])
    s=sub.add_parser('attach');s.add_argument('id');s.add_argument('file',type=Path)
    s=sub.add_parser('screenshot');s.add_argument('output',type=Path);s.add_argument('--width',type=int,default=1500);s.add_argument('--height',type=int,default=1100);s.add_argument('--scroll',type=int,default=0)
    s=sub.add_parser('frames');s.add_argument('--count',type=int,help='Start 1–360 forced redraws; omit to read the latest result')
    s=sub.add_parser('scenario');s.add_argument('file',type=Path)
    a=p.parse_args();directory=a.session.resolve()
    if a.op=='check':
        from studio_document import normalize
        result=inspect(normalize(read_json(a.file)))
    elif a.op=='start':result=start(directory,a.native)
    else:
        session=read_json(directory/'session.json')
        if a.op=='stop':
            for pid in reversed(session['pids']):
                stop_owned(session,pid)
            result={'stopped':session['pids']}
        elif a.op=='offline':
            if session.get('server'):
                stop_owned(session,session['server']);session['server']=None;write_json(directory/'session.json',session)
            result={'service':'offline','scope':'this disposable fixture only'}
        elif a.op=='online':
            if not session.get('server'):
                log=open(directory/'service.log','ab');port=session['url'].rsplit(':',1)[1]
                server=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port',port,'--db',str(directory/'fixture.sqlite')],stdout=log,stderr=log,start_new_session=True)
                session['server']=server.pid;session['pids'].append(server.pid);write_json(directory/'session.json',session)
                for _ in range(60):
                    try:http(session['url']+'/api/health');break
                    except Exception:time.sleep(.1)
            result={'service':'online'}
        elif a.op=='restart-native':
            if not session['native']:raise RuntimeError('No native renderer in this session')
            pid=session['app'];env={p.split(b'=',1)[0].decode():p.split(b'=',1)[1].decode() for p in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if b'=' in p}
            stop_owned(session,pid)
            for _ in range(50):
                if not Path(f'/proc/{pid}').exists():break
                time.sleep(.1)
            Path(session['probe']).unlink(missing_ok=True);log=open(directory/'native.log','ab')
            app=subprocess.Popen([str(NATIVE_BINARY),'--business'],env=env,stdout=log,stderr=log,start_new_session=True)
            session['app']=app.pid;session['pids'].append(app.pid);write_json(directory/'session.json',session)
            for _ in range(80):
                ids=subprocess.run(['xdotool','search','--pid',str(app.pid)],env=env,text=True,capture_output=True).stdout.strip().splitlines()
                if ids and Path(session['probe']).exists():break
                time.sleep(.1)
            session['window']=ids[-1];write_json(directory/'session.json',session)
            subprocess.run(['xdotool','windowmove',session['window'],'0','0','windowsize',session['window'],'1500','1100','windowfocus',session['window'],'mousemove','--window',session['window'],'600','300'],env=env,check=True)
            result=settle(session)
        elif a.op=='attach':
            if not session['native']:raise RuntimeError('Evidence capture requires native mode')
            probe(session,{'op':'attach','id':a.id,'file':str(a.file.resolve())});result=settle(session)
        elif a.op=='screenshot':result=capture(session,a.output,a.width,a.height,a.scroll)
        elif a.op=='frames':
            if not session['native']:raise RuntimeError('Frame sampling requires native mode')
            if a.count is not None and not 1<=a.count<=360:raise ValueError('Frame count must be between 1 and 360')
            result=probe(session,{'op':'frames',**({'count':a.count} if a.count is not None else {})})
        elif a.op=='load':result=operate(directory,session,'load',value=read_json(a.file))
        elif a.op=='scenario':
            steps=read_json(a.file);results=[];started=time.monotonic()
            for step in steps:
                if step['op']=='assert_event':
                    state=operate(directory,session,'inspect');events=state.get('events',[])
                    if not events or events[-1]!=step['equals']:raise AssertionError(f"Last preview event: {events[-1:]!r} != {step['equals']!r}")
                    results.append({'assert_event':True,'passed':True})
                elif step['op']=='assert_data':
                    state=operate(directory,session,'inspect');actual=resolve({'path':step['path']},state['surface']['data'])
                    if actual!=step['equals']:raise AssertionError(f"{step['path']}: {actual!r} != {step['equals']!r}")
                    results.append({'assert_data':step['path'],'passed':True})
                elif step['op']=='assert':
                    state=operate(directory,session,'inspect');node=next(n for n in state['surface']['nodes'] if n['id']==step['id']);actual=node['props'].get(step.get('property','text'))
                    if actual!=step['equals']:raise AssertionError(f"{step['id']}: {actual!r} != {step['equals']!r}")
                    results.append({'assert':step['id'],'passed':True})
                else:
                    state=operate(directory,session,step['op'],step.get('id'),step.get('value'));results.append({'op':step['op'],'notice':state.get('notice')})
            result={'passed':True,'seconds':round(time.monotonic()-started,3),'mode':'native' if session['native'] else 'structural','steps':results};write_json(directory/'scenario-result.json',result)
        else:result=operate(directory,session,a.op,getattr(a,'id',None),json.loads(a.value) if a.op=='fill' else a.value if a.op=='decision' else None)
    if a.compact and 'surface' in result:
        result={**{k:v for k,v in result.items() if k!='surface'},'nodes':[{'id':n['id'],'component':n['component'],**{k:v for k,v in n['props'].items() if k in ('label','text','value','error','children','child','action','readOnly','visible')},**({'valueBinding':n['bindings']['value']['path']} if isinstance(n['bindings'].get('value'),dict) and 'path' in n['bindings']['value'] else {})} for n in result['surface']['nodes']]}
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    try:main()
    except Exception as e:print(json.dumps({'error':str(e)}),file=sys.stderr);sys.exit(1)
