"""Server-side expansion of reusable fragments over SQL data; no app handlers."""
import copy
import hashlib
import re


def get(data, path):
    from ui_apps import pointer
    return pointer(data, path)


def graph(nodes, root):
    ids = {n['id']: n for n in nodes}
    if len(ids) != len(nodes): raise ValueError('Duplicate fragment component ID')
    seen = set()
    def visit(key):
        if key in seen or key not in ids: raise ValueError('Invalid fragment tree')
        seen.add(key)
        for child in ids[key].get('children', []) + ([ids[key]['child']] if 'child' in ids[key] else []): visit(child)
    visit(root)
    if seen != set(ids): raise ValueError('Unreachable fragment node')


def validate(doc):
    slots = set()
    fragments = doc.get('fragments', {})
    if not isinstance(fragments, dict) or len(fragments)>100: raise ValueError('At most 100 named fragments')
    for name, fragment in fragments.items():
        if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,80}', name) or set(fragment)-{'root','components'}:
            raise ValueError('Invalid fragment')
        if not 1 <= len(fragment.get('components', [])) <= 500: raise ValueError('Invalid fragment size')
        graph(fragment['components'], fragment['root'])
    graph(doc['components'], 'root')
    for field in ('repeat', 'switch'):
        for target, rule in doc.get(field, {}).items():
            if target in slots: raise ValueError('A slot has one composition owner')
            slots.add(target)
            get(doc['data'], rule['path'])
            allowed = {'path','fragment','key'} if field=='repeat' else {'path','cases','default'}
            if set(rule)-allowed: raise ValueError('Invalid composition rule')
            names = [rule.get('fragment')] if field=='repeat' else [*rule.get('cases', {}).values(), rule.get('default')]
            if any(name is not None and name not in fragments for name in names): raise ValueError('Unknown fragment')
    # Templates are ordinary catalog components. The runtime validates their
    # expanded graph/bindings before delivering it to either renderer.
    all_nodes = doc['components'] + [n for f in fragments.values() for n in f['components']]
    for slot in slots:
        owners = [n for n in all_nodes if n['id']==slot]
        if not owners or any(n.get('children') or n.get('component') not in {'CStack','CDataGrid','CResourceDay'} for n in owners):
            raise ValueError('Composition owns an empty container: '+slot)
    navigation = doc.get('navigation', {})
    if not isinstance(navigation, dict) or set(navigation)-{'paths'}: raise ValueError('Navigation declares state identity paths')
    paths = navigation.get('paths', [])
    if not isinstance(paths,list) or len(paths)>32 or any(not isinstance(p,str) or not p.startswith('/state/') for p in paths):
        raise ValueError('Navigation paths must name state values')
    for path in paths:
        get(doc['data'],path)
        if any(path==f['path'] or path.startswith(f['path']+'/') for f in doc.get('forms', [])):
            raise ValueError('Navigation identity must be outside editable forms')
    for form in doc.get('forms', []):
        if set(form)-{'path','source','key'} or not form['path'].startswith('/state/'):
            raise ValueError('Forms initialize a transient state object')
        if not isinstance(get(doc['data'], form['path']), dict) or not isinstance(get(doc['data'], form['source']), dict):
            raise ValueError('Forms bind objects')


def expand(doc, data):
    out = []
    def fragment(name, prefix, item=None):
        definition = doc['fragments'][name]
        emit(definition['components'], prefix, item)
        return prefix+definition['root']
    def emit(nodes, prefix='', item=None):
        for original in nodes:
            node = copy.deepcopy(original)
            def local(value):
                if isinstance(value, dict):
                    if set(value)=={'item'}:
                        if item is None: raise ValueError('Item binding outside a repeated fragment')
                        return copy.deepcopy(get(item, value['item']))
                    return {k:local(v) for k,v in value.items()}
                if isinstance(value, list): return [local(v) for v in value]
                return value
            node = local(node)
            for prop in ('options','positions','resources','rowKeys','tone','readOnly','visible','maximum','minimum','title','subtitle'):
                if isinstance(node.get(prop), dict) and set(node[prop])=={'path'}:
                    node[prop] = copy.deepcopy(get(data,node[prop]['path']))
            ident = original['id']; node['id'] = prefix+ident
            if 'child' in node: node['child'] = prefix+node['child']
            if 'children' in node: node['children'] = [prefix+c for c in node['children']]
            out.append(node)
            if len(out)>500: raise ValueError('Expanded app exceeds 500 components')
            if ident in doc.get('switch', {}):
                rule = doc['switch'][ident]
                key = get(data, rule['path'])
                if isinstance(key, list): key = key[0] if key else ''
                key = str(key).lower() if isinstance(key,bool) else str(key)
                name = rule['cases'].get(key, rule.get('default'))
                node['children'] = [fragment(name, prefix+ident+'.', item)] if name else []
            if ident in doc.get('repeat', {}):
                rule = doc['repeat'][ident]; rows = get(data, rule['path'])
                if not isinstance(rows, list) or len(rows)>100: raise ValueError('Repeated sections allow at most 100 rows')
                keys = [str(row[rule.get('key','id')]) for row in rows]
                if len(set(keys)) != len(keys): raise ValueError('Repeated rows require unique keys')
                node['children'] = [fragment(rule['fragment'], prefix+ident+'.'+hashlib.sha256(k.encode()).hexdigest()[:12]+'.', row) for k,row in zip(keys,rows)]
                if node['component']=='CDataGrid':
                    cells=[]
                    for root in node['children']:
                        row=next(n for n in out if n['id']==root)
                        if row['component']!='CStack':raise ValueError('Grid row fragments use a CStack root')
                        cells.extend(row['children']);out.remove(row)
                    node['children']=cells
                    node['rowKeys'] = keys
            if len(out)>500: raise ValueError('Expanded app exceeds 500 components')
    emit(doc['components'])
    return out
