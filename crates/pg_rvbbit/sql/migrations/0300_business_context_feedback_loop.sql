-- 0300: Durable chat-consumption evidence for the Business Context loop.
--
-- The topology and promoted Value System remain the semantic source of truth.
-- These tables record which compact context packet was available to a Calliope
-- turn and which governed concepts were subsequently supported by real tool or
-- query evidence.  They deliberately do not retain another copy of the user
-- message, assistant prose, SQL text, or tool payloads; those already belong to
-- the Calliope turn/surface ledgers.

CREATE TABLE IF NOT EXISTS rvbbit.business_context_turn_receipts (
    turn_id uuid PRIMARY KEY,
    session_id uuid NOT NULL,
    actor text NOT NULL,
    question_hash text NOT NULL,
    packet_mode text NOT NULL DEFAULT 'none',
    packet jsonb NOT NULL DEFAULT '{}'::jsonb,
    used_graph_ids uuid[] NOT NULL DEFAULT ARRAY[]::uuid[],
    used_metrics jsonb NOT NULL DEFAULT '[]'::jsonb,
    used_objects jsonb NOT NULL DEFAULT '[]'::jsonb,
    used_source_relations text[] NOT NULL DEFAULT ARRAY[]::text[],
    tool_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    query_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    feedback_signal text,
    outcome text NOT NULL DEFAULT 'running',
    evidence_score double precision NOT NULL DEFAULT 0.0,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    CONSTRAINT business_context_turn_packet_mode_check CHECK (
        packet_mode IN ('matched','ambient','none')
    ),
    CONSTRAINT business_context_turn_packet_check CHECK (
        jsonb_typeof(packet)='object'
    ),
    CONSTRAINT business_context_turn_metrics_check CHECK (
        jsonb_typeof(used_metrics)='array'
    ),
    CONSTRAINT business_context_turn_objects_check CHECK (
        jsonb_typeof(used_objects)='array'
    ),
    CONSTRAINT business_context_turn_tools_check CHECK (
        jsonb_typeof(tool_evidence)='array'
    ),
    CONSTRAINT business_context_turn_queries_check CHECK (
        jsonb_typeof(query_evidence)='array'
    ),
    CONSTRAINT business_context_turn_feedback_check CHECK (
        feedback_signal IS NULL OR feedback_signal IN (
            'possible_correction','possible_contradiction','affirmation'
        )
    ),
    CONSTRAINT business_context_turn_outcome_check CHECK (
        outcome IN ('running','complete','partial','failed','interrupted')
    ),
    CONSTRAINT business_context_turn_score_check CHECK (
        evidence_score>=0.0 AND evidence_score<=1.0
    )
);

CREATE INDEX IF NOT EXISTS business_context_turn_receipts_recent_idx
    ON rvbbit.business_context_turn_receipts(created_at DESC);
CREATE INDEX IF NOT EXISTS business_context_turn_receipts_feedback_idx
    ON rvbbit.business_context_turn_receipts(feedback_signal,created_at DESC)
    WHERE feedback_signal IS NOT NULL;

CREATE TABLE IF NOT EXISTS rvbbit.business_context_turn_evidence (
    evidence_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    turn_id uuid NOT NULL
        REFERENCES rvbbit.business_context_turn_receipts(turn_id) ON DELETE CASCADE,
    graph_id uuid,
    item_kind text NOT NULL,
    item_key text NOT NULL,
    evidence_kind text NOT NULL,
    strength double precision NOT NULL,
    outcome text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT business_context_evidence_item_kind_check CHECK (
        item_kind IN ('value_system','metric','object','source','feedback')
    ),
    CONSTRAINT business_context_evidence_kind_check CHECK (
        evidence_kind IN (
            'context_tool','governed_metric','query_lineage',
            'automatic_context','user_feedback'
        )
    ),
    CONSTRAINT business_context_evidence_strength_check CHECK (
        strength>=0.0 AND strength<=1.0
    ),
    CONSTRAINT business_context_evidence_outcome_check CHECK (
        outcome IN ('complete','partial','failed','interrupted')
    ),
    CONSTRAINT business_context_evidence_details_check CHECK (
        jsonb_typeof(details)='object'
    ),
    CONSTRAINT business_context_evidence_unique UNIQUE (
        turn_id,item_kind,item_key,evidence_kind
    )
);

