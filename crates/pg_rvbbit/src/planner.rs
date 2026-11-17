//! Phase 2c: planner integration for transparent reads.
//!
//! Architecture (designed with future SQL rewriting in mind):
//!
//! ```text
//! User query
//!     |
//!     v
//! [Postgres parser]
//!     |
//!     v
//! [post_parse_analyze_hook]  <-- Phase 5: REWRITE Query tree using
//!     |                          rvbbit.shreds (e.g. replace
//!     v                          `response->>'foo'` with Var pointing
//! [Postgres planner]              at the typed shred column).
//!     |
//!     v
//! [set_rel_pathlist_hook]    <-- THIS FILE. For each base relation,
//!     |                          if it's an rvbbit table with row
//!     v                          groups, add a CustomPath that the
//! [Postgres executor]            planner can choose.
//! ```
//!
//! Phase 2c (this file) only handles the read-path replacement. The
//! rewriter layer is its own hook; it consumes the same `rvbbit.shreds`
//! catalog but operates entirely on Query trees, never touching this
//! file. Clean separation.

use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::ffi::{c_char, CStr, CString};

use pgrx::pg_guard;
use pgrx::pg_sys;

use crate::kept_plan::KeptPlan;
use crate::{custom_scan, router};

static mut PREV_REL_PATHLIST_HOOK: pg_sys::set_rel_pathlist_hook_type = None;
static mut PREV_GET_RELATION_INFO_HOOK: pg_sys::get_relation_info_hook_type = None;
static mut PREV_PLANNER_HOOK: pg_sys::planner_hook_type = None;

const FIRST_NORMAL_OBJECT_ID: u32 = 16384;

