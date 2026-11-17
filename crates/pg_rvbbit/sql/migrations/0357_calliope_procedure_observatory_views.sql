-- Forward repair for appliances that recorded 0356 before its DataRabbit
-- observability projection was present in the packaged extension image.

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

COMMENT ON VIEW rvbbit.calliope_procedure_observatory IS
    'DataRabbit Procedure catalog projection over semantic versions, execution strategies, trust, and run counts.';
COMMENT ON VIEW rvbbit.calliope_procedure_run_observatory IS
    'DataRabbit Procedure run projection over pinned strategies and durable Action Run receipts.';
