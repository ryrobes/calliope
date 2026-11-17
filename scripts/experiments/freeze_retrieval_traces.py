#!/usr/bin/env python3
"""Freeze recent owner-linked Hermes retrieval traces for retrospective shadowing.

Read-only, bounded, main Calliope store only. No fresh retrieval, tool execution,
memory update, or live context hook. Run inside calliope-server.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path


def main():
    import server
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--owner',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--sessions',type=int,default=8)
    args=p.parse_args()
    if not 1<=args.sessions<=12:p.error('Choose 1-12 sessions')
    if args.output.exists():p.error('Preserve the existing snapshot')
    os.umask(0o077)
    with server._conn() as c:
        c.execute('SET TRANSACTION READ ONLY')
        c.execute("SET LOCAL statement_timeout='15s'")
        c.execute("SET LOCAL lock_timeout='1s'")
        sessions=c.execute("""SELECT s.id,s.hermes_session_id,s.title,max(m.ts) AS last_retrieval_at
            FROM rvbbit.calliope_sessions s JOIN rvbbit.hermes_messages m
              ON m.session_id=s.hermes_session_id AND m.store='default'
            WHERE s.owner_email=%s AND m.tool_name ILIKE '%%ask_brain%%'
              AND m.ts>=now()-interval '30 days'
            GROUP BY s.id,s.hermes_session_id,s.title
            ORDER BY last_retrieval_at DESC,s.id LIMIT %s""",(args.owner,args.sessions)).fetchall()
        rows=[]
        for session in sessions:
            messages=c.execute("""SELECT id,ts,role,content,tool_calls,tool_name,tool_call_id,
                 active,compacted,content_sha256 FROM rvbbit.hermes_messages
                 WHERE store='default' AND session_id=%s ORDER BY ts,id LIMIT 2001""",
                 (session['hermes_session_id'],)).fetchall()
            if len(messages)>2000:
                rows.append({'session':session,'skipped':'more than 2000 messages; no partial prefix replay'})
                continue
            turns=c.execute("""SELECT id,ordinal,user_message,created_at,completed_at,status
                FROM rvbbit.calliope_turns WHERE session_id=%s ORDER BY ordinal""",(session['id'],)).fetchall()
            rows.append({'session':session,'turns':turns,'messages':messages})
    result={'owner':args.owner,'frozen_at':datetime.now(timezone.utc).isoformat(),
            'selection':f'latest {args.sessions} owner-linked main-store sessions with ask_brain in last 30 days',
            'sessions':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,default=str,ensure_ascii=False,indent=2))
    print(json.dumps({'sessions':len(rows),'messages':sum(len(x.get('messages',[])) for x in rows),'output':str(args.output)}))


if __name__=='__main__':main()
