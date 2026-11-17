"""Cube-only query shelves. SQL is compiled from validated fields, never client expressions.

Uses DataRabbit's RollupSpec wire shape. Ratio/weighted-average rules are derived
only when the cube definition exposes the matching aggregate components.
"""
import copy
import math
import re

PIVOT_CAP = 24
GRAINS = {'year', 'quarter', 'month', 'week', 'day', 'hour'}
AGGS = {'sum', 'avg', 'min', 'max', 'count', 'count_distinct', 'median', 'stddev', 'variance'}
COMPARE = {'>', '>=', '<', '<=', '=', '!='}


def ident(value):
    return '"' + value.replace('"', '""') + '"'


def literal(value):
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError('Use a finite numeric value')
        return str(value)
    if not isinstance(value, str) or len(value) > 1000:
        raise ValueError('Filter values must be short text, numbers or null')
    return "'" + value.replace("'", "''") + "'"


def metadata(dataset, definition):
    """Conservative rollup semantics from published SQL, with optional catalog overrides."""
    import sqlglot
    from sqlglot import exp
    expressions = {}
    grouped = False
    try:
        tree = sqlglot.parse_one(definition.get('sql') or '', read='postgres')
        if isinstance(tree, exp.Select):
            grouped = bool(tree.args.get('group'))
            expressions = {e.alias_or_name: e.this if isinstance(e, exp.Alias) else e for e in tree.expressions}
    except sqlglot.errors.SqlglotError:
        pass
    by_sql = {e.sql(dialect='postgres'): name for name, e in expressions.items()}
    overrides = ((definition.get('labels') or {}).get('query_shelf') or {}).get('fields') or {}
    fields = []
    for column in dataset['columns']:
        name, typ = column['name'], column['type'].lower()
        numeric = bool(re.match(r'^(smallint|integer|bigint|numeric|decimal|real|double precision|int[248]|float[48])\b', typ))
        temporal = typ == 'date' or typ.startswith('timestamp')
        expr = expressions.get(name)
        aggregate = expr is not None and any(expr.find_all(exp.AggFunc))
        numeric_key = bool(re.search(r'(^|_)(id|key|code|year|month|day|period)$', name, re.I))
        role = 'date' if temporal else 'measure' if numeric and (aggregate or (not grouped and not numeric_key)) else 'dimension'
        allowed = ['sum', 'avg', 'min', 'max'] if role == 'measure' else ['count_distinct', 'count']
        default, rule, note = allowed[0], None, ''
        if isinstance(expr, (exp.Sum, exp.Count)):
            allowed, default = ['sum', 'min', 'max'], 'sum'
            if isinstance(expr, exp.Count) and isinstance(expr.this, exp.Distinct):
                allowed, default, note = [], None, 'Distinct counts cannot be added across groups.'
        elif isinstance(expr, exp.Min):
            allowed, default = ['min', 'max'], 'min'
        elif isinstance(expr, exp.Max):
            allowed, default = ['max', 'min'], 'max'
        elif aggregate:
            # Match components structurally; never guess from a column's name.
            numerator, denominator = None, None
            if isinstance(expr, exp.Avg):
                numerator = exp.Sum(this=expr.this.copy())
                denominator = exp.Count(this=expr.this.copy())
            elif isinstance(expr, exp.Div):
                numerator, denominator = expr.this, expr.expression
                if isinstance(denominator, exp.Nullif) and denominator.expression.sql() == '0':
                    denominator = denominator.this
            if isinstance(numerator, exp.Sum) and isinstance(denominator, (exp.Sum, exp.Count)):
                n, d = by_sql.get(numerator.sql(dialect='postgres')), by_sql.get(denominator.sql(dialect='postgres'))
                if n and d:
                    rule = {'kind': 'ratio_of_sums', 'numerator': n, 'denominator': d}
            allowed, default = (['sum'], 'sum') if rule else ([], None)
            note = 'Recomputed from component totals at the selected grain.' if rule else 'This pre-aggregated measure needs a declared rollup rule.'
        override = overrides.get(name)
        if isinstance(override, dict):
            # Administrator-maintained metadata only; still validate references below.
            role = override.get('role', role)
            rule = override.get('rule', rule)
            allowed = override.get('aggregates', allowed)
            default = override.get('default_aggregate', default)
        fields.append(dict(column, role=role, aggregates=allowed, default_aggregate=default, rule=rule, note=note))
    names = {f['name'] for f in fields}
    for f in fields:
        if f['rule'] and (f['rule'].get('kind') != 'ratio_of_sums' or
                         f['rule'].get('numerator') not in names or f['rule'].get('denominator') not in names):
            f.update(rule=None, aggregates=[], default_aggregate=None, note='The rollup components are unavailable.')
    return {'relation': dataset['relation'], 'title': dataset['title'], 'grain': definition.get('grain'),
            'version': definition.get('version'), 'fields': fields, 'pivot_cap': PIVOT_CAP}


