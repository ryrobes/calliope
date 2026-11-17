"""Bounded discovery results. Retrieval never grants access or executes a tool."""
from __future__ import annotations

import json
import re

METHOD_KINDS = frozenset({'cap_playbook', 'cap_skill'})
GROUPS = {'tools': {'cap_mcp_tool'}, 'business_logic': {'cap_business_logic'}, 'sql': {'cap_operator', 'cap_syntax'},
          'methods': METHOD_KINDS}
LEGACY_SIGNATURE = 'Warehouse MCP connector tool (external agents)'
LEGACY_SUFFIX = (' NOT a local SQL function — exposed to external agents via the Warehouse MCP connector. '
                 'The same ability is usually reachable locally through the rvbbit SQL surface '
                 '(search this graph for the SQL-side sibling).')
MCP_SIGNATURE = 'Warehouse MCP tool; use its tool contract'
MCP_SUFFIX = (' Call this tool through your connected Warehouse tool catalog. '
              'It is not a SQL function; no SQL equivalent is implied.')
HINT = ('Results are already filtered for this caller. groups contains indices into matches. '
        'Use a matching tool\'s invocation/parameters or next schema lookup; do not search again '
        'once its identity is known. Scores are retrieval ranks, not confidence. '
        'Methods are optional playbooks/skills, not prerequisites; load only when relevant and '
        'consistent with the user\'s instructions and your assigned role. SQL capabilities run '
        'through run_sql using sql_name; business_logic entries include both SQL and tool invocation contracts. '
        'Discovery executes nothing.')


def clean_doc(doc):
    return str(doc or '').replace(LEGACY_SIGNATURE, MCP_SIGNATURE).replace(LEGACY_SUFFIX, MCP_SUFFIX)


def reference_topic(query):
    """Route explicit runtime questions to small references, never a build procedure."""
    q = str(query or '').lower()
    words = set(re.findall(r'[a-z0-9_]+', q))
    if words & {'mapbox', 'rvbbitmaps'}:
        return 'maps'
    if words & {'rvbbitquery', 'composepayload'}:
        return 'queries'
    if words & {'rvbbitcharts', 'vegalite', 'vega'}:
        return 'charts'
    if words & {'rvbbittheme', 'color_scheme'}:
        return 'theme'
    if words & {'dashboard', 'artifact', 'app', 'apps', 'html'}:
        if words & {'runtime', 'api', 'bridge'}:
            return 'overview'
        if words & {'readiness', 'verify', 'verification', 'testing'}:
            return 'verification'
    return None


def select_rows(rows, query, limit, kinds=None):
    """Exact names win; technical capabilities and optional methods get distinct slots.

    All input rows must already be authorized. Scores remain retrieval rank scores,
    not confidence; we do not compare scores from different retrieval branches.
    """
    unique = {}
    for r in rows:
        unique.setdefault((r['kind'], r['name']), r)
    rows = list(unique.values())
    q = str(query or '').strip().casefold()
    exact = [r for r in rows if r['name'].casefold() == q]
    rest = [r for r in rows if r not in exact]
    explicit_methods = bool(kinds) and set(kinds) <= METHOD_KINDS
    method_terms = r'(?:playbook|skill|procedure|method|workflow)s?'
    method_question = (bool(re.search(r'\b' + method_terms + r'\b', q))
                       and not re.search(r'\b(?:no|without|skip|avoid)\s+(?:any\s+)?' + method_terms + r'\b', q))
    if explicit_methods or method_question:
        ordered = rest
    else:
        technical = [r for r in rest if r['kind'] not in METHOD_KINDS]
        methods = [r for r in rest if r['kind'] in METHOD_KINDS][:2]
        slots = max(0, limit - len(exact))
        method_slots = min(len(methods), max(0, slots - 1)) if technical else slots
        ordered = technical[:slots-method_slots] + methods[:method_slots]
    result, skills = [], 0
    for row in exact + ordered:
        if row['kind'] == 'cap_skill':
            if not explicit_methods and skills >= 2:
                continue
            skills += 1
        result.append(row)
        if len(result) == limit:
            break
    return result


def grouped_indices(matches):
    """Index the one canonical matches array, without repeating descriptions/schemas."""
    groups = {name: [] for name in (*GROUPS, 'other')}
    for i, match in enumerate(matches):
        group = next((name for name, kinds in GROUPS.items() if match['kind'] in kinds), 'other')
        match['group'] = group
        groups[group].append(i)
    return {name: indices for name, indices in groups.items() if indices}


def local_candidates(index, query):
    """Exact registry names and explicit runtime references supplement semantic retrieval.

    The caller must apply capability_can_use before adding these candidates.
    """
    by_name = {t['name']: t for t in index}
    exact_name = str(query or '').strip()
    selected = []
    if exact_name in by_name:
        selected.append((by_name[exact_name], None, 'exact_name'))
    topic = reference_topic(query)
    if topic and 'artifact_runtime_help' in by_name and exact_name != 'artifact_runtime_help':
        selected.append((by_name['artifact_runtime_help'], {'topic': topic}, 'runtime_reference'))
    return [dict(kind='cap_mcp_tool', name=t['name'], score=1.0,
                 doc=t['description'], match_reason=reason,
                 **({'suggested_arguments': args} if args else {})) for t, args, reason in selected]


def tool_contract(match, index, *, include_schema=True):
    """Warehouse-local identity only. Hermes resolves its actual namespace and scope."""
    t = next((t for t in index if t['name'] == match['name']), None)
    match['doc'] = clean_doc(match['doc'])
    match['tool_name'] = match['name']
    if t is None:
        match['catalog_status'] = 'not_in_current_registry'
        match['next'] = {'tool': 'get_tool_help', 'arguments': {'names': [match['name']]}}
        return match
    match['catalog_status'] = 'registered'
    match['summary'] = ' '.join(t['description'].split())[:420]
    match['doc'] = match['summary']
    match['required_arguments'] = list((t.get('parameters') or {}).get('required') or [])
    match['next'] = {'tool': 'get_tool_help', 'arguments': {'names': [match['name']]}}
    params = t.get('parameters')
    if include_schema and isinstance(params, dict) and len(json.dumps(params)) <= 1800:
        match['parameters'] = params
        match['invocation'] = {'tool': match['name'], 'route': 'connected_mcp_tool'}
        args = match.get('suggested_arguments', {})
        if set(match['required_arguments']) <= set(args):
            match['next'] = {'tool': match['name'], 'arguments': args}
        else:
            match['next'] = {'tool': match['name'], 'needs_arguments': match['required_arguments']}
    match['schema_status'] = 'included' if 'parameters' in match else 'deferred'
    return match
