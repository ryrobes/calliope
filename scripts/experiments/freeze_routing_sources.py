#!/usr/bin/env python3
"""Freeze bounded read-only source packets for the second retrieval experiment.

Run inside calliope-server with PYTHONPATH=/app. No MCP invocation, observer
refresh, chat/session creation, or source mutation. This is an admin pilot
experiment, not a general-purpose authorization surface.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time


SQL = {
    'registration': """SELECT count(*) AS registered,
        count(*) FILTER (WHERE acceleration_enabled) AS acceleration_enabled
        FROM rvbbit.tables""",
    'observations': """SELECT table_name,status,read_days,last_read_at,
        acceleration_enabled,registered,baseline_missing
        FROM rvbbit.accel_autopilot_candidates
        ORDER BY read_days DESC,last_read_at DESC NULLS LAST,table_name LIMIT 12""",
    'usage': """SELECT engine,count(*) AS calls,
        count(*) FILTER (WHERE ok) AS successful_calls,
        min(ts) AS first_at,max(ts) AS last_at,avg(elapsed_ms) AS avg_elapsed_ms
        FROM rvbbit.mcp_activity WHERE ts>=now()-interval '24 hours'
        AND caller=%s AND tool='run_sql'
        GROUP BY engine ORDER BY calls DESC LIMIT 10""",
    'objects': """SELECT object_name,engine,count(*) AS calls,
        count(*) FILTER (WHERE ok) AS successful_calls,max(ts) AS last_at
        FROM rvbbit.mcp_activity CROSS JOIN LATERAL unnest(objects) AS object_name
        WHERE ts>=now()-interval '24 hours' AND caller=%s AND tool='run_sql'
        GROUP BY object_name,engine ORDER BY calls DESC,object_name LIMIT 12""",
    'integrations': """SELECT server,name,description,input_schema,discovered_at
        FROM rvbbit.mcp_tools
        WHERE name IN ('meta_list_campaigns','tiktok_list_campaigns',
          'tiktok_list_ad_groups','google_list_campaigns','google_list_ad_groups',
          'snapchat_list_campaigns') ORDER BY server,name LIMIT 12""",
}


def main():
    import server
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error('Preserve the existing snapshot; choose a new output')
    os.umask(0o077)
    frozen = json.loads(args.snapshot.read_text())
    owner = frozen['owner']
    results = {}
    start = time.monotonic()
    # Read metadata/aggregates only. Never run the steward snapshot, which
    # refreshes observer state, and never execute the discovered MCP tools.
    with server._conn() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        conn.execute("SET LOCAL statement_timeout='8s'")
        conn.execute("SET LOCAL lock_timeout='1s'")
        for name, sql in SQL.items():
            t = time.monotonic()
            rows = conn.execute(sql, (owner,) if name in ('usage', 'objects') else ()).fetchall()
            results[name] = {'rows': rows, 'sql': sql,
                             'elapsed_ms': round((time.monotonic()-t)*1000, 1)}
    # Same top-level identity as the frozen retrieval; ACL is rechecked.
    # Expand two latest distinct ticket hits from the original project pool.
    project = frozen['cases'][0]
    hits = sorted(project['retrieval']['hits'], key=lambda h: h.get('occurred_at') or '', reverse=True)
    documents = []
    seen = set()
    for hit in hits:
        if hit['doc_id'] in seen:
            continue
        seen.add(hit['doc_id'])
        t = time.monotonic()
        doc = server.tool_brain_get_doc(hit['doc_id'], caller_email=owner)
        if doc.get('error'):
            raise RuntimeError('Document expansion failed access or retrieval check')
        documents.append({'doc_id': hit['doc_id'], 'result': doc,
                          'elapsed_ms': round((time.monotonic()-t)*1000, 1)})
        if len(documents) == 2:
            break
    result = {'owner': owner, 'frozen_at': datetime.now(timezone.utc).isoformat(),
              'parent_snapshot': str(args.snapshot), 'sql_results': results,
              'expanded_documents': documents,
              'elapsed_ms': round((time.monotonic()-start)*1000, 1),
              'limitations': ['Query telemetry covers this caller and the last 24 hours only.',
                 'Engine usage and registration do not prove speedup or causal benefit.',
                 'Registry input schemas are not observed vendor output contracts.',
                 'Document bodies are fresh reads, not historical reconstructions.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, default=str, ensure_ascii=False, indent=2))
    print(json.dumps({'sql_sections': {k: len(v['rows']) for k,v in results.items()},
                      'expanded_documents': len(documents), 'output': str(args.output)}))


if __name__ == '__main__':
    main()