thread_local! {
    /// Set while the planner hooks read rvbbit's catalog through SPI.
    static IN_HOOK: Cell<bool> = const { Cell::new(false) };
    // True-only cache. If the catalog is absent during CREATE EXTENSION or in a
    // sibling database that only hosts pg_cron, do not cache false forever.
    static RVBBIT_TABLES_PRESENT: Cell<bool> = const { Cell::new(false) };
    static IS_RVBBIT_CACHE: RefCell<HashMap<u32, bool>> = RefCell::new(HashMap::new());
    static ROW_GROUPS_CACHE: RefCell<HashMap<u32, i64>> = RefCell::new(HashMap::new());
    /// Cached `(sum_n_rows, sum_n_bytes, row groups)` per table. Filled on
    /// first planner pass over an rvbbit table, persists for the backend's
    /// lifetime. Invalidated locally when this backend runs compact() (see
    /// `invalidate_planner_aggregates`). Cross-backend writes (another
    /// session compacts the table) leave the cache slightly stale until
    /// this backend restarts — that means a slightly off cost estimate for
    /// new row groups, not a correctness issue, so it's an acceptable
    /// tradeoff for saving 2 SPI queries per plan.
    static AGG_CACHE: RefCell<HashMap<u32, (f64, f64, f64)>> = RefCell::new(HashMap::new());
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum LatestAccelReadMode {
    None,
    ParquetOnly,
    ParquetWithReadOverlay,
}

/// Drop the per-table planner aggregate cache. Called from compact() so
/// the same backend immediately sees its own writes reflected in plan
/// row estimates.
pub fn invalidate_planner_aggregates(oid: u32) {
    AGG_CACHE.with(|c| c.borrow_mut().remove(&oid));
}

/// Drop ALL cached aggregate estimates (cross-backend epoch flush).
pub fn invalidate_planner_aggregates_all() {
    AGG_CACHE.with(|c| c.borrow_mut().clear());
}

/// Wrapper to make `CustomPathMethods` (which contains raw fn pointers)
/// usable in a `static`. Static items in Rust must be `Sync`; raw pointers
/// aren't `Sync` by default. The methods table is read-only after init
/// and PG only reads it from inside callbacks we control, so it's safe.
#[repr(transparent)]
pub(crate) struct PathMethodsSync(pub pg_sys::CustomPathMethods);
unsafe impl Sync for PathMethodsSync {}

pub(crate) static RVBBIT_PATH_METHODS: PathMethodsSync =
    PathMethodsSync(pg_sys::CustomPathMethods {
        CustomName: c"RvbbitParquetScan".as_ptr() as *const c_char,
        PlanCustomPath: Some(custom_scan::plan_custom_path),
        ReparameterizeCustomPathByChild: None,
    });

/// Install the planner hooks. Called from `_PG_init`.
pub unsafe fn register_hooks() {
    // get_relation_info_hook fires BEFORE the planner generates any base
    // paths. We use it to override the stale rel->rows/pages estimates so
    // the eventual SeqScan path's cost reflects the real (post-compact)
    // size of the relation. Then set_rel_pathlist_hook adds our custom
    // path with a competitive cost and the planner picks it naturally.
    PREV_GET_RELATION_INFO_HOOK = pg_sys::get_relation_info_hook;
    pg_sys::get_relation_info_hook = Some(rvbbit_get_relation_info_hook);

    PREV_REL_PATHLIST_HOOK = pg_sys::set_rel_pathlist_hook;
    pg_sys::set_rel_pathlist_hook = Some(rvbbit_set_rel_pathlist_hook);

    PREV_PLANNER_HOOK = pg_sys::planner_hook;
    pg_sys::planner_hook = Some(rvbbit_planner_hook);

    pg_sys::RegisterCustomScanMethods(&custom_scan::RVBBIT_SCAN_METHODS.0);
}

#[pg_guard]
unsafe extern "C-unwind" fn rvbbit_planner_hook(
    parse: *mut pg_sys::Query,
    query_string: *const c_char,
    cursor_options: std::ffi::c_int,
    bound_params: pg_sys::ParamListInfo,
) -> *mut pg_sys::PlannedStmt {
    let _planning = PlanningScope::enter();
    let _asof_scope = crate::time_travel::planner_scope(query_string);
    // A current-only table may still physically carry cumulative delta files
    // and correctness tombstones until its next major fold. Falling back to the
    // heap for an AS OF query would silently return LATEST rows, which is much
    // worse than saying history is unavailable. Reject at planner entry before
    // any route, metadata rewrite, or force-heap fallback can erase the AS OF
    // semantics. Nested SPI used by this check is guarded by IN_HOOK.
    if crate::time_travel::active_as_of_enabled() && !IN_HOOK.with(|f| f.get()) {
        if let Some(oid) = first_current_history_rvbbit_rte(parse) {
            pgrx::error!(
                "rvbbit AS OF: table OID {} uses history_policy=current; historical reads are unavailable",
                oid
            );
        }
    }
    if crate::pg_context::nonsystem_view_access_restricted() {
        return call_next_planner(parse, query_string, cursor_options, bound_params);
    }
    // Snapshot the native+vortex route selection at planner entry, before
    // standard_planner's internal route re-computation can clobber the global flag.
    // add_rvbbit_path reads this captured value to stash into the scan node.
    router::set_native_vortex_plan_captured(router::native_vortex_route_selected());
    if force_heap_scan_enabled() {
        return call_next_planner(parse, query_string, cursor_options, bound_params);
    }
    if router::pg_rowstore_route_selected() {
        let planned = call_next_planner(parse, query_string, cursor_options, bound_params);
        router::set_pg_rowstore_route_selected(false);
        return planned;
    }

    let disable_nestloop = !IN_HOOK.with(|f| f.get()) && query_has_join_heavy_rvbbit(parse);
    if !disable_nestloop {
        return call_next_planner(parse, query_string, cursor_options, bound_params);
    }

    // Stash the value to restore in a thread-local so an xact/subxact abort
    // callback can put it back if standard_planner longjmps past the restore
    // below (ffi-02 — otherwise nestloop stays globally disabled for the whole
    // pooled session).
    let saved = pg_sys::enable_nestloop;
    SAVED_NESTLOOP.with(|s| s.set(Some(saved)));
    pg_sys::enable_nestloop = false;
    let planned = call_next_planner(parse, query_string, cursor_options, bound_params);
    pg_sys::enable_nestloop = saved;
    SAVED_NESTLOOP.with(|s| s.set(None));
    planned
}

thread_local! {
    /// Set while the planner hook has temporarily forced `enable_nestloop=false`
    /// for a join-heavy rvbbit query. Restored on the normal path; restored by
    /// `reset_nestloop_guard()` from the abort callback if planning errored.
    static SAVED_NESTLOOP: std::cell::Cell<Option<bool>> = const { std::cell::Cell::new(None) };
}

/// Restore `enable_nestloop` if the planner hook had overridden it and the
/// restore was skipped by a longjmp. Called from the xact/subxact abort
/// callbacks. Must not panic/ereport.
pub(crate) unsafe fn reset_nestloop_guard() {
    SAVED_NESTLOOP.with(|s| {
        if let Some(v) = s.take() {
            pg_sys::enable_nestloop = v;
        }
    });
}

unsafe fn call_next_planner(
    parse: *mut pg_sys::Query,
    query_string: *const c_char,
    cursor_options: std::ffi::c_int,
    bound_params: pg_sys::ParamListInfo,
) -> *mut pg_sys::PlannedStmt {
    if let Some(prev) = PREV_PLANNER_HOOK {
        prev(parse, query_string, cursor_options, bound_params)
    } else {
        pg_sys::standard_planner(parse, query_string, cursor_options, bound_params)
    }
}

unsafe fn query_has_join_heavy_rvbbit(query: *mut pg_sys::Query) -> bool {
    if query.is_null() || (*query).commandType != pg_sys::CmdType::CMD_SELECT {
        return false;
    }
    count_rvbbit_rtes((*query).rtable) >= 3
}

unsafe fn query_allows_rvbbit_file_scan(root: *mut pg_sys::PlannerInfo) -> bool {
    if root.is_null() || (*root).parse.is_null() {
        return false;
    }
    let query = (*root).parse;
    if (*query).commandType != pg_sys::CmdType::CMD_SELECT {
        return false;
    }
    // Row-locking queries need real heap TIDs. A CustomScan over accelerator
    // files cannot satisfy UPDATE/DELETE/FOR UPDATE tuple identity.
    (*query).rowMarks.is_null()
}

/// System columns (`xmin`, `ctid`, `tableoid`, ...) only exist in PostgreSQL's
/// heap tuple. Parquet/Vortex row groups deliberately contain user columns
/// only, so offering a CustomScan for a relation whose query references a
/// negative attribute number can fail at execution or, worse, manufacture a
/// meaningless value. Keep that relation on the heap. This also protects the
/// xid-watermark probes used by refresh_acceleration itself.
unsafe fn query_references_system_column(
    root: *mut pg_sys::PlannerInfo,
    rel: *mut pg_sys::RelOptInfo,
    rti: pg_sys::Index,
) -> bool {
    if root.is_null() || rel.is_null() || (*root).parse.is_null() {
        return false;
    }
    // `pull_varattnos` only accepts an expression tree. Passing the Query node
    // itself trips PostgreSQL's "unplanned subquery" assertion in assert-enabled
    // builds (including pgrx tests). `pull_vars_of_level` is the query-aware API;
    // inspect its current-level Vars before using pull_varattnos on the
    // relation-local expression trees below.
    let query_vars = pg_sys::pull_vars_of_level((*root).parse as *mut pg_sys::Node, 0);
    if !query_vars.is_null() {
        for i in 0..(*query_vars).length {
            let node = pg_sys::list_nth(query_vars, i) as *mut pg_sys::Node;
            if !node.is_null() && (*node).type_ == pg_sys::NodeTag::T_Var {
                let var = node as *mut pg_sys::Var;
                if (*var).varno == rti as i32 && (*var).varlevelsup == 0 && (*var).varattno < 0 {
                    pg_sys::list_free(query_vars);
                    return true;
                }
            }
        }
        pg_sys::list_free(query_vars);
    }

    let mut attnums: *mut pg_sys::Bitmapset = std::ptr::null_mut();
    // For a relation inside EXISTS/other subqueries, the outer Query tree can
    // carry the Var at a different level. The relation-local planner nodes are
    // already normalized to this `rti`, so inspect them as the authoritative
    // fallback as well.
    if !(*rel).reltarget.is_null() {
        pg_sys::pull_varattnos(
            (*(*rel).reltarget).exprs as *mut pg_sys::Node,
            rti,
            &mut attnums,
        );
    }
    let restrictions = (*rel).baserestrictinfo;
    if !restrictions.is_null() {
        for i in 0..(*restrictions).length {
            let rinfo = pg_sys::list_nth(restrictions, i) as *mut pg_sys::RestrictInfo;
            if !rinfo.is_null() {
                // pull_varattnos understands expression nodes, not the planner's
                // RestrictInfo wrapper itself.
                pg_sys::pull_varattnos((*rinfo).clause as *mut pg_sys::Node, rti, &mut attnums);
            }
        }
    }

    let first_low = pg_sys::FirstLowInvalidHeapAttributeNumber as i32;
    let mut member = -1;
    let mut found = false;
    loop {
        member = pg_sys::bms_next_member(attnums, member);
        if member < 0 {
            break;
        }
        let attnum = member + first_low;
        if attnum < 0 {
            found = true;
            break;
        }
    }
    pg_sys::bms_free(attnums);
    found
}

unsafe fn count_rvbbit_rtes(rtable: *mut pg_sys::List) -> usize {
    if rtable.is_null() {
        return 0;
    }
    let mut count = 0usize;
    for i in 0..(*rtable).length {
        let rte = pg_sys::list_nth(rtable, i) as *mut pg_sys::RangeTblEntry;
        if rte.is_null() {
            continue;
        }
        match (*rte).rtekind {
            pg_sys::RTEKind::RTE_RELATION => {
                let oid = (*rte).relid.to_u32();
                if oid >= FIRST_NORMAL_OBJECT_ID && is_rvbbit_table(oid) {
                    count += 1;
                }
            }
            pg_sys::RTEKind::RTE_SUBQUERY => {
                if query_has_join_heavy_rvbbit((*rte).subquery) {
                    count += 3;
                }
            }
            _ => {}
        }
    }
    count
}

unsafe fn first_current_history_rvbbit_rte(query: *mut pg_sys::Query) -> Option<u32> {
    if query.is_null() || (*query).rtable.is_null() {
        return None;
    }
    let rtable = (*query).rtable;
    for i in 0..(*rtable).length {
        let rte = pg_sys::list_nth(rtable, i) as *mut pg_sys::RangeTblEntry;
        if rte.is_null() {
            continue;
        }
        match (*rte).rtekind {
            pg_sys::RTEKind::RTE_RELATION => {
                let oid = (*rte).relid.to_u32();
                // Consult the policy row directly.  A relation can be created
                // and registered in this same backend after an earlier planner
                // lookup cached it as non-rvbbit; that performance cache must
                // never be allowed to bypass the current-only correctness
                // boundary.
                if oid >= FIRST_NORMAL_OBJECT_ID && table_history_is_current(oid) {
                    return Some(oid);
                }
            }
            pg_sys::RTEKind::RTE_SUBQUERY => {
                if let Some(oid) = first_current_history_rvbbit_rte((*rte).subquery) {
                    return Some(oid);
                }
            }
            _ => {}
        }
    }
    None
}

fn table_history_is_current(oid: u32) -> bool {
    let old = IN_HOOK.with(|f| {
        let old = f.get();
        f.set(true);
        old
    });
    let current = crate::catalog_owner::as_owner(|| pgrx::Spi::get_one::<bool>(&format!(
        "SELECT coalesce((SELECT t.history_policy = 'current' \
                            FROM rvbbit.tables t \
                           WHERE t.table_oid = {oid}::oid), false)"
    )))
    .ok()
    .flatten()
    .unwrap_or(false);
    IN_HOOK.with(|f| f.set(old));
    current
}

#[pg_guard]
unsafe extern "C-unwind" fn rvbbit_get_relation_info_hook(
    root: *mut pg_sys::PlannerInfo,
    relation_oid: pg_sys::Oid,
    inhparent: bool,
    rel: *mut pg_sys::RelOptInfo,
) {
    if let Some(prev) = PREV_GET_RELATION_INFO_HOOK {
        prev(root, relation_oid, inhparent, rel);
    }
    if rel.is_null() {
        return;
    }
    if crate::pg_context::nonsystem_view_access_restricted() {
        return;
    }
    let oid_u32 = relation_oid.to_u32();
    if oid_u32 < FIRST_NORMAL_OBJECT_ID {
        return;
    }
    if IN_HOOK.with(|f| f.get()) {
        return;
    }
    if !query_allows_rvbbit_file_scan(root) {
        return;
    }
    if !is_rvbbit_table(oid_u32) {
        return;
    }
    // Standby (hot standby in recovery): acceleration files aren't WAL-shipped and
    // can't be maintained on a read-only node, but the heap rows ARE replicated
    // (rvbbit = heap). Skip acceleration so PostgreSQL plans a plain heap scan —
    // correct, just unaccelerated — instead of erroring on a missing row-group file.
    if pg_sys::RecoveryInProgress() {
        return;
    }
    if force_heap_scan_enabled() || router::pg_rowstore_route_selected() {
        return;
    }
    let read_mode = if as_of_generation_enabled() {
        LatestAccelReadMode::ParquetOnly
    } else {
        latest_accel_read_mode_for_oid(oid_u32)
    };
    if read_mode == LatestAccelReadMode::None {
        return;
    }
    // Our replacement scan is not parallel-aware yet. If PG keeps heap
    // partial paths around for a grouped query, the final plan can bypass
    // parquet entirely and read stale heap residue after export_to_parquet.
    (*rel).consider_parallel = false;
    let total_rows = sum_row_group_rows(oid_u32);
    if total_rows > 0.0 {
        let est_rows = total_rows + estimated_heap_tail_rows(oid_u32, total_rows);
        (*rel).rows = est_rows;
        (*rel).tuples = est_rows;
        // Estimate pages from total parquet bytes / BLCKSZ. Doesn't matter
        // a lot for cost — what matters is that the seqscan path computed
        // later doesn't think the relation is empty.
        let total_bytes = sum_row_group_bytes(oid_u32);
        let est_pages = (total_bytes / 8192.0).ceil() as u32;
        // A retained heap's index paths compete with the parquet scan (see the
        // pathlist hook) and cost their heap fetches from rel->pages, so keep
        // the heap's real size when it is the bigger one: parquet is usually
        // several times smaller, which would make index scans look cheap.
        (*rel).pages = if heap_index_paths_compete(oid_u32) {
            est_pages.max((*rel).pages)
        } else {
            est_pages
        }
        .max(1);
    }
}

#[pg_guard]
unsafe extern "C-unwind" fn rvbbit_set_rel_pathlist_hook(
    root: *mut pg_sys::PlannerInfo,
    rel: *mut pg_sys::RelOptInfo,
    rti: pg_sys::Index,
    rte: *mut pg_sys::RangeTblEntry,
) {
    if let Some(prev) = PREV_REL_PATHLIST_HOOK {
        prev(root, rel, rti, rte);
    }

    if rte.is_null() || rel.is_null() {
        return;
    }
    if crate::pg_context::nonsystem_view_access_restricted() {
        return;
    }
    if (*rte).rtekind != pg_sys::RTEKind::RTE_RELATION {
        return;
    }

    let oid_u32 = (*rte).relid.to_u32();
    if oid_u32 < FIRST_NORMAL_OBJECT_ID {
        return;
    }
    if IN_HOOK.with(|f| f.get()) {
        return;
    }
    if !query_allows_rvbbit_file_scan(root) {
        return;
    }
    if query_references_system_column(root, rel, rti) {
        return;
    }
    if !is_rvbbit_table(oid_u32) {
        return;
    }
    // Standby (hot standby in recovery): acceleration files aren't WAL-shipped and
    // can't be maintained on a read-only node, but the heap rows ARE replicated
    // (rvbbit = heap). Skip acceleration so PostgreSQL plans a plain heap scan —
    // correct, just unaccelerated — instead of erroring on a missing row-group file.
    if pg_sys::RecoveryInProgress() {
        return;
    }
    if force_heap_scan_enabled() || router::pg_rowstore_route_selected() {
        return;
    }
    let read_mode = if as_of_generation_enabled() {
        LatestAccelReadMode::ParquetOnly
    } else {
        latest_accel_read_mode_for_oid(oid_u32)
    };
    if read_mode == LatestAccelReadMode::None {
        return;
    }

    if !(*rte).tablesample.is_null() {
        // The custom scan has no TABLESAMPLE implementation. Never replace a
        // SampleScan with a full Parquet scan, nor sample stale/empty heap residue.
        if as_of_generation_enabled() || !heap_is_authoritative(oid_u32) {
            pgrx::error!(
                "rvbbit: TABLESAMPLE requires a retained authoritative heap; sampling accelerated-only or historical data is not supported"
            );
        }
        return;
    }

    let has_rgs = has_row_groups(oid_u32);
    // An empty latest snapshot has no visible row groups, but retained AS OF
    // generations can still contain rows. Let retained tables resolve AS OF
    // through the custom scan. Current-only tables with no accelerator retain
    // their ordinary heap fallback: historical application views also join
    // live, unaccelerated lookup tables under the statement's AS OF setting.
    if !has_rgs && (!as_of_generation_enabled() || table_history_is_current(oid_u32)) {
        return;
    }

    let total_rows = sum_row_group_rows(oid_u32);
    let est_total_rows = total_rows + estimated_heap_tail_rows(oid_u32, total_rows);
    let est_rows = clamp_custom_scan_rows((*rel).rows, est_total_rows);
    (*rel).tuples = est_total_rows;
    (*rel).consider_parallel = false;

    // Only wipe heap paths when the CustomScan owns the complete read: either
    // parquet is authoritative, or the retained heap has a read-time overlay
    // that merges parquet tombstones with the visible heap tail. Dirty
    // UPDATE/DELETE/TRUNCATE episodes without a complete overlay remain
    // heap-only for correctness.
    //
    // A retained heap still holds every row (writes land there; parquet is a
    // copy), so its index paths read the same answer for the latest view. They
    // keep competing on cost: a parquet scan reads whole row groups, which
    // loses badly to an index for a key lookup or a narrow range. Sequential
    // and partial heap paths still go, so a full read always uses parquet.
    (*rel).pathlist = if heap_index_paths_compete(oid_u32) {
        heap_index_paths((*rel).pathlist)
    } else {
        std::ptr::null_mut()
    };
    (*rel).partial_pathlist = std::ptr::null_mut();
    (*rel).cheapest_total_path = std::ptr::null_mut();
    (*rel).cheapest_startup_path = std::ptr::null_mut();
    (*rel).cheapest_unique_path = std::ptr::null_mut();
    (*rel).cheapest_parameterized_paths = std::ptr::null_mut();

    let total_bytes = sum_row_group_bytes(oid_u32);
    let row_groups = row_group_count(oid_u32);
    add_rvbbit_path(rel, oid_u32, est_rows, est_total_rows, total_bytes, row_groups);
}

/// The index and bitmap heap paths in `pathlist`, in their order (add_path keeps
/// it sorted by cost).
unsafe fn heap_index_paths(pathlist: *mut pg_sys::List) -> *mut pg_sys::List {
    let mut kept: *mut pg_sys::List = std::ptr::null_mut();
    if pathlist.is_null() {
        return kept;
    }
    for i in 0..(*pathlist).length {
        let path = pg_sys::list_nth(pathlist, i) as *mut pg_sys::Path;
        if !path.is_null()
            && matches!(
                (*path).pathtype,
                pg_sys::NodeTag::T_IndexScan
                    | pg_sys::NodeTag::T_IndexOnlyScan
                    | pg_sys::NodeTag::T_BitmapHeapScan
            )
        {
            kept = pg_sys::lappend(kept, path.cast());
        }
    }
    kept
}

/// Whether the heap's index paths may compete with the parquet scan for this
/// table: its heap is retained (it holds every row), the read is of the latest
/// view (AS OF reads only exist in parquet), and `rvbbit.index_paths` (default
/// on) allows it.
fn heap_index_paths_compete(oid: u32) -> bool {
    !as_of_generation_enabled()
        && guc_setting("rvbbit.index_paths")
            .map(|value| setting_enabled(&value, true))
            .unwrap_or(true)
        && heap_is_authoritative(oid)
}

/// Allocate a CustomPath in PG memory and register it with the planner.
unsafe fn add_rvbbit_path(
    rel: *mut pg_sys::RelOptInfo,
    table_oid: u32,
    est_rows: f64,
    total_rows: f64,
    total_bytes: f64,
    row_groups: f64,
) {
    let path_ptr =
        pg_sys::palloc0(std::mem::size_of::<pg_sys::CustomPath>()) as *mut pg_sys::CustomPath;
    let path = &mut *path_ptr;

    path.path.type_ = pg_sys::NodeTag::T_CustomPath;
    path.path.pathtype = pg_sys::NodeTag::T_CustomScan;
    path.path.parent = rel;
    path.path.pathtarget = (*rel).reltarget;
    path.path.param_info = std::ptr::null_mut();
    path.path.parallel_aware = false;
    path.path.parallel_safe = false;
    path.path.parallel_workers = 0;
    path.path.rows = est_rows;
    // There is no heap path left after compaction, so this path can carry
    // realistic cost information. Join planning depends on these numbers:
    // pretending filtered parquet scans are zero-cost/full-row scans pushes
    // PostgreSQL toward huge join cardinalities and unnecessary JIT.
    let (startup_cost, total_cost) = parquet_scan_cost(total_rows, total_bytes, est_rows, row_groups);
    path.path.startup_cost = startup_cost;
    path.path.total_cost = total_cost;
    path.path.pathkeys = std::ptr::null_mut();

    // CUSTOMPATH_SUPPORT_PROJECTION = 0x0004. Tells the planner this path
    // can handle output projection itself, so it doesn't insert a Result
    // node above us — which (we suspect) is what's resolving every Var to
    // attribute 1 regardless of what was requested.
    path.flags = 0x0004;
    path.custom_paths = std::ptr::null_mut();
    // Stash the table OID in custom_private as a List of Integer so
    // PlanCustomPath can recover it. Element 1 carries the native+vortex route
    // selection captured HERE at plan time (where the rewriter's flag is still
    // reliably set) — it then rides the plan node into begin_custom_scan, immune
    // to the execution-time route re-computation that clobbers the global flag.
    let native_vortex = crate::router::native_vortex_plan_captured() as i32;
    let oid_list = pg_sys::lappend_int(std::ptr::null_mut(), table_oid as i32);
    let oid_list = pg_sys::lappend_int(oid_list, native_vortex);
    path.custom_private = oid_list;
    path.methods = &RVBBIT_PATH_METHODS.0;

    pg_sys::add_path(rel, path_ptr as *mut pg_sys::Path);
}

/// `(startup, total)` cost of scanning `row_groups` row groups.
///
/// Parquet scans avoid heap tuple visibility and read fewer projected bytes,
/// but they still pay IO, decode and output tuple costs, and each row group
/// they open costs a fixed amount (its file, footer and column chunks) however
/// few rows it holds. A table fragmented by delta refreshes pays that hundreds
/// of times: 765 row groups of 32 rows took 173 ms on the hub where the
/// table's index answered in 0.04 ms. The first row group is opened and
/// decoded before the first row comes out, which is what a LIMIT pays even
/// when it stops early.
unsafe fn parquet_scan_cost(
    total_rows: f64,
    total_bytes: f64,
    est_rows: f64,
    row_groups: f64,
) -> (f64, f64) {
    let pages = (total_bytes / 8192.0).ceil().max(1.0);
    let input_rows = total_rows.max(est_rows).max(1.0);
    let row_groups = row_groups.max(1.0);
    let io = pages * pg_sys::seq_page_cost * 0.25;
    let decode = input_rows * pg_sys::cpu_tuple_cost * 0.20;
    let output = est_rows.max(1.0) * pg_sys::cpu_tuple_cost;
    let open = row_group_open_cost();
    let startup = open + (io + decode) / row_groups;
    let total = (io + decode + output + open * row_groups).max(1.0);
    (startup.min(total), total)
}

/// Planner cost of opening one row group (`rvbbit.row_group_cost`, default 40):
/// about ten random page reads.
fn row_group_open_cost() -> f64 {
    guc_setting("rvbbit.row_group_cost")
        .and_then(|value| value.trim().parse::<f64>().ok())
        .filter(|cost| cost.is_finite() && *cost >= 0.0)
        .unwrap_or(40.0)
}

fn clamp_custom_scan_rows(pg_est_rows: f64, total_rows: f64) -> f64 {
    if total_rows <= 1.0 {
        return pg_est_rows.max(1.0);
    }
    // After compact, PostgreSQL's heap stats can be absent or stale because
    // the heap is empty and parquet is authoritative. Avoid one-row estimates
    // for full row-group scans; those make nested loops rescan parquet.
    pg_est_rows.max(total_rows * 0.05).min(total_rows).max(1.0)
}

fn force_heap_scan_enabled() -> bool {
    guc_setting("rvbbit.force_heap_scan")
        .map(|value| setting_enabled(&value, false))
        .unwrap_or(false)
}

fn as_of_generation_enabled() -> bool {
    crate::time_travel::active_as_of_enabled()
}

fn setting_enabled(value: &str, default: bool) -> bool {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return default;
    }
    !matches!(
        trimmed.to_ascii_lowercase().as_str(),
        "0" | "false" | "no" | "off" | "disabled"
    )
}

