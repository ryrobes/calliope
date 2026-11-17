import metric_presentation as mp


def test_declared_today_and_fixed_windows_do_not_depend_on_metric_names():
    today = {'params': {'start': None, 'end': None}, 'labels': {'time_zone': 'America/Chicago'},
             'grain': 'One scalar; omitted params default to today CT.'}
    assert mp.measurement(today)['kind'] == 'observation_day'
    assert mp.measurement({**today, 'name': 'monthly_total'}) == mp.measurement(today)
    assert mp.measurement({**today, 'params': {'start': '2026-01-01'}})['kind'] == 'unknown'
    assert mp.measurement({'name': 'daily_leads'})['label'] == 'Latest reading'


def test_cohort_windows_and_negated_sibling_descriptions():
    assert mp.measurement({'grain': 'Persons in the latest 60 Chicago calendar dates, including today.'})['label'] == 'Rolling 60 days'
    assert mp.measurement({'description': 'Not the rolling 60-day median.'})['kind'] == 'unknown'
    cohort = {'labels': {'cohort_start': '2026-01-01'}, 'description': 'Not rolling-60.'}
    assert mp.measurement(cohort)['label'] == 'Cohort since 2026-01-01'


def test_explicit_measurement_label_overrides_legacy_declarations():
    definition = {'description': 'Omitted start/end default to today CT.',
                  'labels': {'display': {'measurement_period': {'label': 'September to date', 'detail': 'Fiscal definition'}}}}
    assert mp.measurement(definition)['label'] == 'September to date'
    assert mp.measurement(definition)['source'] == 'labels'
