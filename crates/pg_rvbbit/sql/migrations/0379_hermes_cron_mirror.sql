-- 0379: Hermes duty-cycle mirror — cron jobs and executions as warehouse data.
--
-- The 2026-09-03 incident: three specialists' nightly crons were silently
-- drift-skipped for two weeks, with the scheduler's "alert once" delivered to
-- an unread local file. The appliance's doctrine is mirror-state-into-SQL,
-- not admin panels: once executions are rows, the existing surfaces (views,
-- alert sweeps, the Work Inbox, DataRabbit) do the observability for free.
-- The mirror worker (services/calliope/hermes_mirror.py) populates these from
-- each profile's cron/jobs.json + cron/executions.db over the read-only
-- rvbbit_hermes volume.

CREATE TABLE IF NOT EXISTS rvbbit.hermes_cron_jobs (
    profile_key text NOT NULL,
    job_id      text NOT NULL,
    name        text,
    schedule    text,
    enabled     boolean,
    provider    text,
    model       text,
    deliver     text,
    skills      jsonb,
    prompt      text,
    raw         jsonb,
    synced_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_key, job_id)
);

CREATE TABLE IF NOT EXISTS rvbbit.hermes_cron_executions (
    profile_key      text NOT NULL,
    execution_id     text NOT NULL,
    job_id           text,
    status           text,
    claimed_at       timestamptz,
    started_at       timestamptz,
    finished_at      timestamptz,
    error            text,
    output_path      text,
    delivery_outcome text,
    hermes_session_id text,
    synced_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_key, execution_id)
);
CREATE INDEX IF NOT EXISTS hermes_cron_executions_job_idx
    ON rvbbit.hermes_cron_executions (profile_key, job_id, started_at DESC);

-- One row per job: last outcome, leading-failure streak, and the two failure
-- classes that have distinct remediations (drift skips vs real failures).
CREATE OR REPLACE VIEW rvbbit.duty_cycle_health AS
WITH ranked AS (
    SELECT e.*,
           row_number() OVER (
               PARTITION BY e.profile_key, e.job_id
               ORDER BY e.started_at DESC NULLS LAST, e.execution_id DESC
           ) AS rn
    FROM rvbbit.hermes_cron_executions e
),
recent AS (
    SELECT profile_key, job_id,
           count(*) FILTER (WHERE status = 'failed')            AS failures_14,
           coalesce(min(rn) FILTER (WHERE status = 'completed') - 1,
                    count(*))                                    AS failure_streak
    FROM ranked
    WHERE rn <= 14
    GROUP BY profile_key, job_id
)
SELECT j.profile_key,
       j.job_id,
       j.name,
       j.schedule,
       j.enabled,
       j.provider,
       j.model,
       (j.provider IS NOT NULL AND j.model IS NOT NULL)         AS pinned,
       l.status                                                 AS last_status,
       l.started_at                                             AS last_run_at,
       left(l.error, 500)                                       AS last_error,
       coalesce(r.failure_streak, 0)                            AS failure_streak,
       coalesce(r.failures_14, 0)                               AS failures_14,
       coalesce(l.error ILIKE '%drift_skip%', false)            AS drift_skipped
FROM rvbbit.hermes_cron_jobs j
LEFT JOIN ranked l
       ON l.profile_key = j.profile_key AND l.job_id = j.job_id AND l.rn = 1
LEFT JOIN recent r
       ON r.profile_key = j.profile_key AND r.job_id = j.job_id;

-- Raise (and clear) Work Inbox items for unhealthy duty cycles. Dedupe by
-- (profile, job): one open item per broken job, refreshed in place; the item
-- self-resolves when the job next completes. Owner resolution is the
-- appliance-v1 heuristic — the most recently active inbox owner, else the
-- most recently active session owner — because system health belongs to
-- whoever is actually operating the box.
CREATE OR REPLACE FUNCTION rvbbit.duty_cycle_alert_tick()
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
    operator text;
    raised integer := 0;
    cleared integer := 0;
    row_record record;
    key text;
BEGIN
    SELECT owner INTO operator FROM (
        SELECT owner_email AS owner, max(created_at) AS at
          FROM rvbbit.calliope_work_items GROUP BY 1
        UNION ALL
        SELECT lower(owner_email), max(created_at)
          FROM rvbbit.calliope_sessions GROUP BY 1
    ) candidates ORDER BY at DESC NULLS LAST LIMIT 1;
    IF operator IS NULL THEN
        RETURN jsonb_build_object('status', 'no_operator');
    END IF;

    FOR row_record IN
        SELECT * FROM rvbbit.duty_cycle_health
        WHERE enabled AND (failure_streak >= 2 OR (drift_skipped AND last_status = 'failed'))
    LOOP
        key := 'duty-cycle:' || row_record.profile_key || ':' || row_record.job_id;
        IF NOT EXISTS (SELECT 1 FROM rvbbit.calliope_work_items
                        WHERE dedupe_key = key AND state IN ('unread', 'seen')) THEN
            INSERT INTO rvbbit.calliope_work_items
                (id, owner_email, kind, source, dedupe_key, title, summary,
                 urgency, state, context, action_prompt)
            VALUES (
                gen_random_uuid(), operator, 'blocked', 'duty_cycle_alert', key,
                format('Nightly duty cycle failing: %s',
                       coalesce(row_record.name, row_record.job_id)),
                format('%s consecutive failure(s) on %s (profile %s). Last error: %s',
                       row_record.failure_streak,
                       coalesce(row_record.name, row_record.job_id),
                       row_record.profile_key,
                       coalesce(left(row_record.last_error, 300), 'unknown')),
                CASE WHEN row_record.failure_streak >= 4 THEN 'high' ELSE 'normal' END,
                'unread',
                jsonb_build_object('profile_key', row_record.profile_key,
                                   'job_id', row_record.job_id,
                                   'failure_streak', row_record.failure_streak,
                                   'drift_skipped', row_record.drift_skipped,
                                   'last_run_at', row_record.last_run_at),
                format('Investigate why the scheduled job "%s" on profile %s keeps failing. '
                       'Query rvbbit.duty_cycle_health and rvbbit.hermes_cron_executions for '
                       'the history, diagnose the error, and propose or apply the fix.',
                       coalesce(row_record.name, row_record.job_id), row_record.profile_key)
            );
            raised := raised + 1;
        END IF;
    END LOOP;

    -- Self-clearing: a job that completed most recently closes its open item.
    UPDATE rvbbit.calliope_work_items w
       SET state = 'done', resolved_at = now(), updated_at = now()
      FROM rvbbit.duty_cycle_health h
     WHERE w.source = 'duty_cycle_alert'
       AND w.state IN ('unread', 'seen')
       AND w.dedupe_key = 'duty-cycle:' || h.profile_key || ':' || h.job_id
       AND h.last_status = 'completed';
    GET DIAGNOSTICS cleared = ROW_COUNT;

    RETURN jsonb_build_object('raised', raised, 'cleared', cleared);
END $$;
