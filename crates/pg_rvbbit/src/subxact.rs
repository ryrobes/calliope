//! Internal subtransactions for side work that must not fail the caller's
//! statement: an implicit prewarm in the parse hook, a planning probe.
//!
//! Catching an ERROR with `PgTryBuilder` alone is not enough. Whatever the
//! failed SPI call held (its active snapshot, buffer pins, relcache refs,
//! locks) is never released, which is where "snapshot still active" and
//! "resource was not closed" warnings come from. A subtransaction gives the
//! failure something to roll back, exactly as a PL/pgSQL EXCEPTION block does.

use std::ffi::c_int;
use std::panic::AssertUnwindSafe;
use std::ptr::{addr_of, addr_of_mut};
use std::sync::atomic::{AtomicBool, AtomicI32, Ordering};
use std::time::Duration;

use pgrx::pg_sys;
use pgrx::pg_sys::ffi::pg_guard_ffi_boundary;
use pgrx::pg_sys::panic::CaughtError;
use pgrx::{PgSqlErrorCode, PgTryBuilder};

/// Runs `f` inside an internal subtransaction, following PL/pgSQL's
/// `exec_stmt_block`. An ERROR inside `f` (SPI included) rolls back only the
/// subtransaction and comes back as `Err(message)`; the caller's statement
/// carries on. A query cancel or statement timeout is rolled back the same
/// way and then raised again, because the user asked for the statement to stop.
pub(crate) fn try_in_subtransaction<R>(f: impl FnOnce() -> R) -> Result<R, String> {
    run_in_subtransaction(f, None)
}

/// [`try_in_subtransaction`] with a time limit on `f`. Postgres arms
/// `statement_timeout` only when a top-level statement starts, so a
/// `SET LOCAL statement_timeout` inside a running statement bounds nothing;
/// this arms a timer of its own instead. When it fires, `f` is cancelled and
/// rolled back, and the cancel comes back as `Err` like any other failure. A
/// user cancel or the statement's own timeout is still raised. A call nested
/// inside another bounded call runs under the outer call's limit.
pub(crate) fn try_in_subtransaction_within<R>(
    limit: Duration,
    f: impl FnOnce() -> R,
) -> Result<R, String> {
    match CallTimer::arm(limit) {
        Some(timer) => run_in_subtransaction(f, Some(&timer)),
        None => run_in_subtransaction(f, None),
    }
}

fn run_in_subtransaction<R>(f: impl FnOnce() -> R, timer: Option<&CallTimer>) -> Result<R, String> {
    unsafe {
        // BeginInternalSubTransaction treats any other state as FATAL.
        if !pg_sys::IsTransactionState() || pg_sys::IsAbortedTransactionBlockState() {
            return Err("no open transaction to run a subtransaction in".to_string());
        }
        let old_context = pg_sys::CurrentMemoryContext;
        let old_owner = pg_sys::CurrentResourceOwner;
        pg_sys::BeginInternalSubTransaction(std::ptr::null());
        // Run in the caller's memory context, as PL/pgSQL does, so whatever
        // `f` allocates there outlives the subtransaction.
        pg_sys::MemoryContextSwitchTo(old_context);

        PgTryBuilder::new(AssertUnwindSafe(move || {
            let out = f();
            pg_sys::ReleaseCurrentSubTransaction();
            pg_sys::MemoryContextSwitchTo(old_context);
            pg_sys::CurrentResourceOwner = old_owner;
            Ok(out)
        }))
        .catch_others(move |caught| {
            // pgrx has already copied the error out and flushed the error state.
            pg_sys::MemoryContextSwitchTo(old_context);
            pg_sys::RollbackAndReleaseCurrentSubTransaction();
            pg_sys::MemoryContextSwitchTo(old_context);
            pg_sys::CurrentResourceOwner = old_owner;
            if is_query_cancel(&caught) {
                match timer.and_then(CallTimer::cancel_message) {
                    Some(message) => return Err(message),
                    None => caught.rethrow(),
                }
            }
            Err(crate::router::caught_error_message(caught))
        })
        .execute()
    }
}