fn guc_setting(name: &str) -> Option<String> {
    let cname = CString::new(name).ok()?;
    let ptr = unsafe { pg_sys::GetConfigOption(cname.as_ptr(), true, false) };
    if ptr.is_null() {
        None
    } else {
        Some(unsafe { CStr::from_ptr(ptr).to_string_lossy().into_owned() })
    }
}

fn rvbbit_tables_catalog_present() -> bool {
    if RVBBIT_TABLES_PRESENT.with(|c| c.get()) {
        return true;
    }
    let old = IN_HOOK.with(|f| {
        let old = f.get();
        f.set(true);
        old
    });
    let present = crate::catalog_owner::as_owner(|| pgrx::Spi::get_one::<bool>("SELECT to_regclass('rvbbit.tables') IS NOT NULL"))
        .ok()
        .flatten()
        .unwrap_or(false);
    IN_HOOK.with(|f| f.set(old));
    if present {
        RVBBIT_TABLES_PRESENT.with(|c| c.set(true));
    }
    present
}

/// The planner hooks are reading rvbbit's catalog: the statement being parsed
/// is one of those reads.
pub(crate) fn in_catalog_read() -> bool {
    IN_HOOK.with(|f| f.get())
}

/// Forget which tables are accelerated (the registry changed).
pub(crate) fn invalidate_is_rvbbit_cache() {
    IS_RVBBIT_CACHE.with(|c| c.borrow_mut().clear());
}

