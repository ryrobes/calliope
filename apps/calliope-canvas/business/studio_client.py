#!/usr/bin/env python3
"""Callie's client for isolated structural/GPUI testing on a Calliope instance. No browser needed."""
import argparse
import base64
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(os.environ.get('CALLIOPE_UI_STUDIO_CONFIG', str(Path.home()/'.config/calliope-ui-studio.json'))))
    parser.add_argument('--session', default='')
    parser.add_argument('--full', action='store_true', help='Return all resolved properties and data, instead of compact nodes')
    sub = parser.add_subparsers(dest='op', required=True)
    s=sub.add_parser('start'); s.add_argument('--native',action='store_true')
    s=sub.add_parser('inspect'); s.add_argument('--id', help='Component ID prefix')
    for op in ['stop','offline','online','restart-native']: sub.add_parser(op)
    for op in ['check','load','scenario']:
        s=sub.add_parser(op); s.add_argument('file',type=Path)
    s=sub.add_parser('example'); s.add_argument('view'); s.add_argument('output',type=Path)
    s=sub.add_parser('catalog'); s.add_argument('output',type=Path)
    s=sub.add_parser('fill'); s.add_argument('id'); s.add_argument('value',help='JSON value')
    s=sub.add_parser('decision'); s.add_argument('id'); s.add_argument('value')
    s=sub.add_parser('press'); s.add_argument('id')
    s=sub.add_parser('fixture_event'); s.add_argument('id',choices=['case_delivery'])
    s=sub.add_parser('attach'); s.add_argument('id'); s.add_argument('file',type=Path)
    s=sub.add_parser('screenshot'); s.add_argument('output',type=Path); s.add_argument('--viewport',choices=['desktop','tablet','phone'],default='desktop'); s.add_argument('--width',type=int); s.add_argument('--height',type=int); s.add_argument('--scroll',type=int,default=0)
    a=parser.parse_args()
    config=json.loads(a.config.read_text())
    body={'op':a.op,'session':a.session,'full':a.full}
    for key in ['native','id','view','viewport','width','height','scroll']:
        if hasattr(a,key): body[key]=getattr(a,key)
    if a.op in ('check','load','scenario'):
        if a.file.stat().st_size>1_500_000: raise ValueError('Input file exceeds 1.5 MB')
        body['steps' if a.op=='scenario' else 'document']=json.loads(a.file.read_text())
    if a.op=='decision': body['value']=a.value
    if a.op=='fill': body['value']=json.loads(a.value)
    if a.op=='attach':
        if a.file.stat().st_size>2*1024*1024: raise ValueError('Evidence exceeds 2 MB')
        body.update(name=a.file.name,data=base64.b64encode(a.file.read_bytes()).decode())
    request=urllib.request.Request(config['url'].rstrip('/')+'/v1/run',json.dumps(body).encode(),{'Content-Type':'application/json','Authorization':'Bearer '+config['token']})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=120) as response: result=json.load(response)
    except urllib.error.HTTPError as e:
        try: message=json.load(e).get('error',str(e))
        except ValueError: message=str(e)
        raise RuntimeError(message) from None
    if a.op in ('example','catalog','screenshot'):
        a.output.parent.mkdir(parents=True,exist_ok=True)
        raw=base64.b64decode(result['data'],validate=True) if a.op=='screenshot' else json.dumps(result,indent=2).encode()
        a.output.write_bytes(raw); a.output.chmod(0o600)
        result={**{k:v for k,v in result.items() if k!='data'},'path':str(a.output.resolve())} if a.op=='screenshot' else {'path':str(a.output.resolve()),'bytes':len(raw)}
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(json.dumps({'error':str(exc)}),file=sys.stderr); sys.exit(1)
