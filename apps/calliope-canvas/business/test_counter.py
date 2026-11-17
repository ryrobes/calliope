import copy
import datetime as dt
import tempfile
import unittest
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from server import Store, Rejected

class PartsDesk(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'test.sqlite')
    def data(self,s): return s['messages'][2]['updateDataModel']['value']
    def snap(self,id='Q-1043'): return self.store.snapshot(id,view='counter')
    def call(self,name,s,context=None,**extra):
        return self.store.action({'view':'counter','selected':s['selected'],'name':name,'context':context or {'draft':s['draft']},'requestId':str(uuid.uuid4()),**extra})
    def stock(self,branch='north',sku='CAP-45-5'):
        with self.store.connect() as db: return dict(db.execute('SELECT * FROM counter_stock WHERE branch=? AND product_id=?',(branch,sku)).fetchone())
    def test_edit_rounding_validation_rollback_and_branch_lookup(self):
        s=self.snap(); original=copy.deepcopy(s['draft']); first=list(s['draft']['lines'])[0]
        s['draft']['lines'][first]['quantity']='2';s['draft']['lines'][first]['unit_price']='24.51';s['draft']['branch']=['south']
        saved=self.call('counter_save',s);self.assertEqual(saved['draft']['branch'],['south'])
        self.assertEqual(self.data(saved)['totals'],{'subtotal':18252,'tax':1186,'total':19438})
        for bad in ['', '-1','2.345','1e3','NaN','10000','1.5','0']:
            broken=copy.deepcopy(saved); broken['draft']['lines'][first]['quantity']=bad
            with self.assertRaises(Rejected): self.call('counter_save',broken)
            self.assertEqual(self.snap()['draft'],saved['draft'])
        stale={**original,'notes':'stale'}
        with self.assertRaises(Rejected) as e: self.call('counter_save',saved,{'draft':stale})
        self.assertEqual(e.exception.status,409)
        broken=copy.deepcopy(saved); keys=list(broken['draft']['lines']);broken['draft']['lines'][keys[0]]['quantity']='9';broken['draft']['lines'][keys[-1]]['unit_price']='oops'
        with self.assertRaises(Rejected):self.call('counter_save',broken)
        self.assertEqual(self.snap()['draft'],saved['draft'])
    def test_new_add_fractional_remove_and_empty_order(self):
        s=self.call('counter_new',self.snap(),{'customer':['oak']});self.assertEqual(s['draft']['lines'],{})
        with self.assertRaises(Rejected):self.call('counter_order',s,{'version':s['draft']['version']})
        s=self.call('counter_add',s,{'draft':s['draft'],'product':['TUBE-38']});line=next(iter(s['draft']['lines']))
        s['draft']['lines'][line]['quantity']='1.25';s=self.call('counter_save',s)
        self.assertEqual(self.data(s)['totals'],{'subtotal':531,'tax':35,'total':566})
        for sku in ['unknown',"' OR 1=1"]:
            with self.assertRaises(Rejected):self.call('counter_add',s,{'draft':s['draft'],'product':[sku]})
        with self.assertRaises(Rejected): self.call('counter_remove',s,{'draft':s['draft'],'line_id':'Q-1043-0'})
        s=self.call('counter_remove',s,{'draft':s['draft'],'line_id':line});self.assertEqual(s['draft']['lines'],{})
    def test_shortage_explicit_backorder_retry_and_locked_prices(self):
        s=self.snap(); before=self.stock()
        with self.assertRaisesRegex(Rejected,'Stock is short'):self.call('counter_order',s,{'version':1})
        self.assertEqual(self.stock(),before)
        body={'view':'counter','selected':s['selected'],'name':'counter_order','context':{'version':1,'allow_backorder':True},'requestId':str(uuid.uuid4())}
        with ThreadPoolExecutor(2) as pool: a,b=pool.map(self.store.action,[body,body])
        self.assertEqual(a,b);self.assertEqual(self.stock()['reserved'],300)
        with self.store.connect() as db:
            r=dict(db.execute("SELECT * FROM counter_order_lines WHERE product_id='CAP-45-5'").fetchone());self.assertEqual((r['quantity'],r['reserved'],r['backorder']),(400,300,100))
        with self.assertRaisesRegex(Rejected,'read-only'): self.call('counter_save',a)
        with self.assertRaisesRegex(Rejected,'already has an order'):self.call('counter_order',a,{'version':2})
        self.assertEqual(self.data(self.snap())['totals'],self.data(s)['totals'])
    def test_competing_quotes_never_oversell_and_branch_change_reserves_correct_stock(self):
        a,b=self.snap(),self.snap('Q-1042'); a['draft']['branch']=['south'];a=self.call('counter_save',a)
        a=self.call('counter_order',a,{'version':a['draft']['version']});self.assertEqual(self.stock('south')['reserved'],400);self.assertEqual(self.stock()['reserved'],0)
        c=self.call('counter_new',b,{'customer':['cedar']});c=self.call('counter_add',c,{'draft':c['draft'],'product':['CAP-45-5']})
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda s:self.call('counter_order',s,{'version':s['draft']['version'],'allow_backorder':True}),[b,c]))
        self.assertEqual(self.stock()['reserved'],300)
        with self.store.connect() as db:
            r=db.execute("SELECT SUM(ol.quantity),SUM(ol.reserved),SUM(ol.backorder) FROM counter_order_lines ol JOIN counter_orders o ON o.id=ol.order_id WHERE o.branch='north' AND product_id='CAP-45-5'").fetchone()
            self.assertEqual(tuple(r),(500,300,200))
    def test_linked_visit_validates_and_opens_both_directions(self):
        s=self.call('counter_order',self.snap(),{'version':1,'allow_backorder':True});count=0
        with self.store.connect() as db:count=db.execute('SELECT COUNT(*) FROM hvac_jobs').fetchone()[0]
        for date in ['', '2026-02-30',(dt.date.today()-dt.timedelta(days=1)).isoformat()]:
            bad=copy.deepcopy(s);bad['draft']['visit_date']=date
            with self.assertRaises(Rejected):self.call('counter_visit',bad)
        bad=copy.deepcopy(s);bad['draft']['technician']=['Everyone']
        with self.assertRaises(Rejected):self.call('counter_visit',bad)
        linked=self.call('counter_visit',s);data=self.data(linked);visit=data['visit'];self.assertIn(data['order']['id'],visit['notes']);self.assertIn('backorder',visit['notes']);self.assertEqual(visit['amount_cents'],0);self.assertIn(data['equipment']['serial'],visit['equipment'])
        field=self.call('set_view',linked,{'view':'hvac','id':visit['id']});self.assertEqual(field['selected'],visit['id'])
        jobs=self.data(field)['jobs'];self.assertEqual(len(jobs),8);self.assertTrue(any(j['id']=='HV-1001' for j in jobs))
        components=field['messages'][1]['updateComponents']['components'];back=next(c for c in components if c['id']=='visit-parts')['action']['event']
        returned=self.store.action({**back,'view':'hvac','selected':visit['id']});self.assertEqual(returned['selected'],s['selected'])
        with self.assertRaisesRegex(Rejected,'already has a linked visit'):self.call('counter_visit',linked)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM hvac_jobs').fetchone()[0],count+1)
    def test_equipment_is_scoped_to_customer_and_saved_relationship_follows_visit(self):
        s=self.snap();s['draft']['equipment']=['EQ-HARBOR-1']
        with self.assertRaisesRegex(Rejected,'equipment for this customer'):self.call('counter_save',s)
        s['draft']['equipment']=['EQ-CEDAR-2'];s=self.call('counter_save',s)
        self.assertEqual(self.data(s)['equipment']['id'],'EQ-CEDAR-2')
        s=self.call('counter_order',s,{'version':s['draft']['version'],'allow_backorder':True});s=self.call('counter_visit',s)
        visit=self.data(s)['visit'];field=self.store.snapshot(visit['id'],view='hvac')
        self.assertEqual(self.data(field)['equipment']['id'],'EQ-CEDAR-2')
        self.assertIn('Air handler',visit['equipment'])
    def test_quote_lookup_retains_older_records(self):
        s=self.snap()
        for _ in range(5):s=self.call('counter_new',s,{'customer':['cedar']})
        self.assertTrue(any(n['id']=='all-quotes' for n in s['messages'][1]['updateComponents']['components']))
        old=self.call('counter_select',s,{'quote':['Q-1042']});self.assertEqual(old['selected'],'Q-1042')
    def test_grid_and_picker_contract_rejects_malformed_records(self):
        from presentation import validate_components
        for component in [
          {'component':'CDataGrid','columns':[],'children':[]},
          {'component':'CDataGrid','columns':[{'label':'A'},{'label':'B'}],'children':['x']},
          {'component':'CRecordPicker','label':'Bad','value':{'path':'/draft'},'options':[{'label':'A','value':'a'},{'label':'B','value':'a'}]},
        ]:
            with self.assertRaises(ValueError):validate_components([{'id':'root',**component}])
