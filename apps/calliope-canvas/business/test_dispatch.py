import base64
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from server import Store,Rejected
import fieldwork

class Dispatch(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'fixture.sqlite')
    def tearDown(self):self.temp.cleanup()
    def call(self,name,context,selected='HV-1001',**extra):return self.store.action({'view':'dispatch','selected':selected,'name':name,'context':context,'source':'Desktop','requestId':str(uuid.uuid4()),**extra})
    def proposed(self,id='HV-1001'):
        d=self.store.snapshot(id,view='dispatch')['draft'];d.update(date=(dt.date.today()+dt.timedelta(days=1)).isoformat(),start='09:00',end='11:00',reason='Customer requested a later visit.');return d
    def preview(self,d,kind='assign',**kwargs):
        p=self.call('dispatch_preview',{'kind':kind,'draft':d,**kwargs},d['id']);return p['state']['command']
    def confirm(self,d,token,**kwargs):return self.call('dispatch_confirm',{'draft':d,'command':token},d['id'],**kwargs)
    def test_preview_has_no_effect_confirm_replays_and_undo_is_guarded(self):
        d=self.proposed();token=self.preview(d);self.assertEqual(self.store.snapshot('HV-1001',view='dispatch')['draft']['date'],dt.date.today().isoformat())
        request=str(uuid.uuid4());a=self.confirm(d,token,requestId=request);b=self.confirm(d,token,requestId=request);self.assertEqual(a,b);self.assertEqual(a['draft']['start'],'09:00')
        undo=copy.deepcopy(a['draft']);undo['reason']='Customer reverted the request.';u=self.preview(undo,'undo',undo=token);result=self.confirm(undo,u);self.assertEqual(result['draft']['date'],dt.date.today().isoformat())
        with self.assertRaises(Rejected):self.preview(result['draft'],'undo',undo=token)
    def test_overlap_rechecked_at_confirmation(self):
        d=self.proposed();token=self.preview(d)
        with self.store.connect() as db:
            db.execute("UPDATE hvac_jobs SET scheduled_date=?,technician='Alex Morgan',status='Scheduled' WHERE id='HV-1002'",(d['date'],))
            db.execute("UPDATE dispatch_slots SET start='10:00',end='12:00' WHERE job_id='HV-1002'")
        with self.assertRaises(Rejected) as e:self.confirm(d,token)
        self.assertEqual(e.exception.status,409)
        with self.assertRaises(Rejected) as e:self.preview(d)
        self.assertIn('dispatch-start',e.exception.fields)
    def test_changed_input_version_expiry_and_replay_collision(self):
        d=self.proposed();token=self.preview(d);modified={**d,'start':'09:30'}
        with self.assertRaises(Rejected):self.confirm(modified,token)
        with self.store.connect() as db:db.execute('UPDATE dispatch_commands SET expires=? WHERE id=?',('2000-01-01',token))
        with self.assertRaises(Rejected):self.confirm(d,token)
        token=self.preview(d)
        with self.store.connect() as db:db.execute("UPDATE hvac_jobs SET version=version+1 WHERE id='HV-1001'")
        with self.assertRaises(Rejected):self.confirm(d,token)
    def test_duplicate_concurrent_confirm_commits_once(self):
        d=self.proposed();token=self.preview(d);request=str(uuid.uuid4())
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:self.confirm(d,token,requestId=request),range(2)))
        self.assertEqual(results[0],results[1])
        with self.store.connect() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM hvac_activity WHERE description LIKE 'Schedule changed%'").fetchone()[0],1)
    def test_typed_validation_and_release(self):
        d=self.proposed();d['end']='08:00'
        with self.assertRaises(Rejected) as e:self.preview(d)
        self.assertIn('dispatch-end',e.exception.fields)
        d=self.proposed();token=self.preview(d);s=self.confirm(d,token);d=s['draft'];d['reason']='Ready for the technician.';token=self.preview(d,'release');s=self.confirm(d,token)
        self.assertEqual(s['messages'][2]['updateDataModel']['value']['job']['status'],'Dispatched')
        with self.assertRaises(Rejected):self.preview(s['draft'])
    def test_dispatch_release_waits_for_linked_parts(self):
        ordered=self.store.action({'view':'counter','selected':'Q-1043','name':'counter_order','context':{'version':1,'allow_backorder':True},'requestId':'test-order-dispatch'})
        linked=self.store.action({'view':'counter','selected':'Q-1043','name':'counter_visit','context':{'draft':ordered['draft']},'requestId':'test-linked-dispatch'})
        job=linked['messages'][2]['updateDataModel']['value']['visit']['id'];d=self.proposed(job);token=self.preview(d);saved=self.confirm(d,token);d=saved['draft'];d['reason']='Ready to leave.'
        with self.assertRaisesRegex(Rejected,'parts shortage'):self.preview(d,'release')
    def report(self):
        return {'requestId':str(uuid.uuid4()),'draft':{'id':'HV-1001','notes':'Filter replaced.','temperature':'12.5','pressure':'115','attachments':['LOCAL-evidence'],'correction_of':''},'files':[{'name':'reading.txt','mime':'text/plain','data':base64.b64encode(b'Gauge reading verified.').decode()}],'source':'Desktop'}
    def test_report_atomic_evidence_replay_and_linked_correction(self):
        body=self.report();a=fieldwork.submit(self.store,body);b=fieldwork.submit(self.store,body);self.assertEqual(a['id'],b['id']);self.assertTrue(b['duplicate'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM field_reports').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM field_evidence').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT status,version FROM hvac_jobs WHERE id='HV-1001'").fetchone()[:],('Scheduled',1))
        correction=self.report();correction['draft'].update(correction_of=a['id'],notes='Correction: filter cleaned, not replaced.');fieldwork.submit(self.store,correction)
        self.assertEqual(len(self.store.snapshot('HV-1001',view='field')['messages'][2]['updateDataModel']['value']['reports']),2)
        body['draft']['notes']='Different payload'
        with self.assertRaises(Rejected):fieldwork.submit(self.store,body)
    def test_report_rejects_missing_bad_evidence_and_cross_visit_correction(self):
        body=self.report();body['files']=[]
        with self.assertRaises(Rejected):fieldwork.submit(self.store,body)
        body=self.report();body['files'][0]['data']='bad'
        with self.assertRaises(Rejected):fieldwork.submit(self.store,body)
        body=self.report();body['draft']['temperature']='200'
        with self.assertRaises(Rejected) as e:fieldwork.submit(self.store,body)
        self.assertIn('field-temp',e.exception.fields)
        first=fieldwork.submit(self.store,self.report());body=self.report();body['draft'].update(id='HV-1002',correction_of=first['id'])
        with self.assertRaises(Rejected):fieldwork.submit(self.store,body)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM field_reports').fetchone()[0],1)
    def test_resource_contract_rejects_invalid_positions(self):
        from presentation import validate_components
        c=self.store.snapshot(view='dispatch')['messages'][1]['updateComponents']['components'];node=next(x for x in c if x['component']=='CResourceDay');node['positions'][0]['lane']=99
        with self.assertRaises(ValueError):validate_components(c)

if __name__=='__main__':unittest.main()
