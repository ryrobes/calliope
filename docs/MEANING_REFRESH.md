# Meaning refresh and publication

Migration 0458 separates published dossiers from current refresh work. Floor reads the latest stored publication per durable subject key. A failed, stale, or rejected draft cannot replace it. Existing visible dossiers are retained as the migration baseline, including their existing provisional status.

## Scheduling and discovery

Meaning has two independently controlled stages. `rvbbit_meaning_sweep` in
`pg_cron` profiles relations, columns, and joins. The Calliope server's
`WAREHOUSE_MEANING_NARRATIVE_WORKER` (default `1`) consumes the SQL narrative queue
to recognize sources, expand the company map, and publish reviewed area dossiers.
A successful cron sweep does not prove that the narrative worker is enabled.
Benchmark baselines may deliberately set it to `0`; restarting cron alone does
not undo that pause. The server logs the disabled state explicitly.

For missing areas, compare `meaning_relations` profiling and `recognized_at`,
`meaning_narrative_queue(20)`, `meaning_recognitions`, `meaning_model_calls`, and
`meaning_published_domains`, then check the server's worker flag and startup log.
Saved metrics and cubes do not themselves create an area.

The worker defers unplaced-table assignment until every eligible new source has
participated in a company map. Otherwise, the gap between recognition and mapping
could classify new-source tables against unrelated old areas and preserve those
premature assignments. Existing published areas stay available during this work.

Company maps are additive. Recognizing a previously incorporated source again does not queue a company remap. New sources can add subjects or members; existing assignments and stated placements are preserved. Subject refresh fingerprints cover membership, column structure, recognition descriptions, published label definitions, and active stated facts. Row counts, timestamps, and usage counters do not invalidate dossiers. Changed subjects retain the existing six-hour refresh cooldown; error retries retain their backoff.

`rvbbit.meaning_merge_subjects(from_key, into_key, reason)` is an explicit administrative operation. It transfers placements, enrichments, and active stated placements, records an alias, and preserves prior dossier versions. Automatic core-less-subject folding is disabled. Migration repair follows each orphan enrichment's current relation placement and records the change in `meaning_association_repairs`; unresolved relations and sentinels are not guessed.

## Cost accounting

`meaning_model_calls` records a reservation and a terminal receipt for every direct narrative completion: recognition, map, dossier, enrichment proposal/revision, and JSON repair. A transport failure retains its reservation because the provider's outcome is unknown. Hidden client transport retries are disabled. The rolling budget uses the latest event per call, independently of subject deletion or subsequent updates.

The default minimum reservation is $0.25 (`WAREHOUSE_MEANING_CALL_RESERVE_USD`), increased for large inputs/output limits using configured token prices. Provider receipts settle actual cost when supplied; otherwise the existing configured price estimate applies. This is a pre-call spending guard, not a guaranteed provider billing ceiling: price changes, reasoning usage, and unknown transport outcomes can differ from estimates. The default narrative budget remains $5 per rolling 24 hours. SQL/Jev profiling, join checks, reviews, and row classification are separate from this direct-completion ledger; the existing $2 enrichment gate remains and is refreshed between work items.

Historical deleted costs cannot be reconstructed. Surviving recent records form a conservative opening balance, which can include older cumulative recognition spend. New events provide proper rolling-window accounting.

## Verification

Use an isolated Postgres image with migration 0458 installed; never point this integration suite at a shared deployment:

```sh
MEANING_TEST_DSN=postgresql://... pytest services/calliope/test_meaning_preservation.py
```

Tests use synthetic metadata and fake model responses, roll back their writes, and cover additive maps, stable publications, changed-source scheduling, row-count stability, explicit merges, association repair, failed and successful review, stale replies, budget enforcement on repair calls, and Floor's last-good cache.
