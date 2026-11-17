"""Measurement-period labels from declared metric metadata, never metric names.

Legacy Core definitions spell out their default window in grain/description.
Recognize only explicit declarations; unfamiliar definitions stay unlabeled.
This does not evaluate SQL, aggregate observations, or change metric meaning.
"""
import re


def measurement(definition):
    labels = definition.get('labels') if isinstance(definition.get('labels'), dict) else {}
    display = labels.get('display') if isinstance(labels.get('display'), dict) else {}
    explicit = display.get('measurement_period') or labels.get('measurement_period')
    zone = str(labels.get('time_zone') or '')[:80]
    if isinstance(explicit, dict) and explicit.get('label'):
        return {'kind': 'explicit', 'label': str(explicit['label'])[:80], 'time_zone': zone,
                'source': 'labels', 'detail': str(explicit.get('detail') or '')[:500]}
    if isinstance(explicit, str) and explicit.strip():
        return {'kind': 'explicit', 'label': explicit.strip()[:80], 'time_zone': zone, 'source': 'labels'}
    params = definition.get('params') or {}
    declaration = ' '.join(str(definition.get(k) or '') for k in ('grain', 'description'))
    if (params.get('start') is None and params.get('end') is None
            and re.search(r'omitted (?:start/end|params) default to today\b', declaration, re.I)):
        return {'kind': 'observation_day', 'label': 'Today', 'time_zone': zone,
                'source': 'declared_default_window',
                'detail': 'Day-to-date value for the day this reading was recorded, in the metric timezone.'}
    # Only an affirmative declaration in grain; descriptions often contain
    # negated sibling definitions such as "not the rolling-60 diagnostic".
    match = re.search(r'\bin the latest (\d{1,3}) (?:[A-Za-z_/]+ )?(?:calendar )?dates\b', str(definition.get('grain') or ''), re.I)
    if match:
        return {'kind': 'rolling', 'label': f'Rolling {int(match[1])} days', 'time_zone': zone,
                'source': 'declared_grain', 'detail': 'Rolling-cohort value at each reading; the chart does not change this window.'}
    start = labels.get('cohort_start')
    if isinstance(start, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', start):
        return {'kind': 'cohort', 'label': 'Cohort since ' + start, 'time_zone': zone,
                'source': 'labels', 'detail': 'Cohort start, not the chart start date.'}
    return {'kind': 'unknown', 'label': 'Latest reading', 'time_zone': zone,
            'source': 'unspecified', 'detail': 'Measurement period is not explicitly declared in the metric metadata.'}
