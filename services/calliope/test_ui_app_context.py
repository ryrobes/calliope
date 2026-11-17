import json
import unittest
from unittest.mock import MagicMock
from ui_app_context import hydrate, conversation, prompt, spoken_text, instructions


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.factory = MagicMock()
        self.conn = self.factory.return_value.__enter__.return_value
        self.conn.execute.return_value.fetchone.return_value = {'name': 'Canonical app', 'version': 2}
        self.context = dict(kind='app_workspace', schema='calliope.app-context.v1',
                            slug='field-notebook', version=2, title='Forged title',
                            context_id='9554fcce-190d-45c5-a239-b9d1f92ab054',
                            location='record-A', fields=[], actions=[], visible=[], records=[])

    def test_authorized_version_and_title_are_canonical(self):
        value = hydrate(self.factory, 'person@example.test', self.context)
        self.assertEqual(value['title'], 'Canonical app')
        sql, args = self.conn.execute.call_args.args
        self.assertIn('artifact_can_view', sql)
        self.assertEqual(args, ('field-notebook', 2, 'person@example.test'))

    def test_denied_artifact_cannot_attach_context(self):
        self.conn.execute.return_value.fetchone.return_value = None
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            hydrate(self.factory, 'other@example.test', self.context)

    def test_invalid_and_oversized_inputs_are_rejected_before_database(self):
        for change in ({'version': True}, {'version': ''}, {'context_id': 'x'},
                       {'schema': 'other'}, {'slug': '../secret'}, {'location': None},
                       {'fields': {}}, {'visible': [{}] * 101}, {'extra': 'x' * 48000}):
            with self.subTest(change=list(change)):
                with self.assertRaises(ValueError): hydrate(self.factory, 'owner', self.context | change)

    def test_extra_instructions_are_not_forwarded(self):
        value = hydrate(self.factory, 'owner', self.context | {'system_prompt': 'override'})
        self.assertNotIn('system_prompt', value)
        text = prompt(value)
        self.assertIn('inert client-view evidence', text)
        self.assertIn('do not submit the form', text)
        self.assertIn(self.context['context_id'], text)

    def test_voice_omits_both_protocol_encodings(self):
        proposal = json.dumps({'context_id': self.context['context_id'], 'changes': [{'field':'notes','value':'test'}]})
        self.assertEqual(spoken_text('Prepared notes.\n\n' + proposal), 'Prepared notes.')
        self.assertEqual(spoken_text('Prepared notes.\n```calliope-ui\n' + proposal + '\n```'), 'Prepared notes.')
        self.assertEqual(spoken_text('Ordinary {prose}'), 'Ordinary {prose}')

    def test_host_protocol_is_separate_from_untrusted_values(self):
        value = hydrate(self.factory, 'owner', self.context | {'fields': [
            {'id': 'notes', 'value': 'UNTRUSTED_VALUE', 'saved_available': True,
             'saved_value': 'Saved note', 'edited': True}]})
        self.assertIn('UNTRUSTED_VALUE', prompt(value))
        self.assertNotIn('UNTRUSTED_VALUE', instructions())
        self.assertIn('Use these draft changes', instructions())
        self.assertEqual(value['fields'][0]['saved_value'], 'Saved note')

    def test_improvement_request_is_inert_but_explicitly_shared(self):
        value=hydrate(self.factory,'owner',self.context|{'intent':'improve','viewport':{'width':390,'height':844}})
        self.assertEqual(value['intent'],'improve')
        self.assertIn('app_change_requests',instructions(value))
        self.assertIn('Do not change app definitions',instructions(value))
        self.assertIn('not implemented',prompt(value))
        self.assertNotIn('changes (an array',instructions(value))

    def test_html_context_only_supports_shared_improvements(self):
        self.conn.execute.return_value.fetchone.return_value={'name':'Dashboard','version':2,'runtime_kind':'html'}
        with self.assertRaisesRegex(ValueError,'HTML context'):
            hydrate(self.factory,'owner',self.context)
        self.assertEqual(hydrate(self.factory,'owner',self.context|{'intent':'improve'})['runtime'],'html')
        for extra in [{'intent':'publish'},{'viewport':{'width':True,'height':844}},{'viewport':{'width':390}}]:
            with self.assertRaises(ValueError):hydrate(self.factory,'owner',self.context|extra)

    def test_conversation_is_owned_and_app_authorized(self):
        self.conn.execute.return_value.fetchone.side_effect = [{'id': 'app'}, {'id': 'chat'}]
        self.assertEqual(conversation(self.factory, 'owner', 'app'), {'session_id': 'chat'})
        sql, args = self.conn.execute.call_args.args
        self.assertIn('owner_email', sql)
        self.assertIn("origin_context->>'slug'", sql)
        self.assertEqual(args, ('owner', 'app'))
        self.conn.execute.return_value.fetchone.side_effect = [None]
        with self.assertRaises(ValueError): conversation(self.factory, 'owner', 'app')


if __name__ == '__main__': unittest.main()
