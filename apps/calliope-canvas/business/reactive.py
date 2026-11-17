"""Application-host derived resources; the A2UI catalog stays unaware of decisions."""
import copy
import json
import os
from pathlib import Path
import re
import urllib.request
from urllib.parse import urlparse


def validate(resources, components, data):
    from studio_document import pointer
    if not isinstance(resources, list) or len(resources) > 4:
        raise ValueError('At most four independent reactive resources')
    ids, targets, patched = set(), set(), set()
    nodes = {n['id']: n for n in components}
    for r in resources:
        if not isinstance(r,dict) or set(r) - {'id','watch','tree','initial','variants','target','debounceMs'}:
            raise ValueError('Invalid reactive resource')
        ident = r.get('id')
        if not isinstance(ident,str) or not re.fullmatch('[a-z][a-z0-9_]{0,31}',ident) or ident in ids:
            raise ValueError('Resources require unique simple IDs')
        ids.add(ident)
        watch = r.get('watch')
        if not isinstance(watch,dict) or not 1 <= len(watch) <= 12:
            raise ValueError('Use 1..12 named watched paths')
        for key,path in watch.items():
            if not re.fullmatch('[a-z][a-z0-9_]{0,31}',key) or not isinstance(path,str) or path == '/resources' or path.startswith('/resources/') or path == '/':
                raise ValueError('Watch source data, never derived resources or the entire model')
            pointer(data,path)
        if type(r.get('debounceMs',350)) is not int or not 100 <= r.get('debounceMs',350) <= 2000:
            raise ValueError('Debounce must be 100..2000 ms')
        target = r.get('target')
        if target is not None:
            if target in targets or nodes.get(target,{}).get('component') != 'CStack' or nodes[target].get('children'):
                raise ValueError('A structural resource owns one empty CStack')
            targets.add(target)
        variants = r.get('variants')
        if not isinstance(variants,dict) or not 1 <= len(variants) <= 16 or r.get('initial') not in variants:
            raise ValueError('Resource requires variants and an initial variant')
        tree = r.get('tree')
        labels = set()
        def leaves(n, depth=0):
            if not isinstance(n,dict) or depth>4: raise ValueError('Invalid decision tree')
            if n.get('kind') == 'leaf': labels.add(n.get('label'))
            elif n.get('kind') == 'branch': leaves(n.get('yes'),depth+1);leaves(n.get('no'),depth+1)
            elif n.get('kind') == 'choice' and isinstance(n.get('routes'),dict):
                for v in n['routes'].values(): leaves(v,depth+1)
            else: raise ValueError('Unknown decision tree node')
        leaves(tree)
        if not labels <= set(variants): raise ValueError('Every leaf needs a variant')
        local_patches = set()
        for variant in variants.values():
            if not isinstance(variant,dict) or set(variant)-{'value','patches','components','root'} or not isinstance(variant.get('value',{}),dict):
                raise ValueError('Variant contains value, property patches and optional child fragment')
            if not isinstance(variant.get('patches',{}),dict): raise ValueError('Patches must be an object')
            for node,props in variant.get('patches',{}).items():
                if node not in nodes or not isinstance(props,dict) or set(props)&{'id','component','child','children','value','action'}:
                    raise ValueError('Patches may change presentation properties, not identity, input binding or actions')
                local_patches.add(node)
            fragment = variant.get('components',[])
            if not isinstance(fragment,list) or len(fragment)>100 or any(not isinstance(n,dict) or not isinstance(n.get('id'),str) or not n['id'].startswith(ident+'.') or n['id'] in nodes for n in fragment):
                raise ValueError('Fragment IDs must be resource.id-prefixed and locally owned')
            if fragment and (target is None or variant.get('root') not in {n['id'] for n in fragment}):
                raise ValueError('Fragment requires target and its root ID')
            if not fragment and variant.get('root'): raise ValueError('Empty fragment cannot have a root')
        if patched & local_patches: raise ValueError('Resources cannot patch the same node')
        patched |= local_patches