/// Cached lookup: is this relation enabled in the rvbbit acceleration registry?
/// The cache is dropped whenever the scan epoch moves (a table was enabled,
/// disabled or rebuilt), so a pooled connection sees a newly accelerated table.
pub(crate) fn is_rvbbit_table(oid: u32) -> bool {
    crate::custom_scan::refresh_caches_if_stale();
    if let Some(cached) = IS_RVBBIT_CACHE.with(|c| c.borrow().get(&oid).copied()) {
        return cached;
    }
    if !rvbbit_tables_catalog_present() {
        return false;
    }
    IN_HOOK.with(|f| f.set(true));
    let result: Result<Option<bool>, _> = crate::catalog_owner::as_owner(|| pgrx::Spi::get_one(&format!(
        "SELECT EXISTS ( \
             SELECT 1 \
             FROM rvbbit.tables t \
             JOIN pg_class c ON c.oid = t.table_oid \
             WHERE t.table_oid = {oid}::oid \
               AND coalesce(t.acceleration_enabled, true) \
         )"
    )));
    IN_HOOK.with(|f| f.set(false));
    let is = result.ok().flatten().unwrap_or(false);
    IS_RVBBIT_CACHE.with(|c| c.borrow_mut().insert(oid, is));
    is
}

/// The heap still holds every row (shadow_heap_retained): writes land there and
/// the parquet copy follows, so heap reads are correct for the latest view.
fn heap_is_authoritative(oid: u32) -> bool {
    plan_table_state(oid).heap_retained
}

