#!/usr/bin/env python3
"""One bounded follow-up search for an existing frozen question, under its owner ACL."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time


def main():
    import server
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--page',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():p.error('Do not overwrite frozen evidence')
    os.umask(0o077)
    snapshot=json.loads(args.snapshot.read_text())
    case=next(c for c in snapshot['cases'] if c['id']==args.page)
    start=time.monotonic()
    # The first retrieval restricted document types. Broaden that filter while
    # preserving caller visibility and the original question, without an LLM rewrite.
    result=server.tool_ask_brain(case['question'],k=24,filters={},caller_email=snapshot['owner'])
    if result.get('error'):raise RuntimeError('Follow-up document search failed')
    receipt={'frozen_at':datetime.now(timezone.utc).isoformat(),'owner':snapshot['owner'],
             'page':args.page,'question':case['question'],'original_filters':case['filters'],
             'followup_filters':{},'retrieval':result,
             'elapsed_ms':round((time.monotonic()-start)*1000,1)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(receipt,default=str,ensure_ascii=False,indent=2))
    print(json.dumps({'count':result['count'],'types':result['types'],'elapsed_ms':receipt['elapsed_ms']}))


if __name__=='__main__':main()
