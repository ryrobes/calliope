"""Definition contracts, not inferred execution scopes or a second registry."""
import re


METRIC_FIELDS = ('population', 'unit', 'time_basis', 'window', 'why')
VAGUE = re.compile(r'\bselected (?:scope|period|date range|window)|\bas requested\b|\btbd\b', re.I)


def unresolved_params(sql):
    return sorted(set(re.findall(r'\{([A-Za-z_][A-Za-z0-9_]*)(?:!)?\}', sql)))


def prove_metric(conn, sql, defaults):
    """Prove the empty invocation; keep proof reads separate from publication."""
    import json
    if not isinstance(defaults, dict):
        raise ValueError('Metric params must be an object of canonical defaults.')
    resolved = conn.execute('SELECT rvbbit.preview_metric_sql(%s,%s::jsonb) AS sql',
                            (sql, json.dumps(defaults))).fetchone()['sql']
    missing = unresolved_params(resolved)
    if missing:
        raise ValueError('Every template parameter needs a default: ' + ', '.join(missing))
    rows = conn.execute(f'SELECT to_jsonb(_metric_proof) AS row FROM ({resolved}) _metric_proof LIMIT 2').fetchall()
    if len(rows) != 1 or not isinstance(rows[0].get('row'), dict):
        raise ValueError('A canonical metric must return exactly one scalar row without arguments.')
    row = rows[0]['row']
    if not (len(row) == 1 or 'value' in row):
        raise ValueError('Return one scalar column or a headline value column.')
    value = row.get('value') if 'value' in row else next(iter(row.values()))
    if isinstance(value, (dict, list)):
        raise ValueError('The headline value must be scalar, not an object or array.')
    return row


def metric_contract(value, params=None):
    """A canonical metric must mean something with an empty invocation."""
    if not isinstance(value, dict):
        raise ValueError('Provide metric_contract with population, unit, time_basis, window and why. '
                         'The metric must be useful without arguments and comparable in daily snapshots.')
    result = {}
    for key in METRIC_FIELDS:
        text = value.get(key)
        if not isinstance(text, str) or not text.strip() or len(text) > 1200 or VAGUE.search(text):
            raise ValueError(f'metric_contract.{key} must explicitly define the default meaning; no unspecified selected scope or period')
        result[key] = text.strip()
    defaults = params if isinstance(params, dict) else {}
    result.update(version=1, defaults=defaults, snapshot='daily',
                  time_zone=value.get('time_zone') or 'UTC')
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(result['time_zone'])
    except (ValueError, KeyError, TypeError):
        raise ValueError('metric_contract.time_zone must be an IANA time zone') from None
    return result


def metric_review(metric):
    """Surface legacy uncertainty without revising definitions or old receipts."""
    labels = metric.get('labels') or {}
    contract = labels.get('metric_contract')
    issues = []
    if not contract:
        issues.append('Default scope has not been recorded as a canonical metric contract.')
    else:
        try:
            checked = metric_contract(contract, metric.get('params'))
            if contract.get('defaults') != checked['defaults']:
                issues.append('Recorded canonical defaults differ from this definition; review the scope.')
        except ValueError as e:
            issues.append(str(e))
    if VAGUE.search(str(metric.get('grain') or '') + ' ' + str(metric.get('description') or '')):
        issues.append('The description refers to an unspecified selection; define the default scope.')
    if labels.get('origin') == 'meaning' and labels.get('window') == 'last {days} days' and not contract:
        issues.append('This window came from the legacy implied-metric rule; confirm its business purpose.')
    return issues


def normalize_idea(item):
    """Preserve an incomplete idea as a proposal; never supply missing semantics."""
    result = {k: str(item[k]).strip()[:1200 if k in METRIC_FIELDS else 400]
              for k in ('name', 'formula', 'grain', *METRIC_FIELDS) if item.get(k)}
    if isinstance(item.get('defaults'), dict):
        result['defaults'] = item['defaults']
    if isinstance(item.get('time_zone'), str):
        result['time_zone'] = item['time_zone'][:80]
    return result


def cube_reuse_check(conn, name, sql, grain, description, reuse=None):
    """Return a concrete reuse comparison before a new materialization exists."""
    if not isinstance(grain, str) or not grain.strip() or VAGUE.search(grain):
        raise ValueError('Define the cube row grain and key explicitly; a chart, date selection or grouping is not a new cube.')
    if not isinstance(description, str) or not description.strip():
        raise ValueError('Describe the reusable fact population and purpose of this cube.')
    candidates = conn.execute(
        "SELECT name,grain,description,sql,rvbbit.sql_relation_refs(sql) AS sources "
        "FROM (SELECT DISTINCT ON(name) name,grain,description,sql FROM rvbbit.cube_defs ORDER BY name,version DESC) c "
        "WHERE name<>%s AND rvbbit.sql_relation_refs(sql) && rvbbit.sql_relation_refs(%s)", (name, sql)).fetchall()
    # Do not claim SQL equivalence from whitespace normalization inside literals.
    duplicate = next((c for c in candidates if c['sql'].strip().rstrip(';') == sql.strip().rstrip(';')), None)
    if duplicate:
        return {'error': {'code': 'CUBE_ALREADY_EXISTS', 'message': 'Reuse the existing cube with the same SQL.',
                          'cube': duplicate['name']}}
    public = [{k: c.get(k) for k in ('name', 'grain', 'description', 'sources')} for c in candidates]
    if candidates:
        reviewed = reuse.get('reviewed', []) if isinstance(reuse, dict) else []
        reason = reuse.get('reason', '') if isinstance(reuse, dict) else ''
        difference = reuse.get('difference') if isinstance(reuse, dict) else None
        if (not isinstance(reviewed, list) or not all(isinstance(n, str) for n in reviewed)
                or not {c['name'] for c in candidates} <= set(reviewed)
                or not isinstance(reason, str) or len(reason.strip()) < 20
                or difference not in ('grain', 'population', 'security', 'refresh', 'performance')):
            return {'error': {'code': 'CUBE_REUSE_REVIEW', 'message':
                'Compare these cubes first. Reuse their dimensions/measures for filters and views. '
                'For a distinct cube, provide reuse.reviewed (names), difference '
                '(grain/population/security/refresh/performance), and a specific reason.', 'candidates': public}}
    return {'reviewed': public, 'rationale': reuse or {}}
