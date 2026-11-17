//! Catalog queries the hooks run for every statement, planned once per backend.
//!
//! An SPI query given as text is parsed, analyzed and planned on every call.
//! For the small catalog reads the planner and rewriter make on each statement,
//! that was most of their cost. A `KeptPlan` prepares its query the first time
//! it runs and keeps the plan, and runs it on its generic plan: left to choose,
//! the plan cache kept planning a custom plan for each oid, which on the hub
//! cost 0.8 ms of the 1.1 ms these reads took. PostgreSQL still replans it when
//! the objects it reads change.

use std::cell::RefCell;
use std::rc::Rc;

use pgrx::pg_sys;
use pgrx::spi::{OwnedPreparedStatement, SpiClient, SpiResult, SpiTupleTable};

pub(crate) struct KeptPlan {
    sql: &'static str,
    plan: RefCell<Option<Rc<OwnedPreparedStatement>>>,
}

impl KeptPlan {
    pub(crate) const fn new(sql: &'static str) -> Self {
        Self {
            sql,
            plan: RefCell::new(None),
        }
    }

    /// Runs `sql` with `$1` = `oid`, for at most one row.
    pub(crate) fn select_oid<'conn>(
        &self,
        client: &SpiClient<'conn>,
        oid: u32,
    ) -> SpiResult<SpiTupleTable<'conn>> {
        let plan = self.oid_plan(client)?;
        let _generic = GenericPlan::enter();
        client.select(&*plan, Some(1), &[pg_sys::Oid::from(oid).into()])
    }

    /// The kept plan of `sql`, whose one parameter, `$1`, is an oid.
    fn oid_plan(&self, client: &SpiClient<'_>) -> SpiResult<Rc<OwnedPreparedStatement>> {
        if let Some(plan) = self.plan.borrow().clone() {
            return Ok(plan);
        }
        let plan = Rc::new(
            client
                .prepare(self.sql, &[pg_sys::PgOid::from(pg_sys::OIDOID)])?
                .keep(),
        );
        *self.plan.borrow_mut() = Some(plan.clone());
        Ok(plan)
    }
}

/// `plan_cache_mode = force_generic_plan` until dropped (also when an ERROR
/// unwinds through it).
struct GenericPlan {
    nest: std::ffi::c_int,
}

impl GenericPlan {
    fn enter() -> Self {
        unsafe {
            let nest = pg_sys::NewGUCNestLevel();
            pg_sys::set_config_option(
                c"plan_cache_mode".as_ptr(),
                c"force_generic_plan".as_ptr(),
                pg_sys::GucContext::PGC_USERSET,
                pg_sys::GucSource::PGC_S_SESSION,
                pg_sys::GucAction::GUC_ACTION_SAVE,
                true,
                0,
                false,
            );
            Self { nest }
        }
    }
}

impl Drop for GenericPlan {
    fn drop(&mut self) {
        unsafe { pg_sys::AtEOXact_GUC(true, self.nest) };
    }
}
