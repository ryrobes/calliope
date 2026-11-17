import copy

import pytest
import cube_shelf as shelf
import data_explorer as data
from test_workspace_sql import db  # noqa: F401

DEFINITION={'version':1,'grain':'region and category','sql':'''SELECT region,category,sum(sales) AS sales,
 sum(profit) AS profit,sum(profit)/nullif(sum(sales),0) AS margin,
 sum(seconds) AS seconds,count(seconds) AS duration_count,avg(seconds) AS avg_seconds,
 count(distinct person) AS distinct_people FROM raw GROUP BY region,category'''}
DATASET={'relation':'cubes.retail','title':'Retail','columns':[{'name':n,'type':t} for n,t in [
 ('region','text'),('category','text'),('sales','double precision'),('profit','double precision'),
 ('margin','double precision'),('seconds','double precision'),('duration_count','bigint'),
 ('avg_seconds','double precision'),('distinct_people','bigint')]]}
CAT=shelf.metadata(DATASET,DEFINITION)

def col(name):return {'name':name}
def measure(name):return {'id':name,'alias':name,'column':col(name),'agg':'sum'}
def spec(**kw):return {'groupBy':[{'column':col('region')}],'measures':[measure('sales')],**kw}


def test_metadata_recomputes_ratios_and_weighted_means_and_blocks_distinct_sum():
    fields={f['name']:f for f in CAT['fields']}
    assert fields['margin']['rule']=={'kind':'ratio_of_sums','numerator':'profit','denominator':'sales'}
    assert fields['avg_seconds']['rule']=={'kind':'ratio_of_sums','numerator':'seconds','denominator':'duration_count'}
    assert fields['distinct_people']['aggregates']==[]
    assert fields['region']['role']=='dimension'
    assert fields['sales']['aggregates']==['sum','min','max']


def test_pivot_filters_are_scoped_and_null_category_has_its_own_column():
    s=spec(pivot={'column':col('category'),'values':['FORGED']},filters=[{'column':col('region'),'op':'in','values':["O'Reilly",None]}])
    query=shelf.Query(CAT,s)
    sql=query.compile(['Office',None])
    assert 'FORGED' not in sql
    assert 'FILTER (WHERE "category" IS NULL)' in sql
    assert "'O''Reilly'" in sql
    assert '"region" IS NULL' in query.probe()
    assert 'LIMIT 25' in query.probe()
    assert query.spec['pivot']['values']==['Office',None]
    data.guard_sql(sql,['cubes.retail'])


def test_filtered_top_n_uses_measure_totals_when_pivoted():
    query=shelf.Query(CAT,spec(pivot={'column':col('category')},limit={'n':3,'byMeasureId':'sales'},having=[{'measureId':'sales','op':'>','value':4}]))
    sql=query.compile(['Office','Tech'])
    assert 'HAVING (SUM("sales")) > 4' in sql
    assert 'ORDER BY SUM("sales") DESC NULLS LAST LIMIT 3' in sql


@pytest.mark.parametrize('change',[
 {'groupBy':[{'column':col('not_a_field')}]},
 {'groupBy':[{'column':col('sales')}]},
 {'measures':[dict(measure('sales'),agg='pg_sleep')]},
 {'measures':[measure('distinct_people')]},
 {'measures':[]},
 {'filters':[{'column':col('sales'),'op':'raw','value':'OR true'}]},
 {'having':[{'measureId':'sales','op':'>','value':float('inf')}]},
 {'orderBy':[{'ref':'sales','dir':'DESC; DELETE'}]},
 {'limit':{'n':2001}},
 {'groupBy':[{'column':col('region'),'grain':'month'}]},
 {'projections':[{'operator':'llm'}]},
])
def test_reject_invalid_specs(change):
    with pytest.raises(ValueError):shelf.Query(CAT,spec(**change))


def test_pivot_overflow_is_explicit_not_silent_truncation():
    query=shelf.Query(CAT,spec(pivot={'column':col('category')}))
    with pytest.raises(ValueError,match='more than 24'):query.compile(list(range(25)))
    with pytest.raises(ValueError,match='No column values'):query.compile([])


def test_probe_excludes_own_filter_keeps_other_filters_and_escapes_search():
    query=shelf.Query(CAT,spec(filters=[{'column':col('category'),'op':'eq','value':'Office'}, {'column':col('region'),'op':'eq','value':'West'}]))
    sql=query.value_probe('category',"a%'_")
    assert "= 'Office'" not in sql and "= 'West'" in sql
    assert "a\\%''\\_" in sql
    data.guard_sql(sql,['cubes.retail'])


def test_actual_postgres_pivot_weighted_means_nulls_zero_and_top_n(db):
    with db() as c:
        c.execute('CREATE SCHEMA cubes; CREATE TABLE cubes.retail(region text,category text,sales float8,profit float8,margin float8,seconds float8,duration_count bigint,avg_seconds float8,distinct_people bigint)')
        c.execute("INSERT INTO cubes.retail VALUES ('West','Office',100,20,.2,30,3,10,1),('West','Tech',900,90,.1,100,1,100,1),('East',NULL,0,0,NULL,0,0,NULL,1)")
        query=shelf.Query(CAT,spec(measures=[measure('margin'),measure('avg_seconds')]))
        rows={r['region']:r for r in c.execute(query.compile()).fetchall()}
        assert rows['West']['margin']==pytest.approx(.11)
        assert rows['West']['avg_seconds']==pytest.approx(32.5)
        assert rows['East']['margin'] is None
        query=shelf.Query(CAT,spec(measures=[measure('margin'),measure('avg_seconds')],pivot={'column':col('category')},orderBy=[{'ref':'margin','dir':'desc'}]))
        values=[r['pivot_value'] for r in c.execute(query.probe()).fetchall()]
        rows=c.execute(query.compile(values)).fetchall()
        assert rows[0]['Office / margin']==pytest.approx(.2)
        assert rows[0]['Tech / avg_seconds']==100
        assert rows[1]['(empty) / margin'] is None
        query=shelf.Query(CAT,spec(pivot={'column':col('category')},limit={'n':1,'byMeasureId':'sales'}))
        assert c.execute(query.compile(values)).fetchone()['region']=='West'


def test_numeric_grouping_key_is_a_dimension_and_date_grain_compiles(db):
    catalog=shelf.metadata({'relation':'cubes.dates','title':'dates','columns':[{'name':'year','type':'integer'},{'name':'day','type':'date'},{'name':'value','type':'bigint'}]},
       {'version':1,'sql':'SELECT year, day, sum(n) AS value FROM raw GROUP BY year,day'})
    assert catalog['fields'][0]['role']=='dimension'
    with db() as c:
        c.execute('CREATE SCHEMA cubes; CREATE TABLE cubes.dates(year integer,day date,value bigint)')
        c.execute("INSERT INTO cubes.dates VALUES(2020,'2020-01-01',5),(2020,'2020-01-03',9)")
        query=shelf.Query(catalog,{'groupBy':[{'column':col('day'),'grain':'month'}],'measures':[measure('value')]})
        assert c.execute(query.compile()).fetchone()['value']==14


def test_explicit_row_count_counts_null_records_and_pivots_with_same_predicate():
    value={'id':'rows','alias':'Record count','column':None,'agg':'count'}
    query=shelf.Query(CAT,spec(measures=[value],pivot={'column':col('category')}),allow_row_count=True)
    sql=query.compile(['Office',None])
    assert 'COUNT(*) FILTER (WHERE "category" IS NULL)' in sql
    with pytest.raises(ValueError):shelf.Query(CAT,spec(measures=[value]))
