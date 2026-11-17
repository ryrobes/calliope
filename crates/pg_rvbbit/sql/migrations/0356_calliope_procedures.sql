-- 0356: Calliope Procedures -- executable, governed Playbooks.
--
-- A Procedure keeps its semantic contract in the immutable Playbook tables.
-- Concrete execution is a separately versioned Strategy, and every launch is
-- pinned into the existing Action Run receipt chassis.  Approved Procedures
-- remain cap_playbook documents so capability_search() stays the one discovery
-- surface for SQL, MCP, skills, methods, and executable procedures.

ALTER TABLE rvbbit.calliope_playbooks
    ADD COLUMN IF NOT EXISTS playbook_kind text NOT NULL DEFAULT 'method';

ALTER TABLE rvbbit.calliope_playbooks
    DROP CONSTRAINT IF EXISTS calliope_playbooks_kind_check;
ALTER TABLE rvbbit.calliope_playbooks
    ADD CONSTRAINT calliope_playbooks_kind_check
    CHECK (playbook_kind IN ('method','procedure'));

ALTER TABLE rvbbit.calliope_action_runs
    DROP CONSTRAINT IF EXISTS calliope_action_runs_status_check;
ALTER TABLE rvbbit.calliope_action_runs
    ADD CONSTRAINT calliope_action_runs_status_check
    CHECK (status IN ('planned','running','parked','complete','failed','cancelled'));

CREATE TABLE IF NOT EXISTS rvbbit.calliope_procedure_strategies (
    id uuid PRIMARY KEY,
    playbook_id uuid NOT NULL,
    playbook_version integer NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    strategy_kind text NOT NULL DEFAULT 'computer',
    status text NOT NULL DEFAULT 'draft',
    source_workflow_id uuid,
    source_workflow_revision integer,
    source_workbench_id uuid REFERENCES rvbbit.calliope_workbenches(id) ON DELETE SET NULL,
    definition jsonb NOT NULL DEFAULT '{}'::jsonb,
    validation jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    retired_at timestamptz,
    CONSTRAINT calliope_procedure_strategies_playbook_fkey
        FOREIGN KEY (playbook_id,playbook_version)
        REFERENCES rvbbit.calliope_playbook_versions(playbook_id,version)
        ON DELETE RESTRICT,
    CONSTRAINT calliope_procedure_strategies_revision_check CHECK (revision >= 1),
    CONSTRAINT calliope_procedure_strategies_kind_check
        CHECK (strategy_kind IN ('computer','browser','mcp','api','sql','mixed')),
    CONSTRAINT calliope_procedure_strategies_status_check
        CHECK (status IN ('draft','validated','retired')),
    CONSTRAINT calliope_procedure_strategies_definition_check
        CHECK (jsonb_typeof(definition)='object'),
    CONSTRAINT calliope_procedure_strategies_validation_check
        CHECK (jsonb_typeof(validation)='object'),
    CONSTRAINT calliope_procedure_strategies_version_key
        UNIQUE (playbook_id,playbook_version,revision),
    CONSTRAINT calliope_procedure_strategies_source_key
        UNIQUE (source_workflow_id,source_workflow_revision)
);

CREATE INDEX IF NOT EXISTS calliope_procedure_strategies_active_idx
    ON rvbbit.calliope_procedure_strategies (playbook_id,playbook_version,revision DESC)
    WHERE status='validated';

CREATE TABLE IF NOT EXISTS rvbbit.calliope_procedure_trust (
    playbook_id uuid PRIMARY KEY REFERENCES rvbbit.calliope_playbooks(id) ON DELETE CASCADE,
    level text NOT NULL DEFAULT 'recorded',
    clean_runs integer NOT NULL DEFAULT 0,
    eligible_at timestamptz,
    changed_by text NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_procedure_trust_level_check CHECK (
        level IN ('recorded','drafted','rehearsed','supervised','autonomous','scheduled')
    ),
    CONSTRAINT calliope_procedure_trust_clean_runs_check CHECK (clean_runs >= 0)
);

