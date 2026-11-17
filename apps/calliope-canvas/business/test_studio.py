import copy
import json
from pathlib import Path
import tempfile
import unittest
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

from server import Store
from presentation import VIEWS
from studio_document import normalize
from studio_service import run, reap


def document():
    return {'components':[
        {'id':'name','component':'TextField','label':'Name','value':{'path':'/draft/name'}},
        {'id':'label','component':'CText','text':'Save'},
        {'id':'save','component':'Button','child':'label','action':{'event':{'name':'save_record','context':{'name':{'path':'/draft/name'}}}}},
        {'id':'root','component':'CStack','children':['name','save']},
    ],'data':{'draft':{'name':'Initial'}}}


class Documents(unittest.TestCase):
    def test_record_link_preview_resolves_identity_without_navigating_or_mutating(self):
        import studio
        ref={'slug':'field-visits','record':'visit','id':'HV-1002'}
        doc={'schema':'calliope.app.v1','title':'Dispatch','components':[
            {'id':'visit','component':'CRecordLink','label':'Open visit','reference':{'path':'/target'}},
            {'id':'root','component':'CStack','children':['visit']}],
            'data':{'target':ref,'state':{'note':'Unfinished dispatch note'}}}
        with tempfile.TemporaryDirectory() as work:
            directory=Path(work)
            studio.write_json(directory/'snapshot.json',normalize(doc))
            result=studio.operate(directory,{'native':False},'press','visit')
            self.assertEqual(result['events'][-1],{'name':'open_record','context':{'componentId':'visit','reference':ref},'simulated':True})
            self.assertEqual(result['surface']['data']['state']['note'],'Unfinished dispatch note')

    def test_browser_link_preview_retains_draft_and_only_records_simulated_event(self):
        import studio
        doc = {'schema':'calliope.app.v1','title':'Link test','components':[
            {'id':'analysis','component':'CLink','label':'Open analysis','href':'/d/visits'},
            {'id':'root','component':'CStack','children':['analysis']},
        ],'data':{'state':{'draft':{'notes':'Keep this note'}}}}
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            studio.write_json(directory/'snapshot.json', normalize(doc))
            state = studio.operate(directory, {'native':False}, 'press', 'analysis')
            self.assertEqual(state['events'], [{'name':'open_link','context':{'componentId':'analysis','href':'/d/visits'},'simulated':True}])
            self.assertEqual(state['surface']['data']['state']['draft']['notes'], 'Keep this note')

    def test_published_definition_keeps_composition_and_simulates_real_inputs(self):
        import studio
        source = Path(__file__).resolve().parents[3]/'services/calliope/examples/ui-apps/hvac-visit.app.json'
        doc = json.loads(source.read_text())
        snapshot = normalize(doc)
        self.assertEqual(snapshot['previewDefinition'], doc)
        self.assertGreater(len(studio.nodes(snapshot)[0]), len(doc['components']))
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            studio.write_json(directory/'snapshot.json', snapshot)
            state = studio.operate(directory, {'native':False}, 'fill', 'notes', 'A new test note')
            self.assertEqual(state['surface']['data']['state']['draft']['notes'], 'A new test note')
            state = studio.operate(directory, {'native':False}, 'press', 'save')
            event = state['events'][-1]
            self.assertTrue(event['simulated'])
            self.assertEqual(event['inputs']['draft']['notes'], 'A new test note')
            self.assertEqual(state['surface']['data']['state']['draft']['notes'], 'A new test note')

    def test_capture_receipt_waits_for_measured_charts_and_exposes_errors(self):
        from studio import render_receipt
        state={'render':{'charts':{'chart':{'width':400,'pending':True,'ready':False}}}}
        self.assertFalse(render_receipt(state)['ready'])
        state['render']['charts']['chart'].update(pending=False,ready=True)
        self.assertTrue(render_receipt(state)['ready'])
        state['render']['charts']['chart'].update(ready=False,error='Conversion failed')
        self.assertEqual(render_receipt(state)['chart_errors'],{'chart':'Conversion failed'})
    def test_all_eight_fixture_compositions_can_be_remixed(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/'fixture.sqlite')
            for view in VIEWS:
                self.assertEqual(normalize(s.snapshot(view=view))['view'],'studio')

    def test_missing_binding_and_unknown_builtin_capability(self):
        for change in ('binding','url'):
            d=document()
            if change=='binding': d['components'][0]['value']={'path':'/missing'}
            else: d['components'][2]['action']={'url':'file:///etc/passwd'}
            with self.assertRaises(ValueError): normalize(d)

    def test_cycles_duplicate_ids_orphans_and_shared_children(self):
        for change in ('cycle','duplicate','orphan','shared'):
            d=document()
            if change=='cycle': d['components'][-1]['children'].append('root')
            elif change=='duplicate': d['components'].append(copy.deepcopy(d['components'][0]))
            elif change=='orphan': d['components'].append({'id':'orphan','component':'CText','text':'unused'})
            else: d['components'][-1]['children'].append('label')
            with self.assertRaises(ValueError): normalize(d)

    def test_chart_network_code_and_nonfinite_rejected(self):
        from studio_document import chart
        for spec in ({'data':{'values':[],'url':'http://example.test'}}, {'data':{'values':[]},'mark':'image'}):
            with self.assertRaises(ValueError): chart(spec)
        d=document(); d['data']['bad']=float('nan')
        with self.assertRaises(ValueError): normalize(d)

    def test_malformed_and_oversized_documents(self):
        for d in ([],{}, {'components':[None],'data':{}}, {'components':[{'id':'root','component':'Unknown'}],'data':{}}, {'data':{'text':'x'*1_500_000},'components':[]}):
            with self.assertRaises(ValueError): normalize(d)


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
    def tearDown(self):
        from studio_service import stop
        for path in self.root.iterdir():
            if path.is_dir(): stop(path)
        self.tmp.cleanup()
    def command(self,op,**args): return run({'op':op,'session':'test',**args},self.root)

    def test_starter_live_filters_record_only_changes_and_preserve_drafts(self):
        catalog=run({'op':'catalog'},self.root)
        self.assertIn('CSection',catalog['catalog']['components'])
        self.assertTrue(run({'op':'check','document':catalog['starter']},self.root)['valid'])
        self.assertEqual(catalog['starter']['schema'], 'calliope.app.v1')
        self.command('start');self.command('load',document=catalog['legacyFixtureStarter'])
        self.command('fill',id='notes',value='Keep my local note')
        self.command('fill',id='search',value='Harbor')
        self.command('fill',id='search',value='Harbor')
        state=self.command('inspect')
        self.assertEqual(state['events'],[{'name':'work_filter','context':{'search':'Harbor','status':['open']}}])
        self.assertEqual(next(n for n in state['nodes'] if n['id']=='notes')['value'],'Keep my local note')
        for change in ('command','shape','delay'):
            d=copy.deepcopy(catalog['legacyFixtureStarter']);bar=next(n for n in d['components'] if n['component']=='CFilterBar')
            if change=='command':bar['onChange']='save_notes'
            if change=='shape':d['data']['filters']='not an object'
            if change=='delay':bar['debounceMs']=0
            with self.assertRaises(ValueError):normalize(d)

    def test_fixture_filters_query_without_apply_and_preserve_notes(self):
        self.command('start');self.command('press',id='view-receiving')
        self.command('fill',id='receipt-notes',value='Keep delivery notes')
        self.command('fill',id='receiving-search',value='nonexistent order')
        state=self.command('inspect')
        self.assertEqual(next(n for n in state['nodes'] if n['id']=='receipt-notes')['value'],'Keep delivery notes')
        self.assertTrue(any(n['id']=='receiving-empty' for n in state['nodes']))
        self.assertFalse(any(n['id'].startswith('select-PO-') for n in state['nodes']))

    def test_generated_definition_events_and_binding_parity(self):
        self.command('start')
        self.command('load',document=document())
        self.command('fill',id='name',value='Edited')
        self.command('press',id='save')
        state=self.command('inspect')
        self.assertEqual(state['events'],[{'name':'save_record','context':{'name':'Edited'}}])
        self.assertEqual(next(n for n in state['nodes'] if n['id']=='name')['value'],'Edited')
        result=self.command('scenario',steps=[{'op':'assert_event','equals':state['events'][0]}])
        self.assertTrue(result['passed'])
        self.command('stop'); self.assertFalse((self.root/'test').exists())

    def test_fixture_scenario_mutates_only_disposable_store(self):
        self.command('start')
        steps=json.loads((Path(__file__).parent/'scenarios/dispatch.json').read_text())
        self.assertTrue(self.command('scenario',steps=steps)['passed'])
        self.assertTrue((self.root/'test/fixture.sqlite').is_file())

    def test_bad_session_unknown_operation_and_scenario_fail(self):
        with self.assertRaises(ValueError): run({'op':'start','session':'../escape'},self.root)
        self.command('start')
        with self.assertRaises(ValueError): self.command('scenario',steps=[{'op':'shell','value':'id'}])
        with self.assertRaises(ValueError): self.command('scenario',steps=[{'op':'assert','id':'dispatch-start','property':'value','equals':'wrong'}])
        with self.assertRaises(ValueError): self.command('exec')

    def test_expiry_reclaims_fixture(self):
        self.command('start')
        reap(self.root,now=(self.root/'test').stat().st_mtime+3601)
        self.assertFalse((self.root/'test').exists())

    def test_example_and_check_require_no_session(self):
        example=run({'op':'example','view':'field'},self.root)
        self.assertTrue(run({'op':'check','document':example},self.root)['valid'])
        self.assertEqual(list(self.root.iterdir()),[])


