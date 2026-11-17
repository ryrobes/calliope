//! When people read tables.
//!
//! Two things run on this evidence: the freshness tick refreshes a dirty
//! accelerated table sooner when someone is reading it, and the autopilot
//! accelerates tables that are read on several days and parks ones nobody reads.
//!
//! Every SELECT whose rows go to a client connection (an app, DataRabbit, psql,
//! Calliope's agents) records the tables it reads here, at ExecutorRun
//! (ExecutorStart of a plain SELECT still has a placeholder destination),
//! whether it reads them directly, through a view, from the heap or from the
//! accelerated copy. rvbbit's own work never counts: refreshes, cron jobs and
//! functions run their SQL inside the server (SPI and the like), not to a
//! client. Statements that write (INSERT ... SELECT, data-modifying CTEs) don't
//! count, nor do temporary tables, catalogs or rvbbit's own tables. A session
//! that sweeps tables in the background opts out with
//! `SET rvbbit.count_reads = off` (or per role: `ALTER ROLE ... SET`).
//!
//! The table lives in shared memory, keyed by database and table (lost on
//! restart); the tick and the autopilot copy it into rvbbit.accel_read_evidence
//! and rvbbit.accel_read_days so the evidence survives restarts. When the table
//! is full around an OID, the slot read longest ago is reused: it has been
//! copied out long since.

use std::cell::RefCell;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};

use pgrx::lwlock::PgLwLock;
use pgrx::prelude::*;
use pgrx::shmem::PGRXSharedMemory;

const NSLOTS: usize = 4096;
const PROBE: usize = 64;
/// A backend records a table's read at most this often.
const RECORD_EVERY_SECS: i64 = 10;

#[derive(Copy, Clone)]
struct ReadSlot {
    /// Database OID (OIDs are per database).
    db: u32,
    /// Table OID; 0 = empty.
    oid: u32,
    /// Last read, PostgreSQL-epoch microseconds.
    last_read: i64,
    reads: u64,
}
unsafe impl PGRXSharedMemory for ReadSlot {}

const EMPTY: ReadSlot = ReadSlot {
    db: 0,
    oid: 0,
    last_read: 0,
    reads: 0,
};

static READS: PgLwLock<[ReadSlot; NSLOTS]> = unsafe { PgLwLock::new(c"rvbbit_table_reads") };
static SHMEM_READY: AtomicBool = AtomicBool::new(false);

thread_local! {
    /// Per backend: when each table's read was last written to shared memory.
    static LAST_RECORDED: RefCell<HashMap<u32, i64>> = RefCell::new(HashMap::new());
}

static mut PREV_EXECUTOR_RUN_HOOK: pg_sys::ExecutorRun_hook_type = None;

/// Chain the ExecutorRun hook. Called from `_PG_init`.
pub unsafe fn register_hooks() {
    PREV_EXECUTOR_RUN_HOOK = pg_sys::ExecutorRun_hook;
    pg_sys::ExecutorRun_hook = Some(rvbbit_read_evidence_executor_run_hook);
}

#[pg_guard]
unsafe extern "C-unwind" fn rvbbit_read_evidence_executor_run_hook(
    query_desc: *mut pg_sys::QueryDesc,
    direction: pg_sys::ScanDirection::Type,
    count: u64,
) {
    note_reads(query_desc);
    if let Some(prev) = PREV_EXECUTOR_RUN_HOOK {
        prev(query_desc, direction, count);
    } else {
        pg_sys::standard_ExecutorRun(query_desc, direction, count);
    }
}

/// Register the shared-memory table. Must be called from `_PG_init`.
pub fn register_shmem() {
    pgrx::pg_shmem_init!(
        READS = {
            SHMEM_READY.store(true, Ordering::SeqCst);
            [EMPTY; NSLOTS]
        }
    );
}

fn record(db: u32, oid: u32, now: i64) {
    let mut slots = READS.exclusive();
    let start = (oid as usize ^ (db as usize).rotate_left(16)).wrapping_mul(2_654_435_761) % NSLOTS;
    let mut oldest = start;
    for i in 0..PROBE {
        let at = (start + i) % NSLOTS;
        let slot = &mut slots[at];
        if (slot.db == db && slot.oid == oid) || slot.oid == 0 {
            if slot.oid == 0 {
                slot.reads = 0;
            }
            slot.db = db;
            slot.oid = oid;
            slot.last_read = slot.last_read.max(now);
            slot.reads = slot.reads.saturating_add(1);
            return;
        }
        if slot.last_read < slots[oldest].last_read {
            oldest = at;
        }
    }
    slots[oldest] = ReadSlot { db, oid, last_read: now, reads: 1 };
}

