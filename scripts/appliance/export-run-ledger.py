#!/usr/bin/env python3
"""Export one Hermes run (or a session's runs) from metadata-only JSONL telemetry."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import math

COVERAGE = {
    'included': ['Hermes /v1/runs status transitions', 'AIAgent execution spans and inherited child spans',
                 'main-loop model attempts including Hutch and subscription provider facades',
                 'chat-completions/Anthropic internal stream attempt metadata',
                 'Hermes pre/post tool hooks'],
    'not_guaranteed': ['SDK-internal retries or provider-facade internal calls',
                       'auxiliary inference outside the main agent loop', 'Hindsight and PostgreSQL inference',
                       'worker paths that do not propagate the run context', 'external side effects'],
    'sizes': 'Complete SDK JSON representation, not HTTP wire bytes; null when serialization is unavailable.',
    'tokens': 'Hermes v0.21.4 canonical: input_tokens is uncached; prompt_tokens includes cache read/write. '
              'reasoning_tokens is informational and must not be added to output_tokens again.',
    'completion': 'A terminal parent run does not establish descendant quiescence. Inspect open spans/attempts.',
}


def snapshot_rows(files, diagnostics):
    for path, limit in files:
        with path.open('rb') as handle:
            while handle.tell() < limit:
                line = handle.readline(limit - handle.tell())
                if not line.endswith(b'\n'):
                    diagnostics['partial_lines'] += 1
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict) or row.get('schema_version') != 1 or not row.get('event_id'):
                        raise ValueError('unsupported row')
                    yield row
                except (ValueError, UnicodeError):
                    diagnostics['invalid_lines'] += 1


def summarize(rows):
    counts = Counter(row['event'] for row in rows)
    starts = {r['attempt_id'] for r in rows if r['event'] == 'model.started' and r.get('attempt_id')}
    terminals = {r['attempt_id'] for r in rows if r['event'] in ('model.completed','model.failed') and r.get('attempt_id')}
    streams = {r['stream_attempt_id'] for r in rows if r['event']=='stream.started' and r.get('stream_attempt_id')}
    stream_ends = {r['stream_attempt_id'] for r in rows if r['event'] in ('stream.completed','stream.failed') and r.get('stream_attempt_id')}
    agents = {r['agent_span_id'] for r in rows if r['event'] == 'agent.started'}
    ended = {r['agent_span_id'] for r in rows if r['event'] == 'agent.ended'}
    tool_key = lambda r: (r.get('agent_span_id'), r.get('session_id'), r.get('tool_call_id') or r.get('event_id'))
    tools = {tool_key(r) for r in rows if r['event'] == 'tool.started'}
    done_tools = {tool_key(r) for r in rows if r['event'] == 'tool.completed'}
    usage_rows = [r['usage'] for r in rows if r['event']=='model.completed' and isinstance(r.get('usage'),dict)]
    tokens = {key: sum(u[key] for u in usage_rows if key in u) if any(key in u for u in usage_rows) else None
              for key in ('prompt_tokens','input_tokens','cache_read_tokens','cache_write_tokens','output_tokens','reasoning_tokens')}
    status = {}
    for row in rows:
        if row['event'] == 'run.status':
            status[row.get('run_id')] = row.get('status')
    return {'event_counts': dict(counts),
            'stream_attempts_started': len(streams), 'stream_attempts_without_terminal': len(streams-stream_ends),
            'stream_attempts_failed': counts['stream.failed'],
            'stream_retries': sum(r['event']=='stream.started' and r.get('stream_attempt_no',1)>1 for r in rows),
            'failed_stream_seconds': sum(r.get('duration_ms',0)/1000 for r in rows if r['event']=='stream.failed'), 'run_statuses': status,
            'observed_span_seconds': rows[-1]['epoch_seconds'] - rows[0]['epoch_seconds'] if rows else None,
            'model_attempts_started': len(starts), 'model_attempts_without_terminal': len(starts-terminals),
            'model_terminals_without_start': len(terminals-starts) + sum(1 for r in rows if
                r['event'] in ('model.completed','model.failed') and not r.get('attempt_id')),
            'open_agent_spans': len(agents-ended), 'open_tool_calls': len(tools-done_tools),
            'unidentified_tool_events': sum(r['event'] in ('tool.started','tool.completed') and not r.get('tool_call_id') for r in rows),
            'tool_errors': len({tool_key(r) for r in rows if r['event']=='tool.completed' and r.get('status') not in ('success','ok',None)}),
            'unique_tool_completions': len(done_tools),
            'duplicate_tool_terminals': counts['tool.completed']-len(done_tools),
            'successful_requests_with_usage': len(usage_rows),
            'successful_requests_without_usage': counts['model.completed']-len(usage_rows),
            'known_usage_totals': tokens, 'cost_usd': None, 'cost_status': 'unpriced',
            'cache': summarize_cache(rows),
            'write_failures_observed': max((r.get('write_failures_in_process',0) for r in rows), default=0)}


def summarize_cache(rows):
    """Actual token evidence, never inferred discounts or duplicate stream charges."""
    def number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0

    def totals(items):
        reported = [r for r in items if isinstance(r.get('cache_usage'), dict) and any(
            number(r['cache_usage'].get(k)) for k in ('cache_read_tokens', 'cache_write_tokens'))]
        paired = [r['cache_usage'] for r in reported if number(r['cache_usage'].get('prompt_tokens'))
                  and number(r['cache_usage'].get('cache_read_tokens'))]
        known_reads = [r['cache_usage']['cache_read_tokens'] for r in reported if number(r['cache_usage'].get('cache_read_tokens'))]
        known_writes = [r['cache_usage']['cache_write_tokens'] for r in reported if number(r['cache_usage'].get('cache_write_tokens'))]
        denominator = sum(u['prompt_tokens'] for u in paired)
        costs = [r['reported_cost_usd'] for r in items if number(r.get('reported_cost_usd'))]
        return {
            'completed_requests': len(items), 'requests_with_provider_cache_counters': len(reported),
            'requests_with_read_counter': len(known_reads), 'requests_with_write_counter': len(known_writes),
            'requests_with_cache_hits': sum(v > 0 for v in known_reads),
            'cache_read_tokens': sum(known_reads) if known_reads else None,
            'cache_write_tokens': sum(known_writes) if known_writes else None,
            'cache_read_fraction': sum(u['cache_read_tokens'] for u in paired) / denominator if denominator else None,
            'read_fraction_prompt_tokens': denominator if paired else None,
            'requests_with_reported_cost': len(costs), 'reported_cost_usd': sum(costs) if costs else None,
        }

    completed = [r for r in rows if r.get('event') in ('model.completed', 'aux.completed')]
    grouped = {}
    for r in completed:
        key = (r['event'], r.get('provider'), r.get('model'), r.get('profile'))
        grouped.setdefault(key, []).append(r)
    by_route = [{'event': k[0], 'provider': k[1], 'model': k[2], 'profile': k[3], **totals(v)}
                for k, v in sorted(grouped.items(), key=lambda kv: tuple(str(x or '') for x in kv[0]))]
    previous = {}
    transitions = Counter()
    identities = Counter()
    measured = 0
    for r in rows:
        if r.get('event') != 'model.started' or not isinstance(r.get('cache_request'), dict):
            continue
        current = r['cache_request']; measured += 1
        identities[current.get('identity_source', 'none')] += 1
        # Identity survives compression. Legacy/no-identity requests use the
        # physical session; do not compare unrelated agents that lack either.
        scope = current.get('identity_sha256') or r.get('session_id')
        if not scope:
            continue
        key = (scope, r.get('model'), r.get('provider'), r.get('profile'))
        prior = previous.get(key)
        if prior:
            same_tools = current.get('tools_sha256') == prior.get('tools_sha256')
            same_system = current.get('system_prefix_sha256') == prior.get('system_prefix_sha256')
            transitions['compared_requests'] += 1
            transitions['tool_list_changed' if not same_tools else 'tool_list_unchanged'] += 1
            transitions['system_prefix_changed' if not same_system else 'system_prefix_unchanged'] += 1
            if same_tools and same_system:
                transitions['both_unchanged'] += 1
        previous[key] = current
    return {'overall': totals(completed), 'by_route': by_route,
            'request_identity_sources': dict(identities), 'requests_with_prefix_fingerprints': measured,
            'prefix_transitions': dict(transitions),
            'coverage': 'Only provider-reported counters; missing is unknown, including legacy captures. '
                        'Read fraction uses requests reporting both prompt and read tokens. '
                        'Costs are reported totals, not calculated cache savings. Stream diagnostics are excluded. '
                        'Prefix hashes detect changes in tools and initial system/developer messages; they do not prove a cache hit.'}


def export(root, output, *, run_id=None, session_id=None, label=None):
    # Freeze byte boundaries: later appends belong to a later export; no writers are blocked.
    files = [(p, p.stat().st_size) for p in sorted(root.glob('*.jsonl'))]
    diagnostics = Counter()
    run_ids = {run_id} if run_id else set()
    if session_id:
        for row in snapshot_rows(files, Counter()):
            if row.get('session_id') == session_id and row.get('run_id'):
                run_ids.add(row['run_id'])
    rows, seen = [], set()
    for row in snapshot_rows(files, diagnostics):
        if row.get('run_id') not in run_ids and not (session_id and row.get('session_id') == session_id):
            continue
        if row['event_id'] in seen:
            diagnostics['duplicate_events'] += 1
            continue
        rows.append(row)
        seen.add(row['event_id'])
    rows.sort(key=lambda r: r['epoch_seconds'])
    if not rows:
        raise ValueError('No matching ledger events; capture may be disabled or the ID may be wrong')
    output.mkdir(parents=True, exist_ok=False)
    events = b''.join((json.dumps(row, separators=(',',':'), ensure_ascii=False)+'\n').encode() for row in rows)
    (output/'events.jsonl').write_bytes(events)
    summary = summarize(rows)
    summary['source_diagnostics'] = dict(diagnostics)
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    manifest = {'schema_version':1, 'exported_at':datetime.now(timezone.utc).isoformat(),
                'selector':{'run_id':run_id, 'session_id':session_id}, 'label':label,
                'run_ids':sorted(run_ids), 'events_sha256':hashlib.sha256(events).hexdigest(),
                'event_count':len(rows), 'coverage':COVERAGE, 'pricing':None,
                'source_snapshot':[{'file':p.name,'bytes':n} for p,n in files]}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger-dir', type=Path, default=Path('/opt/data/calliope-ledger'))
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument('--run-id')
    selector.add_argument('--session-id', help='Hermes session ID; includes all observed runs for the session')
    parser.add_argument('--output', type=Path, required=True, help='New directory, never overwritten')
    parser.add_argument('--label', help='Optional benchmark trial name')
    args = parser.parse_args()
    try:
        result = export(args.ledger_dir,args.output,run_id=args.run_id,session_id=args.session_id,label=args.label)
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
