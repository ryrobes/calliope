# An employee request becomes an app revision

These production files extend the parent Northstar example. A technician asked
for a follow-up reason on visit reports and a dispatcher breakdown by reason.
Callie prepared the changes through the ordinary authoring tools and published
My visits v3, Dispatch v3, and the HTML analysis v2. Review remains unchanged.

Apply `migration.sql` after the parent's setup. It adds a nullable column without
rewriting history; old v2 report inserts still work. Publish the native JSON and
matching manifests with `update_live_app`, preserving ownership and grants.
Publish `northstar-analysis.html` as HTML. The manifests' `change_requests` UUID is
the local acceptance-test request: replace it with your own explicitly shared
request UUID, or remove the property when installing a standalone example.

The aggregate covers **all saved reports, across all submission dates**, including
superseded reports and accepted visits. It counts reports, not visits. Existing
reports and unanswered reasons remain **Not recorded**. The active queue analysis
is a separate, bounded population. Both reads use the existing dispatcher access
predicate and the existing app snapshot route.

Run the isolated SQL compatibility checks with PGlite (verified with 0.5.8):

```sh
npm install --prefix /tmp/northstar-sql-check @electric-sql/pglite@0.5.8
PGLITE_MODULE=/tmp/northstar-sql-check/node_modules/@electric-sql/pglite/dist/index.js \
  node check-sql.mjs
```

This creates an in-memory fictional fixture, not an instance connection. The
checks cover optional values, invalid inputs, actor/version guards, old inserts,
duplicate submissions, and the report population. Live device and publication
evidence is recorded in [the revision-loop report](../../../../../../docs/NATIVE_APP_REVISION_LOOP.md)
and [the compact verification receipt](verification.json).
