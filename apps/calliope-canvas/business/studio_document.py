"""Bounded authoring documents for the shared Calliope catalog.

This is a preview contract, not a new business-command or SQL execution API.
"""
import copy
import json
import math
import re
from pathlib import Path
from presentation import SPEC, validate_components

MAX_BYTES = 1_500_000
BUILTINS = {
    'Button': {'child', 'variant', 'action', 'weight'},
    'TextField': {'label', 'value', 'variant', 'weight'},
    'ChoicePicker': {'label', 'value', 'options', 'variant', 'weight'},
    'CheckBox': {'label', 'value', 'weight'},
}


def pointer(data, path):
    if not isinstance(path, str) or not path.startswith('/') or len(path) > 500:
        raise ValueError('Bindings require a bounded absolute JSON pointer')
    current = data
    for part in path[1:].split('/'):
        key = part.replace('~1', '/').replace('~0', '~')
        try:
            current = current[int(key)] if isinstance(current, list) else current[key]
        except (KeyError, IndexError, ValueError, TypeError):
            raise ValueError('Missing binding: ' + path) from None
    return current


def resolve(value, data):
    if isinstance(value, dict) and set(value) == {'path'}:
        return copy.deepcopy(pointer(data, value['path']))
    if isinstance(value, dict):
        return {k: resolve(v, data) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, data) for v in value]
    return value


