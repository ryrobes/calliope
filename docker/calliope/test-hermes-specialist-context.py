"""Exercise the shipped profile against Hermes' real deterministic pruner.

No provider requests. Run in the Hermes image; fixtures model the dashboard
contract and proved SQL that Iris repeatedly lost during the V3 benchmark.
"""
import copy
import json
import runpy
from pathlib import Path

from agent.context_compressor import ContextCompressor

profile = runpy.run_path(str(Path(__file__).with_name('reconcile-hermes-specialists.py')))
policy = profile['_profile_config_base']('dashboard-studio')['compression']


def compressor(settings):
    return ContextCompressor(
        model='calliope-opus', config_context_length=262144, max_tokens=32768,
        **{k: v for k, v in settings.items() if k != 'max_attempts'},
    )


def exchange(index, name, content):
    return [
        {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': str(index), 'type': 'function', 'function': {
                'name': name, 'arguments': json.dumps({'query': 'proved query ' + str(index)})}}]},
        {'role': 'tool', 'tool_call_id': str(index), 'name': name, 'content': content},
    ]


messages = [{'role': 'user', 'content': 'Build the app using the verified data and runtime contract.'}]
messages += exchange(0, 'mcp__Specialist__dashboard_template',
                     'runtime contract: rvbbitQuery and rvbbitMaps\n' * 700)
for i in range(1, 8):
    messages += exchange(i, 'mcp__Specialist__run_sql_multi',
                         json.dumps({'query': i, 'rows': [{'state': 'WA', 'count': j + i} for j in range(400)]}))
for i in range(8, 20):
    messages += exchange(i, 'terminal', f'validated dependency {i}')
original = copy.deepcopy(messages)
old = dict(policy, protect_last_n=6, proactive_prune_tokens=9000,
           proactive_prune_min_result_chars=1200, proactive_prune_min_reclaim_tokens=1000)
pruned, count = compressor(old).prune_tool_results_only(messages, current_tokens=80000)
assert count > 0 and pruned[2]['content'] != original[2]['content'], 'must reproduce V3 loss'

kept, count = compressor(policy).prune_tool_results_only(messages, current_tokens=80000)
assert count == 0 and kept is messages and kept == original, 'retain working evidence below budget'

pruned, count = compressor(policy).prune_tool_results_only(messages, current_tokens=130000)
assert count > 0, 'substantial old results must remain reclaimable under pressure'
assert pruned[-12:] == original[-12:], 'retain the recent working tail'
assert messages == original, 'pruning must not mutate the original transcript'
print('PASS: V3 evidence loss reproduced; working context retained; pressure pruning and tail protection work')