CREATE TABLE IF NOT EXISTS rvbbit.calliope_procedure_runs (
    run_id uuid PRIMARY KEY REFERENCES rvbbit.calliope_action_runs(id) ON DELETE RESTRICT,
    playbook_id uuid NOT NULL,
    playbook_version integer NOT NULL,
    strategy_id uuid NOT NULL REFERENCES rvbbit.calliope_procedure_strategies(id) ON DELETE RESTRICT,
    strategy_revision integer NOT NULL,
    trigger_kind text NOT NULL DEFAULT 'request',
    trust_level text NOT NULL DEFAULT 'rehearsed',
    state text NOT NULL DEFAULT 'planned',
    workbench_id uuid REFERENCES rvbbit.calliope_workbenches(id) ON DELETE SET NULL,
    guided_run_id uuid,
    current_step integer NOT NULL DEFAULT 0,
    requested_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    parked_at timestamptz,
    completed_at timestamptz,
    CONSTRAINT calliope_procedure_runs_playbook_fkey
        FOREIGN KEY (playbook_id,playbook_version)
        REFERENCES rvbbit.calliope_playbook_versions(playbook_id,version)
        ON DELETE RESTRICT,
    CONSTRAINT calliope_procedure_runs_trigger_check
        CHECK (trigger_kind IN ('request','manual','schedule','api')),
    CONSTRAINT calliope_procedure_runs_trust_check
        CHECK (trust_level IN ('recorded','drafted','rehearsed','supervised','autonomous','scheduled')),
    CONSTRAINT calliope_procedure_runs_state_check
        CHECK (state IN ('planned','running','parked','complete','failed','cancelled')),
    CONSTRAINT calliope_procedure_runs_step_check CHECK (current_step >= 0)
);

CREATE INDEX IF NOT EXISTS calliope_procedure_runs_procedure_created_idx
    ON rvbbit.calliope_procedure_runs (playbook_id,created_at DESC);
CREATE INDEX IF NOT EXISTS calliope_procedure_runs_active_idx
    ON rvbbit.calliope_procedure_runs (state,created_at)
    WHERE state IN ('planned','running','parked');

