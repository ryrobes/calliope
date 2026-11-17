import copy
import datetime as dt
import tempfile
import unittest
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from server import Store, Rejected
import cases
from reactive import compose
from studio_document import normalize


class Cases(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'case.sqlite')
    def snap(self, **kw):return self.store.snapshot('CASE-HARBOR',view='case',**kw)
    def data(self,s):return s['messages'][2]['updateDataModel']['value']
    def call(self,name,context=None,**kw):
        return self.store.action({'view':'case','selected':'CASE-HARBOR','name':name,'context':context or {},'requestId':str(uuid.uuid4()),**kw})
    def delivery(self):
        d=self.snap()['draft']['receipt'];d['reference']='DELIVERY-CASE-001';d['lines']['PO-CASE-01-0']['quantity']='1';return d
    def receive(self):
        d=self.delivery();p=self.call('case_receipt_preview',{'draft':d})
        return self.call('case_receive_confirm',{'draft':d,'review':p['state']['receipt_review']})
    def appointment(self):
        d=self.snap()['draft']['appointment'];d.update(start='14:00',end='16:00',reason='Motor reserved; return to finish repair.');return d
    def test_complete_workflow_uses_real_stock_allocation_and_review(self):
        self.assertEqual(self.data(self.snap())['status']['phase'],'waiting')
        with self.assertRaisesRegex(Rejected,'shortage'):self.call('case_schedule_preview',{'draft':self.appointment()})
        d=self.delivery();p=self.call('case_receipt_preview',{'draft':d})
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM receiving_receipts').fetchone()[0],0)
            self.assertEqual(db.execute("SELECT reserved FROM counter_stock WHERE product_id='MOTOR-ECM' AND branch='north'").fetchone()[0],0)
        result=self.call('case_receive_confirm',{'draft':d,'review':p['state']['receipt_review']})
        data=self.data(result);self.assertEqual(data['status']['phase'],'ready');self.assertEqual(data['allocations'][0]['quantity'],100)
        self.assertEqual(result['acknowledgedDraftPaths'],['/receipt'])
        d=self.appointment();p=self.call('case_schedule_preview',{'draft':d})
        self.assertIsNone(self.data(self.snap())['status']['job']['start'])
        done=self.call('case_schedule_confirm',{'draft':d,'command':p['state']['command']})
        self.assertEqual(self.data(done)['status']['phase'],'scheduled');self.assertEqual(done['acknowledgedDraftPaths'],['/appointment'])
        self.assertEqual(self.data(done)['status']['job']['status'],'Scheduled')
        # Restart does not manufacture a second case or reset the workflow.
        self.assertEqual(self.data(Store(self.store.path).snapshot(view='case'))['status']['phase'],'scheduled')
    def test_receipt_replay_is_atomic_and_changed_input_rejected(self):
        d=self.delivery();p=self.call('case_receipt_preview',{'draft':d});ctx={'draft':d,'review':p['state']['receipt_review']}
        altered=copy.deepcopy(ctx);altered['draft']['reference']='Changed after review'
        with self.assertRaises(Rejected):self.call('case_receive_confirm',altered)
        rid=str(uuid.uuid4())
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:self.call('case_receive_confirm',ctx,requestId=rid),range(2)))
        self.assertEqual(results[0],results[1])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM receiving_receipts').fetchone()[0],1)
        with self.assertRaises(Rejected):self.call('case_receive_confirm',ctx)
    def test_confirmation_rechecks_parts_and_schedule_after_preview(self):
        self.receive();d=self.appointment();p=self.call('case_schedule_preview',{'draft':d});ctx={'draft':d,'command':p['state']['command']}
        with self.store.connect() as db:
            db.execute("UPDATE counter_order_lines SET reserved=0,backorder=quantity WHERE order_id='SO-CASE-01'")
        with self.assertRaisesRegex(Rejected,'shortage'):self.call('case_schedule_confirm',ctx)
        with self.store.connect() as db:
            db.execute("UPDATE counter_order_lines SET reserved=quantity,backorder=0 WHERE order_id='SO-CASE-01'")
            db.execute("UPDATE hvac_jobs SET scheduled_date=?,technician='Sam Rivera',status='Scheduled' WHERE id='HV-1001'",(d['date'],))
            db.execute("UPDATE dispatch_slots SET start='15:00',end='17:00' WHERE job_id='HV-1001'")
        with self.assertRaisesRegex(Rejected,'schedule changed'):self.call('case_schedule_confirm',ctx)
    def test_expired_or_other_case_receipt_and_stale_po_rejected(self):
        d=self.delivery();p=self.call('case_receipt_preview',{'draft':d});ctx={'draft':d,'review':p['state']['receipt_review']}
        with self.store.connect() as db:db.execute("UPDATE receiving_orders SET version=version+1 WHERE id='PO-CASE-01'")
        with self.assertRaises(Rejected):self.call('case_receive_confirm',ctx)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM receiving_receipts').fetchone()[0],0)
        d=self.delivery();d['id']='PO-2001'
        with self.assertRaises(Rejected):self.call('case_receipt_preview',{'draft':d})
        d=self.delivery();p=self.call('case_receipt_preview',{'draft':d})
        with self.store.connect() as db:db.execute("UPDATE work_case_receipt_reviews SET expires='2000-01-01'")
        with self.assertRaises(Rejected):self.call('case_receive_confirm',{'draft':d,'review':p['state']['receipt_review']})
    def test_preferences_notes_context_and_pins_survive_restart(self):
        s=self.snap();d=s['draft'];d['notes']='We verified the replacement.';d['handoff']='Side gate access.'
        self.call('case_save',{'draft':d});self.call('case_preferences',{'focus':['equipment'],'role':['technician']});self.call('case_history',{'value':True});self.call('case_pin',{'value':False})
        restored=self.data(Store(self.store.path).snapshot(view='case'))
        self.assertEqual(restored['draft']['notes'],d['notes']);self.assertEqual(restored['case']['focus'],'equipment');self.assertEqual(restored['case']['history'],1)
        self.assertFalse(cases.summaries(self.store)[0]['pinned']);self.assertEqual(restored['context']['scope'],'fictional-local-business-desk')
        self.assertNotIn('review_return_visit',restored['context']['allowed_actions'])
        with self.assertRaises(Rejected):self.call('case_save',{'draft':d})
    def test_all_compositions_and_inspector_survive_reactive_reconciliation(self):
        for mode in ('overview','receipt','schedule'):
            s=self.call('case_tool',{'mode':mode})
            s=self.call('open_record',{'kind':'equipment','recordId':'EQ-HARBOR-1'},state=s['state'])
            data=self.data(s)
            for label in ('equipment','parts','schedule'):
                trial=copy.deepcopy(data);trial['resources']['support']['label']=label
                components=compose(s['baseComponents'],s['reactive'],trial['resources'])
                normalize({'components':components,'data':trial})
                self.assertTrue(any(n['id']=='record-pane' for n in components))
                self.assertEqual(next(n for n in components if n['id']=='case-handoff')['value'],{'path':'/draft/handoff'})
    def test_failed_response_rolls_back_delegated_mutation(self):
        from unittest.mock import patch
        d=self.delivery();p=self.call('case_receipt_preview',{'draft':d})
        with patch('cases.snapshot',side_effect=ValueError('Broken definition')):
            with self.assertRaises(Rejected):self.call('case_receive_confirm',{'draft':d,'review':p['state']['receipt_review']})
        self.assertEqual(self.data(self.snap())['status']['phase'],'waiting')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM receiving_receipts').fetchone()[0],0)


if __name__=='__main__':unittest.main()