/// Latest-view accelerated scans are correct when parquet is authoritative
/// (legacy empty heap or clean retained heap). They are also correct for the
/// read-time overlay case: a retained heap with a dirty episode that can be
/// represented as parquet row groups minus tombstones plus visible heap tuples
/// whose xmin is newer than the last refresh watermark. Insert-only dirtiness
/// is always overlay-readable. UPDATE/DELETE/TRUNCATE dirtiness also needs a
/// complete row-identity overlay, because old accelerated rows must be
/// tombstoned before the heap tail can replace them.
///
/// Historical AS OF reads are handled by the caller and intentionally bypass
/// this latest-view check.
fn latest_accel_read_mode_for_oid(oid: u32) -> LatestAccelReadMode {
    plan_table_state(oid).read_mode
}

fn estimated_heap_tail_rows(oid: u32, parquet_rows: f64) -> f64 {
    plan_table_state(oid)
        .overlay_live_tuples
        .map_or(0.0, |live| (live - parquet_rows as i64).max(0) as f64)
}

/// Does the latest snapshot have row groups? Not cached across plans because
/// compact() changes it.
fn has_row_groups(oid: u32) -> bool {
    plan_table_state(oid).has_row_groups
}

/// What planning needs from an accelerated table's catalog rows. The hooks ask
/// for it several times per table (get_relation_info, then set_rel_pathlist),
/// so one planning reads it once, through a kept plan: re-parsing and planning
/// these catalog queries on every ask was most of a short statement's planning
/// time. A table with no row groups gets no accelerated path, so its overlay
/// readiness (a millisecond or two to work out) isn't asked.
#[derive(Clone, Copy)]
struct PlanTableState {
    read_mode: LatestAccelReadMode,
    heap_retained: bool,
    /// The heap's live tuples, when a read-time overlay adds the heap tail.
    overlay_live_tuples: Option<i64>,
    has_row_groups: bool,
}

