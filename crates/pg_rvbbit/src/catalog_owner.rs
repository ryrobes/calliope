//! Reading rvbbit's own catalog as its owner.
//!
//! The planner and scan hooks run inside every role's queries, including roles
//! with no rights on schema rvbbit (a read-only role selecting from a view over
//! an accelerated table). Their metadata reads go through SPI, which runs as the
//! current user, so those roles got "permission denied for schema rvbbit".
//!
//! `as_owner` runs such a read as the owner of schema rvbbit, the way PostgreSQL
//! runs a SECURITY DEFINER function: the user is switched with
//! SECURITY_LOCAL_USERID_CHANGE, search_path is pinned and
//! standard_conforming_strings forced on, and everything is put back when the
//! closure returns or an ERROR unwinds through it. Wrap only reads of rvbbit
//! metadata: never SQL that carries text from the user's query.
//!
//! `can_use_rvbbit` says whether the current user may use rvbbit directly: read
//! the catalog the rewriter reads and call its functions. A role granted part of
//! that catalog (a common workaround for the old errors) is treated like one with
//! none of it, so a missing grant can never break its queries.
//!
//! Everyone else is a reader. The rewriter still answers a reader's query from
//! metadata or a fast-path function when the reader may read every table in it
//! whole (`may_read_whole`): its catalog reads go through `as_owner`, its
//! replacement queries are parsed as the reader with the fast-path calls bound by
//! OID, and the fast-path functions check `require_readable` when they run.

use std::cell::Cell;
use std::ffi::CString;

use pgrx::pg_sys;

thread_local! {
    static OWNER: Cell<Option<pg_sys::Oid>> = const { Cell::new(None) };
}

/// Owner of schema rvbbit (the installing role), cached per backend.
fn owner_oid() -> Option<pg_sys::Oid> {
    if let Some(oid) = OWNER.with(|o| o.get()) {
        return Some(oid);
    }
    let owner = pgrx::Spi::get_one::<pg_sys::Oid>(
        "SELECT nspowner FROM pg_catalog.pg_namespace WHERE nspname = 'rvbbit'",
    )
    .ok()
    .flatten()?;
    OWNER.with(|o| o.set(Some(owner)));
    Some(owner)
}

/// rvbbit's catalog as the rewriter, its router and the functions they call
/// read it, as the current user. Operational metadata only: no secrets.
const REWRITER_CATALOG: &[&str] = &[
    "acceleration_state",
    "accel_policy",
    "column_bitmaps",
    "delete_log",
    "fleet_endpoints",
    "generations",
    "gqe_warm_state",
    "group_stats",
    "hot_objects",
    "layout_variant_status",
    "operators",
    "route_decisions",
    "route_decision_summary",
    "route_executions",
    "route_model",
    "route_observations",
    "route_observation_summary",
    "route_optimization_candidates",
    "route_optimize_runs",
    "route_overlay",
    "route_profile_entries",
    "route_profile_points",
    "route_profiles",
    "route_profile_summary",
    "route_runtime_summary",
    "route_shadow_decisions",
    "route_shape_samples",
    "route_shape_summary",
    "route_training_queries",
    "route_training_results",
    "route_training_runs",
    "route_training_summary",
    "row_groups",
    "row_groups_visible",
    "row_group_variants",
    "row_identity_map",
    "shreds",
    "table_dirty_state",
    "tables",
    "text_dictionaries",
    "workload_layout_recommendations",
];

/// True when the current user can use rvbbit directly: a superuser, or a role
/// with USAGE on schema rvbbit and SELECT on every relation in
/// REWRITER_CATALOG that exists.
pub(crate) fn can_use_rvbbit() -> bool {
    unsafe {
        let user = pg_sys::GetUserId();
        if pg_sys::superuser_arg(user) {
            return true;
        }
        let nsp = pg_sys::get_namespace_oid(c"rvbbit".as_ptr(), true);
        if nsp == pg_sys::InvalidOid {
            return false;
        }
        if pg_sys::object_aclcheck(
            pg_sys::NamespaceRelationId,
            nsp,
            user,
            pg_sys::ACL_USAGE as pg_sys::AclMode,
        ) != pg_sys::AclResult::ACLCHECK_OK
        {
            return false;
        }
        REWRITER_CATALOG.iter().all(|name| {
            let name = CString::new(*name).expect("relation name");
            let rel = pg_sys::get_relname_relid(name.as_ptr(), nsp);
            rel == pg_sys::InvalidOid
                || pg_sys::pg_class_aclcheck(rel, user, pg_sys::ACL_SELECT as pg_sys::AclMode)
                    == pg_sys::AclResult::ACLCHECK_OK
        })
    }
}