def bounded(value, depth=0):
    if depth > 32:
        raise ValueError('Document nesting exceeds 32 levels')
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError('Object keys must be strings')
            bounded(child, depth + 1)
    elif isinstance(value, list):
        if len(value) > 5000:
            raise ValueError('Array exceeds 5000 items')
        for child in value:
            bounded(child, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Non-finite numbers are not supported')


def chart(spec):
    import sys
    sys.path.append(str(Path(__file__).resolve().parents[3] / 'services/calliope'))
    from ui_charts import validate
    validate(spec)


def normalize(document):
    """Accept terse {components,data} or a fixture snapshot, return inert preview."""
    bounded(document)
    if len(json.dumps(document, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError('Definition exceeds 1.5 MB')
    if not isinstance(document, dict):
        raise ValueError('Expected a definition object')
    if document.get('schema') == 'calliope.app.v1':
        from studio_contract import studio_snapshot
        return studio_snapshot(document)
    if document.get('previewDefinition'):
        from studio_contract import studio_snapshot
        data = document['messages'][2]['updateDataModel']['value']
        return studio_snapshot(document['previewDefinition'], data=data, state=data['state'],
                               expected_hash=document['preview']['definition_sha256'])
    if document.get('reactive'):
        from reactive import prepare
        if 'messages' in document:
            data = next(m['updateDataModel']['value'] for m in document['messages'] if 'updateDataModel' in m)
            data = copy.deepcopy(data); data.pop('resources', None)
            return prepare({'components':document['baseComponents'], 'data':data, 'reactive':document['reactive']})
        if set(document) - {'components','data','title','reactive'}:
            raise ValueError('Unexpected reactive document property')
        return prepare(document)
    if 'messages' in document:
        messages = document['messages']
        if not isinstance(messages, list) or len(messages) != 3:
            raise ValueError('Use one create/components/data surface')
        try:
            create, update, model = (messages[i][key] for i, key in enumerate(
                ('createSurface', 'updateComponents', 'updateDataModel')))
            if create['catalogId'] != SPEC['id'] or len({v['surfaceId'] for v in (create, update, model)}) != 1:
                raise ValueError('Unexpected catalog or surface')
            components, data = update['components'], model['value']
        except (KeyError, TypeError):
            raise ValueError('Malformed surface messages') from None
    else:
        if set(document) - {'components', 'data', 'title'}:
            raise ValueError('Use components, data and optional title')
        components, data = document.get('components'), document.get('data')
    if not isinstance(data, dict) or not isinstance(components, list):
        raise ValueError('Definition needs components array and data object')
    data = copy.deepcopy(data)
    data.setdefault('errors', {})
    # Server forms omit empty errors; an authoring preview makes them explicit.
    for node in components:
        path = node.get('error', {}).get('path') if isinstance(node, dict) and isinstance(node.get('error'), dict) else None
        if isinstance(path, str) and path.startswith('/errors/') and path.count('/') == 2 and isinstance(data['errors'], dict):
            data['errors'].setdefault(path[8:].replace('~1','/').replace('~0','~'), '')
    if not all(isinstance(n, dict) and isinstance(n.get('id'), str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', n['id']) and isinstance(n.get('component'), str) for n in components):
        raise ValueError('Components require bounded IDs and type names')
    by_id = {n['id']: n for n in components}
    if not 1 <= len(components) <= 500 or len(by_id) != len(components):
        raise ValueError('Invalid component count or duplicate IDs')
    visited = set()
    def walk(ident, depth=0):
        if not isinstance(ident,str) or ident not in by_id or depth>24:
            raise ValueError('Invalid child reference or graph depth')
        if ident in visited:
            raise ValueError('A component may have only one parent: ' + ident)
        visited.add(ident)
        node = by_id[ident]
        if not isinstance(node.get('children',[]),list):
            raise ValueError('Children must be a list')
        for child in node.get('children', []) + ([node['child']] if 'child' in node else []):
            walk(child,depth+1)
    walk('root')
    if visited != set(by_id):
        raise ValueError('Unreachable components: ' + ', '.join(sorted(set(by_id) - visited)[:8]))
    try:
        validate_components(components)
    except (KeyError, TypeError, ZeroDivisionError, RecursionError) as exc:
        raise ValueError('Malformed component graph or properties') from exc
    for node in components:
        kind = node['component']
        p = resolve({k: v for k, v in node.items() if k not in ('id', 'component')}, data)
        if kind in BUILTINS and set(p) - BUILTINS[kind]:
            raise ValueError('Unknown builtin property: ' + node['id'])
        for key in ('text', 'label', 'description', 'help', 'unit', 'error', 'title'):
            if key in p and not isinstance(p[key], str):
                raise ValueError(f'{node["id"]}.{key} must resolve to text')
        if kind in ('CField', 'TextField') and not isinstance(p.get('value'), str):
            raise ValueError('Text inputs must bind a string: ' + node['id'])
        for key in ('required', 'visible', 'readOnly', 'pending'):
            if key in p and type(p[key]) is not bool:
                raise ValueError('Expected boolean: ' + node['id'] + '.' + key)
        if kind == 'CFilterBar' and not isinstance(p.get('value'), dict):
            raise ValueError('Live filters must bind an object of filter values')
        if kind == 'CheckBox' and type(p.get('value')) is not bool:
            raise ValueError('Checkbox must bind a boolean')
        if kind in ('ChoicePicker', 'CRecordPicker'):
            options = p.get('options')
            if not isinstance(options, list) or not 1 <= len(options) <= 100 or not all(isinstance(o, dict) and set(o) == {'label', 'value'} and all(isinstance(v, str) for v in o.values()) for o in options):
                raise ValueError('Invalid picker options')
            choices = [o['value'] for o in options]
            if len(set(choices)) != len(choices) or not isinstance(p.get('value'), list) or not all(v in choices for v in p['value']):
                raise ValueError('Picker value is not an offered choice')
        if kind == 'Button':
            action = p.get('action')
            if not isinstance(p.get('child'), str) or p.get('variant', 'borderless') not in ('primary', 'borderless') or not isinstance(action, dict) or set(action) != {'event'}:
                raise ValueError('Buttons require child and event action')
            event = action['event']
            if not isinstance(event, dict) or set(event) - {'name', 'context'} or not isinstance(event.get('name'), str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', event['name']) or not isinstance(event.get('context', {}), dict):
                raise ValueError('Invalid action event')
        if kind == 'TextField' and p.get('variant', 'shortText') not in ('shortText', 'longText'):
            raise ValueError('Unsupported text field variant')
        if kind == 'ChoicePicker' and p.get('variant', 'mutuallyExclusive') != 'mutuallyExclusive':
            raise ValueError('Only single-choice pickers are supported')
        if kind == 'CVegaChart':
            chart(p['spec'])
        if kind == 'CAttachments' and (not isinstance(p.get('value'), list) or p['value']):
            raise ValueError('Preview attachments start empty; no existing file handles')
    data = copy.deepcopy(data)
    data.setdefault('draft', {})
    data.setdefault('errors', {})
    surface = 'transaction-desk'
    return {'view': 'studio', 'selected': 'preview', 'revision': 0,
            'draft': data['draft'], 'notice': 'Generated preview · actions are recorded only',
            'messages': [
                {'version': 'v0.9', 'createSurface': {'surfaceId': surface, 'catalogId': SPEC['id']}},
                {'version': 'v0.9', 'updateComponents': {'surfaceId': surface, 'components': copy.deepcopy(components)}},
                {'version': 'v0.9', 'updateDataModel': {'surfaceId': surface, 'value': data}}]}
