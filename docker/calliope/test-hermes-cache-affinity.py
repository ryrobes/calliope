"""Pinned Hermes transport integration; no provider calls."""
import copy
import os
import unittest
from unittest.mock import patch

from agent.transports.chat_completions import ChatCompletionsTransport, _finish_kwargs
from gateway.calliope_cache_affinity import prepare_request

URL = 'https://hutch.example/v1'
TOOLS = [{'type': 'function', 'function': {'name': name, 'parameters': {'type': 'object'}}}
         for name in ['zebra', 'alpha']]


class AffinityTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'RVBBIT_CLOVER_OPENAI_BASE_URL': URL})
        self.env.start(); self.addCleanup(self.env.stop)

    def build(self, **changes):
        params = {'base_url': URL, 'session_id': 'physical-1', 'cache_scope_id': 'logical-root', **changes}
        request = {'messages': [{'role': 'system', 'content': 'current instructions'}], 'tools': copy.deepcopy(TOOLS)}
        result = _finish_kwargs(request, request['messages'], params, supports_prompt_cache_key=False)
        return result

    def test_real_transport_stable_across_rotation_and_input_changes(self):
        a = self.build()
        b = self.build(session_id='physical-2')
        self.assertEqual(a['extra_headers'], b['extra_headers'])
        self.assertEqual([t['function']['name'] for t in a['tools']], ['alpha', 'zebra'])
        self.assertEqual([t['function']['name'] for t in TOOLS], ['zebra', 'alpha'])
        self.assertNotEqual(a['extra_headers'], self.build(cache_scope_id='child')['extra_headers'])
        b['messages'][0]['content'] = 'new instructions and state'
        b.pop('extra_headers')
        prepare_request(b, {'base_url': URL, 'session_id': 'physical-2', 'cache_scope_id': 'logical-root'})
        self.assertEqual(a['extra_headers'], b['extra_headers'])

    def test_profiles_and_child_sessions_are_separate(self):
        with patch('hermes_constants.get_hermes_home', return_value='/data/profiles/iris'):
            a = self.build()
        with patch('hermes_constants.get_hermes_home', return_value='/data/profiles/cassandra'):
            b = self.build()
        self.assertNotEqual(a['extra_headers'], b['extra_headers'])

    def test_explicit_body_and_header_identity_wins(self):
        for explicit in [{'prompt_cache_key': 'caller'}, {'extra_body': {'session_id': 'caller'}},
                         {'extra_headers': {'X-Session-ID': 'caller'}},
                         {'extra_headers': {'x-hutch-cache-scope': 'caller'}}]:
            request = copy.deepcopy(explicit)
            prepare_request(request, {'base_url': URL, 'session_id': 's'})
            self.assertEqual(request, explicit)

    def test_unknown_endpoint_or_no_session_is_unchanged(self):
        for url in ['https://hutch.example.evil/v1', 'https://other.example/v1', 'https://hutch.example/other']:
            r = self.build(base_url=url)
            self.assertNotIn('extra_headers', r)
            self.assertEqual(r['tools'], TOOLS)
        self.assertNotIn('extra_headers', self.build(session_id=None, cache_scope_id=None))

    def test_explicit_tool_cache_breakpoints_keep_their_order(self):
        tools = copy.deepcopy(TOOLS)
        tools[-1]['cache_control'] = {'type': 'ephemeral'}
        request = {'tools': copy.deepcopy(tools)}
        prepare_request(request, {'base_url': URL, 'session_id': 's'})
        self.assertEqual(request['tools'], tools)

    def test_full_custom_transport_preserves_request_budget_and_scope(self):
        from providers import get_provider_profile
        request = ChatCompletionsTransport().build_kwargs(
            'calliope-sol', [{'role': 'user', 'content': 'Reply with OK.'}],
            base_url=URL, session_id='physical', cache_scope_id='logical',
            provider_profile=get_provider_profile('custom'), max_tokens=1024,
            max_tokens_param_fn=lambda n: {'max_tokens': n},
            request_overrides={'extra_body': {'reasoning': {'effort': 'low'}}},
        )
        self.assertEqual(request['max_tokens'], 1024)
        self.assertEqual(request['extra_body']['reasoning'], {'effort': 'low'})
        self.assertEqual(len(request['extra_headers']['x-hutch-cache-scope']), 64)


if __name__ == '__main__':
    unittest.main()
