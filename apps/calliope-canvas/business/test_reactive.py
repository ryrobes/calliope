import copy
import tempfile
import unittest
from pathlib import Path
from server import Store
from studio_document import normalize
from reactive import compose, initial

class ReactiveDocuments(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as d:
            self.snapshot=Store(Path(d)/'fixture.db').snapshot(view='adaptive')
        s=self.snapshot
        self.document={'components':s['baseComponents'],'data':{k:v for k,v in s['messages'][2]['updateDataModel']['value'].items() if k!='resources'},'reactive':s['reactive']}
    def test_navigation_leaves_adaptive_with_a_valid_destination_record(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'fixture.db')
            result=store.action({'name':'set_view','context':{'view':'dossier'},'view':'adaptive','selected':'HV-1001'})
            self.assertEqual(result['selected'],'cedar')
    def test_future_fields_have_error_bindings_before_first_mount(self):
        d=copy.deepcopy(self.document);v=d['reactive'][0]['variants']['parts']
        v['components'].append({'id':'next.future','component':'CField','label':'Future','value':{'path':'/draft/handoff'},'error':{'path':'/errors/next.future'}})
        next(n for n in v['components'] if n['id']==v['root'])['children'].append('next.future')
        result=normalize(d)
        self.assertEqual(result['messages'][2]['updateDataModel']['value']['errors']['next.future'],'')
    def test_all_outcomes_preserve_editor_and_input_data(self):
        d=self.document; states=initial(d['reactive'])
        for label,variant in d['reactive'][0]['variants'].items():
            states['next'].update(label=label,value=variant['value'])
            n=normalize({'components':compose(d['components'],d['reactive'],states),'data':{**d['data'],'resources':states}})
            nodes=n['messages'][1]['updateComponents']['components']
            self.assertEqual(next(c for c in nodes if c['id']=='next.handoff')['value'],{'path':'/draft/handoff'})
            self.assertEqual(n['draft'],d['data']['draft'])
        self.assertEqual(normalize(self.snapshot)['reactive'],d['reactive'])
    def test_reject_cycles_cross_ownership_or_unvalidated_future_outcome(self):
        for change in ('watch','target','id','binding','patch','leaf'):
            d=copy.deepcopy(self.document);r=d['reactive'][0];v=r['variants']['parts']
            if change=='watch':r['watch']['notes']='/resources/next/value'
            if change=='target':r['target']='root'
            if change=='id':v['components'][0]['id']='adaptive-notes'
            if change=='binding':v['components'][0]['text']={'path':'/does/not/exist'}
            if change=='patch':v['patches']['adaptive-notes']={'value':{'path':'/draft/handoff'}}
            if change=='leaf':r['tree']['routes']['parts']['label']='missing'
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):normalize(d)
    def test_structural_forced_outcome_marks_receipt_and_retains_draft(self):
        import studio
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);s=normalize(self.document);studio.write_json(p/'snapshot.json',s)
            session={'native':False}
            studio.operate(p,session,'fill','next.handoff','Do not lose this')
            result=studio.operate(p,session,'decision','next','parts')
            self.assertEqual(result['surface']['data']['draft']['handoff'],'Do not lose this')
            self.assertEqual(result['surface']['data']['resources']['next']['receipt']['source'],'studio-forced')
