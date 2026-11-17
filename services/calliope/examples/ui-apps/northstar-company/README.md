# Northstar connected company apps

Fictional HVAC workspace authored by Callie through the normal SQL, UI Studio,
preview and publication tools. This is a reusable example of three apps sharing
records; it does not install a special data service.

- `northstar-dispatch`: a dispatcher assigns versioned visits.
- `northstar-my-visits`: a technician sees assigned work, keeps local drafts and
  submits a report with attachment IDs.
- `northstar-review`: a dispatcher reviews saved reports and their evidence.
- `northstar-analysis.html`: an ordinary browser dashboard reading the existing
  authorized Dispatch snapshot. Its counts cover the bounded active queue.

The JSON definitions pair with their `.manifest.json` action contracts. Records
and append-only reports/reviews live in Postgres. `setup.sql` is the fictional
fixture, not an application migration or production customer seed.

To reproduce, create suitable local test accounts through Calliope account
management, adapt the fixture staff emails, run the setup with ordinary workspace
SQL, and publish the exact files with `create_live_app` (native `runtime_kind`
`a2ui`, `source_files["app.json"]` containing JSON text). Share My visits with the
technician and dispatcher; share Dispatch, Review and analysis with the dispatcher.
No credentials are included. Existing app grants and actor-bound SQL both apply.

This example's access checks are scoped to the app itself. It does not change the instance's broader SQL permissions or add database row-level
security. Keep those permissions in mind when adapting it for real staff.

The [follow-up reason revision](follow-up-reason/README.md) adds an employee-requested
field and companion HTML breakdown while preserving these baseline versions and
their pending drafts.
