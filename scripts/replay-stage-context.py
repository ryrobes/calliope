#!/usr/bin/env python3
"""Read-only Jev replay over a deliberately exported private Stage snapshot.

Input/output stay outside the repo. No DB mutations or chat continuations.
Provide OPENROUTER_API_KEY in the environment. Labels are review judgments,
not automatically inferred ground truth from what the original agent did.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/calliope'))
import stage_context as sc


async def run(args):
    args.out.mkdir(parents=True, exist_ok=True, mode=0o700)
    cases = [json.loads(line) for line in args.cases.read_text().splitlines()]
    if not 1 <= len(cases) <= 30:
        raise ValueError('Export 1-30 cases for this bounded replay')
    key = os.environ['OPENROUTER_API_KEY']
    rows=[]
    with (args.out/'replay.jsonl').open('x') as out:
        os.chmod(args.out/'replay.jsonl', 0o600)
        for c in cases:
            items=c['items']
            if any(str(i['created_at']) >= str(c['created_at']) for i in items):
                raise ValueError('Candidate timestamp is not before the turn')
            selected=next((i for i in items if i['surface_id']==c.get('selected_surface_id')), None)
            history=[]
            for h in c.get('history') or []:
                history.extend([{'role':'user','text':h.get('user_message') or ''},
                                {'role':'assistant','text':h.get('assistant_message') or ''}])
            pool=sc.candidates(items,c['message'],history,selected)
            start=time.monotonic()
            r={'id':c['id'],'candidate_ids':[i['surface_id'] for i in pool],
               'recent_ids':[i['surface_id'] for i in items[:24]],
               'recency8':[i['surface_id'] for i in pool[:8]]}
            terms=sc.words(c['message'])
            r['text8']=[i['surface_id'] for _,i in sorted(enumerate(pool),key=lambda p:(-len(terms & sc.words(json.dumps(sc.descriptor(p[1])))),p[0]))[:8]]
            try:
                chosen,response=await asyncio.wait_for(sc.decide(c['message'],history,pool,selected,key),2.8)
                r.update(selected_ids=[i['surface_id'] for i in chosen],response=response)
            except Exception as exc:
                r.update(error=type(exc).__name__,selected_ids=r['recent_ids'])
            r['ms']=(time.monotonic()-start)*1000
            r['older_ids']=[i for i in r['selected_ids'] if i not in r['recent_ids']]
            r['before_descriptor_bytes']=len(json.dumps([sc.descriptor(i) for i in items[:24]]).encode())
            r['after_descriptor_bytes']=len(json.dumps([sc.descriptor(i) for i in items if i['surface_id'] in r['selected_ids']]).encode())
            out.write(json.dumps(r)+'\n');out.flush();rows.append(r)
            print(f"{len(rows)}/{len(cases)}: {len(r['selected_ids'])} items, {len(r['older_ids'])} older, {r['ms']:.0f} ms, {r.get('error','ok')}",flush=True)
    summary={'date':datetime.now(timezone.utc).isoformat(),'version':sc.VERSION,'cases':len(rows),
             'errors':sum('error' in r for r in rows),'median_ms':statistics.median(r['ms'] for r in rows),
             'cost_usd':sum(r.get('response',{}).get('usage',{}).get('cost',0) for r in rows),
             'cases_recalling_older_items':sum(bool(r['older_ids']) for r in rows),
             'before_descriptor_bytes':sum(r['before_descriptor_bytes'] for r in rows),
             'after_descriptor_bytes':sum(r['after_descriptor_bytes'] for r in rows),
             'cases_sha256':hashlib.sha256(args.cases.read_bytes()).hexdigest(),
             'module_sha256':hashlib.sha256(Path(sc.__file__).read_bytes()).hexdigest(),
             'qualification':'Private historical metadata replay. No independent relevance labels. Byte counts are descriptor proxies, not actual Hermes input token savings; earlier payloads may have been updated in place.'}
    (args.out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--cases',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    asyncio.run(run(p.parse_args()))