/// True when the current user may SELECT the whole relation (a table-level
/// grant, not just some columns) and no row level security applies to them.
/// Answers rvbbit computes from metadata or parquet stand in for exactly that.
pub(crate) fn may_read_whole(rel: pg_sys::Oid) -> bool {
    unsafe {
        pg_sys::pg_class_aclcheck(rel, pg_sys::GetUserId(), pg_sys::ACL_SELECT as pg_sys::AclMode)
            == pg_sys::AclResult::ACLCHECK_OK
            && pg_sys::check_enable_rls(rel, pg_sys::InvalidOid, true)
                != pg_sys::CheckEnableRlsResult::RLS_ENABLED as std::ffi::c_int
    }
}

/// Errors unless the current user `may_read_whole(rel)`. The fast-path
/// functions read parquet directly, so they check this themselves: a cached
/// plan can outlive the role that planned it, and a caller with rights on
/// schema rvbbit can call them by hand.
pub(crate) fn require_readable(rel: pg_sys::Oid) {
    if may_read_whole(rel) {
        return;
    }
    let name = unsafe {
        let name = pg_sys::get_rel_name(rel);
        if name.is_null() {
            format!("{}", rel.to_u32())
        } else {
            std::ffi::CStr::from_ptr(name).to_string_lossy().into_owned()
        }
    };
    pgrx::ereport!(
        ERROR,
        pgrx::PgSqlErrorCode::ERRCODE_INSUFFICIENT_PRIVILEGE,
        format!("permission denied for table {name}"),
        "rvbbit's fast paths need SELECT on the whole table and no row level security"
    );
}

/// Run `f` (a read of rvbbit's own metadata) as the owner of schema rvbbit.
/// A superuser or the owner runs it unchanged.
pub(crate) fn as_owner<T>(f: impl FnOnce() -> T) -> T {
    let mut saved_user = pg_sys::InvalidOid;
    let mut saved_ctx: std::ffi::c_int = 0;
    unsafe { pg_sys::GetUserIdAndSecContext(&mut saved_user, &mut saved_ctx) };
    if unsafe { pg_sys::superuser_arg(saved_user) } {
        return f();
    }
    let Some(owner) = owner_oid() else {
        return f();
    };
    if owner == saved_user {
        return f();
    }
    let restore = Restore {
        user: saved_user,
        ctx: saved_ctx,
        nest: unsafe { pg_sys::NewGUCNestLevel() },
        done: false,
    };
    unsafe {
        pg_sys::SetUserIdAndSecContext(
            owner,
            saved_ctx | pg_sys::SECURITY_LOCAL_USERID_CHANGE as std::ffi::c_int,
        );
    }
    set_local_guc("search_path", "pg_catalog, rvbbit, pg_temp");
    set_local_guc("standard_conforming_strings", "on");
    let out = f();
    restore.finish();
    out
}

fn set_local_guc(name: &str, value: &str) {
    let name = CString::new(name).expect("GUC name");
    let value = CString::new(value).expect("GUC value");
    unsafe {
        pg_sys::set_config_option(
            name.as_ptr(),
            value.as_ptr(),
            pg_sys::GucContext::PGC_USERSET,
            pg_sys::GucSource::PGC_S_SESSION,
            pg_sys::GucAction::GUC_ACTION_SAVE,
            true,
            0,
            false,
        );
    }
}

struct Restore {
    user: pg_sys::Oid,
    ctx: std::ffi::c_int,
    nest: std::ffi::c_int,
    done: bool,
}

