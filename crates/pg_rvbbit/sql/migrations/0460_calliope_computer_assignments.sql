-- Ordinary desktop work is an explicit assignment mode, including headless runs.
-- Existing read-only and proposal-only assignments keep their existing contract.
ALTER TABLE rvbbit.calliope_work_orders
    DROP CONSTRAINT calliope_work_orders_approval_check;
ALTER TABLE rvbbit.calliope_work_orders
    ADD CONSTRAINT calliope_work_orders_approval_check
    CHECK (approval_policy IN ('read_only','propose_changes','computer_work'));

CREATE INDEX IF NOT EXISTS calliope_computer_stop_occurrence_idx
    ON rvbbit.calliope_workbench_events ((payload->>'key'))
    WHERE event_type='agent.stop.requested';
