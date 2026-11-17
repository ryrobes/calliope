"""Published Meaning subjects for the personal Data explorer.

An area is a dossier over several relations, not a queryable relation. Its prose
may discuss any member, so only expose dossiers whose complete membership is
currently selectable by the caller. Discovery never starts a Meaning scan.
"""
import json
import re


def catalog(factory, available):
    with factory(read_only=True) as conn:
        rows = conn.execute("""SELECT domain_key,name,about,members,core,roles,areas,questions,
          candidate_metrics,candidate_breakdowns,gaps,dossier_at,updated_at
          FROM rvbbit.meaning_published_domains ORDER BY name""").fetchall()
    result = []
    for row in rows:
        members = list(dict.fromkeys(row.get('members') or []))
        if not members or not set(members).issubset(available):
            continue
        result.append(dict(key=row['domain_key'], name=row['name'], about=row.get('about') or '',
                           members=members, core=row.get('core') or [], roles=row.get('roles') or {},
                           subareas=row.get('areas') or [], questions=row.get('questions') or [],
                           candidate_metrics=row.get('candidate_metrics') or [], gaps=row.get('gaps') or [],
                           candidate_breakdowns=row.get('candidate_breakdowns') or [],
                           published_at=str(row.get('dossier_at') or row.get('updated_at') or '')))
    return result


def cards(areas, query, matched_relations=()):
    words = re.findall(r'\w+', query.casefold())
    ranked = []
    for area in areas:
        haystack = json.dumps(area, ensure_ascii=False, default=str).casefold()
        literal = not words or all(word in haystack for word in words)
        related = bool(set(area['members']) & set(matched_relations))
        if not literal and not related:
            continue
        ranked.append((2 if literal else 1, {
            'key': area['key'], 'name': area['name'], 'about': area['about'][:700],
            'table_count': len(area['members']), 'subarea_count': len(area['subareas']),
            'question_count': len(area['questions']), 'gap_count': len(area['gaps']),
            'metric_idea_count': len(area['candidate_metrics']),
            'breakdown_count': len(area['candidate_breakdowns']),
            'subtopics': [s['name'] for s in area['subareas'] if s.get('name')][:3],
            'sample_question': next(iter(area['questions']), None),
            'published_at': area['published_at']}))
    return [card for _, card in sorted(ranked, key=lambda item: -item[0])][:40]


def sql_relations(statement):
    """Use real lineage to associate saved metrics/cubes, not fuzzy name matches."""
    import sqlglot
    from sqlglot.optimizer.scope import traverse_scope
    try:
        return {f'{table.db or "public"}.{table.name}'
                for scope in traverse_scope(sqlglot.parse_one(statement, read='postgres'))
                for _, table in scope.selected_sources.values() if isinstance(table, sqlglot.exp.Table)}
    except Exception:
        return set()


def related(factory, area, available):
    with factory(read_only=True) as conn:
        cubes = conn.execute('SELECT name,sql FROM rvbbit.cube_catalog ORDER BY name').fetchall()
        metrics = conn.execute('SELECT name,sql FROM rvbbit.metric_catalog ORDER BY name').fetchall()
    members = set(area['members'])
    cube_refs = []
    for cube in cubes:
        ref = 'cubes.' + cube['name']
        refs = sql_relations(cube['sql'])
        if ref in available and refs & members and refs.issubset(available):
            cube_refs.append(ref)
    metric_names = []
    for metric in metrics:
        refs = sql_relations(metric['sql'])
        if refs & (members | set(cube_refs)) and refs.issubset(available):
            metric_names.append(metric['name'])
    return cube_refs, metric_names


def handoff(area, kind, index):
    field = {'gap': 'gaps', 'question': 'questions', 'metric': 'candidate_metrics', 'breakdown': 'candidate_breakdowns'}.get(kind)
    if field is None or type(index) is not int or not 0 <= index < len(area.get(field, [])):
        raise ValueError('Choose a question, metric idea or breakdown from this area')
    question = area[field][index]
    label = 'open question' if kind == 'gap' else 'sample question'
    guidance = ''
    if kind == 'metric':
        metric = question
        label = 'proposed metric'
        question = (f'{metric.get("name") or "Metric idea"}\n'
                    f'Proposed calculation: {metric.get("formula") or "Not yet defined"}\n'
                    f'Proposed grain: {metric.get("grain") or "Not yet defined"}\n'
                    f'Why it might help: {metric.get("why") or "Not yet described"}')
        guidance = ('This is a metric idea, not an approved or saved metric definition. '
                    'Check for an existing saved metric first. Explore its usefulness, validate the '
                    'calculation, grain, units and denominator against the data, and help me refine '
                    'the definition before suggesting a saved metric. A metric returns one scalar value '
                    'for the selected scope and period; grouped comparisons belong in a separate breakdown.\n\n')
    elif kind == 'breakdown':
        label = 'proposed breakdown'
        question = (f'{question["name"]}\nPurpose: {question["purpose"]}\n'
                    f'Measures: {"; ".join(question["measures"])}\n'
                    f'Group by: {"; ".join(question["dimensions"])}\n'
                    f'Each result row: {question.get("grain") or "Confirm the grouping"}\n'
                    f'Caveats: {question.get("caveats") or "Validate before interpreting"}')
        guidance = ('This is a named grouped analysis, not a scalar metric or an approved saved definition. '
                    'Explore the comparison and its purpose. Validate the measures, grouping, denominators '
                    'and joins; preserve separate populations and clarify alternative groupings. '
                    'Help refine its narrative and interpret the results before suggesting a saved query or cube.\n\n')
    # A compact, visible composer draft. Callie retrieves the current dossier
    # using its key before investigating; never send or mutate metadata here.
    about = area['about'][:1200]
    sources = ', '.join(area['members'])
    prompt = (f'Help me explore this {label} '
              f'from the Meaning area "{area["name"]}":\n\n{question}\n\n{guidance}'
              f'Area key: {area["key"]}\nPublished: {area["published_at"]}\n'
              f'Area description: {about}\nMember datasets: {sources}\n\n'
              'Read the current Meaning dossier for this area and the relevant table definitions first. '
              'Investigate with the available data and shared database memory. Distinguish evidence '
              'from assumptions; ask me for missing business context when needed. If we establish new '
              'facts, enrich the appropriate Meaning metadata using the supported Meaning tools, '
              'preserving existing knowledge and recording the evidence. Do not mark an unknown '
              'resolved without evidence or treat proposed metrics, breakdowns or unverified joins as established facts.')
    if len(prompt) > 5500:
        raise ValueError('This question has too much context for Chat. Choose a more specific question.')
    return {'prompt': prompt, 'area_key': area['key'], 'question': question}