fn is_query_cancel(caught: &CaughtError) -> bool {
    let report = match caught {
        CaughtError::PostgresError(report) | CaughtError::ErrorReport(report) => report,
        CaughtError::RustPanic { ereport, .. } => ereport,
    };
    report.sql_error_code() == PgSqlErrorCode::ERRCODE_QUERY_CANCELED
}

// utils/timeout.h, which pgrx doesn't bind. Enum values are PG18's.
type TimeoutId = c_int;
const STATEMENT_TIMEOUT: TimeoutId = 3;
const USER_TIMEOUT: TimeoutId = 13;

extern "C-unwind" {
    fn RegisterTimeout(id: TimeoutId, handler: extern "C" fn()) -> TimeoutId;
    fn enable_timeout_after(id: TimeoutId, delay_ms: c_int);
    fn disable_timeout(id: TimeoutId, keep_indicator: bool);
    fn get_timeout_active(id: TimeoutId) -> bool;
}

/// This backend's timeout reason for bounded calls, registered on first use.
static CALL_TIMEOUT_ID: AtomicI32 = AtomicI32::new(-1);
/// Set by the handler. It runs inside the SIGALRM handler, on whichever
/// thread took the signal, so this is process-wide rather than thread-local.
static CALL_TIMEOUT_FIRED: AtomicBool = AtomicBool::new(false);
/// A bounded call is running.
static CALL_TIMER_ARMED: AtomicBool = AtomicBool::new(false);

/// Queues a query cancel for the next CHECK_FOR_INTERRUPTS, as PG's own SIGINT
/// handler does. It runs in signal context, so it only sets flags;
/// handle_sig_alarm has already set the latch.
extern "C" fn call_timeout_handler() {
    CALL_TIMEOUT_FIRED.store(true, Ordering::SeqCst);
    unsafe {
        if !addr_of!(pg_sys::proc_exit_inprogress).read_volatile() {
            addr_of_mut!(pg_sys::InterruptPending).write_volatile(1);
            addr_of_mut!(pg_sys::QueryCancelPending).write_volatile(1);
        }
    }
}

/// The timer behind one bounded call, disarmed on drop.
struct CallTimer {
    id: TimeoutId,
    limit: Duration,
    statement_timeout_armed: bool,
}

impl CallTimer {
    /// None when there's nothing to arm: no limit, or an outer bounded call
    /// whose timer already covers this one.
    fn arm(limit: Duration) -> Option<CallTimer> {
        if limit.is_zero()
            || CALL_TIMER_ARMED
                .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .is_err()
        {
            return None;
        }
        let delay_ms = limit.as_millis().clamp(1, c_int::MAX as u128) as c_int;
        unsafe {
            let mut id = CALL_TIMEOUT_ID.load(Ordering::SeqCst);
            if id < 0 {
                id = pg_guard_ffi_boundary(|| RegisterTimeout(USER_TIMEOUT, call_timeout_handler));
                CALL_TIMEOUT_ID.store(id, Ordering::SeqCst);
            }
            CALL_TIMEOUT_FIRED.store(false, Ordering::SeqCst);
            let statement_timeout_armed =
                pg_guard_ffi_boundary(|| get_timeout_active(STATEMENT_TIMEOUT));
            pg_guard_ffi_boundary(|| enable_timeout_after(id, delay_ms));
            Some(CallTimer {
                id,
                limit,
                statement_timeout_armed,
            })
        }
    }

    /// The error for a cancel that came from this timer alone. None for a
    /// user cancel, or when the statement's own timeout fired too (it fires
    /// once and then goes inactive), so those still stop the statement.
    fn cancel_message(&self) -> Option<String> {
        let statement_timeout_fired =
            self.statement_timeout_armed && !unsafe { get_timeout_active(STATEMENT_TIMEOUT) };
        if !CALL_TIMEOUT_FIRED.load(Ordering::SeqCst) || statement_timeout_fired {
            return None;
        }
        let limit = if self.limit.subsec_millis() == 0 {
            format!("{} s", self.limit.as_secs())
        } else {
            format!("{} ms", self.limit.as_millis())
        };
        Some(format!(
            "canceled: this call ran past its {limit} time limit"
        ))
    }
}

