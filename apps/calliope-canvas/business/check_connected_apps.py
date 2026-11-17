#!/usr/bin/env python3
"""Exercise the authored Northstar record links in a disposable GPUI profile.

Requires dispatcher access to the three apps. Edits only local drafts; never
assigns a visit, submits a report or reviews work. Uses a private Xvfb display.
"""
import argparse
import json
from pathlib import Path
import time

from capture_published import isolated_app
from studio import probe, capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with isolated_app('northstar-review', out/'native.log', args.connection) as session:
        def call(op, **values):
            result = probe(session, {'op': op, **values})
            assert not result.get('error'), result
            return result

        def wait(predicate=lambda s: True):
            end = time.monotonic() + 45
            while time.monotonic() < end:
                state = call('inspect')
                if not state.get('busy') and (not state.get('slug') or state.get('draft_saved')) and predicate(state):
                    assert not state.get('failed'), state.get('notice')
                    return state
                time.sleep(.2)
            (out/'failure.json').write_text(json.dumps(state, indent=2))
            raise RuntimeError(state.get('notice'))

        def selected(slug, ident):
            return wait(lambda s: s['slug'] == slug and
                        s['surface']['data']['model']['record']['id'] == ident)

        def open_record(slug, ident):
            call('open_record', reference={'slug': slug, 'record': 'visit', 'id': ident})
            return selected(slug, ident)

        # The destination has its own older selection and history. Entering it
        # through a link must not make Back visit that unrelated cached record.
        open_record('northstar-review', 'NS-2005')
        call('home')
        wait(lambda s: not s['slug'])
        open_record('northstar-dispatch', 'NS-2002')
        call('fill', id='technician', value=['northstar-other@local.test'])
        wait(lambda s: s['surface']['data']['state']['draft']['technician'] ==
             ['northstar-other@local.test'])
        call('press', id='open-my-visits')
        selected('northstar-my-visits', 'NS-2002')
        marker = 'Unsubmitted cross-app navigation check.'
        call('fill', id='notes', value=marker)
        wait(lambda s: s['surface']['data']['state']['draft']['notes'] == marker)
        # Re-enter from Home as after a restart. A record-link request omits state,
        # but must still send saved form hints before server-dependent SQL runs.
        call('home')
        wait(lambda s: not s['slug'])
        state = open_record('northstar-my-visits', 'NS-2002')
        assert state['surface']['data']['state']['draft']['notes'] == marker
        call('home')
        wait(lambda s: not s['slug'])
        open_record('northstar-dispatch', 'NS-2002')
        call('press', id='open-my-visits')
        state = selected('northstar-my-visits', 'NS-2002')
        assert state['surface']['data']['state']['draft']['notes'] == marker
        call('press', id='open-review')
        state = selected('northstar-review', 'NS-2002')
        assert state['history_depth'] == 0 and state['return_depth'] == 2
        call('back')
        state = selected('northstar-my-visits', 'NS-2002')
        assert state['surface']['data']['state']['draft']['notes'] == marker
        call('back')
        state = selected('northstar-dispatch', 'NS-2002')
        assert state['surface']['data']['state']['draft']['technician'] == \
            ['northstar-other@local.test']
        assert state['return_depth'] == 0
        capture(session, out/'desktop.png', 1440, 1000, 0)
        call('home')
        state = wait(lambda s: not s['slug'])
        recent = [a['slug'] for a in state['apps'] if a.get('last_used')]
        assert set(recent) >= {'northstar-dispatch', 'northstar-my-visits', 'northstar-review'}
        report = {'same_record_across_apps': True, 'back_skips_old_destination_history': True,
                  'reopened_record_keeps_draft': True,
                  'source_and_destination_drafts_retained': True, 'recent_apps': recent,
                  'business_commands_executed': 0}
        (out/'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