const PLAN_TABLE_STATE_SQL: &str = "\
    SELECT CASE \
             WHEN pg_catalog.pg_relation_size(t.table_oid) = 0 \
               OR (s.retained AND NOT s.dirty) THEN 'parquet' \
             WHEN s.overlay AND coalesce(a.last_refresh_xid, 0) > 0 THEN 'overlay' \
             ELSE 'none' \
           END, \
           s.retained, \
           s.overlay, \
           coalesce(pg_catalog.pg_stat_get_live_tuples(t.table_oid), 0)::bigint, \
           s.has_row_groups \
      FROM rvbbit.tables t \
      JOIN rvbbit.table_dirty_state ds ON ds.table_oid = t.table_oid \
      LEFT JOIN rvbbit.acceleration_state a ON a.table_oid = t.table_oid \
     CROSS JOIN LATERAL ( \
           SELECT EXISTS (SELECT 1 FROM rvbbit.row_groups_visible g \
                           WHERE g.table_oid = t.table_oid) AS has_row_groups \
           ) rg \
     CROSS JOIN LATERAL ( \
           SELECT rg.has_row_groups, \
                  coalesce(ds.shadow_heap_retained, false) AS retained, \
                  coalesce(ds.shadow_heap_dirty, false) AS dirty, \
                  coalesce(ds.shadow_heap_retained, false) \
                  AND coalesce(ds.shadow_heap_dirty, false) \
                  AND rg.has_row_groups \
                  AND ((NOT coalesce(ds.dirty_has_update, false) \
                        AND NOT coalesce(ds.dirty_has_delete, false) \
                        AND NOT coalesce(ds.dirty_has_truncate, false)) \
                       OR rvbbit.accel_overlay_ready(t.table_oid)) AS overlay \
           ) s \
     WHERE t.table_oid = $1";

