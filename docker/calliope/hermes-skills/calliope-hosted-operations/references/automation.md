# Automation and scheduler health

Keep the schedulers distinct:

- Mirror schedules belong to Warren/dlt and appear in `mirror_status`.
- RVBBIT maintenance uses Postgres/pg_cron jobs such as catalog crawls and acceleration/variant/vortex ticks.
- Hermes cron is for agent prompts and delivery; it may legitimately have no jobs.
- Alerts, workflows, work orders, system learning, and playbooks have their own Warehouse status surfaces.

For a read-only health pass:

1. Call `mirror_status` for mirror schedules.
2. Inspect the relevant Warehouse status tools for alerts, system learning, workflows, or work orders instead of inferring them from Hermes cron.
3. For Hermes cron, use `/opt/hermes/.venv/bin/hermes cron list`, `cron status`, and `cron runs`; `hermes` may not be on `PATH` and there is no `hermes mcp status` command.
4. Treat a recent ticker heartbeat with an empty Hermes job list as healthy-idle, not broken.
5. Report last success, next due time, overdue/stale work, and errors by owning plane.

Hosted defaults should schedule catalog crawl nightly and the RVBBIT acceleration, variant, and vortex ticks. Do not automatically enable the generic mirror-postgres or alerts jobs unless configured for the customer.
