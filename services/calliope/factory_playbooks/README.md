# Built-in Playbooks

These methods ship in the calliope-server image. On startup,
`server._ensure_factory_playbooks()` installs approved, everyone-visible copies
owned by `factory@rvbbit.ai`. Stable capability names give fresh installations
the same catalog; contract changes create new immutable versions. Unchanged
contracts do not create versions. User refinements remain separate copies.

## Canon source

Distilled from real approved playbooks:

| Method | File |
| --- | --- |
| Answers | `answers.json` |
| Decision | `decision.json` |
| What Changed | `what-changed.json` |
| Dashboards | `dashboards.json` |

The contract text is preserved except that customer-specific lead/CPL
definitions and calendar/timezone assumptions in What Changed and Decision were
replaced. These now use the deployment's local definitions and business
calendar, asking when consequential context is missing. Source owners, sharing
grants, session references and deployment-specific capability suffixes were
removed.

## Retired method

`playbook.from-vague-dashboard-request-to-art-directed-live-artifact` is replaced
by this catalog. After successful seeding, startup archives only that exact
factory-owned playbook through the normal audited lifecycle API. Its versions,
receipts and user-owned copies are retained. Fresh installations never seed it.

The Dockerfile already copies this directory; no separate import is required.
