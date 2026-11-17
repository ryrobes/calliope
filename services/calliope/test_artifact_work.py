import contextlib
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from uuid import uuid4
from unittest.mock import patch
import artifact_work

class ArtifactWorkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.config=SimpleNamespace(file_root=self.temp.name)
        self.sid,self.turn,self.run=map(str,(uuid4(),uuid4(),uuid4()))
        self.status='running'
    def event(self, build='one', **kwargs):
        return dict(build_id=build, phase='composing', updated_at=time.time(), revision=1, **kwargs)
    def connection(self):
        me=self
        class Conn:
            def execute(self, sql, params):
                assert params[0]==me.sid
                return SimpleNamespace(fetchall=lambda:[{'id':id,'status':me.status} for id in params[1]])
        return contextlib.nullcontext(Conn())
    def test_session_and_specialist_isolation(self):
        e=self.event();artifact_work.record(self.config,self.sid,self.turn,e)
        artifact_work.record(self.config,self.sid,self.turn,e,{'run_id':self.run,'key':'iris'})
        items=artifact_work.snapshot(self.config,self.connection,self.sid)
        self.assertEqual({i['agent_key'] for i in items},{'iris','calliope'})
        self.assertEqual(artifact_work.snapshot(self.config,self.connection,str(uuid4())),[])
    def test_replay_does_not_revive_or_refresh_published_card(self):
        e=self.event();e['phase']='publishing'
        artifact_work.record(self.config,self.sid,self.turn,e)
        artifact_work.record(self.config,self.sid,self.turn,self.event('unrelated'))
        artifact_work.published(self.config,self.sid,self.turn)
        artifact_work.record(self.config,self.sid,self.turn,e)
        items=artifact_work.snapshot(self.config,self.connection,self.sid)
        self.assertEqual({i['build_id']:i['phase'] for i in items},{'one':'published','unrelated':'composing'})
        with patch('artifact_work.time.time',return_value=time.time()+15):
            items=artifact_work.snapshot(self.config,self.connection,self.sid)
            self.assertEqual([i['build_id'] for i in items],['unrelated'])
    def test_cancel_removes_builds_and_preserves_last_preview_during_edits(self):
        e=self.event(preview_html='<h1>saved</h1>');artifact_work.record(self.config,self.sid,self.turn,e)
        artifact_work.record(self.config,self.sid,self.turn,self.event())
        self.assertIn('saved',artifact_work.snapshot(self.config,self.connection,self.sid)[0]['preview_html'])
        self.status='cancelled'
        self.assertEqual(artifact_work.snapshot(self.config,self.connection,self.sid)[0]['phase'],'stopped')
        with patch('artifact_work.time.time',return_value=time.time()+15):
            self.assertEqual(artifact_work.snapshot(self.config,self.connection,self.sid),[])
    def test_bounded_retention(self):
        for i in range(40): artifact_work.record(self.config,self.sid,self.turn,self.event(str(i)))
        self.assertEqual(len(list(artifact_work.folder(self.config,self.sid).glob('*.json'))),32)
        self.assertEqual(len(artifact_work.snapshot(self.config,self.connection,self.sid)),8)

    def test_completed_app_gets_bounded_capture_grace_but_cancelled_does_not(self):
        self.record_at(1000, 'app', kind='a2ui', render_app={'schema':'calliope.app.v1'})
        self.status='complete'
        self.assertTrue(self.visible_at(1030)['app']['producer_complete'])
        self.assertEqual(self.visible_at(1301), {})
        self.status='cancelled'
        self.assertEqual(self.visible_at(1030), {})

    def record_at(self, at, build, phase='preview', specialist=None, **fields):
        with patch('artifact_work.time.time', return_value=at):
            event = self.event(build, **fields)
            event['phase'] = phase
            artifact_work.record(self.config, self.sid, self.turn, event, specialist)
        return event

    def visible_at(self, at):
        with patch('artifact_work.time.time', return_value=at):
            return {e['build_id']: e for e in artifact_work.snapshot(self.config, self.connection, self.sid)}

    def test_failed_old_file_retires_while_replacement_and_agent_continue(self):
        # The observed Iris run: a stale overwrite was blocked; a namespaced
        # replacement was then written and published, leaving the run active.
        self.record_at(1000, 'old', 'failed', filename='ground-sky-shell.html')
        self.record_at(1005, 'new', filename='shell.html')
        self.assertEqual(set(self.visible_at(1010)), {'old', 'new'})
        self.assertEqual(set(self.visible_at(1013)), {'new'})
        self.record_at(1020, 'new', 'publishing')
        with patch('artifact_work.time.time', return_value=1021):
            artifact_work.published(self.config, self.sid, self.turn)
        self.assertEqual(self.visible_at(1040), {})
        self.record_at(1050, 'old', 'writing')
        self.assertEqual(set(self.visible_at(1051)), {'old'}, 'A real retry remains visible')

    def test_inactive_draft_hides_without_deletion_and_resumes_on_a_new_edit(self):
        old = self.record_at(1000, 'old', preview_html='<p>Old draft</p>')
        self.record_at(1040, 'new', 'composing')
        self.assertEqual(set(self.visible_at(1059)), {'old', 'new'})
        self.assertEqual(set(self.visible_at(1061)), {'new'})
        with patch('artifact_work.time.time', return_value=1070):
            artifact_work.record(self.config, self.sid, self.turn, old)
        self.assertEqual(set(self.visible_at(1071)), {'new'}, 'Replay is not renewed activity')
        self.record_at(1080, 'new', 'publishing')
        with patch('artifact_work.time.time', return_value=1081):
            artifact_work.published(self.config, self.sid, self.turn)
        self.assertEqual(self.visible_at(1100), {}, 'Publication cannot resurrect the older draft')
        self.assertEqual(len(list(artifact_work.folder(self.config, self.sid).glob('*.json'))), 2)
        self.record_at(1110, 'old', 'writing')
        visible = self.visible_at(1111)
        self.assertEqual(set(visible), {'old'})
        self.assertIn('Old draft', visible['old']['preview_html'])

    def test_parallel_runs_and_image_requests_do_not_displace_a_lone_draft(self):
        self.record_at(1000, 'lead')
        self.record_at(1100, 'iris', specialist={'run_id':self.run,'key':'iris'})
        self.record_at(1190, 'image-one', 'generating', kind='image', call_id='c')
        self.assertEqual(set(self.visible_at(1300)), {'lead', 'iris', 'image-one'})
        self.record_at(1301, 'iris-new', specialist={'run_id':self.run,'key':'iris'})
        self.assertEqual(set(self.visible_at(1302)), {'lead', 'iris-new', 'image-one'})

    def test_recently_edited_sibling_files_remain_visible(self):
        self.record_at(1000, 'first')
        self.record_at(1040, 'second')
        self.record_at(1050, 'first', 'writing')
        self.assertEqual(set(self.visible_at(1099)), {'first', 'second'})
        self.assertEqual(set(self.visible_at(1101)), {'first'})
        self.assertEqual(set(self.visible_at(1800)), {'first'}, 'Thinking alone never implies abandonment')

    def test_failed_attempt_at_another_path_does_not_displace_the_last_saved_draft(self):
        self.record_at(1000, 'saved')
        self.record_at(1100, 'blocked', 'failed')
        self.assertEqual(set(self.visible_at(1101)), {'saved', 'blocked'})
        self.assertEqual(set(self.visible_at(1113)), {'saved'})

if __name__=='__main__':unittest.main()
