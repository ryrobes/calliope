import base64,copy,tempfile,unittest,uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from server import Store,Rejected
import attachments,receiving

class Receiving(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=Store(Path(self.tmp.name)/'demo.sqlite')
    def snap(self,id='PO-2001'):return self.store.snapshot(id,view='receiving')
    def data(self,s):return s['messages'][2]['updateDataModel']['value']
    def call(self,name,s,context=None,**extra):return self.store.action({'name':name,'view':s['view'],'selected':s['selected'],'context':context if context is not None else {'draft':s['draft']},'requestId':str(uuid.uuid4()),'panes':s.get('panes',[]),**extra})
    def delivery(self,s,quantity='1'):
        s=copy.deepcopy(s);s['draft']['reference']='SUP-DEL-2001';s['draft']['lines'][s['selected']+'-0']['quantity']=quantity;return s
    def backorder(self):
        s=self.store.snapshot('Q-1043',view='counter');s=self.call('counter_order',s,{'version':s['draft']['version'],'allow_backorder':True});return self.call('counter_visit',s)
    def test_partial_fifo_branch_isolation_full_completion_and_live_visit(self):
        order=self.backorder();order_id=self.data(order)['order']['id'];visit=self.data(order)['visit']['id']
        second=self.call('counter_new',order,{'customer':['cedar']});second=self.call('counter_add',second,{'draft':second['draft'],'product':['CAP-45-5']});second['draft']['lines'][next(iter(second['draft']['lines']))]['quantity']='2';second=self.call('counter_save',second);second=self.call('counter_order',second,{'version':second['draft']['version'],'allow_backorder':True});second_id=self.data(second)['order']['id']
        with self.store.connect() as db:
            db.execute("UPDATE counter_orders SET created='2026-01-01' WHERE id=?",(order_id,));db.execute("UPDATE counter_orders SET created='2026-01-02' WHERE id=?",(second_id,));before=dict(db.execute("SELECT * FROM counter_stock WHERE branch='south' AND product_id='CAP-45-5'").fetchone())
            self.assertGreater(receiving.readiness(db,visit)['shortage'],0)
        s=self.call('receiving_post',self.delivery(self.snap(),'2'))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT backorder FROM counter_order_lines WHERE order_id=? AND product_id=?',(order_id,'CAP-45-5')).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT backorder FROM counter_order_lines WHERE order_id=? AND product_id=?',(second_id,'CAP-45-5')).fetchone()[0],100)
            self.assertEqual(receiving.readiness(db,visit)['shortage'],0)
            self.assertEqual(dict(db.execute("SELECT * FROM counter_stock WHERE branch='south' AND product_id='CAP-45-5'").fetchone()),before)
            self.assertEqual(db.execute('SELECT received FROM receiving_lines WHERE id=?',('PO-2001-0',)).fetchone()[0],200)
        self.assertTrue(any(n.get('text')=='All parts reserved' for n in self.store.snapshot(visit,view='hvac')['messages'][1]['updateComponents']['components']))
        s=self.delivery(s,'8')
        for id,n in [('PO-2001-1','3'),('PO-2001-2','20'),('PO-2001-3','100')]:s['draft']['lines'][id]['quantity']=n
        s=self.call('receiving_post',s)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT SUM(expected-received) FROM receiving_lines WHERE po_id='PO-2001'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM receiving_receipts').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT backorder FROM counter_order_lines WHERE order_id=? AND product_id=?',(second_id,'CAP-45-5')).fetchone()[0],0)
    def test_replay_is_exactly_once_and_stale_draft_is_rejected(self):
        s=self.delivery(self.snap());body={'name':'receiving_post','view':'receiving','selected':s['selected'],'context':{'draft':s['draft']},'requestId':str(uuid.uuid4())}
        with ThreadPoolExecutor(2) as pool:a,b=pool.map(self.store.action,[body,body])
        self.assertEqual(a,b)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM receiving_receipts').fetchone()[0],1)
        with self.assertRaises(Rejected) as err:self.call('receiving_post',s)
        self.assertEqual(err.exception.status,409)
        body['context']['draft']['reference']='different'
        with self.assertRaisesRegex(Rejected,'already used'):self.store.action(body)
    def test_field_errors_and_rejections_leave_no_partial_stock_changes(self):
        s=self.delivery(self.snap());before=self.data(s)['receiving_lines']
        for key,value in [('reference',''),('received_date','2026-02-30'),('received_time','25:61'),('has_damage','yes')]:
            invalid=copy.deepcopy(s);invalid['draft'][key]=value
            with self.assertRaises(Rejected):self.call('receiving_post',invalid)
        for quantity in ['', '11','1.5','NaN','-1','1e2']:
            with self.assertRaises(Rejected) as err:self.call('receiving_post',self.delivery(s,quantity))
            self.assertEqual(err.exception.status,422);self.assertIn('receive-PO-2001-0',err.exception.fields)
        s['draft']['has_damage']=True
        with self.assertRaises(Rejected) as err:self.call('receiving_post',s)
        self.assertIn('receipt-damage-notes',err.exception.fields)
        s['draft']['damage_notes']='Box damaged; received usable units only.';s['draft']['attachments']=['ATT-missing']
        with self.assertRaises(Rejected):self.call('receiving_post',s)
        self.assertEqual(self.data(self.snap())['receiving_lines'],before)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM receiving_receipts').fetchone()[0],0)
    def test_delivery_evidence_is_bounded_and_linked_to_immutable_receipt(self):
        upload=lambda **kw:attachments.upload(self.store,{'name':'delivery.txt','mime':'text/plain','data':base64.b64encode(b'Delivery 001: one usable capacitor.').decode(),**kw})
        for overrides in [{'name':'../escape.txt'},{'mime':'text/html'},{'mime':'image/png'},{'data':'bad-base64'},{'data':base64.b64encode(b'x'*(attachments.MAX_BYTES+1)).decode()}]:
            with self.assertRaises(Rejected):upload(**overrides)
        a=upload();s=self.delivery(self.snap());s['draft']['attachments']=[a['id']];posted=self.call('receiving_post',s)
        record=attachments.get(self.store,a['id']);self.assertTrue(record['receipt_id']);self.assertEqual(record['content'],b'Delivery 001: one usable capacitor.')
        reused=self.delivery(posted);reused['draft']['attachments']=[a['id']]
        with self.assertRaisesRegex(Rejected,'already belongs'):self.call('receiving_post',reused)
        pane=self.call('open_record',posted,{'kind':'receipt','recordId':record['receipt_id']});self.assertTrue(any(n.get('text')=='Posted · immutable' for n in pane['messages'][1]['updateComponents']['components']))
    def test_bounded_sql_search_sort_page_and_record_navigation(self):
        s=self.snap();state={'search':'','branch':'all','status':'open','sort':'supplier','page':0}
        with self.store.connect() as db:
            rows,total=receiving.queue(db,state);self.assertEqual(total,9);self.assertEqual(len(rows),5);self.assertEqual([r['supplier'] for r in rows],sorted(r['supplier'] for r in rows))
            state['page']=1;rows,total=receiving.queue(db,state);self.assertEqual(len(rows),4)
            state.update(search='%');rows,total=receiving.queue(db,state);self.assertEqual(total,0)
        for bad in [{'sort':'p.id; DROP TABLE receiving_orders'},{'page':-1},{'page':'2'},{'search':'x'*101}]:
            with self.assertRaises(ValueError):receiving.filters(bad)
        pane=self.call('open_record',s,{'kind':'customer','recordId':'cedar'});self.assertEqual(pane['selected'],s['selected']);self.assertEqual(pane['draft'],s['draft']);self.assertEqual(len(pane['panes']),1)
        pane=self.call('open_record',pane,{'kind':'equipment','recordId':'EQ-CEDAR-2'});self.assertEqual(len(pane['panes']),2)
        pane=self.call('back_record',pane,{});self.assertEqual(len(pane['panes']),1)
        pane=self.call('close_record',pane,{});self.assertEqual(pane['panes'],[])
        components=pane['messages'][1]['updateComponents']['components'];self.assertEqual(sum(n['id']=='root' for n in components),1);self.assertFalse(any(n['id']=='record-pane' for n in components))
    def test_invalid_inspector_does_not_commit_a_receipt_before_returning_an_error(self):
        s=self.delivery(self.snap())
        with self.assertRaises(Rejected):self.call('receiving_post',s,panes=[{'kind':'order','id':'missing-order'}])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM receiving_receipts').fetchone()[0],0)