CREATE OR REPLACE FUNCTION rvbbit.calliope_playbook_capability_doc(
    p_playbook_id uuid
) RETURNS text
LANGUAGE sql STABLE
AS $fn$
    SELECT 'capability ' || p.capability_name
        || E'\nkind: cap_playbook'
        || E'\nplaybook kind: ' || p.playbook_kind
        || E'\ntitle: ' || v.title
        || CASE WHEN p.playbook_kind='procedure'
            THEN E'\nsignature: run_calliope_procedure(''' || p.capability_name || ''', inputs, session_id)'
            ELSE E'\nsignature: read_calliope_playbook(''' || p.capability_name || ''')'
           END
        || E'\nversion: ' || v.version::text
        || E'\nreadiness: ' || v.readiness
        || E'\n' || v.synopsis
        || E'\noutcome: ' || (v.semantic_contract->>'outcome')
        || E'\nuse when: ' || coalesce((
            SELECT string_agg(value,'; ' ORDER BY ordinal)
              FROM jsonb_array_elements_text(v.semantic_contract->'when_to_use')
                   WITH ORDINALITY AS item(value,ordinal)
        ),'')
        || CASE WHEN jsonb_array_length(v.semantic_contract->'triggers') > 0
            THEN E'\ntrigger language: ' || coalesce((
                SELECT string_agg(value,', ' ORDER BY ordinal)
                  FROM jsonb_array_elements_text(v.semantic_contract->'triggers')
                       WITH ORDINALITY AS item(value,ordinal)
            ),'') ELSE '' END
        || CASE WHEN jsonb_array_length(v.semantic_contract->'required_capabilities') > 0
            THEN E'\nrequires: ' || coalesce((
                SELECT string_agg(value,', ' ORDER BY ordinal)
                  FROM jsonb_array_elements_text(v.semantic_contract->'required_capabilities')
                       WITH ORDINALITY AS item(value,ordinal)
            ),'') ELSE '' END
        || CASE WHEN p.playbook_kind='procedure'
            THEN E'\nAn approved executable Procedure. Call run_calliope_procedure with the exact capability name; the service pins its immutable contract and validated strategy into a supervised run.'
            ELSE E'\nAn identity-scoped reusable method. Load the authorized immutable version before applying it; concrete tools remain adaptive.'
           END
      FROM rvbbit.calliope_playbooks p
      JOIN rvbbit.calliope_playbook_versions v
        ON v.playbook_id=p.id AND v.version=p.approved_version
     WHERE p.id=p_playbook_id
       AND p.approved_version IS NOT NULL
       AND NOT p.archived
$fn$;

CREATE OR REPLACE VIEW rvbbit.calliope_procedure_observatory AS
SELECT p.id AS procedure_id,
       p.capability_name,
       p.owner_email,
       p.latest_version,
       p.approved_version,
       p.archived,
       v.title,
       v.synopsis,
       v.readiness,
       t.level AS trust_level,
       coalesce(t.clean_runs,0) AS clean_runs,
       s.id AS strategy_id,
       s.revision AS strategy_revision,
       s.strategy_kind,
       s.status AS strategy_status,
       s.source_workflow_id,
       s.source_workflow_revision,
       s.validation,
       p.updated_at,
       coalesce(r.total_runs,0) AS total_runs,
       r.last_run_at,
       r.last_run_state
  FROM rvbbit.calliope_playbooks p
  LEFT JOIN rvbbit.calliope_playbook_versions v
    ON v.playbook_id=p.id AND v.version=p.latest_version
  LEFT JOIN rvbbit.calliope_procedure_trust t ON t.playbook_id=p.id
  LEFT JOIN LATERAL (
      SELECT x.* FROM rvbbit.calliope_procedure_strategies x
       WHERE x.playbook_id=p.id
       ORDER BY (x.status='validated') DESC,x.playbook_version DESC,x.revision DESC
       LIMIT 1
  ) s ON true
  LEFT JOIN LATERAL (
      SELECT count(*)::int AS total_runs,
             max(pr.created_at) AS last_run_at,
             (array_agg(pr.state ORDER BY pr.created_at DESC))[1] AS last_run_state
        FROM rvbbit.calliope_procedure_runs pr WHERE pr.playbook_id=p.id
  ) r ON true
 WHERE p.playbook_kind='procedure';

CREATE OR REPLACE VIEW rvbbit.calliope_procedure_run_observatory AS
SELECT pr.run_id,
       pr.playbook_id AS procedure_id,
       p.capability_name,
       v.title,
       pr.playbook_version,
       pr.strategy_revision,
       s.strategy_kind,
       pr.trigger_kind,
       pr.trust_level,
       pr.state,
       pr.workbench_id,
       pr.guided_run_id,
       pr.current_step,
       pr.requested_by,
       ar.session_id,
       ar.steps,
       ar.result,
       ar.verification,
       ar.error,
       pr.created_at,
       pr.started_at,
       pr.parked_at,
       pr.completed_at
  FROM rvbbit.calliope_procedure_runs pr
  JOIN rvbbit.calliope_action_runs ar ON ar.id=pr.run_id
  JOIN rvbbit.calliope_playbooks p ON p.id=pr.playbook_id
  JOIN rvbbit.calliope_playbook_versions v
    ON v.playbook_id=pr.playbook_id AND v.version=pr.playbook_version
  JOIN rvbbit.calliope_procedure_strategies s ON s.id=pr.strategy_id;

COMMENT ON COLUMN rvbbit.calliope_playbooks.playbook_kind IS
    'method is adaptive knowledge; procedure is an approved contract with separately pinned execution strategies.';
COMMENT ON TABLE rvbbit.calliope_procedure_strategies IS
    'Versioned execution mechanisms beneath immutable semantic Procedure versions.';
COMMENT ON TABLE rvbbit.calliope_procedure_runs IS
    'Procedure-specific linkage over the existing durable Action Run receipt.';
