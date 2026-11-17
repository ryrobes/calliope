-- 0394: session lineage — parent/child grouping for Hermes forensics.
--
-- A multi-agent turn spawns sidecar Hermes sessions (one per specialist
-- thread). The lineage was always in canon — calliope_specialist_threads
-- records both the child hermes session and the source Calliope session —
-- but the forensics surfaces treated every session as a sibling. These two
-- views make the family tree queryable so "this session cost X tokens"
-- can mean the whole family, while each sidecar stays individually
-- inspectable.
--
-- hermes_session_lineage is the extension point: as other sidecar kinds
-- grow durable links (style-capture sessions, image-lane sessions), they
-- join as additional UNION arms with their own relationship label.

CREATE OR REPLACE VIEW rvbbit.hermes_session_lineage AS
SELECT th.hermes_session_id  AS child_hermes_session_id,
       cs.hermes_session_id  AS parent_hermes_session_id,
       'specialist_thread'   AS relationship,
       sp.specialist_key,
       th.id                 AS thread_id,
       th.source_session_id  AS parent_calliope_session_id
  FROM rvbbit.calliope_specialist_threads th
  JOIN rvbbit.calliope_sessions cs ON cs.id = th.source_session_id
  LEFT JOIN rvbbit.calliope_specialists sp ON sp.id = th.specialist_id
 WHERE th.hermes_session_id IS NOT NULL
   AND cs.hermes_session_id IS NOT NULL;

-- Family cost rollup, keyed by the parent hermes session. Children are
-- summed from the overview; the parent's own numbers stay on its overview
-- row, so family total = parent row + these columns. Deliberately excludes
-- costs billed outside Hermes sessions (Hindsight retains, embedders) —
-- undercount-by-design, same doctrine as the Work Ledger.
CREATE OR REPLACE VIEW rvbbit.hermes_session_family AS
SELECT l.parent_hermes_session_id      AS session_id,
       count(*)                        AS child_sessions,
       sum(o.api_calls)                AS child_api_calls,
       sum(o.sum_input_tokens)         AS child_input_tokens,
       sum(o.sum_output_tokens)        AS child_output_tokens,
       sum(o.compactions)              AS child_compactions,
       sum(o.replayed_user_messages)   AS child_replays
  FROM rvbbit.hermes_session_lineage l
  JOIN rvbbit.hermes_session_overview o
    ON o.session_id = l.child_hermes_session_id
 GROUP BY 1;