const NO_PLAN_TABLE_STATE: PlanTableState = PlanTableState {
    read_mode: LatestAccelReadMode::None,
    heap_retained: false,
    overlay_live_tuples: None,
    has_row_groups: false,
};

thread_local! {
    /// The planning `PLAN_TABLE_STATES` was read for, and what it read.
    static PLAN_TABLE_STATES: RefCell<(u64, HashMap<u32, PlanTableState>)> =
        RefCell::new((0, HashMap::new()));
    static PLAN_TABLE_STATE: KeptPlan = const { KeptPlan::new(PLAN_TABLE_STATE_SQL) };
    /// The planner call in progress (0: none), numbered from `PLANNINGS`.
    static CURRENT_PLANNING: Cell<u64> = const { Cell::new(0) };
    static PLANNINGS: Cell<u64> = const { Cell::new(0) };
}

/// Numbers one planner call for as long as it runs, so the table states it
/// reads are its own: a later statement, or a planning nested inside it, reads
/// them again.
struct PlanningScope {
    previous: u64,
}

impl PlanningScope {
    fn enter() -> Self {
        let id = PLANNINGS.with(|n| {
            n.set(n.get().wrapping_add(1).max(1));
            n.get()
        });
        Self {
            previous: CURRENT_PLANNING.with(|c| c.replace(id)),
        }
    }
}

