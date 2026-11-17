"""Stage items as A2UI documents for native clients (docs/STAGE_A2UI_PLAN.md).

The surface payload stays the model. A versioned template per kind projects it
to A2UI messages plus the actions a host may run, so clients render and submit
without knowing the kind. A client declares its catalog and components; an
item it cannot render keeps the hand-off it already shows. Projected kinds:
open input requests (answerable cards), query results (`CResultTable`),
Vega-Lite charts (`CVegaChart`, data resolved here, theme left to renderers)
find results (a ranked list composed from CText/CStack/CSurface), answers and
specialist threads (CMarkdown).
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

TEMPLATE_VERSION = 1
# Same catalog as published apps (ui_apps.CATALOG); kept literal to avoid
# importing the app runtime into this projection.
CATALOG = 'https://calliope.local/a2ui/workspace/v1'
INPUT_COMPONENTS = frozenset({'Column', 'Row', 'CText', 'Text', 'TextField', 'ChoicePicker', 'CheckBox', 'Button'})
QUERY_COMPONENTS = frozenset({'CResultTable'})
CHART_COMPONENTS = frozenset({'CVegaChart'})
FIND_COMPONENTS = frozenset({'CText', 'CStack', 'CSurface'})
FIND_LEAD = 5
TEXT_COMPONENTS = frozenset({'CText', 'CStack', 'CSurface', 'CMarkdown'})
SPECIALIST_MODES = ('specialist_thread', 'specialist_run')
THREAD_MESSAGES = 3
RECEIPT_COMPONENTS = frozenset({'CText', 'CStack', 'CSurface', 'CResultTable', 'Button', 'Text', 'Row'})
SQL_EXECUTORS = ('local_sql', 'workspace_sql')
METRIC_COMPONENTS = frozenset({'CStack', 'CText', 'CMetric'})
DECISION_COMPONENTS = frozenset({'CStack', 'CText', 'CSurface', 'Button', 'Text', 'Row'})
RECEIPT_STATEMENTS = 5
CHART_HEIGHT = 300
MAX_FIELDS = 12
# Stored results are usually small (pilot median 3 rows, p99 149, cap 1,000;
# up to 10,000 with an explicit limit). Phones get a bounded slice.
MAX_ROWS = 2000
MAX_ROW_BYTES = 1_000_000
MAX_CELL_CHARS = 10_000
MAX_COLUMNS = 256
VISIBLE_ROWS = 12


def supports(client: Any, needed: frozenset[str]) -> bool:
    client = client if isinstance(client, dict) else {}
    components = client.get('components')
    return (client.get('catalog_id') == CATALOG and isinstance(components, list)
            and needed <= {str(c) for c in components})


def wants_chart_data(surface: dict[str, Any], client: Any) -> bool:
    """Live SQL charts need their datasets resolved (viewer-scoped, cached) first."""
    payload = surface.get('payload') if isinstance(surface.get('payload'), dict) else {}
    return surface.get('kind') == 'chart' and bool(payload.get('queries')) and supports(client, CHART_COMPONENTS)


def project(surface: dict[str, Any], client: Any, *, can_answer: bool = True,
            chart_data: Any = None) -> dict[str, Any]:
    surface_id = str(surface.get('id') or '')
    base = {'surface_id': surface_id, 'kind': surface.get('kind'), 'template_version': TEMPLATE_VERSION}
    payload = surface.get('payload') if isinstance(surface.get('payload'), dict) else {}
    if surface.get('kind') == 'query':
        if not supports(client, QUERY_COMPONENTS):
            return {**base, 'fallback': 'unsupported_client'}
        try:
            return {**base, **_query_view(surface_id, payload)}
        except ValueError as exc:
            return {**base, 'fallback': 'unprojectable', 'reason': str(exc)}
    if surface.get('kind') == 'answer' or (
            surface.get('kind') == 'action' and payload.get('mode') in SPECIALIST_MODES and not payload.get('transient')):
        if not supports(client, TEXT_COMPONENTS):
            return {**base, 'fallback': 'unsupported_client'}
        view = _answer_view if surface.get('kind') == 'answer' else _specialist_view
        try:
            return {**base, **view(surface_id, payload)}
        except ValueError as exc:
            return {**base, 'fallback': 'unprojectable', 'reason': str(exc)}
    if surface.get('kind') == 'action' and isinstance(payload.get('run'), dict) and (
            payload.get('mode') == 'action_receipt'
            or (payload['run'].get('action_snapshot') or {}).get('executor') in SQL_EXECUTORS):
        if not supports(client, RECEIPT_COMPONENTS):
            return {**base, 'fallback': 'unsupported_client'}
        try:
            return {**base, **_receipt_view(surface_id, payload, can_answer=can_answer)}
        except ValueError as exc:
            return {**base, 'fallback': 'unprojectable', 'reason': str(exc)}
    if surface.get('kind') == 'decision':
        if not supports(client, DECISION_COMPONENTS):
            return {**base, 'fallback': 'unsupported_client'}
        try:
            return {**base, **_decision_view(surface_id, payload, can_answer=can_answer)}
        except ValueError as exc:
            return {**base, 'fallback': 'unprojectable', 'reason': str(exc)}
    if surface.get('kind') == 'metric':
        if not supports(client, METRIC_COMPONENTS):
            return {**base, 'fallback': 'unsupported_client'}
        try:
            return {**base, **_metric_view(surface_id, str(surface.get('title') or ''), payload)}
        except ValueError as exc:
            return {**base, 'fallback': 'unprojectable', 'reason': str(exc)}
    if surface.get('kind') == 'find':
        if not supports(client, FIND_COMPONENTS):
            return {**base, 'fallback': 'unsupported_client'}
        try:
            return {**base, **_find_view(surface_id, payload)}
        except ValueError as exc:
            return {**base, 'fallback': 'unprojectable', 'reason': str(exc)}
    if surface.get('kind') == 'chart':
        if not supports(client, CHART_COMPONENTS):
            return {**base, 'fallback': 'unsupported_client'}
        try:
            return {**base, **_chart_view(surface_id, str(surface.get('title') or ''), payload, chart_data)}
        except ValueError as exc:
            return {**base, 'fallback': 'unprojectable', 'reason': str(exc)[:300]}
    state = payload.get('request_state') if isinstance(payload.get('request_state'), dict) else {}
    if surface.get('kind') != 'input' or state.get('status') != 'open':
        return {**base, 'fallback': 'not_projected'}
    if not can_answer:
        return {**base, 'fallback': 'read_only'}
    if not supports(client, INPUT_COMPONENTS):
        return {**base, 'fallback': 'unsupported_client'}
    try:
        return {**base, **_input_view(surface_id, payload, state)}
    except ValueError as exc:
        return {**base, 'fallback': 'unprojectable', 'reason': str(exc)}


def _text(value: Any, limit: int) -> str:
    return str(value or '').strip()[:limit]


def _options(field: dict[str, Any]) -> list[dict[str, str]]:
    options = []
    for option in field.get('options') or []:
        value = option.get('value') if isinstance(option, dict) else option
        label = option.get('label') if isinstance(option, dict) else option
        if isinstance(value, str) and value and len(options) < 100:
            options.append({'label': _text(label or value, 200), 'value': value})
    return options


def _input_view(surface_id: str, payload: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    try:
        request_id = str(uuid.UUID(str(state.get('id') or '')))
    except ValueError:
        raise ValueError('The question has no request id') from None
    revision = state.get('revision')
    if type(revision) is not int or revision < 1:
        raise ValueError('The question has no revision')
    fields = [f for f in payload.get('fields') or [] if isinstance(f, dict) and f.get('key')][:MAX_FIELDS]
    if not fields:
        raise ValueError('The question has no fields')
    prior = state.get('answers') if isinstance(state.get('answers'), dict) else {}
    components: list[dict[str, Any]] = []
    children: list[str] = []

    def add(component: dict[str, Any]) -> str:
        components.append(component)
        return component['id']

    children.append(add({'id': 'title', 'component': 'CText', 'size': 'title',
                         'text': _text(payload.get('title'), 200) or 'Callie has a question'}))
    prompt = _text(payload.get('prompt'), 2000)
    if prompt:
        children.append(add({'id': 'prompt', 'component': 'CText', 'size': 'body', 'tone': 'muted', 'text': prompt}))
    answers: dict[str, Any] = {}
    paths: dict[str, str] = {}
    for index, field in enumerate(fields):
        key, slot = str(field['key']), f'f{index}'
        path = f'/answers/{slot}'
        paths[key] = path
        label = _text(field.get('label') or key, 200) + (' *' if field.get('required') else '')
        kind = str(field.get('type') or 'text')
        initial = prior.get(key, field.get('default'))
        if kind in ('choice', 'select'):
            options = _options(field)
            if not options:
                raise ValueError(f'{label} has no options')
            offered = {option['value'] for option in options}
            answers[slot] = [initial] if initial in offered else []
            children.append(add({'id': f'field-{index}', 'component': 'ChoicePicker', 'label': label,
                                 'options': options, 'value': {'path': path},
                                 'variant': 'mutuallyExclusive'}))
        elif kind == 'boolean':
            answers[slot] = initial is True
            children.append(add({'id': f'field-{index}', 'component': 'CheckBox', 'label': label,
                                 'value': {'path': path}}))
        else:
            answers[slot] = initial if isinstance(initial, str) else ''
            children.append(add({'id': f'field-{index}', 'component': 'TextField', 'label': label,
                                 'value': {'path': path},
                                 'variant': 'longText' if kind == 'textarea' or field.get('multiline') else 'shortText'}))
    add({'id': 'submit-label', 'component': 'Text', 'text': _text(payload.get('submit_label'), 60) or 'Send answer'})
    add({'id': 'skip-label', 'component': 'Text', 'text': 'Skip'})
    add({'id': 'submit', 'component': 'Button', 'child': 'submit-label', 'variant': 'primary',
         'action': {'event': {'name': 'stage.input.answer'}}})
    add({'id': 'skip', 'component': 'Button', 'child': 'skip-label', 'variant': 'borderless',
         'action': {'event': {'name': 'stage.input.cancel'}}})
    children.append(add({'id': 'actions', 'component': 'Row', 'children': ['submit', 'skip']}))
    components.insert(0, {'id': 'root', 'component': 'Column', 'children': children})
    surface = f'stage-{surface_id}'
    endpoint = f'/api/calliope/input-requests/{request_id}'
    return {
        'messages': [
            {'version': 'v0.9', 'createSurface': {'surfaceId': surface, 'catalogId': CATALOG}},
            {'version': 'v0.9', 'updateComponents': {'surfaceId': surface, 'components': components}},
            {'version': 'v0.9', 'updateDataModel': {'surfaceId': surface, 'path': '/', 'value': {'answers': answers}}},
        ],
        # The host runs these verbatim: a fixed body plus values read from the
        # data model. It needs no knowledge of input requests.
        'actions': {
            'stage.input.answer': {'method': 'POST', 'path': f'{endpoint}/answer',
                                   'body': {'revision': revision}, 'fields': {'answers': paths},
                                   'done': 'Answer sent. Callie is continuing.'},
            'stage.input.cancel': {'method': 'POST', 'path': f'{endpoint}/cancel',
                                   'body': {'revision': revision},
                                   'done': 'Question skipped. Callie will continue without it.'},
        },
        'height_hint': 150 + 95 * len(fields) + (40 if prompt else 0),
    }


_INTEGER_TYPES = {'int2', 'int4', 'int8', 'integer', 'smallint', 'bigint', 'int', 'long'}
_DECIMAL_TYPES = {'float4', 'float8', 'numeric', 'decimal', 'number', 'real', 'double', 'double precision', 'money'}
_TIME_TYPES = {'date', 'time', 'timetz', 'timestamp', 'timestamptz', 'datetime', 'interval', '1186'}
_TEXT_TYPES = {'text', 'varchar', 'char', 'bpchar', 'name', 'string', 'uuid', 'citext'}
# Numbers that identify rather than measure: never grouped ("2,019", "10,452").
_PLAIN_NAME = re.compile(r'(^|_)(id|year|yr|fy|zip|zipcode|postal|code|phone|number|no|num)$', re.I)


def _column_format(name: str, column_type: str, values: list[Any]) -> str:
    kind = column_type.strip().lower()
    if kind in ('bool', 'boolean'):
        return 'boolean'
    if kind in ('json', 'jsonb'):
        return 'json'
    if kind in _TIME_TYPES:
        return 'time'
    sample = [v for v in values if v is not None][:200]
    numbers = bool(sample) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in sample)
    if kind not in _INTEGER_TYPES | _DECIMAL_TYPES:
        if kind in _TEXT_TYPES or not numbers:
            return 'json' if sample and all(isinstance(v, (dict, list)) for v in sample) else 'text'
    if _PLAIN_NAME.search(name):
        return 'plain'
    if kind in _INTEGER_TYPES or (numbers and all(isinstance(v, int) for v in sample)):
        return 'integer'
    return 'decimal'


def _cell(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
        return value[:MAX_CELL_CHARS] + '…'
    return value


def _result_table(component_id: str, payload: dict[str, Any], path: str,
                  visible: int = VISIBLE_ROWS) -> tuple[dict[str, Any], list[list[Any]]]:
    """A stored result (`columns` + `rows`) as a CResultTable bound to `path`."""
    rows = payload.get('rows')
    if not isinstance(rows, list):
        raise ValueError('The result has no rows')
    columns: list[tuple[str, str]] = []
    for index, column in enumerate(payload.get('columns') if isinstance(payload.get('columns'), list) else []):
        name = column if isinstance(column, str) else column.get('name') if isinstance(column, dict) else None
        kind = column.get('type') if isinstance(column, dict) else ''
        columns.append((str(name or f'Column {index + 1}')[:200], str(kind or '')[:40]))
    if not columns:
        for row in rows:
            if isinstance(row, dict):
                columns += [(str(k)[:200], '') for k in row if (str(k)[:200], '') not in columns]
            elif isinstance(row, list):
                columns += [(f'Column {i + 1}', '') for i in range(len(columns), len(row))]
    columns = columns[:MAX_COLUMNS]
    if not columns:
        raise ValueError('The result has no columns')
    matrix, size = [], 0
    for row in rows[:MAX_ROWS]:
        if isinstance(row, dict):
            cells = [_cell(row.get(name)) for name, _ in columns]
        elif isinstance(row, list):
            cells = [_cell(row[i]) if i < len(row) else None for i in range(len(columns))]
        else:
            cells = [_cell(row)] + [None] * (len(columns) - 1)
        size += len(json.dumps(cells, default=str))
        if matrix and size > MAX_ROW_BYTES:
            break
        matrix.append(cells)
    table = {
        'id': component_id, 'component': 'CResultTable',
        'columns': [{'name': name, 'type': kind or 'text',
                     'format': _column_format(name, kind, [r[i] for r in matrix])}
                    for i, (name, kind) in enumerate(columns)],
        'rows': {'path': path},
        'rowCount': max(len(rows), payload.get('row_count') if type(payload.get('row_count')) is int else 0),
        'truncated': payload.get('truncated') is True,
        'visibleRows': visible,
    }
    return table, json.loads(json.dumps(matrix, default=str))


def _query_view(surface_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    table, matrix = _result_table('root', payload, '/rows')
    surface = f'stage-{surface_id}'
    shown = max(1, min(len(matrix), VISIBLE_ROWS))
    return {
        'messages': [
            {'version': 'v0.9', 'createSurface': {'surfaceId': surface, 'catalogId': CATALOG}},
            {'version': 'v0.9', 'updateComponents': {'surfaceId': surface, 'components': [table]}},
            {'version': 'v0.9', 'updateDataModel': {'surfaceId': surface, 'path': '/', 'value': {'rows': matrix}}},
        ],
        'actions': {},
        'height_hint': 48 + 30 + 30 * shown + 28,
    }


def without_repeated_title(spec: dict[str, Any], title: str) -> dict[str, Any]:
    """The card shows the item's title; a chart title repeating it is dropped.

    A subtitle stays: Vega drops the whole title group when its text is empty,
    so the text becomes a one-pixel blank. Mirrors the web Stage's mount.
    """
    own = spec.get('title')
    text = own.get('text') if isinstance(own, dict) else own
    def words(value: Any) -> str:
        value = ' '.join(map(str, value)) if isinstance(value, list) else str(value or '')
        return ' '.join(value.split()).lower()
    if not title or not words(text) or words(text) != words(title):
        return spec
    subtitle = own.get('subtitle') if isinstance(own, dict) else None
    spec = dict(spec)
    if subtitle:
        spec['title'] = {**own, 'text': ' ', 'fontSize': 1, 'subtitlePadding': 0}
    else:
        spec.pop('title')
    return spec


def _chart_view(surface_id: str, title: str, payload: dict[str, Any], chart_data: Any) -> dict[str, Any]:
    import ui_charts
    spec = payload.get('spec')
    if not isinstance(spec, dict) or payload.get('format', 'vega-lite') != 'vega-lite':
        raise ValueError('The chart has no Vega-Lite spec')
    # Same serialization as the web Stage's chart-data response (Decimals as text).
    spec = json.loads(json.dumps(spec, default=str))
    if payload.get('queries'):
        if not isinstance(chart_data, dict) or not isinstance(chart_data.get('datasets'), dict):
            reason = chart_data.get('error') if isinstance(chart_data, dict) else None
            raise ValueError(str(reason or 'The chart data is unavailable'))
        datasets = json.loads(json.dumps(chart_data['datasets'], default=str))
        spec['datasets'] = {**(spec.get('datasets') if isinstance(spec.get('datasets'), dict) else {}), **datasets}
    spec = without_repeated_title(spec, title)
    ui_charts.validate(spec)
    chart = {'id': 'root', 'component': 'CVegaChart', 'spec': {'path': '/spec'}, 'height': CHART_HEIGHT,
             'description': (title or 'Chart')[:200]}
    surface = f'stage-{surface_id}'
    return {
        'messages': [
            {'version': 'v0.9', 'createSurface': {'surfaceId': surface, 'catalogId': CATALOG}},
            {'version': 'v0.9', 'updateComponents': {'surfaceId': surface, 'components': [chart]}},
            {'version': 'v0.9', 'updateDataModel': {'surfaceId': surface, 'path': '/', 'value': {'spec': spec}}},
        ],
        'actions': {},
        'height_hint': CHART_HEIGHT + 48,
    }


def _count(value: Any) -> str | None:
    try:
        return f'{int(value):,}'
    except (TypeError, ValueError):
        return None


def _find_view(surface_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Matches as the web Stage ranks them: what was used, else the top five."""
    hits = [h for h in payload.get('hits') or [] if isinstance(h, dict) and h.get('object')]
    used = [h for h in hits if h.get('used')]
    lead = used or hits[:FIND_LEAD]
    folded = len(hits) - len(lead)
    components: list[dict[str, Any]] = []
    children: list[str] = []

    def add(component: dict[str, Any]) -> str:
        components.append(component)
        return component['id']

    summary = [f'{len(hits)} match' + ('' if len(hits) == 1 else 'es')]
    if used:
        summary.append(f'{len(used)} used')
    if payload.get('confidence'):
        summary.append(f"{_text(payload['confidence'], 20)} confidence")
    summary += [label for flag, label in (('rerank', 'reranked'), ('judge', 'judged')) if payload.get(flag)]
    query = _text(payload.get('query'), 200)
    children.append(add({'id': 'summary', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                         'text': (f'Searched for “{query}” · ' if query else '') + ' · '.join(summary)}))
    degraded = [str(d) for d in payload.get('degraded') or [] if d]
    if degraded:
        children.append(add({'id': 'degraded', 'component': 'CText', 'size': 'caption', 'tone': 'warm',
                             'text': _text('Partial search — ' + '; '.join(degraded), 300)}))
    for index, hit in enumerate(lead):
        kind = {'db_column': 'column', 'cube': 'cube'}.get(str(hit.get('kind')), 'table')
        facts = [_text(hit.get('adapter') or hit.get('role'), 40) or None,
                 f"{_count(hit['rows_est'])} rows" if _count(hit.get('rows_est')) else None,
                 'used in this answer' if hit.get('used') else None]
        if isinstance(hit.get('answers_ask'), (int, float)):
            facts.append(f"{round(hit['answers_ask'] * 100)}% likely to answer")
        lines = [add({'id': f'hit-{index}-name', 'component': 'CText', 'size': 'body', 'family': 'mono',
                      'text': _text(hit['object'], 200)}),
                 add({'id': f'hit-{index}-facts', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                      'text': ' · '.join([kind] + [f for f in facts if f])})]
        why = [str(w) for w in hit.get('why') or [] if w]
        if why:
            lines.append(add({'id': f'hit-{index}-why', 'component': 'CText', 'size': 'caption',
                              'text': _text('Matched on ' + ', '.join(why), 200)}))
        values = [v for v in hit.get('matched_values') or [] if isinstance(v, dict) and v.get('value') is not None][:6]
        if values:
            shown = ', '.join(f"{v['value']}" + (f" ×{_count(v['n'])}" if _count(v.get('n')) else '') for v in values)
            lines.append(add({'id': f'hit-{index}-values', 'component': 'CText', 'size': 'caption', 'family': 'mono',
                              'text': _text('Values: ' + shown, 300)}))
        stack = add({'id': f'hit-{index}-lines', 'component': 'CStack', 'direction': 'column', 'gap': 'xs',
                     'children': lines})
        children.append(add({'id': f'hit-{index}', 'component': 'CSurface', 'treatment': 'outlined',
                             'padding': 'sm', 'tone': 'accent' if hit.get('used') else 'neutral', 'child': stack}))
    if folded > 0:
        children.append(add({'id': 'more', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                             'text': f'Also considered {folded} more — open the Stage on the web for all of them.'}))
    if not lead:
        children.append(add({'id': 'empty', 'component': 'CText', 'size': 'body', 'tone': 'muted',
                             'text': 'Nothing matched.'}))
    components.insert(0, {'id': 'root', 'component': 'CStack', 'direction': 'column', 'gap': 'sm',
                          'children': children})
    surface = f'stage-{surface_id}'
    return {
        'messages': [
            {'version': 'v0.9', 'createSurface': {'surfaceId': surface, 'catalogId': CATALOG}},
            {'version': 'v0.9', 'updateComponents': {'surfaceId': surface, 'components': components}},
        ],
        'actions': {},
        'height_hint': 60 + 110 * len(lead),
    }