class Query:
    def __init__(self, catalog, raw, *, allow_row_count=False):
        if not isinstance(raw, dict):
            raise ValueError('Choose a cube shelf configuration')
        self.catalog = catalog
        self.allow_row_count = allow_row_count
        self.fields = {f['name']: f for f in catalog['fields']}
        self.source = '.'.join(ident(p) for p in catalog['relation'].split('.'))
        self.spec = copy.deepcopy(raw)
        if raw.get('projections'):
            raise ValueError('Cube shelves support grouping and measures only')
        for key, cap in [('groupBy', 8), ('measures', 8), ('filters', 24), ('having', 8), ('orderBy', 8)]:
            value = raw.get(key, [])
            if not isinstance(value, list) or len(value) > cap or any(not isinstance(x, dict) for x in value):
                raise ValueError('Too many or invalid shelf entries')
            self.spec[key] = value
        self.groups = [(self.column(t), self.dimension(t)) for t in self.spec['groupBy']]
        if len({name for name, _ in self.groups}) != len(self.groups):
            raise ValueError('A field can appear on Rows once')
        self.measures = self.spec['measures']
        if not self.measures:
            raise ValueError('Add a measure to Values')
        aliases, ids = set(), set()
        for m in self.measures:
            self.measure(m)
            alias, mid = m.get('alias'), m.get('id')
            if not isinstance(alias, str) or not alias or len(alias.encode()) > 63 or alias in aliases or alias in dict(self.groups):
                raise ValueError('Measure names must be unique')
            if not isinstance(mid, str) or not mid or len(mid) > 200 or mid in ids:
                raise ValueError('Measure identities must be unique')
            aliases.add(alias); ids.add(mid)
        self.where = self.filters()
        self.pivot = self.spec.get('pivot')
        if self.pivot:
            if not isinstance(self.pivot, dict):
                raise ValueError('Choose one field for Columns')
            name = self.column(self.pivot)
            if name in dict(self.groups):
                raise ValueError('Move the column field out of Rows')
            self.dimension(self.pivot)
            # Client-provided distinct values are never authoritative.
            self.pivot['values'] = []
            self.pivot.pop('measureIds', None)
            self.pivot.pop('truncated', None)
        self.having = self.having_sql()
        self.order, self.limit = self.order_sql()

    def column(self, obj):
        col = obj.get('column')
        name = col.get('name') if isinstance(col, dict) else None
        if name not in self.fields:
            raise ValueError('A selected field is no longer in this cube')
        return name

    def dimension(self, term):
        name = self.column(term)
        if self.fields[name]['role'] == 'measure':
            raise ValueError('Use dimensions on Rows and Columns')
        value = ident(name)
        grain = term.get('grain')
        if grain:
            if grain not in GRAINS or self.fields[name]['role'] != 'date':
                raise ValueError('Choose a valid date grouping')
            value = f"date_trunc('{grain}', {value})"
        return value

    def measure(self, m, predicate=None):
        if self.allow_row_count and m.get('column') is None and m.get('agg') == 'count':
            return 'COUNT(*)' + (f' FILTER (WHERE {predicate})' if predicate else '')
        name = self.column(m)
        field = self.fields[name]
        agg = m.get('agg')
        if agg not in AGGS or agg not in field['aggregates']:
            raise ValueError(f'Choose a supported aggregation for {name}')
        suffix = f' FILTER (WHERE {predicate})' if predicate else ''
        rule = field.get('rule')
        if rule:
            return f"(SUM({ident(rule['numerator'])}){suffix})::double precision / NULLIF(SUM({ident(rule['denominator'])}){suffix},0)"
        col = ident(name)
        if agg == 'count_distinct':
            return f'COUNT(DISTINCT {col}){suffix}'
        if agg == 'median':
            return f'percentile_cont(0.5) WITHIN GROUP (ORDER BY {col}){suffix}'
        return f'{agg.upper()}({col}){suffix}'

    def filters(self, excluding=None):
        clauses = []
        ops = {'eq':'=', 'neq':'!=', 'gt':'>', 'gte':'>=', 'lt':'<', 'lte':'<='}
        for f in self.spec['filters']:
            name = self.column(f)
            if name == excluding:
                continue
            col, op = ident(name), f.get('op')
            if op in ('is_null', 'not_null'):
                clauses.append(f"{col} IS {'NOT ' if op=='not_null' else ''}NULL")
            elif op in ('in', 'not_in'):
                values = f.get('values')
                if not isinstance(values, list) or not 1 <= len(values) <= 200:
                    raise ValueError('Choose between 1 and 200 filter values')
                nonnull = [literal(v) for v in values if v is not None]
                parts = [f"{col} {'NOT IN' if op=='not_in' else 'IN'} ({','.join(nonnull)})"] if nonnull else []
                if None in values:
                    parts.append(f"{col} IS {'NOT ' if op=='not_in' else ''}NULL")
                clauses.append('(' + (' AND ' if op=='not_in' else ' OR ').join(parts) + ')')
            elif op in ops and 'value' in f:
                if f['value'] is None:
                    if op not in ('eq', 'neq'):
                        raise ValueError('Null supports equals or not equals')
                    clauses.append(f"{col} IS {'NOT ' if op=='neq' else ''}NULL")
                else:
                    clauses.append(f'{col} {ops[op]} {literal(f["value"])}')
            else:
                raise ValueError('Choose a supported filter')
        return ' WHERE ' + ' AND '.join(clauses) if clauses else ''

    def having_sql(self):
        measures = {m['id']:m for m in self.measures}
        parts = []
        for term in self.spec['having']:
            if term.get('measureId') not in measures or term.get('op') not in COMPARE or type(term.get('value')) not in (int, float):
                raise ValueError('Choose a valid measure filter')
            parts.append(f"({self.measure(measures[term['measureId']])}) {term['op']} {literal(term['value'])}")
        return ' HAVING ' + ' AND '.join(parts) if parts else ''

    def order_sql(self):
        # Use expressions for measure sorts even when the measure is pivoted away.
        expressions = dict(self.groups)
        expressions.update({m['alias']:self.measure(m) for m in self.measures})
        terms = []
        for term in self.spec['orderBy']:
            if term.get('ref') not in expressions or term.get('dir') not in ('asc', 'desc'):
                raise ValueError('Choose a valid sort field')
            terms.append(f"{expressions[term['ref']]} {term['dir'].upper()} NULLS LAST")
        limit = self.spec.get('limit')
        cap = ''
        if limit:
            if not isinstance(limit, dict) or type(limit.get('n')) is not int or not 1 <= limit['n'] <= 2000 or limit.get('dir', 'desc') not in ('asc', 'desc'):
                raise ValueError('Limit rows to between 1 and 2,000')
            measure = next((m for m in self.measures if m['id'] == limit.get('byMeasureId', self.measures[0]['id'])), None)
            if not measure:
                raise ValueError('Choose a measure for Top N')
            if not terms:
                terms.append(self.measure(measure) + ' ' + limit.get('dir', 'desc').upper() + ' NULLS LAST')
            cap = ' LIMIT ' + str(limit['n'])
        if not terms and self.groups:
            terms = [expr + ' ASC NULLS LAST' for _, expr in self.groups]
        return (' ORDER BY ' + ', '.join(terms) if terms else ''), cap

    def probe(self):
        expr = self.dimension(self.pivot)
        return f'SELECT {expr} AS pivot_value FROM {self.source}{self.where} GROUP BY 1 ORDER BY 1 NULLS LAST LIMIT {PIVOT_CAP+1}'

    def compile(self, values=None):
        selects = [f'{expr} AS {ident(name)}' for name, expr in self.groups]
        if self.pivot:
            if not isinstance(values, list) or len(values) > PIVOT_CAP:
                raise ValueError(f'This pivot has more than {PIVOT_CAP} columns. Filter values or choose a broader date grouping.')
            if not values:
                raise ValueError('No column values match these filters')
            if len(values)*len(self.measures)+len(self.groups) > 96:
                raise ValueError('This pivot is too wide. Use fewer values or measures.')
            expr = self.dimension(self.pivot)
            used = set(dict(self.groups))
            for i, value in enumerate(values):
                pred = f'{expr} IS NULL' if value is None else f'{expr} = {literal(value)}'
                for m in self.measures:
                    label = f'{"(empty)" if value is None else value} / {m["alias"]}'
                    # PostgreSQL identifiers are limited to 63 UTF-8 bytes.
                    label = label.encode()[:52].decode('utf8',errors='ignore')
                    base = label
                    n = 1
                    while label in used:
                        label = f'{base} {n}'; n += 1
                    used.add(label)
                    selects.append(f'{self.measure(m,pred)} AS {ident(label)}')
            self.pivot['values'] = values
        else:
            selects += [f'{self.measure(m)} AS {ident(m["alias"])}' for m in self.measures]
        grouping = ' GROUP BY ' + ', '.join(expr for _,expr in self.groups) if self.groups else ''
        return 'SELECT\n  ' + ',\n  '.join(selects) + f'\nFROM {self.source}{self.where}{grouping}{self.having}{self.order}{self.limit}'

    def value_probe(self, name, search=''):
        if name not in self.fields:
            raise ValueError('Unknown cube field')
        col = ident(name)
        where = self.filters(excluding=name)
        if search:
            term = str(search).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            where += (' AND ' if where else ' WHERE ') + f'{col}::text ILIKE {literal("%"+term+"%")}'
        return f'SELECT {col} AS value,COUNT(*) AS frequency FROM {self.source}{where} GROUP BY {col} ORDER BY frequency DESC,{col} NULLS LAST LIMIT 201'
