/**
 * Relation sizes that never wait on a table being rewritten.
 *
 * pg_total_relation_size() and friends open the relation, so they queue
 * behind an ACCESS EXCLUSIVE lock. A loader that truncates and reloads one
 * table at a time (a Salesforce sync, a dbt full refresh) holds that lock for
 * seconds to minutes, and every size listing then stalls on whichever table is
 * loading. Listings run on the small metadata pool: a few stalled listings
 * fill it, and the next request fails with "timeout exceeded when trying to
 * connect" although the database is healthy.
 *
 * These fragments read the exact size when nothing holds the relation
 * exclusively, and otherwise the size recorded at its last vacuum or analyze
 * (pg_class.relpages), which takes no lock at all. A lock taken in the instant
 * between the two is caught by the metadata pool's lock_timeout.
 */

/** CTE listing relations held ACCESS EXCLUSIVE in this database right now. */
export const BUSY_RELATIONS_CTE = `busy_relations AS MATERIALIZED (
  SELECT DISTINCT l.relation AS oid
    FROM pg_locks l
   WHERE l.locktype = 'relation' AND l.granted AND l.mode = 'AccessExclusiveLock'
     AND l.database = (SELECT d.oid FROM pg_database d WHERE d.datname = current_database())
)`

/** True when the table, its TOAST table or one of its indexes is being rewritten. */
export function relationBusy(alias = "c"): string {
  return `(EXISTS (SELECT 1 FROM busy_relations b WHERE b.oid = ${alias}.oid OR b.oid = ${alias}.reltoastrelid)
    OR EXISTS (SELECT 1 FROM pg_index bi JOIN busy_relations b ON b.oid = bi.indexrelid WHERE bi.indrelid = ${alias}.oid))`
}

const BLOCK = `current_setting('block_size')::bigint`
const toastPages = (alias: string) =>
  `coalesce((SELECT t.relpages FROM pg_class t WHERE t.oid = ${alias}.reltoastrelid), 0)`
const indexPages = (alias: string) =>
  `coalesce((SELECT sum(ic.relpages) FROM pg_index pi JOIN pg_class ic ON ic.oid = pi.indexrelid WHERE pi.indrelid = ${alias}.oid), 0)`

/** Heap + TOAST + indexes. Requires BUSY_RELATIONS_CTE in the statement. */
export function safeTotalSize(alias = "c"): string {
  return `(CASE WHEN ${relationBusy(alias)}
    THEN (${alias}.relpages::bigint + ${toastPages(alias)} + ${indexPages(alias)}) * ${BLOCK}
    ELSE coalesce(pg_total_relation_size(${alias}.oid), 0) END)::bigint`
}

/** The table's own heap. Requires BUSY_RELATIONS_CTE in the statement. */
export function safeHeapSize(alias = "c"): string {
  return `(CASE WHEN ${relationBusy(alias)} THEN ${alias}.relpages::bigint * ${BLOCK}
    ELSE coalesce(pg_relation_size(${alias}.oid), 0) END)::bigint`
}

/** All indexes of the table. Requires BUSY_RELATIONS_CTE in the statement. */
export function safeIndexSize(alias = "c"): string {
  return `(CASE WHEN ${relationBusy(alias)} THEN ${indexPages(alias)} * ${BLOCK}
    ELSE coalesce(pg_indexes_size(${alias}.oid), 0) END)::bigint`
}

/** The table's TOAST relation. Requires BUSY_RELATIONS_CTE in the statement. */
export function safeToastSize(alias = "c"): string {
  return `(CASE WHEN ${alias}.reltoastrelid = 0 THEN 0
    WHEN ${relationBusy(alias)} THEN ${toastPages(alias)} * ${BLOCK}
    ELSE coalesce(pg_relation_size(${alias}.reltoastrelid), 0) END)::bigint`
}
