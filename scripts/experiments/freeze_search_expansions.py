#!/usr/bin/env python3
"""Fresh search/expansion fixtures for tool-local enrichment, no conversation edits.

Reissue the last valid natural ask_brain query from each frozen owner-linked
thread, using its filters and a common k=8. Freeze nearby chunks and document
heads for every returned source, solely as private evaluation fixtures. The
selector will never see these expansions before choosing.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import time

from jev_retrieval_shadow import canonical,calls


def queries(snapshot):
    out=[];seen=set()
    for row in snapshot['sessions']:
        valid=[c['arguments'] for m in canonical(row.get('messages',[])) for c in calls(m)
               if c['name']=='ask_brain' and isinstance(c['arguments'],dict) and c['arguments'].get('query')]
        if not valid:continue
        args=valid[-1];query=args['query'];filters=args.get('filters') or {}
        identity=json.dumps([query,filters],sort_keys=True)
        if identity in seen:continue
        seen.add(identity)
        out.append({'id':hashlib.sha256(identity.encode()).hexdigest()[:16],
                    'query':query,'filters':filters,'k':8,
                    'origin':'last valid recorded ask_brain query in owner-linked thread',
                    'thread_id':str(row['session']['id']),'thread_title':row['session']['title']})
    return out


def source_candidates(result):
    docs={}
    for rank,h in enumerate(result.get('hits') or []):
        key=str(h['doc_id'])
        if key not in docs:
            docs[key]={'key':f'D{len(docs)+1:02}','doc_id':h['doc_id'],'rank':rank,
                       'title':h['title'],'source':h.get('source'),'doc_type':h.get('doc_type'),
                       'occurred_at':h.get('occurred_at'),'excerpts':[]}
        docs[key]['excerpts'].append({'chunk_idx':h['chunk_idx'],'text':h['chunk']})
    return list(docs.values())


def expanded_packet(candidate,context,head):
    """Add whole new chunks only. Original hits are never removed or altered."""
    known={e['chunk_idx'] for e in candidate['excerpts']}
    anchor=candidate['excerpts'][0]['chunk_idx']
    chunks={c['idx']:c['chunk'] for c in context.get('chunks',[])+head.get('chunks',[])}
    # A document head can contain updates superseding a stale hit. Prioritize
    # it, then closest neighbors. Present kept chunks in natural source order.
    ordered=sorted((idx for idx in chunks if idx not in known),key=lambda idx:(idx!=0,abs(idx-anchor),idx))
    kept=[];omitted=[];used=0
    for idx in ordered:
        item={'chunk_idx':idx,'text':chunks[idx]};size=len(json.dumps(item,ensure_ascii=False))
        if used+size>10000:omitted.append(idx);continue
        kept.append(item);used+=size
    return {'key':candidate['key'],'doc_id':candidate['doc_id'],'title':candidate['title'],
            'source':candidate['source'],'chunks':sorted(kept,key=lambda c:c['chunk_idx']),
            'omitted_chunk_indices':omitted,'added_chars':used,
            'scope':'Neighbor window +/-2 around first hit plus document head, with original hit chunks excluded. Not necessarily the whole document.'}


def main():
    import server
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--traces',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();os.umask(0o077);args.output.mkdir(parents=True,exist_ok=True)
    snapshot=json.loads(args.traces.read_text());cases=queries(snapshot)
    manifest={'version':1,'owner':snapshot['owner'],'queries':cases,
              'protocol':'Eight fresh hits; two source expansions maximum per branch; <=10000 added characters per source, whole chunks only.'}
    path=args.output/'query-manifest.json'
    if path.exists() and json.loads(path.read_text())!=manifest:raise ValueError('Changed query manifest')
    path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    output=[]
    for case in cases:
        file=args.output/(case['id']+'.json')
        if file.exists():output.append(json.loads(file.read_text()));continue
        start=time.monotonic()
        result=server.tool_ask_brain(case['query'],k=8,filters=case['filters'],caller_email=snapshot['owner'])
        if result.get('error'):raise RuntimeError('Caller-scoped search failed')
        case={**case,'retrieval_ms':round((time.monotonic()-start)*1000,1),'base_result':result,
              'candidates':source_candidates(result),'expansions':{},'frozen_at':datetime.now(timezone.utc).isoformat()}
        for c in case['candidates']:
            start=time.monotonic();anchor=c['excerpts'][0]['chunk_idx']
            context=server.tool_brain_context(c['doc_id'],anchor,window=2,caller_email=snapshot['owner'])
            head=server.tool_brain_context(c['doc_id'],0,window=0,caller_email=snapshot['owner']) if anchor>2 else {'chunks':[]}
            if context.get('error') or head.get('error'):raise RuntimeError('Caller-scoped expansion failed')
            packet=expanded_packet(c,context,head)
            case['expansions'][c['key']]={'packet':packet,'elapsed_ms':round((time.monotonic()-start)*1000,1),
                                        'tool_calls':1+int(anchor>2)}
        file.write_text(json.dumps(case,default=str,ensure_ascii=False,indent=2));output.append(case)
        print(json.dumps({'id':case['id'],'sources':len(case['candidates']),'query':case['query'],'search_ms':case['retrieval_ms']}),flush=True)
    (args.output/'frozen.json').write_text(json.dumps({'manifest':manifest,'cases':output},default=str,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
