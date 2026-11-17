# Calliope source snapshots

This monorepo was created from clean, tracked source snapshots. The
original repository histories remain available in their public repositories;
they were intentionally not copied into this repository.

| Imported tree | Source repository | Source commit | Monorepo location |
| --- | --- | --- | --- |
| RVBBIT SQL and hosted services | `https://github.com/ryrobes/rvbbit-sql` | `8d970abc5eb74073dded167ad7cef34c67f749b3` | repository root |
| RVBBIT Lens / Data Rabbit | `https://github.com/ryrobes/rvbbit-lens` | `c087234451fa87d7416b990e189a476c2423778a` | `datarabbit/` |

Both trees were imported with `git archive`, so ignored build output, local
state, credentials, and each source repository's `.git` directory were
excluded by construction.
