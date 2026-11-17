#!/usr/bin/env python3
"""Rebuild built-in text vector stores through the installation's SQL embedder.

Stop application/memory writers and scheduled embedding jobs first. Original
records and memory facts stay intact. Each table commits atomically; repeating
the command is safe and reuses the SQL embedding cache. No provider key needed.
"""
import argparse
import json
import os
import time

import psycopg
from psycopg import sql

# Only known stores with recoverable input text. Mirrors are refreshed below.
STORES = [
    ("rvbbit.catalog_docs", "embedding", "doc", "real[]"),
    ("rvbbit.brain_chunks", "embedding", "text", "real[]"),
    ("rvbbit.proposal_exemplars", "subject_embedding", "subject", "real[]"),
    ("rvbbit.catalog_snapshots", "embedding", "CASE WHEN kind='db_table' THEN rvbbit.catalog_table_doc(fingerprint) ELSE rvbbit.catalog_column_doc(schema_name,rel_name,'',fingerprint) END", "real[]"),
    ("rvbbit.hermes_doc_chunks", "embedding", "coalesce((SELECT title FROM rvbbit.hermes_docs d WHERE d.bundle_id=t.bundle_id AND d.doc_id=t.doc_id),'') || E'\\n' || heading || E'\\n' || text", "real[]"),
    ("hindsight.memory_units", "embedding", "text", "vector"),
    ("hindsight.mental_models", "embedding", "content", "vector"),
]


def rebuild(conn, batch_size=64):
    profile = conn.execute("SELECT profile FROM rvbbit.inference_settings WHERE singleton").fetchone()[0]
    dimensions = int(profile["embedding_dimensions"])
    report = {"profile": profile, "stores": []}
    for table, column, expression, vector_type in STORES:
        started = time.monotonic()
        with conn.transaction():
            if not conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]:
                continue
            target = sql.Identifier(*table.split('.'))
            # Lock out writes rather than let new vectors race an old profile.
            conn.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(target))
            rows = conn.execute(sql.SQL("SELECT ctid::text, coalesce(({})::text,'') FROM {} t WHERE {} IS NOT NULL").format(
                sql.SQL(expression), target, sql.Identifier(column))).fetchall()
            update = sql.SQL("UPDATE {} SET {}=%s::{} WHERE ctid=%s::tid").format(target, sql.Identifier(column), sql.SQL(vector_type))
            for offset in range(0, len(rows), batch_size):
                chunk = rows[offset:offset+batch_size]
                texts = [text for _,text in chunk]
                conn.execute("SELECT rvbbit.embed_batch(%s::text[])", (texts,))
                vectors = conn.execute("SELECT rvbbit.embed(t) FROM unnest(%s::text[]) t", (texts,)).fetchall()
                if any(len(v[0]) != dimensions for v in vectors):
                    raise RuntimeError("Embedding profile dimensions changed during rebuild")
                updates = [(json.dumps(v[0]) if vector_type=='vector' else v[0], row[0]) for row,v in zip(chunk,vectors,strict=True)]
                with conn.cursor() as cur:
                    cur.executemany(update, updates)
                if offset % (batch_size*20) == 0:
                    print(json.dumps({"table":table,"completed":min(offset+batch_size,len(rows)),"total":len(rows)}),flush=True)
            if table=='rvbbit.hermes_doc_chunks':
                conn.execute("UPDATE rvbbit.hermes_doc_bundles SET embedding_model=%s,embedding_dimensions=%s,manifest=manifest || %s::jsonb", (
                    profile['embedding_model'],dimensions,json.dumps({'embedding_model':profile['embedding_model'],'embedding_dimensions':dimensions})))
        report['stores'].append({'table':table,'rows':len(rows),'seconds':round(time.monotonic()-started,2)})
        print(json.dumps(report['stores'][-1]),flush=True)
    with conn.transaction():
        graphs = conn.execute("SELECT graph_id FROM rvbbit.catalog_docs GROUP BY graph_id ORDER BY graph_id='db_catalog'").fetchall()
        for (graph,) in graphs:
            result = conn.execute("SELECT rvbbit.pgvector_refresh_catalog(%s)",(graph,)).fetchone()[0]
            if result.get('error') or result.get('ok') is False:
                raise RuntimeError('Catalog vector refresh failed: '+json.dumps(result))
        for (name,) in conn.execute("SELECT index_name FROM rvbbit.vector_sources").fetchall():
            result = conn.execute("SELECT rvbbit.vector_refresh(%s)",(name,)).fetchone()[0]
            if result.get('ok') is False:
                raise RuntimeError('Vector source refresh failed: '+json.dumps(result))
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsn-env',default='WAREHOUSE_DSN')
    parser.add_argument('--batch-size',type=int,default=64)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if not 1<=args.batch_size<=256: parser.error('batch size must be 1..256')
    with psycopg.connect(os.environ[args.dsn_env],autocommit=True) as conn:
        report=rebuild(conn,args.batch_size)
    with open(args.output,'w') as f: json.dump(report,f,indent=2)
