-- 0367: the work ledger — hours returned, receipt-backed.
--
-- Classifies completed work objects (answered questions, dashboards and
-- their revisions, synthesized documents, briefs, workflow and procedure
-- runs) into a flat view, credits each class a conservative fixed
-- human-minutes estimate, prices hours at per-member rates, and snapshots
-- period reports into a table so they replicate anywhere a mirror reaches.
--
-- Honesty rules, enforced by shape:
--   * Credits are flat per-class minutes set by an operator — never
--     model-estimated, always visible on the report.
--   * The view deliberately EXCLUDES machinery (raw queries, sketches,
--     failed runs): when in doubt the ledger undercounts.
--   * Operators (rates row with excluded=true) drop out of snapshots.

CREATE TABLE IF NOT EXISTS rvbbit.work_ledger_credits (
    class        text PRIMARY KEY,
    label        text NOT NULL,
    minutes_each int  NOT NULL CHECK (minutes_each >= 0),
    sort         int  NOT NULL DEFAULT 100,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

INSERT INTO rvbbit.work_ledger_credits (class, label, minutes_each, sort) VALUES
    ('question',           'Business questions answered',        10, 10),
    ('dashboard_build',    'Dashboards & live apps built',      120, 20),
    ('dashboard_revision', 'Dashboard revisions shipped',        15, 30),
    ('document',           'Documents synthesized',              15, 40),
    ('brief',              'Morning briefs prepared',            15, 50),
    ('workflow_run',       'Recurring reports run',              45, 60),
    ('procedure_run',      'Governed procedures completed',      30, 70)
ON CONFLICT (class) DO NOTHING;

CREATE TABLE IF NOT EXISTS rvbbit.work_ledger_rates (
    owner_email text PRIMARY KEY,
    hourly_usd  numeric(10,2) NOT NULL DEFAULT 45 CHECK (hourly_usd >= 0),
    excluded    boolean NOT NULL DEFAULT false,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
-- '*' is the default rate for members without their own row.
INSERT INTO rvbbit.work_ledger_rates (owner_email, hourly_usd)
VALUES ('*', 45) ON CONFLICT (owner_email) DO NOTHING;

-- One row per completed work object. Portable: every source table also
-- exists on legacy appliances, and each block degrades to zero rows when
-- its source is absent from the deployment's usage.
CREATE OR REPLACE VIEW rvbbit.work_ledger_v AS
    SELECT 'question'::text AS class, s.owner_email, t.created_at AS occurred_at,
           t.id::text AS ref, t.session_id::text AS session_ref
    FROM rvbbit.calliope_turns t
    JOIN rvbbit.calliope_sessions s ON s.id = t.session_id
    WHERE t.user_message IS NOT NULL AND t.assistant_message IS NOT NULL
      AND t.user_message NOT LIKE '[%'
      AND t.user_message NOT LIKE 'Brief ·%'
      AND t.user_message NOT LIKE 'Work Inbox ·%'
      AND t.user_message NOT LIKE 'Explore Dream ·%'
      AND t.user_message NOT LIKE 'Gallery %'
      AND t.user_message NOT LIKE 'Manual Workflow ·%'
      AND t.user_message NOT LIKE 'Bring in Sheet%'
      AND t.user_message NOT LIKE 'Refresh private Doc%'
UNION ALL
    SELECT 'dashboard_build', d.owner_email, d.created_at, d.slug, NULL
    FROM rvbbit.dashboards d
    WHERE NOT d.archived
UNION ALL
    SELECT 'dashboard_revision', d.owner_email, v.created_at,
           d.slug || ' v' || v.version, NULL
    FROM rvbbit.dashboard_versions v
    JOIN rvbbit.dashboards d ON d.id = v.dashboard_id
    WHERE v.version > 1 AND NOT d.archived
UNION ALL
    SELECT 'document', s.owner_email, sur.created_at, sur.id::text,
           sur.session_id::text
    FROM rvbbit.calliope_surfaces sur
    JOIN rvbbit.calliope_sessions s ON s.id = sur.session_id
    WHERE sur.kind = 'document'
UNION ALL
    SELECT 'brief', b.owner_email, b.created_at, b.id::text, b.session_id::text
    FROM rvbbit.calliope_briefs b
UNION ALL
    SELECT 'workflow_run', w.owner_email, coalesce(w.completed_at, w.started_at),
           w.id::text, w.session_id::text
    FROM rvbbit.calliope_workflow_runs w
    WHERE w.status = 'complete'
UNION ALL
    SELECT 'procedure_run', r.requested_by, coalesce(r.completed_at, r.created_at),
           r.run_id::text, ar.session_id::text
    FROM rvbbit.calliope_procedure_runs r
    JOIN rvbbit.calliope_action_runs ar ON ar.id = r.run_id
    WHERE r.state = 'complete';

CREATE TABLE IF NOT EXISTS rvbbit.work_ledger_snapshots (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    period_start date NOT NULL,
    period_end   date NOT NULL,
    generated_by text,
    generated_at timestamptz NOT NULL DEFAULT now(),
    report       jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS work_ledger_snapshots_period_idx
    ON rvbbit.work_ledger_snapshots (period_start, period_end, generated_at DESC);

-- Aggregate a period into one replicable jsonb report and store it.
-- Members with rates.excluded=true (operators, test accounts) are dropped.
CREATE OR REPLACE FUNCTION rvbbit.work_ledger_snapshot(
    p_start date,
    p_end date,
    p_generated_by text DEFAULT NULL
) RETURNS jsonb LANGUAGE sql AS $$
WITH scoped AS (
    SELECT v.*
    FROM rvbbit.work_ledger_v v
    LEFT JOIN rvbbit.work_ledger_rates r ON r.owner_email = v.owner_email
    WHERE v.occurred_at >= p_start
      AND v.occurred_at < p_end + 1
      AND NOT coalesce(r.excluded, false)
), default_rate AS (
    SELECT hourly_usd FROM rvbbit.work_ledger_rates WHERE owner_email = '*'
), by_class AS (
    SELECT c.class, c.label, c.minutes_each, c.sort, count(s.*) AS n,
           round(count(s.*) * c.minutes_each / 60.0, 1) AS hours
    FROM rvbbit.work_ledger_credits c
    LEFT JOIN scoped s ON s.class = c.class
    GROUP BY c.class, c.label, c.minutes_each, c.sort
), by_member AS (
    SELECT s.owner_email, count(*) AS n,
           round(sum(c.minutes_each) / 60.0, 1) AS hours,
           round(sum(c.minutes_each) / 60.0
                 * coalesce(r.hourly_usd, (SELECT hourly_usd FROM default_rate)), 0) AS value_usd
    FROM scoped s
    JOIN rvbbit.work_ledger_credits c ON c.class = s.class
    LEFT JOIN rvbbit.work_ledger_rates r ON r.owner_email = s.owner_email
    GROUP BY s.owner_email, r.hourly_usd
), totals AS (
    SELECT coalesce(sum(hours), 0) AS hours, coalesce(sum(value_usd), 0) AS value_usd,
           count(*) AS members
    FROM by_member
), report AS (
    SELECT jsonb_build_object(
        'period_start', p_start,
        'period_end', p_end,
        'members', (SELECT members FROM totals),
        'total_hours', (SELECT hours FROM totals),
        'total_value_usd', (SELECT value_usd FROM totals),
        'default_hourly_usd', (SELECT hourly_usd FROM default_rate),
        'classes', (SELECT jsonb_agg(jsonb_build_object(
            'class', class, 'label', label, 'minutes_each', minutes_each,
            'count', n, 'hours', hours) ORDER BY sort) FROM by_class),
        'members_detail', (SELECT coalesce(jsonb_agg(jsonb_build_object(
            'owner_email', owner_email, 'count', n, 'hours', hours,
            'value_usd', value_usd) ORDER BY hours DESC), '[]'::jsonb) FROM by_member)
    ) AS doc
), stored AS (
    INSERT INTO rvbbit.work_ledger_snapshots (period_start, period_end, generated_by, report)
    SELECT p_start, p_end, p_generated_by, doc FROM report
    RETURNING report
)
SELECT report FROM stored;
$$;