/// `SET rvbbit.count_reads = off` keeps this session's reads out of the evidence.
fn counting_enabled() -> bool {
    let ptr = unsafe { pg_sys::GetConfigOption(c"rvbbit.count_reads".as_ptr(), true, false) };
    if ptr.is_null() {
        return true;
    }
    let value = unsafe { std::ffi::CStr::from_ptr(ptr) }.to_string_lossy();
    !matches!(value.trim().to_ascii_lowercase().as_str(), "off" | "false" | "0" | "no")
}

/// A permanent table, partitioned table or materialized view outside rvbbit's schema.
unsafe fn countable(oid: pg_sys::Oid, relkind: std::ffi::c_char) -> bool {
    let kind = relkind as u8;
    if !matches!(kind, b'r' | b'p' | b'm') {
        return false;
    }
    if pg_sys::get_rel_persistence(oid) as u8 != b'p' {
        return false;
    }
    let ns = pg_sys::get_rel_namespace(oid);
    ns != pg_sys::InvalidOid && ns != rvbbit_namespace() && !pg_sys::IsToastNamespace(ns)
}

fn rvbbit_namespace() -> pg_sys::Oid {
    thread_local! {
        static NS: std::cell::Cell<Option<pg_sys::Oid>> = const { std::cell::Cell::new(None) };
    }
    NS.with(|c| {
        if let Some(ns) = c.get().filter(|ns| *ns != pg_sys::InvalidOid) {
            return ns;
        }
        let ns = unsafe { pg_sys::get_namespace_oid(c"rvbbit".as_ptr(), true) };
        c.set(Some(ns));
        ns
    })
}

/// Record the tables this SELECT reads, when its rows go to a client.
unsafe fn note_reads(query_desc: *mut pg_sys::QueryDesc) {
    if !SHMEM_READY.load(Ordering::Relaxed) || query_desc.is_null() {
        return;
    }
    let dest = (*query_desc).dest;
    if dest.is_null()
        || !matches!(
            (*dest).mydest,
            pg_sys::CommandDest::DestRemote
                | pg_sys::CommandDest::DestRemoteExecute
                | pg_sys::CommandDest::DestRemoteSimple
        )
    {
        return;
    }
    let stmt = (*query_desc).plannedstmt;
    if stmt.is_null()
        || (*stmt).rtable.is_null()
        || (*stmt).commandType != pg_sys::CmdType::CMD_SELECT
        || (*stmt).hasModifyingCTE
    {
        return;
    }
    if !counting_enabled() {
        return;
    }
    let db = pg_sys::MyDatabaseId.to_u32();
    let now = pg_sys::GetCurrentTimestamp();
    for i in 0..pg_sys::list_length((*stmt).rtable) {
        let rte = pg_sys::list_nth((*stmt).rtable, i) as *mut pg_sys::RangeTblEntry;
        if rte.is_null() || (*rte).rtekind != pg_sys::RTEKind::RTE_RELATION {
            continue;
        }
        let oid = (*rte).relid.to_u32();
        if oid < pg_sys::FirstNormalObjectId {
            continue;
        }
        let due = LAST_RECORDED.with(|m| {
            m.borrow()
                .get(&oid)
                .map_or(true, |&t| now - t >= RECORD_EVERY_SECS * 1_000_000)
        });
        if !due {
            continue;
        }
        LAST_RECORDED.with(|m| m.borrow_mut().insert(oid, now));
        if countable((*rte).relid, (*rte).relkind) {
            record(db, oid, now);
        }
    }
}

/// Reads of this database's tables by client SELECTs since the server
/// started: the last read time and how many reads were recorded (at most one
/// per backend per table every 10 seconds).
#[pg_extern]
fn table_reads() -> TableIterator<
    'static,
    (
        name!(table_oid, pg_sys::Oid),
        name!(last_read_at, TimestampWithTimeZone),
        name!(reads, i64),
    ),
> {
    if !SHMEM_READY.load(Ordering::Relaxed) {
        return TableIterator::new(Vec::new());
    }
    let db = unsafe { pg_sys::MyDatabaseId }.to_u32();
    let rows: Vec<_> = {
        let slots = READS.share();
        slots
            .iter()
            .filter(|s| s.oid != 0 && s.db == db)
            .filter_map(|s| {
                TimestampWithTimeZone::try_from(s.last_read)
                    .ok()
                    .map(|t| (pg_sys::Oid::from(s.oid), t, s.reads as i64))
            })
            .collect()
    };
    TableIterator::new(rows)
}

#[cfg(any(test, feature = "pg_test"))]
#[pg_schema]
mod tests {
    use pgrx::prelude::*;

    #[pg_test]
    fn table_reads_is_callable() {
        // A non-preloaded test instance has no segment and returns no rows.
        let n: Option<i64> = Spi::get_one("SELECT count(*) FROM rvbbit.table_reads()").unwrap();
        assert!(n.is_some());
    }
}