CREATE INDEX IF NOT EXISTS business_context_turn_evidence_item_idx
    ON rvbbit.business_context_turn_evidence(
        graph_id,item_kind,item_key,observed_at DESC
    );

CREATE OR REPLACE VIEW rvbbit.business_context_evidence_summary AS
SELECT evidence.graph_id,evidence.item_kind,evidence.item_key,
       count(*)::bigint AS observations,
       count(*) FILTER (WHERE evidence.outcome='complete')::bigint
           AS completed_observations,
       count(*) FILTER (WHERE evidence.evidence_kind='user_feedback')::bigint
           AS feedback_observations,
       avg(evidence.strength)::double precision AS average_strength,
       max(evidence.observed_at) AS last_observed_at
  FROM rvbbit.business_context_turn_evidence evidence
 GROUP BY evidence.graph_id,evidence.item_kind,evidence.item_key;

COMMENT ON TABLE rvbbit.business_context_turn_receipts IS
    'One bounded receipt per ordinary Calliope turn: the automatically supplied Business Context packet and the governed concepts later evidenced by tools or query lineage.';
COMMENT ON TABLE rvbbit.business_context_turn_evidence IS
    'Normalized, append-only evidence observations suitable for topology confidence, correction review, and future proactive need-to-know scans.';
COMMENT ON VIEW rvbbit.business_context_evidence_summary IS
    'Aggregated usage and feedback support for promoted Value Systems, metrics, objects, and sources; it is evidence for the steward, never an automatic semantic mutation.';

INSERT INTO rvbbit.capability_mcp_tools(name,summary) VALUES (
    'business_context_for_question',
    'Inspect the compact promoted Business Context packet automatically supplied to an ordinary Calliope chat. It returns question-matched Value Systems when possible and a bounded ambient navigation map otherwise, including governed metrics, dimensions, relationships, sources, freshness, pending changes, and accumulated usage evidence without retained source SQL.'
)
ON CONFLICT (name) DO UPDATE SET summary=EXCLUDED.summary;

DO $capability$
DECLARE
    v_summary text;
    v_doc text;
    v_node bigint;
BEGIN
    SELECT summary INTO v_summary
      FROM rvbbit.capability_mcp_tools
     WHERE name='business_context_for_question';
    v_doc := rvbbit.capability_doc(
        'cap_mcp_tool','business_context_for_question',
        'Warehouse MCP connector tool (external agents)',
        v_summary||' NOT a local SQL function - exposed to agents through the Warehouse MCP connector.',
        '','varies',ARRAY['mcp','business-context','chat','feedback-loop']
    );
    v_node := rvbbit.kg_assert_node(
        'cap_mcp_tool','business_context_for_question',
        jsonb_build_object('search_doc',v_doc),1.0,'',0.0,
        'rvbbit_capabilities'
    );
    INSERT INTO rvbbit.catalog_docs(
        node_id,graph_id,kind,schema_name,rel_name,col_name,
        doc,embedding,embedded_at,updated_at
    ) VALUES (
        v_node,'rvbbit_capabilities','cap_mcp_tool',NULL,
        'business_context_for_question',NULL,v_doc,NULL,NULL,clock_timestamp()
    ) ON CONFLICT (graph_id,node_id) DO UPDATE SET
        kind=EXCLUDED.kind,rel_name=EXCLUDED.rel_name,doc=EXCLUDED.doc,
        embedding=NULL,embedded_at=NULL,updated_at=clock_timestamp();
END
$capability$;
