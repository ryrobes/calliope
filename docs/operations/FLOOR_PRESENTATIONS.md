# Floor presentation warming

Migration 0407 adds a durable current-label cache and a bounded warmer. Initial
Floor loading reads the cache; it never invokes the title/status model. A separate
authenticated endpoint returns available labels immediately and starts one bounded
background refresh per server process. The browser polls while work is pending and
updates existing cards without resetting filters, pagination, scroll, or focus.
Late responses are ignored after navigation or entry into a historical frame.

The canonical cron installers register `rvbbit_floor_presentations` at minutes
2, 17, 32, and 47. It calls `rvbbit.floor_presentations_warm()` in the application
database. This is separate from `work_graph_snapshot_run()`: snapshots copy the
work graph's stored fields and do not run Floor label inference.

Both paths use the same SQL candidate builder and existing memoized
`clover_llm_extract` operator. Its exact source text and prompt are preserved,
so existing successful operator receipts can populate the new cache without
another model call. Candidate budgets remain 20 awaiting, 5 in motion, and 25
done per viewer; cron covers those budgets per owner across the system. Older
completed cards stay searchable using their canonical titles and summaries.

Cache reads join authorized candidates and require a matching content/operator
fingerprint. Changed inputs fall back to canonical titles until fresh labels arrive.
The in-motion freshness bucket changes every 30 minutes. Historical frames use
stored historical titles, never current generated labels.

Each item takes a nonblocking advisory transaction lock shared by cron and server
refreshes. Each successful item commits separately. Invalid model output is held
for retry after five minutes. Cron considers at most 100 pending candidates per
pass (configurable up to 200), checks a five-minute time budget between calls,
and prioritizes awaiting/in-motion work. A call already running retains its backend
timeout. The cache prunes entries untouched for 30 days. These operations do not
edit source work items, but model calls write normal receipts and can incur cost.

Deployment: apply 0407 and register the cron job before enabling the new server
routes. SQL warming can be hotpatched without restarting PostgreSQL or Calliope.
The server/UI files require a Calliope server rollout; do not restart an active
embedded excavation worker solely to activate this change. Future PostgreSQL
images embed the migration, and the normal cron installers reconcile the job.

Validation includes disposable PostgreSQL tests for scope, input/model invalidation,
cache-only reads, failure backoff, per-item concurrency, per-item commit survival,
and background response latency; Chromium verifies render-before-enrichment and
preserved board state when labels arrive. Node tests reject obsolete updates.
