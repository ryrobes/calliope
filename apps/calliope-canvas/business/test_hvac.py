import tempfile
import unittest
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from server import Store, Rejected
from hvac import QUERIES

class HVAC(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'test.sqlite')
    def data(self,snapshot): return snapshot['messages'][2]['updateDataModel']['value']
    def action(self,name,context,**extra):
        return self.store.action({'view':'hvac','name':name,'context':context,'requestId':str(uuid.uuid4()),**extra})
    def test_charts_are_exact_sql_results_and_filters_persist(self):
        snap=self.action('hvac_filter',{'technician':['Sam Rivera'],'days':['7']})
        data=self.data(snap)
        self.assertTrue(all(j['technician']=='Sam Rivera' for j in data['jobs']))
        with self.store.connect() as db:
            for key,sql in QUERIES.items():
                expected=[dict(r) for r in db.execute(sql,data['query_params'])]
                self.assertEqual(data['charts'][key]['data']['values'],expected)
        refreshed=self.action('refresh',{},state=snap['state'],selected=snap['selected'])
        self.assertEqual(snap['state'],refreshed['state'])
        self.assertEqual(data['charts'],self.data(refreshed)['charts'])
        self.assertEqual(len(self.data(self.store.snapshot(view='hvac'))['chart_rows']['mix']),9)
    def test_status_mutation_updates_charts_and_is_atomic_on_retry(self):
        before=self.store.snapshot(view='hvac')
        data=self.data(before)
        body={'view':'hvac','name':'hvac_advance','context':before['draft'],'requestId':str(uuid.uuid4()),'source':'Desktop'}
        with ThreadPoolExecutor(2) as pool: results=list(pool.map(self.store.action,[body,body]))
        self.assertEqual(results[0],results[1])
        after=results[0]
        self.assertEqual(after['revision'],before['revision']+1)
        self.assertEqual(after['draft']['version'],2)
        self.assertEqual(self.data(after)['job']['status'],'Dispatched')
        self.assertNotEqual(self.data(after)['charts']['status'],data['charts']['status'])
        with self.assertRaises(Rejected) as e: self.action('save_deal',{**before['draft'],'notes':'stale'})
        self.assertEqual(e.exception.status,409)
        self.assertEqual(self.store.revision(),after['revision'])
    def test_completion_changes_week_value_notes_persist_and_domains_stay_separate(self):
        estate=self.store.snapshot()
        snap=self.store.snapshot(selected='HV-1003',view='hvac')
        before=sum(r['invoice_value'] for r in self.data(snap)['chart_rows']['revenue'])
        price=self.data(snap)['job']['amount_cents']/100
        done=self.action('hvac_advance',snap['draft'])
        after=sum(r['invoice_value'] for r in self.data(done)['chart_rows']['revenue'])
        self.assertAlmostEqual(after-before,price)
        saved=self.action('save_deal',{**done['draft'],'notes':'Filter replaced. Readings recorded.'},source='Web')
        self.assertEqual(Store(self.store.path).snapshot('HV-1003',view='hvac')['draft'],saved['draft'])
        back=self.action('set_view',{'view':'dossier'},selected='HV-1003')
        self.assertEqual(back['selected'],'cedar')
        self.assertEqual(self.store.snapshot()['draft'],estate['draft'])
    def test_invalid_filter_and_chart_binding_are_rejected(self):
        before=self.store.revision()
        for context in [{'technician':["' OR 1=1"],'days':['7']},{'technician':['Everyone'],'days':['0']},{'technician':[],'days':['7']},{'technician':['Everyone'],'days':['']}]:
            with self.assertRaises(Rejected): self.action('hvac_filter',context)
        from presentation import validate_components
        for spec in [{'path':'/draft'},{'url':'https://example.com'}]:
            with self.assertRaises(ValueError): validate_components([{'id':'root','component':'CVegaChart','spec':spec,'description':'Invalid'}])
        self.assertEqual(self.store.revision(),before)

    def test_chart_clicks_drill_into_matching_sql_visits_and_clear(self):
        before=self.store.snapshot(view='hvac')
        for chart,datum in [('status',{'status':'Complete'}),('mix',{'technician':'Sam Rivera','job_type':'Repair'}),('revenue',self.data(before)['chart_rows']['revenue'][0])]:
            snap=self.action('hvac_chart_select',{'componentId':'chart-'+chart,'datum':datum})
            rows=self.data(snap)['jobs']
            self.assertTrue(rows)
            if chart=='status': self.assertTrue(all(r['status']=='Complete' for r in rows))
            if chart=='mix': self.assertTrue(all(r['technician']=='Sam Rivera' and r['job_type']=='Repair' for r in rows))
            if chart=='revenue':
                import datetime as dt
                week=dt.date.fromisoformat(datum['week_label'])
                self.assertTrue(all(r['status']=='Complete' and week<=dt.date.fromisoformat(r['scheduled_date'])<week+dt.timedelta(days=7) for r in rows))
            refreshed=self.action('refresh',{},state=snap['state'],selected=snap['selected'])
            self.assertEqual(snap['state'],refreshed['state'])
            cleared=self.action('hvac_chart_clear',{},state=snap['state'])
            self.assertNotIn('chart_selection',cleared['state'])
        self.assertEqual(before['revision'],self.store.revision())

    def test_chart_clicks_reject_unknown_and_stale_values(self):
        for context in [
            {'componentId':'not-a-chart','datum':{}},
            {'componentId':'chart-status','datum':{'status':"' OR 1=1"}},
            {'componentId':'chart-revenue','datum':{'week_label':'1900-01-01'}},
            {'componentId':'chart-revenue','datum':{'week_label':None}},
            {'componentId':'chart-mix','datum':{'technician':'Alex Morgan','job_type':'Repair'}},
        ]:
            with self.assertRaises(Rejected):
                self.action('hvac_chart_select',context,state={'technician':'Sam Rivera','days':7})
