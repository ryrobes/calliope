#!/usr/bin/env python3
"""Freeze ACL-scoped chunk metadata and hit-centered windows for an offline trial."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

import jev_retrieval_trial as provider


def digest(text):
    return hashlib.md5(text.encode()).hexdigest()


def sections(case, metadata):
    """Plan only from original excerpts plus index/hash metadata, never new text."""
    known = {digest(e['text']) for d in case['candidates'] for e in d['excerpts']}
    out, seen, excluded = [], {}, []
    for d in case['candidates']:
        rows = metadata[str(d['doc_id'])]
        indexed = {r['idx']: r['hash'] for r in rows}
        for e in d['excerpts']:
            if indexed.get(e['chunk_idx']) != digest(e['text']):
                raise ValueError('Source changed since original search; freeze a fresh corpus')
            anchor = e['chunk_idx']
            new = [r for r in rows if abs(r['idx'] - anchor) <= 2 and r['hash'] not in known]
            if not new:
                excluded.append({'doc_id': d['doc_id'], 'anchor': anchor, 'reason': 'no_unseen_chunks_in_window'})
                continue
            identity = tuple(sorted({r['hash'] for r in new}))
            ref = {'doc_id': d['doc_id'], 'title': d['title'], 'source': d['source'], 'anchor': anchor}
            if identity in seen:
                seen[identity]['equivalent_sources'].append(ref)
                continue
            item = {'key': f'S{len(out)+1:02}', **ref, 'doc_type': d.get('doc_type'),
                    'occurred_at': d.get('occurred_at'), 'excerpt': e['text'],
                    'document_chunks': len(rows), 'unseen_window_chunks': len(new),
                    'new_hashes': list(identity), 'equivalent_sources': []}
            seen[identity] = item
            out.append(item)
    return out, excluded


def public_sections(case):
    return [{k: v for k, v in s.items() if k != 'new_hashes'} for s in case['sections']]


def main():
    import server
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True)
    old = json.loads(args.snapshot.read_text())
    owner = old['manifest']['owner']
    output = []
    manifest = {'version': 2, 'source_sha256': hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),
                'owner': owner, 'window': 2, 'max_sections': 2, 'max_added_chars_per_section': 10000}
    path = args.output / 'manifest.json'
    if path.exists() and json.loads(path.read_text()) != manifest:
        raise ValueError('Changed fixture source; use a fresh output directory')
    provider.dump(path, manifest)
    for original in old['cases']:
        path = args.output / (original['id'] + '.json')
        if path.exists():
            output.append(json.loads(path.read_text()))
            continue
        case = {k: deepcopy(original[k]) for k in ('id', 'query', 'filters', 'base_result', 'candidates')}
        ids = [d['doc_id'] for d in case['candidates']]
        start = time.monotonic()
        with server._ro() as c:
            c.execute("SET LOCAL statement_timeout = '15s'")
            rows = c.execute('''SELECT c.doc_id, c.idx, md5(c.text) AS hash
                FROM rvbbit.brain_chunks c
                WHERE c.doc_id = ANY(%s::bigint[])
                  AND EXISTS (SELECT 1 FROM rvbbit.brain_visible_docs(%s) v WHERE v.doc_id = c.doc_id)
                ORDER BY c.doc_id, c.idx''', (ids, owner)).fetchall()
        metadata = {str(did): [] for did in ids}
        for r in rows:
            metadata[str(r['doc_id'])].append({'idx': r['idx'], 'hash': r['hash']})
        case['metadata_ms'] = round((time.monotonic() - start) * 1000, 1)
        case['chunk_metadata'] = metadata
        case['sections'], case['excluded'] = sections(case, metadata)
        case['windows'] = {}
        for section in case['sections']:
            start = time.monotonic()
            context = server.tool_brain_context(section['doc_id'], section['anchor'], window=2, caller_email=owner)
            if context.get('error'):
                raise ValueError('Caller-scoped context failed')
            indexed = {r['idx']: r['hash'] for r in metadata[str(section['doc_id'])]}
            expected = {idx for idx in indexed if abs(idx - section['anchor']) <= 2}
            if {r['idx'] for r in context['chunks']} != expected:
                raise ValueError('Window changed or ACL revoked during freeze')
            for r in context['chunks']:
                if digest(r['chunk']) != indexed[r['idx']]:
                    raise ValueError('Window changed during freeze')
            case['windows'][section['key']] = {'chunks': context['chunks'],
                'elapsed_ms': round((time.monotonic() - start) * 1000, 1)}
        case['frozen_at'] = datetime.now(timezone.utc).isoformat()
        provider.dump(path, case)
        output.append(case)
        print(json.dumps({'id': case['id'], 'sections': len(case['sections']),
                          'excluded_windows': len(case['excluded']), 'metadata_ms': case['metadata_ms']}), flush=True)
    provider.dump(args.output / 'frozen.json', {'manifest': manifest, 'cases': output})


if __name__ == '__main__':
    main()
