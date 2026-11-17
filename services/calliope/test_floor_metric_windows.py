"""Floor periods compare observation time, not a metric's repeated business date."""
from pathlib import Path
import pytest
import server
from test_workspace_sql import db  # noqa: F401


@pytest.fixture
def observed_metrics(db, monkeypatch):
    with db() as conn:
        conn.execute("""CREATE TABLE rvbbit.metric_catalog(
            name text, version int, description text, params jsonb, grain text,
            labels jsonb, check_sql text, category text, subcategory text, created_at timestamptz);
            INSERT INTO rvbbit.metric_catalog VALUES('monthly',1,'Monthly total','{}','month',
            '{"display":{"preferred_direction":"higher"}}',NULL,'Core','Revenue',now());
            CREATE TABLE rvbbit.metric_observations(
                observation_id bigint GENERATED ALWAYS AS IDENTITY,metric_name text DEFAULT 'monthly',
                metric_version int DEFAULT 1,value jsonb,verdict jsonb DEFAULT '{"ok":true}',
                status text DEFAULT 'ok',trigger text DEFAULT 'schedule',params jsonb DEFAULT '{}',
                data_as_of timestamptz DEFAULT '2025-01-01',observed_at timestamptz);
        """)
    with db() as conn:
        conn.execute('CREATE VIEW rvbbit.metric_defs AS SELECT name,version,params FROM rvbbit.metric_catalog')
        migration=Path(__file__).parents[2]/'crates/pg_rvbbit/sql/migrations/0470_canonical_metric_scopes.sql'
        conn.execute(migration.read_text().split('-- Discovery is allowed')[0])
    monkeypatch.setattr(server, '_conn', db)
    monkeypatch.setattr(server, '_metric_user_relations', lambda owner: (set(), set()))
    return db


@pytest.mark.parametrize('days',[1,7,30,90])
def test_rolling_windows_include_restatements_and_exclude_old_readings(observed_metrics, days):
    with observed_metrics() as conn:
        for age, value in [(days+2, 999), (days*.8,100), (days*.1,150)]:
            conn.execute("INSERT INTO rvbbit.metric_observations(value,observed_at) VALUES(%s::jsonb,now()-(%s * interval '1 day'))", (str(value),age))
    result = server._metric_catalog_snapshot(days=days,time_basis='observed')
    metric = result['metrics'][0]
    assert metric['snapshot']['value'] == 150
    assert [point['value'] for point in metric['series']] == [100,150]
    assert metric['series_scope'] == 'observed_window'
    assert metric['trend']['percent'] == 50
    assert f'last {days} day' in metric['trend']['basis']


def test_sparse_window_does_not_calculate_change_from_outside_period(observed_metrics):
    with observed_metrics() as conn:
        conn.execute("INSERT INTO rvbbit.metric_observations(value,observed_at) VALUES('100',now()-interval '3 days'),('150',now()-interval '1 hour')")
    metric = server._metric_catalog_snapshot(days=1,time_basis='observed')['metrics'][0]
    assert metric['snapshot']['value'] == 150
    assert len(metric['series']) == 1
    assert metric['trend'] is None
    with pytest.raises(ValueError,match='time_basis'):
        server._metric_catalog_snapshot(time_basis='unsupported')


@pytest.mark.parametrize('period,interval',[('day','1 day'),('week','1 week'),('month','1 month'),('quarter','3 months')])
def test_floor_period_is_calendar_grain_and_keeps_latest_reading_not_sum(observed_metrics, period, interval):
    with observed_metrics() as conn:
        for offset,value in [(13,999),(2,10),(1,20),(0,30)]:
            conn.execute("INSERT INTO rvbbit.metric_observations(value,observed_at) VALUES(%s::jsonb,date_trunc(%s,now())-(%s * %s::interval))",(str(value),period,offset,interval))
        conn.execute("INSERT INTO rvbbit.metric_observations(value,observed_at) VALUES('35',now())")
    metric=server._metric_catalog_snapshot(floor_period=period)['metrics'][0]
    assert [p['value'] for p in metric['series']]==[10,20,35]
    assert metric['snapshot']['value']==35
    assert metric['series_scope']=='period_snapshots' and metric['series_period']==period
    assert all(p['period_start'] for p in metric['series'])
    assert 'not period totals' in metric['trend']['basis']


def test_week_boundary_uses_metric_timezone_and_sparse_periods_stay_sparse(observed_metrics):
    with observed_metrics() as conn:
        conn.execute("UPDATE rvbbit.metric_catalog SET labels='{\"time_zone\":\"America/Chicago\"}'")
        # Around the preceding Monday boundary: Sunday in Chicago is still
        # the prior week even after UTC has crossed into Monday.
        conn.execute("""INSERT INTO rvbbit.metric_observations(value,observed_at) VALUES
          ('10',(date_trunc('week',now() AT TIME ZONE 'America/Chicago')-interval '1 week'-interval '1 hour') AT TIME ZONE 'America/Chicago'),
          ('20',(date_trunc('week',now() AT TIME ZONE 'America/Chicago')-interval '1 week'+interval '1 hour') AT TIME ZONE 'America/Chicago')""")
    metric=server._metric_catalog_snapshot(floor_period='week')['metrics'][0]
    assert [p['value'] for p in metric['series']]==[10,20]
    with observed_metrics() as conn:
        conn.execute("DELETE FROM rvbbit.metric_observations")
        conn.execute("INSERT INTO rvbbit.metric_observations(value,observed_at) VALUES('30',now())")
    metric=server._metric_catalog_snapshot(floor_period='week')['metrics'][0]
    assert len(metric['series'])==1 and metric['trend'] is None
    assert metric['series_scope']=='period_snapshots'
    with pytest.raises(ValueError):server._metric_catalog_snapshot(floor_period='year')
    with pytest.raises(ValueError):server._metric_catalog_snapshot(floor_period='week',ticks=24)