impl Drop for PlanningScope {
    fn drop(&mut self) {
        CURRENT_PLANNING.with(|c| c.set(self.previous));
    }
}

fn plan_table_state(oid: u32) -> PlanTableState {
    let planning = CURRENT_PLANNING.with(|c| c.get());
    let cached = PLAN_TABLE_STATES.with(|c| {
        let c = c.borrow();
        (planning != 0 && c.0 == planning)
            .then(|| c.1.get(&oid).copied())
            .flatten()
    });
    if let Some(state) = cached {
        return state;
    }
    IN_HOOK.with(|f| f.set(true));
    let state = crate::catalog_owner::as_owner(|| read_plan_table_state(oid));
    IN_HOOK.with(|f| f.set(false));
    let state = state.unwrap_or(NO_PLAN_TABLE_STATE);
    if planning != 0 {
        PLAN_TABLE_STATES.with(|c| {
            let mut c = c.borrow_mut();
            if c.0 != planning {
                *c = (planning, HashMap::new());
            }
            c.1.insert(oid, state);
        });
    }
    state
}

fn read_plan_table_state(oid: u32) -> Result<PlanTableState, pgrx::spi::Error> {
    pgrx::Spi::connect(|client| {
        let mut state = NO_PLAN_TABLE_STATE;
        let rows = PLAN_TABLE_STATE.with(|p| p.select_oid(&client, oid))?;
        for row in rows {
            let overlay = row.get::<bool>(3)?.unwrap_or(false);
            state = PlanTableState {
                read_mode: match row.get::<String>(1)?.as_deref() {
                    Some("parquet") => LatestAccelReadMode::ParquetOnly,
                    Some("overlay") => LatestAccelReadMode::ParquetWithReadOverlay,
                    _ => LatestAccelReadMode::None,
                },
                heap_retained: row.get::<bool>(2)?.unwrap_or(false),
                overlay_live_tuples: if overlay {
                    Some(row.get::<i64>(4)?.unwrap_or(0))
                } else {
                    None
                },
                has_row_groups: row.get::<bool>(5)?.unwrap_or(false),
            };
        }
        Ok(state)
    })
}

fn sum_row_group_rows(oid: u32) -> f64 {
    aggregate_for_oid(oid).0
}

fn sum_row_group_bytes(oid: u32) -> f64 {
    aggregate_for_oid(oid).1
}

fn row_group_count(oid: u32) -> f64 {
    aggregate_for_oid(oid).2
}

/// Fetch `(sum_n_rows, sum_n_bytes, row groups)` for a relation, using the
/// backend-local cache. They are read together because the planner always
/// asks for all of them on the same plan, and one SPI returning three
/// columns is meaningfully cheaper than three SPIs.
fn aggregate_for_oid(oid: u32) -> (f64, f64, f64) {
    crate::custom_scan::refresh_caches_if_stale();
    if let Some(cached) = AGG_CACHE.with(|c| c.borrow().get(&oid).copied()) {
        return cached;
    }
    IN_HOOK.with(|f| f.set(true));
    let mut rows = 0i64;
    let mut bytes = 0i64;
    let mut groups = 0i64;
    let _ = crate::catalog_owner::as_owner(|| pgrx::Spi::connect(|client| -> Result<(), pgrx::spi::Error> {
        let table = client.select(
            &format!(
                "SELECT coalesce(sum(n_rows), 0)::bigint, \
                        coalesce(sum(n_bytes), 0)::bigint, \
                        count(*) \
                 FROM rvbbit.row_groups_visible WHERE table_oid = {oid}::oid"
            ),
            None,
            &[],
        )?;
        for row in table {
            rows = row.get::<i64>(1)?.unwrap_or(0);
            bytes = row.get::<i64>(2)?.unwrap_or(0);
            groups = row.get::<i64>(3)?.unwrap_or(0);
        }
        Ok(())
    }));
    IN_HOOK.with(|f| f.set(false));
    let aggregates = (rows as f64, bytes as f64, groups as f64);
    AGG_CACHE.with(|c| c.borrow_mut().insert(oid, aggregates));
    aggregates
}