impl Restore {
    fn finish(mut self) {
        self.put_back(true);
    }

    fn put_back(&mut self, success: bool) {
        if self.done {
            return;
        }
        self.done = true;
        unsafe {
            pg_sys::AtEOXact_GUC(success, self.nest);
            pg_sys::SetUserIdAndSecContext(self.user, self.ctx);
        }
    }
}

impl Drop for Restore {
    /// An ERROR inside `f` unwinds through here: put the caller's identity and
    /// settings back before the error reaches whoever handles it.
    fn drop(&mut self) {
        self.put_back(false);
    }
}

#[cfg(any(test, feature = "pg_test"))]
#[pgrx::pg_schema]
mod tests {
    use pgrx::prelude::*;

    /// A login role with no rights on schema rvbbit, reading a plain table
    /// through a view it was granted.
    fn reader_setup() {
        Spi::run("CREATE ROLE rvbbit_test_reader").unwrap();
        Spi::run("CREATE SCHEMA rvbbit_test_app").unwrap();
        Spi::run("CREATE TABLE rvbbit_test_app.orders AS SELECT g AS id, g * 1.5 AS amount FROM generate_series(1, 10) g").unwrap();
        Spi::run("CREATE SCHEMA rvbbit_test_api").unwrap();
        Spi::run("CREATE VIEW rvbbit_test_api.orders AS SELECT id, amount FROM rvbbit_test_app.orders").unwrap();
        Spi::run("GRANT USAGE ON SCHEMA rvbbit_test_api TO rvbbit_test_reader").unwrap();
        Spi::run("GRANT SELECT ON rvbbit_test_api.orders TO rvbbit_test_reader").unwrap();
    }