def initial(resources):
    return {r['id']: {'value':copy.deepcopy(r['variants'][r['initial']].get('value',{})),
                     'label':r['initial'], 'status':'idle', 'message':'Waiting for context'} for r in resources}


def compose(base, resources, states):
    nodes = copy.deepcopy(base)
    by_id = {n['id']: n for n in nodes}
    for r in resources:
        v = r['variants'][states.get(r['id'],{}).get('label',r['initial'])]
        for ident,patch in v.get('patches',{}).items(): by_id[ident].update(copy.deepcopy(patch))
        if r.get('target'): by_id[r['target']]['children'] = [v['root']] if v.get('root') else []
        nodes.extend(copy.deepcopy(v.get('components',[])))
    return nodes


def prepare(document):
    """Validate all outcomes before the first render, including their bindings."""
    from studio_document import normalize
    resources = document.get('reactive', [])
    base, data = copy.deepcopy(document['components']), copy.deepcopy(document['data'])
    if 'resources' in data: raise ValueError('The host owns /resources')
    validate(resources, base, data)
    states = initial(resources)
    # Independent ownership means validate one substitution at a time, not a
    # combinatorial product. Every fragment is confined to its own subtree.
    for r in resources:
        for label,v in r['variants'].items():
            trial = copy.deepcopy(states); trial[r['id']].update(label=label,value=v.get('value',{}))
            checked = normalize({'components':compose(base,resources,trial),'data':{**data,'resources':trial}})
            data['errors'] = checked['messages'][2]['updateDataModel']['value']['errors']
    snapshot = normalize({'components':compose(base,resources,states),'data':{**data,'resources':states}})
    snapshot['reactive'] = copy.deepcopy(resources)
    snapshot['baseComponents'] = base
    if len(json.dumps(snapshot).encode()) > 1_900_000:
        raise ValueError('Expanded reactive definition exceeds probe budget')
    return snapshot


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


def evaluate(body):
    """Only the operator selects the endpoint; definitions cannot supply URLs."""
    default_config = Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))/'calliope-canvas/decision.json'
    config_file = os.environ.get('CALLIOPE_DECISION_CONFIG') or (str(default_config) if default_config.is_file() else None)
    if config_file:
        config = json.loads(Path(config_file).read_text())
        base = config['url']
        headers = {'Authorization':'Bearer '+config['token']}
    elif os.environ.get('CALLIOPE_DECISION_URL') and os.environ.get('STUDIO_TOKEN_FILE'):
        base = os.environ['CALLIOPE_DECISION_URL']
        headers = {'Authorization':'Bearer '+Path(os.environ['STUDIO_TOKEN_FILE']).read_text().strip()}
    else:
        path = Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))/'calliope-canvas/connection.json'
        config = json.loads(path.read_text())
        base = config['base_url'].rstrip('/')+'/api/calliope/ui/decision'
        headers = {'Cookie':'wh_session='+config['session_cookie']}
    parsed = urlparse(base)
    internal = bool(os.environ.get('STUDIO_TOKEN_FILE')) and parsed.hostname == 'calliope-server'
    if parsed.username or parsed.password or parsed.fragment or (parsed.scheme != 'https' and not (parsed.scheme=='http' and (parsed.hostname in ('localhost','127.0.0.1','::1') or internal))):
        raise ValueError('Decision endpoint requires HTTPS or trusted local transport')
    headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(base,json.dumps(body,allow_nan=False).encode(),headers)
    with urllib.request.build_opener(NoRedirect).open(request,timeout=10) as response:
        if 'application/json' not in response.headers.get('Content-Type',''): raise ValueError('Decision endpoint did not return JSON')
        raw = response.read(128001)
        if len(raw)>128000: raise ValueError('Decision receipt exceeds limit')
        return json.loads(raw)