def _document(surface_id: str, components: list[dict[str, Any]], height: int) -> dict[str, Any]:
    surface = f'stage-{surface_id}'
    return {
        'messages': [
            {'version': 'v0.9', 'createSurface': {'surfaceId': surface, 'catalogId': CATALOG}},
            {'version': 'v0.9', 'updateComponents': {'surfaceId': surface, 'components': components}},
        ],
        'actions': {},
        'height_hint': height,
    }


def _answer_view(surface_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    answer = str(payload.get('answer') or '').strip()
    if not answer:
        raise ValueError('The answer is empty')
    components = [{'id': 'answer', 'component': 'CMarkdown', 'text': answer[:20_000]}]
    caveat = _text(payload.get('caveat'), 2000)
    if caveat:
        components.append({'id': 'caveat', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                           'text': f'Caveat · {caveat}'})
    refs = payload.get('evidence_refs') if isinstance(payload.get('evidence_refs'), list) else []
    if refs:
        components.append({'id': 'evidence', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                           'text': f"Backed by {len(refs)} Stage item{'' if len(refs) == 1 else 's'}"})
    root = {'id': 'root', 'component': 'CStack', 'direction': 'column', 'gap': 'sm',
            'children': [c['id'] for c in components]}
    return _document(surface_id, [root] + components, 80 + min(len(answer), 2000) // 2)


def _elapsed(ms: Any) -> str | None:
    try:
        seconds = int(ms) // 1000
    except (TypeError, ValueError):
        return None
    return f'{seconds // 60}m {seconds % 60}s' if seconds >= 60 else f'{seconds}s'


def _specialist_view(surface_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """A specialist's working thread: who, how far, and the latest exchange."""
    specialist = payload.get('specialist') if isinstance(payload.get('specialist'), dict) else {}
    name = _text(specialist.get('name') or specialist.get('key'), 60) or 'Specialist'
    status = {'complete': 'finished', 'completed': 'finished', 'running': 'working', 'queued': 'queued',
              'failed': 'stopped with an error', 'cancelled': 'cancelled'}.get(
        str(payload.get('status') or payload.get('thread_status') or ''), str(payload.get('status') or 'working'))
    rounds = payload.get('round_count') if type(payload.get('round_count')) is int else None
    head = [name, status] + ([f"{rounds} round{'' if rounds == 1 else 's'}"] if rounds else [])
    if _elapsed(payload.get('elapsed_ms')):
        head.append(_elapsed(payload['elapsed_ms']))
    components: list[dict[str, Any]] = [
        {'id': 'head', 'component': 'CText', 'size': 'caption', 'tone': 'accent', 'text': ' · '.join(head)}]
    objective = _text(payload.get('objective'), 600)
    if objective:
        components.append({'id': 'objective', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                           'text': 'Assignment · ' + objective + ('…' if len(str(payload.get('objective'))) > 600 else '')})
    words = lambda text: ' '.join(str(text or '').split())
    # The handoff repeats the assignment shown above it.
    transcript = [m for m in payload.get('transcript') or []
                  if isinstance(m, dict) and str(m.get('text') or '').strip()
                  and not (objective and (m.get('kind') == 'handoff'
                                          or words(m.get('text')) == words(payload.get('objective'))))]
    shown = transcript[-THREAD_MESSAGES:]
    for index, message in enumerate(shown):
        speaker = name if message.get('role') == 'specialist' else 'Callie'
        round_label = f" · round {message['round']}" if type(message.get('round')) is int else ''
        text = str(message['text']).strip()
        label = {'id': f'msg-{index}-who', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                 'text': speaker + round_label}
        body = {'id': f'msg-{index}-text', 'component': 'CMarkdown',
                'text': text[:4000] + ('…' if len(text) > 4000 else '')}
        stack = {'id': f'msg-{index}-lines', 'component': 'CStack', 'direction': 'column', 'gap': 'xs',
                 'children': [label['id'], body['id']]}
        components += [label, body, stack, {'id': f'msg-{index}', 'component': 'CSurface', 'treatment': 'outlined',
                                            'padding': 'sm', 'child': stack['id']}]
    if not shown:
        components.append({'id': 'waiting', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                           'text': 'Waiting for the first working note…'})
    tools = payload.get('tool_activity') if isinstance(payload.get('tool_activity'), list) else []
    outputs = payload.get('stage_surfaces') if isinstance(payload.get('stage_surfaces'), list) else []
    facts = []
    if len(transcript) > len(shown):
        facts.append(f'{len(transcript) - len(shown)} earlier messages')
    if tools:
        facts.append(f"{len(tools)} tool step{'' if len(tools) == 1 else 's'}")
    if outputs:
        facts.append(f"{len(outputs)} Stage output{'' if len(outputs) == 1 else 's'}")
    if facts:
        components.append({'id': 'facts', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                           'text': ' · '.join(facts)})
    top = [c['id'] for c in components if c['id'] in ('head', 'objective', 'waiting', 'facts')
           or (c['id'].startswith('msg-') and c['id'].count('-') == 1)]
    order = ['head', 'objective'] + [f'msg-{i}' for i in range(len(shown))] + ['waiting', 'facts']
    root = {'id': 'root', 'component': 'CStack', 'direction': 'column', 'gap': 'sm',
            'children': [i for i in order if i in top]}
    return _document(surface_id, [root] + components, 140 + 180 * len(shown))


def _sign_in_url(payload: dict[str, Any], run: dict[str, Any]) -> str | None:
    """An OAuth connection's authorization URL, as the web receipt offers it."""
    candidate = (payload.get('authorization') or {}).get('authorization_url') if isinstance(payload.get('authorization'), dict) else None
    for step in run.get('steps') or []:
        if not candidate and isinstance(step, dict) and step.get('id') == 'authorize':
            candidate = (step.get('result') or {}).get('authorization_url') if isinstance(step.get('result'), dict) else None
    candidate = str(candidate or '')
    return candidate if candidate.startswith('https://') and len(candidate) <= 4096 and ' ' not in candidate else None


def _receipt_view(surface_id: str, payload: dict[str, Any], *, can_answer: bool) -> dict[str, Any]:
    """An action receipt: what ran, how it ended, and its results.

    Local SQL receipts awaiting review carry the frozen statement and an
    approval recipe bound to its SHA-256, as the web Stage's Approve button.
    """
    run = payload['run']
    action = payload.get('action') if isinstance(payload.get('action'), dict) else run.get('action_snapshot') or {}
    executor = action.get('executor')
    status = str(run.get('status') or 'planned')
    values = run.get('input_redacted') if isinstance(run.get('input_redacted'), dict) else {}
    sql = str(values.get('sql') or '').strip()
    sha = str(values.get('sql_sha256') or '').strip().lower()
    review = ((run.get('verification') or {}).get('local_sql_review') or {}) if isinstance(run.get('verification'), dict) else {}
    result = run.get('result') if isinstance(run.get('result'), dict) else {}
    components: list[dict[str, Any]] = []
    data: dict[str, Any] = {}
    actions: dict[str, Any] = {}

    def add(component: dict[str, Any]) -> str:
        components.append(component)
        return component['id']

    children: list[str] = []
    sql_receipt = executor in SQL_EXECUTORS and action.get('receipt_only') and sql and re.fullmatch(r'[0-9a-f]{64}', sha)
    if not sql_receipt:
        summary = _text(run.get('error') or payload.get('message') or action.get('summary')
                        or (run.get('plan') or {}).get('summary'), 600)
        facts = [{'complete': 'Complete', 'failed': 'Failed', 'running': 'Running'}.get(status, status.capitalize()),
                 str(action.get('risk') or '').replace('_', ' ') or None]
        children.append(add({'id': 'status', 'component': 'CText', 'size': 'caption',
                             'tone': 'warm' if status == 'failed' else 'accent', 'text': ' · '.join(f for f in facts if f)}))
        if summary:
            children.append(add({'id': 'summary', 'component': 'CText', 'size': 'body', 'text': summary}))
        sign_in = _sign_in_url(payload, run)
        if sign_in and status not in ('complete', 'failed', 'cancelled') and can_answer:
            add({'id': 'sign-in-label', 'component': 'Text', 'text': 'Finish sign-in ↗'})
            add({'id': 'sign-in', 'component': 'Button', 'child': 'sign-in-label', 'variant': 'primary',
                 'action': {'event': {'name': 'stage.connection.sign_in'}}})
            children.append(add({'id': 'actions', 'component': 'Row', 'children': ['sign-in']}))
            actions['stage.connection.sign_in'] = {'method': 'OPEN', 'url': sign_in}
        components.insert(0, {'id': 'root', 'component': 'CStack', 'direction': 'column', 'gap': 'sm',
                               'children': children})
        document = _document(surface_id, components, 90)
        document['actions'] = actions
        return document
    workspace = executor == 'workspace_sql'
    retired = review.get('state') == 'retired'
    pending = (not workspace and status == 'planned' and review.get('state') not in ('retired', 'unavailable'))
    label = 'Calliope workspace' if workspace else 'Local SQL'
    state = ('Retired' if retired else 'Waiting for your approval' if pending
             else {'complete': 'Complete', 'failed': 'Failed', 'running': 'Running'}.get(status, status.capitalize()))
    head = [label, state, str(values.get('statement_type') or '').upper() or None]
    if isinstance(result.get('elapsed_ms'), (int, float)):
        head.append(f"{int(result['elapsed_ms']):,} ms")
    children.append(add({'id': 'status', 'component': 'CText', 'size': 'caption',
                         'tone': 'warm' if status == 'failed' or pending else 'accent',
                         'text': ' · '.join(h for h in head if h)}))
    if run.get('error'):
        children.append(add({'id': 'error', 'component': 'CText', 'size': 'body', 'tone': 'warm',
                             'text': _text(run['error'], 1200)}))
    if review.get('message'):
        children.append(add({'id': 'review', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                             'text': _text(review['message'], 600)}))
    statements = result.get('statements') if isinstance(result.get('statements'), list) else ([result] if result else [])
    tables = 0
    for index, statement in enumerate(statements[:RECEIPT_STATEMENTS]):
        if not isinstance(statement, dict):
            continue
        if isinstance(statement.get('columns'), list) and statement['columns'] and isinstance(statement.get('rows'), list):
            table, matrix = _result_table(f'result-{index}', statement, f'/results/r{index}', visible=8)
            data[f'r{index}'] = matrix
            children.append(add(table))
            tables += 1
        else:
            line = [f"Statement {statement.get('statement') or index + 1}" if workspace else None,
                    _text(statement.get('status') or 'Executed', 80)]
            if isinstance(statement.get('rows_affected'), int):
                line.append(f"{statement['rows_affected']:,} row{'' if statement['rows_affected'] == 1 else 's'} affected")
            children.append(add({'id': f'result-{index}', 'component': 'CText', 'size': 'caption',
                                 'text': ' · '.join(x for x in line if x)}))
    if len(statements) > RECEIPT_STATEMENTS:
        children.append(add({'id': 'more-results', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                             'text': f'{len(statements) - RECEIPT_STATEMENTS} more statements in the full receipt.'}))
    if pending or not tables:
        shown = sql if len(sql) <= 1500 else sql[:1500] + '…'
        code = add({'id': 'sql-text', 'component': 'CText', 'size': 'caption', 'family': 'mono', 'text': shown})
        children.append(add({'id': 'sql', 'component': 'CSurface', 'treatment': 'inset', 'padding': 'sm', 'child': code}))
    children.append(add({'id': 'sha', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                         'text': f'SHA-256 {sha[:16]}…'}))
    if pending and can_answer:
        try:
            run_id = str(uuid.UUID(str(run.get('id') or '')))
        except ValueError:
            raise ValueError('The receipt has no run id') from None
        add({'id': 'approve-label', 'component': 'Text', 'text': 'Approve and run'})
        add({'id': 'approve', 'component': 'Button', 'child': 'approve-label', 'variant': 'primary',
             'action': {'event': {'name': 'stage.sql.approve'}}})
        children.append(add({'id': 'actions', 'component': 'Row', 'children': ['approve']}))
        actions['stage.sql.approve'] = {
            'method': 'POST', 'path': f'/api/calliope/action-runs/{run_id}/approve-local-sql',
            'body': {'expected_sql_sha256': sha}, 'fields': {}, 'follow': False,
            'done': 'Approved. The SQL ran; its receipt is updated.'}
    components.insert(0, {'id': 'root', 'component': 'CStack', 'direction': 'column', 'gap': 'sm', 'children': children})
    document = _document(surface_id, components, 120 + 300 * tables + (140 if pending or not tables else 0))
    if data:
        surface = f'stage-{surface_id}'
        document['messages'].append({'version': 'v0.9', 'updateDataModel': {
            'surfaceId': surface, 'path': '/', 'value': {'results': data}}})
    document['actions'] = actions
    return document


_METRIC_KEYS = ('value', 'result', 'total', 'count', 'amount', 'revenue', 'rate', 'percentage', 'percent', 'score')


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value and abs(value) != float('inf') else None
    if isinstance(value, str) and value.strip():
        try:
            number = float(value)
        except ValueError:
            return None
        return number if number == number and abs(number) != float('inf') else None
    return None


def metric_number(value: Any) -> float | None:
    """The web's metricDatum: a number, a one-item list, or a named field."""
    if _number(value) is not None:
        return _number(value)
    if isinstance(value, list):
        return metric_number(value[0]) if len(value) == 1 else None
    if not isinstance(value, dict):
        return None
    lowered = {str(k).lower(): v for k, v in value.items()}
    for key in _METRIC_KEYS:
        if key in lowered and _number(lowered[key]) is not None:
            return _number(lowered[key])
    numbers = [_number(v) for v in value.values() if _number(v) is not None]
    return numbers[0] if len(numbers) == 1 else None


def _plain(number: float, decimals: int) -> str:
    text = f'{number:,.{decimals}f}'
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _compact(number: float, decimals: int) -> str:
    for size, suffix in ((1e12, 'T'), (1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        if abs(number) >= size:
            scaled = number / size
            return _plain(scaled, min(decimals, 2 if abs(scaled) < 100 else 0)) + suffix
    return _plain(number, decimals)


def format_metric(number: float, display: Any = None) -> str:
    """theme/metric-cards.js formatValue, for en-US: the Stage, Floor and Gallery format."""
    display = display if isinstance(display, dict) else {}
    fmt = str(display.get('format') or '').strip().lower()
    unit = str(display.get('unit') or '').strip().lower()
    decimals = 0 if fmt in ('int', 'integer', 'count') else (
        max(0, min(int(display['decimals']), 8)) if isinstance(display.get('decimals'), int) else 2)
    if fmt == 'currency' or (not fmt and (display.get('currency') or unit == 'usd')):
        sign = '-' if number < 0 else ''
        body = _compact(abs(number), decimals) if abs(number) >= 10000 else f'{abs(number):,.{decimals}f}'
        rendered = f'{sign}$' + body if (display.get('currency') or 'USD') == 'USD' else body + f" {display['currency']}"
    elif fmt in ('percent', 'percentage', 'pct', 'rate', '%') or (not fmt and unit in ('percent', 'percentage', 'pct', '%')):
        share = number if abs(number) <= 1.25 else number / 100
        rendered = _plain(share * 100, decimals) + '%'
    else:
        rendered = _compact(number, decimals) if abs(number) >= 100000 else _plain(number, decimals)
    for key in ('prefix', 'suffix', 'unit'):
        if str(display.get(key) or '').strip().lower() in ('count', 'counts'):
            display = {**display, key: None}
    if display.get('prefix') and not rendered.startswith(str(display['prefix'])):
        rendered = str(display['prefix']) + rendered
    if display.get('suffix') and not rendered.endswith(str(display['suffix'])):
        rendered += str(display['suffix'])
    if display.get('unit') and not display.get('prefix') and not display.get('suffix') and \
            str(display['unit']).strip().lower() not in ('%', 'percent', 'percentage', 'pct', 'usd', 'currency'):
        rendered += ' ' + str(display['unit'])
    return rendered


def _metric_view(surface_id: str, title: str, payload: dict[str, Any]) -> dict[str, Any]:
    number = metric_number(payload.get('result'))
    raw = payload.get('result')
    if number is None and (raw is None or isinstance(raw, (dict, list))):
        raise ValueError('The metric has no single value')
    value = format_metric(number, payload.get('display')) if number is not None else _text(raw, 80)
    observations = [o for o in payload.get('observations') or payload.get('history') or [] if isinstance(o, dict)]
    series = []
    for item in observations:
        point = metric_number(item.get('value', item.get('result')))
        if point is not None:
            series.append((str(item.get('data_as_of') or item.get('observed_at') or ''), point))
    series.sort(key=lambda p: p[0])
    trend = payload.get('trend') if isinstance(payload.get('trend'), dict) else {}
    percent = trend.get('percent') if isinstance(trend.get('percent'), (int, float)) else None
    if percent is None and len(series) > 1 and series[0][1]:
        percent = (series[-1][1] - series[0][1]) / abs(series[0][1]) * 100
    latest = payload.get('snapshot') if isinstance(payload.get('snapshot'), dict) else (observations[0] if observations else {})
    status = latest.get('status') or {True: 'Passing', False: 'Breaching'}.get(latest.get('verdict'), 'Governed metric')
    caption = []
    if payload.get('data_as_of'):
        caption.append('As of ' + str(payload['data_as_of'])[:16].replace('T', ' ') + ' UTC')
    caption.append(f'{len(series)} observations' if len(series) > 1 else ('Saved snapshot' if payload.get('frozen') else 'Current snapshot'))
    metric = {'id': 'metric', 'component': 'CMetric', 'label': _text(payload.get('name') or title, 120), 'value': value,
              'status': _text(str(status), 60), 'caption': ' · '.join(caption)}
    if percent is not None:
        metric['delta'] = f"{'↗' if percent > 0 else '↘' if percent < 0 else '—'} {abs(percent):.1f}%"
        metric['meaning'] = trend.get('meaning') if trend.get('meaning') in ('good', 'bad') else 'neutral'
    components = [metric]
    if payload.get('description'):
        components.append({'id': 'description', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                           'text': _text(payload['description'], 600)})
    root = {'id': 'root', 'component': 'CStack', 'direction': 'column', 'gap': 'sm',
            'children': [c['id'] for c in components]}
    return _document(surface_id, [root] + components, 150)


def _decision_view(surface_id: str, payload: dict[str, Any], *, can_answer: bool) -> dict[str, Any]:
    """A human decision: the options with their consequences, the recommended
    one marked; open ones carry a choose recipe per option bound to the revision."""
    try:
        decision_id = str(uuid.UUID(str(payload.get('id') or '')))
    except ValueError:
        raise ValueError('The decision has no id') from None
    options = [o for o in payload.get('options') or [] if isinstance(o, dict) and o.get('key')]
    if not options:
        raise ValueError('The decision has no options')
    status = str(payload.get('status') or 'open')
    open_ = status == 'open'
    revision = payload.get('revision') if type(payload.get('revision')) is int else None
    selected, recommended = payload.get('selected_option'), payload.get('recommended_option')
    components: list[dict[str, Any]] = []
    actions: dict[str, Any] = {}

    def add(component: dict[str, Any]) -> str:
        components.append(component)
        return component['id']

    kicker = 'Waiting on you' if open_ else {'answered': 'Decision recorded', 'cancelled': 'Cancelled',
                                            'expired': 'Expired'}.get(status, status.capitalize())
    children = [add({'id': 'kicker', 'component': 'CText', 'size': 'caption', 'tone': 'warm' if open_ else 'accent',
                     'text': f"{kicker} · {_text(payload.get('decision_kind') or 'choice', 30)}"})]
    if payload.get('summary'):
        children.append(add({'id': 'summary', 'component': 'CText', 'size': 'body', 'tone': 'muted',
                             'text': _text(payload['summary'], 1200)}))
    for index, option in enumerate(options[:6]):
        key = str(option['key'])
        mark = 'Chosen' if key == selected else 'Recommended' if key == recommended else ''
        lines = [add({'id': f'option-{index}-label', 'component': 'CText', 'size': 'body',
                      'text': _text(option.get('label') or key, 160) + (f' · {mark}' if mark else '')})]
        detail = _text(option.get('consequence') or option.get('summary'), 600)
        if detail:
            lines.append(add({'id': f'option-{index}-detail', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                              'text': detail}))
        if open_ and can_answer and revision:
            event = f'stage.decision.choose.{index}'
            add({'id': f'option-{index}-choose-label', 'component': 'Text', 'text': 'Choose this'})
            add({'id': f'option-{index}-choose', 'component': 'Button', 'child': f'option-{index}-choose-label',
                 'variant': 'primary' if key == recommended else 'borderless', 'action': {'event': {'name': event}}})
            lines.append(add({'id': f'option-{index}-actions', 'component': 'Row',
                              'children': [f'option-{index}-choose']}))
            actions[event] = {'method': 'POST', 'path': f'/api/calliope/decisions/{decision_id}/answer',
                              'body': {'selected_option': key, 'revision': revision}, 'fields': {}, 'follow': False,
                              'done': f"Decision recorded: {_text(option.get('label') or key, 80)}."}
        stack = add({'id': f'option-{index}-lines', 'component': 'CStack', 'direction': 'column', 'gap': 'xs',
                     'children': lines})
        children.append(add({'id': f'option-{index}', 'component': 'CSurface', 'treatment': 'outlined', 'padding': 'sm',
                             'tone': 'accent' if mark else 'neutral', 'child': stack}))
    if payload.get('response_note'):
        children.append(add({'id': 'note', 'component': 'CText', 'size': 'caption', 'tone': 'muted',
                             'text': 'Note · ' + _text(payload['response_note'], 600)}))
    components.insert(0, {'id': 'root', 'component': 'CStack', 'direction': 'column', 'gap': 'sm', 'children': children})
    document = _document(surface_id, components, 120 + 110 * len(options))
    document['actions'] = actions
    return document
