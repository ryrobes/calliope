-- Preserve old surface IDs/pins while retiring compressed-history duplicates.
-- Only repair when the same execution + named batch member has an exact
-- complete-prefix match. Never guess SQL from another execution or rerun it.
WITH repairs AS (
 SELECT clipped.id, full_receipt.id AS canonical_id, full_receipt.source
 FROM rvbbit.calliope_surfaces clipped
 CROSS JOIN LATERAL (
   SELECT f.id,f.source FROM rvbbit.calliope_surfaces f
   WHERE f.session_id=clipped.session_id AND f.tool_call_id=clipped.tool_call_id
     AND f.kind='query' AND f.id<>clipped.id
     AND coalesce(f.source#>>'{args,query_name}','')=coalesce(clipped.source#>>'{args,query_name}','')
     AND right(rtrim(f.source->>'sql'),14)<>'...[truncated]'
     AND starts_with(f.source->>'sql',left(rtrim(clipped.source->>'sql'),length(rtrim(clipped.source->>'sql'))-14))
   ORDER BY f.created_at,f.ordinal LIMIT 1
 ) full_receipt
 WHERE clipped.kind='query' AND coalesce(clipped.tool_call_id,'')<>''
   AND right(rtrim(clipped.source->>'sql'),14)='...[truncated]'
)
UPDATE rvbbit.calliope_surfaces s
SET source=s.source || jsonb_build_object('sql',r.source->>'sql','sql_truncated',false,
     'args',coalesce(s.source->'args','{}'::jsonb) || jsonb_build_object('sql',r.source->>'sql')),
    presentation=coalesce(s.presentation,'{}'::jsonb) || jsonb_build_object('duplicate_query_receipt_of',r.canonical_id)
FROM repairs r WHERE s.id=r.id;
