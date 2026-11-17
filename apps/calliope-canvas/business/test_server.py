import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from server import Store, Rejected
from presentation import validate_components, VIEWS


class Transactions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'test.sqlite')
        self.addCleanup(self.tmp.cleanup)

    def action(self, name, context, **extra):
        return self.store.action({'name':name,'context':context,'requestId':str(uuid.uuid4()),**extra})

    def test_save_persists_across_connections_and_binds_to_definition(self):
        draft = self.store.snapshot()['draft']
        draft['notes'] = 'New roof quote\nConfirmed with buyer.'
        result = self.action('save_deal', draft, source='Desktop')
        fresh = Store(self.store.path).snapshot()
        self.assertEqual(fresh['draft']['notes'], draft['notes'])
        self.assertEqual(fresh['draft']['version'], 2)
        self.assertEqual(result['revision'], fresh['revision'])
        self.assertEqual(fresh['messages'][2]['updateDataModel']['value']['draft'], fresh['draft'])

    def test_stale_edit_rejected_without_overwrite(self):
        draft = self.store.snapshot()['draft']
        self.action('save_deal', {**draft,'notes':'Desktop wins'})
        with self.assertRaises(Rejected) as error:
            self.action('save_deal', {**draft,'notes':'Stale web'})
        self.assertEqual(error.exception.status,409)
        self.assertEqual(self.store.snapshot()['draft']['notes'],'Desktop wins')

    def test_retry_exactly_once_even_concurrently(self):
        body = {'name':'save_deal','context':self.store.snapshot()['draft'],'requestId':str(uuid.uuid4())}
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(self.store.action,[body,body]))
        self.assertEqual(results[0],results[1])
        self.assertEqual(self.store.snapshot()['draft']['version'],2)
        with self.assertRaises(Rejected):
            self.store.action({**body,'context':{**body['context'],'notes':'different'}})

    def test_invalid_input_rolls_back(self):
        before = self.store.snapshot()
        for change in [{'closing_date':'2026-02-30'},{'owner':'Unknown'},{'notes':'x'*4001}]:
            with self.assertRaises(Rejected):
                self.action('save_deal',{**before['draft'],**change})
        self.assertEqual(before,self.store.snapshot())

    def test_commitments_gate_stage_and_are_scoped_to_record(self):
        draft = self.store.snapshot()['draft']
        with self.assertRaises(Rejected):
            self.action('advance_deal',draft)
        with self.assertRaises(Rejected):
            self.action('complete_task',{**draft,'task_id':'oak-0'})
        for task in ['cedar-1','cedar-2']:
            self.action('complete_task',{**self.store.snapshot()['draft'],'task_id':task})
        result = self.action('advance_deal',self.store.snapshot()['draft'])
        self.assertEqual(result['messages'][2]['updateDataModel']['value']['selected']['stage'],'Closing')

    def test_filter_and_selection_are_read_only(self):
        revision = self.store.revision()
        result = self.action('filter',{'owner':['Sam Rivera']})
        ids = {c['id'] for c in result['messages'][1]['updateComponents']['components']}
        self.assertIn('deal-harbor',ids)
        self.assertNotIn('deal-cedar',ids)
        self.assertEqual(self.action('select_deal',{'id':'oak'})['selected'],'oak')
        self.assertEqual(self.store.revision(),revision)

    def test_definition_has_no_dangling_or_recursive_children(self):
        for ident in ['cedar','harbor','oak','willow','station']:
            nodes = {c['id']:c for c in self.store.snapshot(ident)['messages'][1]['updateComponents']['components']}
            def walk(key, ancestors):
                self.assertNotIn(key,ancestors)
                node = nodes[key]
                for child in node.get('children',[]) + ([node['child']] if 'child' in node else []):
                    walk(child, ancestors|{key})
            walk('root',set())

    def test_all_presentations_use_same_records_and_preserve_view(self):
        revision=self.store.revision()
        for view in ('dossier','pipeline','briefing'):
            result=self.action('set_view',{'view':view},selected='oak')
            self.assertEqual(result['view'],view)
            self.assertEqual(result['selected'],'oak')
            refreshed=self.action('refresh',{},selected='oak',view=view)
            self.assertEqual(refreshed['view'],view)
            self.assertEqual(refreshed['draft'],result['draft'])
            validate_components(result['messages'][1]['updateComponents']['components'])
        self.assertEqual(self.store.revision(),revision)
        with self.assertRaises(Rejected): self.action('set_view',{'view':'unknown'})

    def test_catalog_rejects_unbounded_styles_assets_and_cycles(self):
        for node in [
            {'id':'root','component':'CText','text':'Hello','size':'72px'},
            {'id':'root','component':'CText','text':{'call':'eval'}},
            {'id':'root','component':'CSurface','child':'root'},
            {'id':'root','component':'CStack','children':['missing']},
            {'id':'root','component':'CStack','children':[],'style':{'position':'fixed'}},
            {'id':'root','component':'CArtwork','asset':'../../secret'},
            {'id':'root','component':'CMeter','label':'x','value':2},
        ]:
            with self.assertRaises(ValueError): validate_components([node])

if __name__ == '__main__':
    unittest.main()
