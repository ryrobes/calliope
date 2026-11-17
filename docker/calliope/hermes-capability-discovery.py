"""Attach invocation contracts from the current Hermes session to discovery receipts.

No tool execution, tool-list mutation, name guessing, provider call or cross-session cache.
Warehouse ACLs choose candidates; Hermes's scoped registry chooses callable identities.
"""
from __future__ import annotations

import copy
import json
import logging

MAX_SCHEMA_CHARS = 1800
MAX_SCHEMAS = 3
MAX_ADDED_CHARS = 9000


def _decode(result):
    """Preserve the MCP result envelope and its JSON/string representation."""
    is_string = isinstance(result, str)
    outer = json.loads(result) if is_string else copy.deepcopy(result)
    if not isinstance(outer, dict):
        return None, None
    nested = outer.get('result')
    if isinstance(nested, str):
        try:
            payload = json.loads(nested)
        except ValueError:
            return None, None
        def encode(value):
            outer['result'] = json.dumps(value, ensure_ascii=False)
            return json.dumps(outer, ensure_ascii=False) if is_string else outer
    else:
        payload = outer
        def encode(value):
            return json.dumps(value, ensure_ascii=False) if is_string else value
    return payload, encode


def enrich(result, *, source, definitions, direct_names, deferred_names):
    try:
        payload, encode = _decode(result)
    except (ValueError, TypeError):
        return result
    capability = source.startswith('mcp__') and source.endswith('__capability_search')
    if not isinstance(payload, dict) or payload.get('error'):
        return result
    if capability:
        if payload.get('discovery_version') != 1 or not isinstance(payload.get('matches'), list):
            return result
        prefix = source.rsplit('__', 1)[0] + '__'
    elif source == 'tool_search' and isinstance(payload.get('tools'), dict):
        prefix = ''
    else:
        return result
    direct = set(direct_names)
    allowed = direct | set(deferred_names)
    catalog = {td['function']['name']: td['function'] for td in definitions
               if isinstance(td, dict) and isinstance(td.get('function'), dict)
               and td['function'].get('name') in allowed}
    budget = MAX_ADDED_CHARS
    schemas = 0

    def contract(record, name, *, suggested=None):
        nonlocal budget, schemas
        # Never retain server-side guesses about availability, schemas or the
        # model's transport. They can differ for each specialist/profile.
        for key in ('parameters', 'next', 'invocation', 'required_arguments',
                    'schema_status', 'already_available', 'tool_name'):
            record.pop(key, None)
        definition = catalog.get(name)
        record['catalog_status'] = 'available' if definition else 'not_in_agent_scope'
        if not definition:
            record['schema_status'] = 'unavailable'
            return
        route = 'direct' if name in direct else 'tool_call'
        record['tool_name'] = name
        record['already_available'] = route == 'direct'
        params = definition.get('parameters') or {'type': 'object', 'properties': {}}
        required = list(params.get('required') or [])
        record['required_arguments'] = required
        record['invocation'] = {'tool': name, 'route': route}
        record['next'] = {'tool': 'tool_describe', 'arguments': {'names': [name]}}
        size = len(json.dumps(params, ensure_ascii=False))
        if size > MAX_SCHEMA_CHARS or size > budget or schemas >= MAX_SCHEMAS:
            if route == 'direct':
                # A direct tool's complete schema is already in the prompt.
                # Such profiles may have no deferred-tool bridge at all.
                record['schema_status'] = 'in_tool_list'
                record['next'] = {'tool': name, 'needs_arguments': required}
            else:
                record['schema_status'] = 'deferred'
            return
        schemas += 1
        budget -= size
        record['parameters'] = params
        record['schema_status'] = 'included'
        # Suggested arguments only shortcut a no-side-effect reference read.
        # Never manufacture arguments or execute a capability while searching.
        args = suggested if isinstance(suggested, dict) else {}
        properties = params.get('properties') or {}
        args_valid = (set(args) <= set(properties) and all(
            not isinstance(properties.get(k), dict) or 'enum' not in properties[k]
            or value in properties[k]['enum'] for k, value in args.items()))
        if args_valid and set(required) <= set(args):
            record['next'] = ({'tool': name, 'arguments': args} if route == 'direct' else
                              {'tool': 'tool_call', 'arguments': {'calls': [{'name': name, 'arguments': args}]}})
        else:
            record['next'] = {'tool': name if route == 'direct' else 'tool_call',
                              'target': name, 'needs_arguments': required}

    if capability:
        for match in payload['matches'][:25]:
            if not isinstance(match, dict):
                continue
            if match.get('kind') == 'cap_mcp_tool':
                # Only the originating server may resolve its short names.
                # A bare match must never jump to a similarly named server/tool.
                name = match.get('name')
                if not isinstance(name, str):
                    continue
                full = prefix + name if not name.startswith('mcp__') else name
                if not full.startswith(prefix):
                    full = ''
                suggested = match.get('suggested_arguments') if name == 'artifact_runtime_help' else None
                contract(match, full, suggested=suggested)
            elif match.get('kind') in ('cap_skill', 'cap_playbook'):
                loader = match.get('load_with') or match.get('convert_with')
                if not isinstance(loader, dict):
                    continue
                tool = loader.get('tool', '')
                full = 'skill_view' if tool == 'skill_view' else prefix + tool
                match['loader'] = {}
                contract(match['loader'], full)
        payload['hint'] = ('Groups index matches. Follow invocation with the included parameters; '
            'use next only after supplying needs_arguments. A schema_status=deferred result needs '
            'the exact tool_describe lookup, not another search; in_tool_list means the full schema '
            'is already declared in your tools. Methods are optional documents, '
            'not required steps. not_in_agent_scope means the catalog found a capability this agent '
            'cannot invoke. No tools were enabled or executed by discovery.')
    else:
        for name, record in list(payload['tools'].items())[:50]:
            # Remote connector schemas have their own scoped resolver.
            if name in catalog and isinstance(record, dict):
                contract(record, name)
        payload['note'] = ('Use included parameters and invocation directly. Use tool_describe '
                           'for schemas marked deferred or remote connector entries without '
                           'parameters. Known names do not need another search.')
    payload['session_catalog'] = {'resolved': True, 'schemas_included': schemas,
                                  'schema_budget_chars': MAX_ADDED_CHARS,
                                  'tool_list_changed': False}
    return encode(payload)


def enrich_for_agent(agent, source, result):
    if source != 'tool_search' and not (source.startswith('mcp__') and source.endswith('__capability_search')):
        return result
    try:
        import model_tools
        from agent.tool_executor import _tool_search_scoped_names
        definitions = model_tools.get_tool_definitions(
            enabled_toolsets=getattr(agent, 'enabled_toolsets', None),
            disabled_toolsets=getattr(agent, 'disabled_toolsets', None),
            quiet_mode=True, skip_tool_search_assembly=True) or []
        direct = set(getattr(agent, 'valid_tool_names', None) or [])
        deferred = _tool_search_scoped_names(agent) if 'tool_call' in direct else set()
        return enrich(result, source=source, definitions=definitions,
                      direct_names=direct, deferred_names=deferred)
    except Exception:
        # Discovery still works if a rolling upgrade cannot resolve a registry.
        # Do not alter ordinary tool results or log credential-bearing payloads.
        logging.getLogger(__name__).debug('Discovery contract enrichment unavailable', exc_info=True)
        return result