class Transport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from studio_service import Handler
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.server.token='test-only-studio-token-that-is-not-a-deployed-secret'
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
    def request(self,body,headers=None,path='/v1/run'):
        request=urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}'+path,json.dumps(body).encode(),{'Content-Type':'application/json',**(headers or {})})
        try:
            with urllib.request.urlopen(request,timeout=5) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)
    def test_authentication_precedes_operation(self):
        for headers in ({},{'Authorization':'Bearer wrong'}):
            self.assertEqual(self.request({'op':'catalog'},headers)[0],401)
    def test_browser_origins_rejected(self):
        self.assertEqual(self.request({'op':'catalog'},{'Authorization':'Bearer '+self.server.token,'Origin':'https://example.test'})[0],403)
    def test_authenticated_catalog_and_definition_check(self):
        h={'Authorization':'Bearer '+self.server.token}
        self.assertEqual(self.request({'op':'catalog'},h)[0],200)
        self.assertTrue(self.request({'op':'check','document':document()},h)[1]['valid'])
    def test_unknown_route_and_bad_definition_fail(self):
        h={'Authorization':'Bearer '+self.server.token}
        self.assertEqual(self.request({'op':'check','document':{}},h)[0],400)
        self.assertEqual(self.request({'op':'catalog'},h,path='/not-a-route')[0],404)


if __name__=='__main__': unittest.main()
