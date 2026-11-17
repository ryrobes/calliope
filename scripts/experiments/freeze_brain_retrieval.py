#!/usr/bin/env python3
"""Freeze permitted Page evidence for a private retrieval trial.

Run inside calliope-server, PYTHONPATH=/app. Reads existing Pages and calls the
normal caller-scoped Document Brain retrieval; embedding caches may populate.
Never starts a chat, updates a Page, or modifies source documents.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import time
from uuid import UUID


def main():
    import server
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--owner',required=True)
    p.add_argument('--page',type=UUID,action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if not 1<=len(args.page)<=10:p.error('Choose one to ten Page questions')
    if args.output.exists():p.error('Output already exists; preserve the frozen snapshot')
    os.umask(0o077);cases=[]
    with server._conn() as conn:
        pages=conn.execute("SELECT id,title,question,standing_brief,source_filters FROM rvbbit.calliope_pages WHERE owner_email=%s AND id=ANY(%s::uuid[]) AND status<>'archived' ORDER BY created_at",(args.owner,[str(i) for i in args.page])).fetchall()
    if {str(r['id']) for r in pages}!={str(i) for i in args.page}:raise ValueError('Page missing, archived, or not owned by caller')
    for page in pages:
        start=time.monotonic()
        retrieval=server.tool_ask_brain(page['question'],k=24,filters=page['source_filters'],caller_email=args.owner)
        if retrieval.get('error'):raise RuntimeError('Brain retrieval failed')
        cases.append({'id':str(page['id']),'title':page['title'],'question':page['question'],'context':page['standing_brief'],
                      'filters':page['source_filters'],'origin':'existing_user_page_question',
                      'retrieval_ms':round((time.monotonic()-start)*1000),'retrieval':retrieval})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'owner':args.owner,'frozen_at':datetime.now(timezone.utc).isoformat(),'cases':cases},default=str,ensure_ascii=False,indent=2))
    print(json.dumps({'cases':len(cases),'snapshot':str(args.output)}))

if __name__=='__main__':main()
