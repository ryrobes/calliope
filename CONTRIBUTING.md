# Contributing

Thanks for helping. Calliope is licensed under the Apache License 2.0, and
contributions are accepted under the same license.

## Sign off your commits

Every commit needs a `Signed-off-by` line certifying the
[Developer Certificate of Origin](https://developercertificate.org/): that you
wrote the change, or otherwise have the right to submit it under this
project's license. Git adds the line for you:

```bash
git commit -s
```

The line must use your real name and an email address you can be reached at.
There is no separate contributor license agreement.

## Before you open a pull request

- Keep a change to one subject, and say in the description what it fixes or
  adds and how you checked it.
- Add or update tests next to the code you changed (`services/calliope/test_*.py`,
  `tests/`, or the crate's own tests).
- Migrations in `crates/pg_rvbbit/sql/migrations/` are forward-only: add a new
  one rather than editing a shipped one.
- Don't commit credentials, customer data or machine-specific settings.

For larger changes, open an issue first so the approach can be agreed before
you spend time on it.