impl Drop for CallTimer {
    fn drop(&mut self) {
        unsafe {
            pg_guard_ffi_boundary(|| disable_timeout(self.id, false));
            // The timer can fire after `f` finished but before this disarm.
            // Drop the cancel it queued so it can't hit the caller instead.
            if CALL_TIMEOUT_FIRED.swap(false, Ordering::SeqCst) {
                addr_of_mut!(pg_sys::QueryCancelPending).write_volatile(0);
            }
        }
        CALL_TIMER_ARMED.store(false, Ordering::SeqCst);
    }
}

#[cfg(any(test, feature = "pg_test"))]
#[pgrx::pg_schema]
mod tests {
    use std::time::{Duration, Instant};

    use pgrx::prelude::*;

    use super::{try_in_subtransaction, try_in_subtransaction_within};

    const OVER_300_MS: &str = "canceled: this call ran past its 300 ms time limit";

    fn sleep_within(limit: Duration, seconds: f64) -> Result<bool, String> {
        try_in_subtransaction_within(limit, || {
            Spi::run(&format!("SELECT pg_sleep({seconds})")).is_ok()
        })
    }

    #[pg_test]
    fn subtransaction_keeps_success_and_rolls_back_failure() {
        Spi::run("CREATE TABLE subxact_demo (id int)").unwrap();
        let failed = try_in_subtransaction(|| {
            Spi::run("INSERT INTO subxact_demo VALUES (1)").unwrap();
            Spi::run("SELECT 1 / (random() * 0)::int").unwrap();
        });
        assert_eq!(failed, Err("division by zero".to_string()));
        let kept =
            try_in_subtransaction(|| Spi::run("INSERT INTO subxact_demo VALUES (2)").is_ok());
        assert_eq!(kept, Ok(true));
        let ids: Option<i64> = Spi::get_one("SELECT sum(id) FROM subxact_demo").unwrap();
        assert_eq!(
            ids,
            Some(2),
            "only the successful subtransaction's row remains"
        );
    }

    #[pg_test]
    fn subtransaction_leaves_parallel_mode_after_a_worker_error() {
        // Force a parallel plan whose worker raises. Caught without a
        // subtransaction, this left the transaction in parallel mode, and every
        // later GUC write failed with 25000.
        Spi::run("SET LOCAL debug_parallel_query = on").unwrap();
        let failed = try_in_subtransaction(|| {
            Spi::run("SELECT 1 / (g - g) FROM generate_series(1, 10) g").unwrap();
        });
        assert_eq!(failed, Err("division by zero".to_string()));
        assert!(!unsafe { pg_sys::IsInParallelMode() });
        Spi::run("SET LOCAL work_mem = '8MB'").unwrap();
    }

    #[pg_test]
    fn bounded_call_is_cancelled_at_its_limit() {
        let started = Instant::now();
        assert_eq!(
            sleep_within(Duration::from_millis(300), 10.0),
            Err(OVER_300_MS.to_string())
        );
        assert!(started.elapsed() < Duration::from_secs(5));
        // The statement carries on, with no timer or cancel left behind.
        assert_eq!(sleep_within(Duration::from_secs(10), 0.5), Ok(true));
        Spi::run("SELECT pg_sleep(0.5)").unwrap();
    }

    #[pg_test]
    fn nested_bounded_call_runs_under_the_outer_limit() {
        let started = Instant::now();
        let outer = try_in_subtransaction_within(Duration::from_millis(300), || {
            sleep_within(Duration::from_secs(30), 10.0)
        });
        assert_eq!(outer, Err(OVER_300_MS.to_string()));
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[pg_test]
    fn user_cancel_inside_a_bounded_call_still_cancels() {
        let outcome = PgTryBuilder::new(|| {
            let _ = try_in_subtransaction_within(Duration::from_secs(30), || {
                Spi::run("SELECT pg_cancel_backend(pg_backend_pid()), pg_sleep(5)").is_ok()
            });
            "finished"
        })
        .catch_when(PgSqlErrorCode::ERRCODE_QUERY_CANCELED, |_| "cancelled")
        .execute();
        assert_eq!(outcome, "cancelled");
        // The unwind disarmed the timer: a new bounded call arms and fires.
        assert_eq!(
            sleep_within(Duration::from_millis(300), 10.0),
            Err(OVER_300_MS.to_string())
        );
    }
}