    #[pg_test]
    fn a_role_without_rvbbit_rights_can_query() {
        reader_setup();
        Spi::run("SET LOCAL ROLE rvbbit_test_reader").unwrap();
        assert!(!crate::catalog_owner::can_use_rvbbit());
        assert_eq!(Spi::get_one::<i32>("SELECT 1").unwrap(), Some(1));
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM rvbbit_test_api.orders").unwrap(),
            Some(10)
        );
        // The base table stays protected: only the view was granted.
        let direct = crate::subxact::try_in_subtransaction(|| {
            Spi::get_one::<i64>("SELECT count(*) FROM rvbbit_test_app.orders")
        });
        assert!(direct.is_err());
    }

    #[pg_test]
    fn part_of_the_catalog_counts_as_none_of_it() {
        reader_setup();
        // The workaround people reach for: USAGE plus a few catalog tables.
        Spi::run("GRANT USAGE ON SCHEMA rvbbit TO rvbbit_test_reader").unwrap();
        Spi::run("GRANT SELECT ON rvbbit.tables, rvbbit.route_profiles TO rvbbit_test_reader").unwrap();
        Spi::run("SET LOCAL ROLE rvbbit_test_reader").unwrap();
        assert!(!crate::catalog_owner::can_use_rvbbit());
        assert_eq!(Spi::get_one::<i32>("SELECT 1").unwrap(), Some(1));
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM rvbbit_test_api.orders").unwrap(),
            Some(10)
        );
    }

    #[pg_test]
    fn the_whole_catalog_makes_an_rvbbit_user() {
        reader_setup();
        Spi::run("GRANT USAGE ON SCHEMA rvbbit TO rvbbit_test_reader").unwrap();
        Spi::run("GRANT SELECT ON ALL TABLES IN SCHEMA rvbbit TO rvbbit_test_reader").unwrap();
        Spi::run("SET LOCAL ROLE rvbbit_test_reader").unwrap();
        assert!(crate::catalog_owner::can_use_rvbbit());
    }

    #[pg_test]
    fn as_owner_switches_and_always_switches_back() {
        reader_setup();
        Spi::run("SET LOCAL ROLE rvbbit_test_reader").unwrap();
        Spi::run("SET LOCAL search_path = rvbbit_test_api").unwrap();
        let inside = crate::catalog_owner::as_owner(|| {
            Spi::get_two::<String, String>("SELECT current_user::text, current_setting('search_path')")
                .unwrap()
        });
        assert_ne!(inside.0.as_deref(), Some("rvbbit_test_reader"));
        assert_eq!(inside.1.as_deref(), Some("pg_catalog, rvbbit, pg_temp"));

        let failed = crate::subxact::try_in_subtransaction(|| {
            crate::catalog_owner::as_owner(|| Spi::run("SELECT 1 / 0"))
        });
        assert!(failed.is_err());
        let after = Spi::get_two::<String, String>("SELECT current_user::text, current_setting('search_path')")
            .unwrap();
        assert_eq!(after.0.as_deref(), Some("rvbbit_test_reader"));
        assert_eq!(after.1.as_deref(), Some("rvbbit_test_api"));
        // The owner's metadata is readable through as_owner, not without it.
        assert!(crate::catalog_owner::as_owner(|| {
            Spi::get_one::<i64>("SELECT count(*) FROM rvbbit.tables")
        })
        .is_ok());
    }

    fn rel(name: &str) -> pg_sys::Oid {
        Spi::get_one::<pg_sys::Oid>(&format!("SELECT '{name}'::regclass::oid"))
            .unwrap()
            .unwrap()
    }

    #[pg_test]
    fn reading_whole_needs_a_table_grant_and_no_row_security() {
        reader_setup();
        Spi::run("CREATE TABLE rvbbit_test_app.cols AS SELECT 1 AS id, 'x' AS secret").unwrap();
        Spi::run("GRANT USAGE ON SCHEMA rvbbit_test_app TO rvbbit_test_reader").unwrap();
        Spi::run("GRANT SELECT (id) ON rvbbit_test_app.cols TO rvbbit_test_reader").unwrap();
        Spi::run("CREATE TABLE rvbbit_test_app.owned AS SELECT 1 AS id").unwrap();
        Spi::run("ALTER TABLE rvbbit_test_app.owned ENABLE ROW LEVEL SECURITY").unwrap();
        Spi::run("CREATE POLICY just_one ON rvbbit_test_app.owned USING (id = 1)").unwrap();
        Spi::run("GRANT SELECT ON rvbbit_test_app.orders, rvbbit_test_app.owned TO rvbbit_test_reader").unwrap();
        let (orders, cols, owned) = (
            rel("rvbbit_test_app.orders"),
            rel("rvbbit_test_app.cols"),
            rel("rvbbit_test_app.owned"),
        );
        let view = rel("rvbbit_test_api.orders");
        // The table's owner (the test's superuser) is never held to its policies.
        assert!(crate::catalog_owner::may_read_whole(owned));

        Spi::run("SET LOCAL ROLE rvbbit_test_reader").unwrap();
        assert!(crate::catalog_owner::may_read_whole(orders));
        assert!(crate::catalog_owner::may_read_whole(view));
        assert!(!crate::catalog_owner::may_read_whole(cols), "only some columns were granted");
        assert!(!crate::catalog_owner::may_read_whole(owned), "row level security applies");
        assert!(crate::subxact::try_in_subtransaction(|| crate::catalog_owner::require_readable(orders)).is_ok());
        let refused = crate::subxact::try_in_subtransaction(|| crate::catalog_owner::require_readable(cols));
        assert!(refused.unwrap_err().contains("permission denied for table cols"));
    }

    #[pg_test]
    fn a_fast_path_called_by_hand_checks_the_table() {
        reader_setup();
        let orders = rel("rvbbit_test_app.orders");
        // Rights on rvbbit let a role call the function, not read the table.
        Spi::run("GRANT USAGE ON SCHEMA rvbbit TO rvbbit_test_reader").unwrap();
        Spi::run("SET LOCAL ROLE rvbbit_test_reader").unwrap();
        let sql = format!("SELECT rvbbit.count_distinct_int({}::oid, 'id')", orders.to_u32());
        let refused = crate::subxact::try_in_subtransaction(|| Spi::get_one::<i64>(&sql));
        assert!(refused.unwrap_err().contains("permission denied for table orders"));
    }
}
