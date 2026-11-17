//! Cross-row pre-warming for semantic operators.
//!
//! `rvbbit.prewarm_operator(name, sql, max_concurrent)` runs an SQL
//! query, collects the rows, and dispatches one UnitOfWork per row to
//! the thread pool — N concurrent provider calls instead of one. The
//! leader thread collects results, populates L1 cache, and logs receipts.
//!
//! After a prewarm, the user's actual SELECT query hits cache for every
//! row: 1000+ row queries go from "provider-bound" to "microsecond
//! per row".
//!
//! Why this and not custom scan:
//!   - PG executes UDFs serially per row; cross-row parallelism requires
//!     a custom scan that pre-batches before yielding rows to the executor.
//!   - That's the same parallel-dispatch logic this function does — just
//!     wrapped in custom-scan machinery + planner hook so it's automatic.
//!   - Until that lands, this is the explicit two-step pattern that
//!     unlocks the same speedup. Stage 1 here; auto-detection is Stage 2.
//!
//! Threading model:
//!   - The leader process collects inputs from SPI.
//!   - Thread pool workers issue HTTP calls to OpenRouter (no PG state).
//!   - Provider semaphore (per-backend) caps concurrent calls.
//!   - Leader collects results, writes receipts (SPI INSERTs in leader
//!     are allowed; we are NOT in PG parallel mode), populates L1 cache.

use std::collections::HashSet;
use std::sync::Arc;

use pgrx::prelude::*;
use pgrx::JsonB;
use serde_json::Value;

use crate::flow;
use crate::providers::ChatRequest;
use crate::unit_of_work::{self, OpDef, WorkResult};

/// Outcome of one `warm` pass.
pub struct WarmStats {
    pub n_inputs: i64,
    pub n_cache_hits: i64,
    pub n_executed: i64,
    pub n_errors: i64,
}

/// Batched + concurrent cache-fill for one scalar operator — the core
/// bulk-execution engine.
///
/// Pass 1: check L1 + L2 cache for every input, partitioning into hits and
/// misses. Misses are deduplicated by content hash so identical inputs run
/// exactly once. Pass 2: dispatch the unique misses — batched specialist
/// chunks for single-step specialist ops (one HTTP call per `batch_size`
/// chunk, `max_concurrent` chunks in flight), one pool task per row for
/// everything else. Results land in L1 + receipts, so any later per-row
/// executor call over the same inputs resolves from cache.
pub fn warm(op: &Arc<OpDef>, opts: &Value, inputs: Vec<Value>) -> WarmStats {
    // The SQL session is only visible on this leader thread. Carry its
    // telemetry identity inside opts before any per-row work is dispatched.
    let enriched_opts = unit_of_work::with_session_request_user(opts);
    let opts = &enriched_opts;

    // Pre-load specialist specs on the leader before any pool dispatch —
    // worker threads can only read the spec cache, not do the SPI load.
    crate::specialists::warm_operator_specs(op.steps.as_ref(), op.takes.as_ref());
    // Pre-load Python handler/env specs for the same reason.
    crate::python_runtime::warm_operator_specs(op.steps.as_ref(), op.takes.as_ref());

    // Some nodes need the leader (SPI is illegal on a pool thread), so an
    // operator that contains one can't ride the pooled batched path.
    if crate::unit_of_work::contains_leader_node(op.steps.as_ref())
        || crate::unit_of_work::contains_leader_node(op.takes.as_ref().and_then(|t| t.get("nodes")))
    {
        return warm_on_leader(op, opts, inputs);
    }

    let n_inputs = inputs.len() as i64;
    let mut n_cache_hits = 0i64;

    // Pass 1: partition into hits / unique misses.
    let mut seen: HashSet<Vec<u8>> = HashSet::new();
    let mut to_run: Vec<(usize, Value)> = Vec::new();
    for (i, inp) in inputs.iter().enumerate() {
        let hash = build_hash(op, opts, inp);
        if crate::cache::get(&hash).is_some() || lookup_cached_l2(&hash).is_some() {
            n_cache_hits += 1;
        } else if seen.insert(hash) {
            // First sighting of this content. Later identical inputs are
            // covered once this one's cache entry lands.
            to_run.push((i, inp.clone()));
        }
    }

    // Pass 2: dispatch the unique misses.
    //   - single-step kind=specialist op: batch by spec.batch_size, one
    //     HTTP call per batch if the transport client-batches.
    //   - everything else: one pool job per row (LLM / code path).
    let pool = flow::pool();
    let executed: Vec<(Value, WorkResult)> = if let Some(spec_name) = single_specialist_name(op) {
        dispatch_batched_specialist(op, &spec_name, to_run, opts, pool)
    } else if let Some((step_name, spec_name)) =
        batchable_specialist_step(op).filter(|(_, spec)| spec_client_batches(spec))
    {
        // Multi-step op whose one heavy step is a batch-capable specialist
        // (e.g. rvbbit.about -> rerank specialist + a local code step): batch
        // that step across rows, then run the cheap steps per row.
        dispatch_batched_multistep_specialist(op, &step_name, &spec_name, to_run, opts, pool)
    } else {
        dispatch_per_row(op, to_run, opts, pool)
    };

    let mut n_executed = 0i64;
    let mut n_errors = 0i64;
    for (inp, result) in executed {
        // The full flow runs on the leader: pre-wards gate the input,
        // retry re-runs validation failures, post-wards gate the output.
        // The batched first attempt above is the common case; only rows
        // that fail validation re-run (sequentially, here) so the cached
        // value matches the single-row path exactly.
        let result = match crate::validator::check_pre_wards(op, &inp) {
            Err(reason) => crate::validator::errored(reason),
            Ok(()) => {
                let retried = crate::validator::apply_retry(op, &inp, opts, result);
                crate::validator::apply_post_wards(op, &inp, retried)
            }
        };
        if result.error.is_some() {
            n_errors += 1;
        } else {
            n_executed += 1;
            let hash = build_hash(op, opts, &inp);
            crate::cache::put(&hash, result.output.clone());
            log_receipt_leader(op, &hash, &result, &inp);
        }
    }

    WarmStats {
        n_inputs,
        n_cache_hits,
        n_executed,
        n_errors,
    }
}

/// Warm an operator that contains a sql node — it must run on the leader
/// (SPI cannot run on a pool thread), so each row's full flow runs here,
/// sequentially. For high-volume sql-lookup work, prefer a JOIN in the
/// outer query over a sql node inside the operator.
fn warm_on_leader(op: &Arc<OpDef>, opts: &Value, inputs: Vec<Value>) -> WarmStats {
    let n_inputs = inputs.len() as i64;
    let mut n_cache_hits = 0i64;
    let mut n_executed = 0i64;
    let mut n_errors = 0i64;
    let mut seen: HashSet<Vec<u8>> = HashSet::new();
    for inp in &inputs {
        let hash = build_hash(op, opts, inp);
        if crate::cache::get(&hash).is_some() || lookup_cached_l2(&hash).is_some() {
            n_cache_hits += 1;
            continue;
        }
        if !seen.insert(hash.clone()) {
            continue;
        }
        let result = match crate::validator::check_pre_wards(op, inp) {
            Err(reason) => crate::validator::errored(reason),
            Ok(()) => {
                let first = crate::takes::execute_attempt(op, inp, opts, None);
                let retried = crate::validator::apply_retry(op, inp, opts, first);
                crate::validator::apply_post_wards(op, inp, retried)
            }
        };
        if result.error.is_some() {
            n_errors += 1;
        } else {
            n_executed += 1;
            crate::cache::put(&hash, result.output.clone());
            log_receipt_leader(op, &hash, &result, inp);
        }
    }
    WarmStats {
        n_inputs,
        n_cache_hits,
        n_executed,
        n_errors,
    }
}

/// Cross-row pre-warm driven by an explicit SQL query. The query's output
/// columns must line up with the operator's arg_names, in order.
#[pg_extern]
fn prewarm_operator(
    op_name: &str,
    sql: &str,
    max_concurrent: default!(i32, 8),
) -> TableIterator<
    'static,
    (
        name!(n_inputs, i64),
        name!(n_cache_hits, i64),
        name!(n_executed, i64),
        name!(n_errors, i64),
        name!(wall_ms, i64),
    ),
> {
    let op = match load_op(op_name) {
        Some(o) => Arc::new(o),
        None => pgrx::error!("rvbbit.prewarm_operator: unknown operator '{}'", op_name),
    };
    if op.shape != "scalar" {
        pgrx::error!(
            "rvbbit.prewarm_operator: only scalar operators are supported (got shape={})",
            op.shape
        );
    }
    // max_concurrent is advisory — the real ceilings are the thread pool
    // size (RVBBIT_POOL_SIZE) and each specialist's catalog max_concurrent.
    let _advisory = max_concurrent;

    let arg_names = load_arg_names(op_name);
    let opts = Value::Object(Default::default());
    let inputs_per_row = collect_inputs(sql, &arg_names);

    let t0 = std::time::Instant::now();
    let stats = warm(&op, &opts, inputs_per_row);
    let wall = t0.elapsed().as_millis().min(i64::MAX as u128) as i64;

    TableIterator::once((
        stats.n_inputs,
        stats.n_cache_hits,
        stats.n_executed,
        stats.n_errors,
        wall,
    ))
}

// ---- Dispatch shapes -----------------------------------------------------

/// Per-row dispatch — one pool task per row, each running the full operator
/// pipeline via unit_of_work::execute. The original LLM-style path.
fn dispatch_per_row(
    op: &Arc<OpDef>,
    to_run: Vec<(usize, Value)>,
    opts: &Value,
    pool: &flow::Pool,
) -> Vec<(Value, WorkResult)> {
    let mut receivers = Vec::with_capacity(to_run.len());
    for (_i, inputs) in to_run {
        let op_arc: Arc<OpDef> = op.clone();
        let opts_clone = opts.clone();
        let rx = pool.submit(move || {
            // Worker-thread context — pure HTTP, no SPI. execute_attempt
            // runs the takes ensemble inline (no pool re-entry) when the
            // operator has one.
            let result = crate::takes::execute_attempt(&op_arc, &inputs, &opts_clone, None);
            (inputs, result)
        });
        receivers.push(rx);
    }
    receivers
        .into_iter()
        .map(|rx| {
            let row = rx
                .recv()
                .unwrap_or_else(|_| pgrx::error!("rvbbit: pool worker panicked during prewarm"));
            // Leader-side live progress: one operator call resolved.
            crate::live_counters::tick(&op.name, 1);
            row
        })
        .collect()
}

/// Batched specialist dispatch — chunk rows by spec.batch_size and send
/// one HTTP call per chunk (transport permitting). Each row gets its own
/// WorkResult so the post-loop cache + receipt machinery is unchanged.
fn dispatch_batched_specialist(
    op: &Arc<OpDef>,
    spec_name: &str,
    to_run: Vec<(usize, Value)>,
    opts: &Value,
    pool: &flow::Pool,
) -> Vec<(Value, WorkResult)> {
    // Spec load happens in the LEADER — workers can only read the cache.
    let spec = match crate::specialists::load_spec(spec_name) {
        Ok(s) => s,
        Err(e) => {
            // Fail the whole batch with one error per row.
            return to_run
                .into_iter()
                .map(|(_, inputs)| (inputs, fail_work(&format!("specialist load: {e}"))))
                .collect();
        }
    };

    // Resolve the step's `inputs` template once, then per-row.
    let step = op
        .steps
        .as_ref()
        .and_then(|s| s.as_array())
        .and_then(|a| a.first());
    let inputs_template = step
        .and_then(|s| s.get("inputs").cloned())
        .unwrap_or(Value::Object(Default::default()));
    // Same step name the per-row path records (execute() names unnamed steps step_<i>).
    let step_name = step
        .and_then(|s| s.get("name"))
        .and_then(|v| v.as_str())
        .unwrap_or("step_0")
        .to_string();

    // Render each row's `inputs` template against its own scope.
    let mut per_row_payloads: Vec<(Value, Value)> = Vec::with_capacity(to_run.len());
    for (_, inputs) in to_run {
        let scope = unit_of_work::Scope::new(inputs.clone(), opts.clone());
        let rendered = unit_of_work::render_value_templates(&inputs_template, &scope);
        per_row_payloads.push((inputs, rendered));
    }

    let batch_size = if crate::specialists::transport_for(&spec.transport)
        .map(|t| t.client_batches())
        .unwrap_or(false)
    {
        spec.batch_size.max(1)
    } else {
        1
    };

    // Chunk + dispatch. Each task sends one HTTP call and returns
    // outputs aligned with its slice of inputs.
    type ChunkResult = (
        Vec<Value>, // original `inputs` per row
        Result<(Vec<Value>, Vec<crate::specialists::Usage>), String>, // outputs + per-output usage, OR per-batch error
        i32,        // latency_ms for the batch
        String,     // spec name (for sub_call.model)
    );
    // Cap concurrent in-flight HTTP batches at the specialist's catalog
    // max_concurrent. The thread pool may be larger; this protects the
    // sidecar's GPU from overcommit (queued forward passes / OOM).
    let sem = crate::flow::Semaphore::new(spec.max_concurrent.max(1));
    let mut receivers: Vec<crossbeam_channel::Receiver<ChunkResult>> = Vec::new();
    for chunk in per_row_payloads.chunks(batch_size) {
        let chunk_inputs: Vec<Value> = chunk.iter().map(|(i, _)| i.clone()).collect();
        let payloads: Vec<Value> = chunk.iter().map(|(_, p)| p.clone()).collect();
        let spec_arc = spec.clone();
        let sem = sem.clone();
        let rx = pool.submit(move || {
            let _permit = sem.acquire();
            let t0 = std::time::Instant::now();
            let res = crate::specialists::predict_batch(&spec_arc, &payloads);
            let latency = t0.elapsed().as_millis().min(i32::MAX as u128) as i32;
            match res {
                Ok(resp) => (
                    chunk_inputs,
                    Ok((resp.outputs, resp.usage)),
                    latency,
                    spec_arc.name.clone(),
                ),
                Err(e) => (
                    chunk_inputs,
                    Err(e.to_string()),
                    latency,
                    spec_arc.name.clone(),
                ),
            }
        });
        receivers.push(rx);
    }

    let mut out: Vec<(Value, WorkResult)> = Vec::new();
    for rx in receivers {
        let (chunk_inputs, outputs, latency, spec_name) = rx
            .recv()
            .unwrap_or_else(|_| pgrx::error!("rvbbit: pool worker panicked during prewarm"));
        // Leader-side live progress: a batch of `chunk_inputs.len()` calls landed.
        crate::live_counters::tick(&op.name, chunk_inputs.len() as u64);
        match outputs {
            Ok((outs, usage)) => {
                for (i, (inputs, value)) in chunk_inputs.into_iter().zip(outs.into_iter()).enumerate() {
                    let text = value_to_text(&value);
                    let sub = unit_of_work::specialist_sub_call(
                        &step_name,
                        &spec_name,
                        &spec,
                        &value,
                        usage.get(i),
                        latency,
                    );
                    out.push((
                        inputs,
                        WorkResult {
                            output: text,
                            total_tokens_in: sub.tokens_in,
                            total_tokens_out: sub.tokens_out,
                            sub_calls: vec![sub],
                            total_latency_ms: latency,
                            error: None,
                        },
                    ));
                }
            }
            Err(e) => {
                for inputs in chunk_inputs {
                    out.push((inputs, fail_work(&format!("specialist '{spec_name}': {e}"))));
                }
            }
        }
    }
    out
}

fn fail_work(err: &str) -> WorkResult {
    WorkResult {
        output: String::new(),
        sub_calls: vec![],
        total_tokens_in: 0,
        total_tokens_out: 0,
        total_latency_ms: 0,
        error: Some(err.into()),
    }
}

fn value_to_text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// Returns the specialist name iff `op` is a single-step kind=specialist op.
/// Multi-step ops (LLM+code+specialist) fall through to per-row dispatch.
fn single_specialist_name(op: &OpDef) -> Option<String> {
    let arr = op.steps.as_ref()?.as_array()?;
    if arr.len() != 1 {
        return None;
    }
    let step = arr.first()?;
    let kind = step.get("kind")?.as_str()?;
    if kind != "specialist" {
        return None;
    }
    step.get("specialist")?.as_str().map(|s| s.to_string())
}

/// True when the specialist's transport actually client-batches, so folding N
/// rows into one call is a real win (not N serial calls server-side).
fn spec_client_batches(spec_name: &str) -> bool {
    let spec = match crate::specialists::get_cached_spec(spec_name)
        .or_else(|| crate::specialists::load_spec(spec_name).ok())
    {
        Some(s) => s,
        None => return false,
    };
    crate::specialists::transport_for(&spec.transport)
        .map(|t| t.client_batches())
        .unwrap_or(false)
}

/// Detect a multi-step op whose single heavy step is a specialist and whose
/// other steps are local `code` (e.g. rvbbit.about = rerank specialist +
/// json_get code). Returns (step_name, specialist_name). The specialist step's
/// `inputs` must reference only op inputs/opts (not earlier step outputs), so it
/// can be rendered and batched across rows independently.
fn batchable_specialist_step(op: &OpDef) -> Option<(String, String)> {
    let arr = op.steps.as_ref()?.as_array()?;
    if arr.len() < 2 {
        return None; // single-step specialist is handled by single_specialist_name
    }
    let mut found: Option<(String, String)> = None;
    for (i, step) in arr.iter().enumerate() {
        match step.get("kind").and_then(|v| v.as_str()).unwrap_or("") {
            "specialist" => {
                if found.is_some() {
                    return None; // more than one specialist — not the simple shape
                }
                let name = step
                    .get("name")
                    .and_then(|v| v.as_str())
                    .map(String::from)
                    .unwrap_or_else(|| format!("step_{i}"));
                let spec = step.get("specialist")?.as_str()?.to_string();
                let tmpl = step
                    .get("inputs")
                    .map(|v| v.to_string())
                    .unwrap_or_default();
                if tmpl.contains("steps.") {
                    return None; // depends on a prior step — can't pre-batch
                }
                found = Some((name, spec));
            }
            "code" => {}      // local, runs per-row after the batch
            _ => return None, // llm / python / sql / mcp — leave to per-row
        }
    }
    found
}

/// Batch the one specialist step of a multi-step op across rows, then finish
/// each row's cheap (code) steps locally with the specialist output seeded.
/// Produces the same final output + op input_hash as the per-row path, so the
/// subsequent per-row query resolves entirely from cache.
fn dispatch_batched_multistep_specialist(
    op: &Arc<OpDef>,
    step_name: &str,
    spec_name: &str,
    to_run: Vec<(usize, Value)>,
    opts: &Value,
    pool: &flow::Pool,
) -> Vec<(Value, WorkResult)> {
    let spec = match crate::specialists::load_spec(spec_name) {
        Ok(s) => s,
        Err(e) => {
            return to_run
                .into_iter()
                .map(|(_, inputs)| (inputs, fail_work(&format!("specialist load: {e}"))))
                .collect();
        }
    };

    let inputs_template = op
        .steps
        .as_ref()
        .and_then(|s| s.as_array())
        .and_then(|a| {
            a.iter()
                .find(|s| s.get("name").and_then(|v| v.as_str()) == Some(step_name))
        })
        .and_then(|s| s.get("inputs").cloned())
        .unwrap_or(Value::Object(Default::default()));

    let mut per_row_payloads: Vec<(Value, Value)> = Vec::with_capacity(to_run.len());
    for (_, inputs) in to_run {
        let scope = unit_of_work::Scope::new(inputs.clone(), opts.clone());
        let rendered = unit_of_work::render_value_templates(&inputs_template, &scope);
        per_row_payloads.push((inputs, rendered));
    }

    let batch_size = if crate::specialists::transport_for(&spec.transport)
        .map(|t| t.client_batches())
        .unwrap_or(false)
    {
        spec.batch_size.max(1)
    } else {
        1
    };

    type ChunkResult = (
        Vec<Value>,
        Result<(Vec<Value>, Vec<crate::specialists::Usage>), String>,
        i32,
    );
    let sem = crate::flow::Semaphore::new(spec.max_concurrent.max(1));
    let mut receivers: Vec<crossbeam_channel::Receiver<ChunkResult>> = Vec::new();
    for chunk in per_row_payloads.chunks(batch_size) {
        let chunk_inputs: Vec<Value> = chunk.iter().map(|(i, _)| i.clone()).collect();
        let payloads: Vec<Value> = chunk.iter().map(|(_, p)| p.clone()).collect();
        let spec_arc = spec.clone();
        let sem = sem.clone();
        let rx = pool.submit(move || {
            let _permit = sem.acquire();
            let t0 = std::time::Instant::now();
            let res = crate::specialists::predict_batch(&spec_arc, &payloads);
            let latency = t0.elapsed().as_millis().min(i32::MAX as u128) as i32;
            match res {
                Ok(resp) => (chunk_inputs, Ok((resp.outputs, resp.usage)), latency),
                Err(e) => (chunk_inputs, Err(e.to_string()), latency),
            }
        });
        receivers.push(rx);
    }

    let steps = op
        .steps
        .as_ref()
        .and_then(|s| s.as_array())
        .cloned()
        .unwrap_or_default();
    let mut out: Vec<(Value, WorkResult)> = Vec::new();
    for rx in receivers {
        let (chunk_inputs, outputs, latency) = rx
            .recv()
            .unwrap_or_else(|_| pgrx::error!("rvbbit: pool worker panicked during prewarm"));
        // Leader-side live progress: a batch of `chunk_inputs.len()` calls landed.
        crate::live_counters::tick(&op.name, chunk_inputs.len() as u64);
        match outputs {
            Ok((outs, usage)) => {
                for (i, (inputs, value)) in chunk_inputs.into_iter().zip(outs.into_iter()).enumerate() {
                    // Mirror run_step_specialist exactly: step output is
                    // {"output": value}, so {{ steps.<name>.output }} resolves,
                    // and the seed sub-call carries the same usage and cost.
                    let seed_sub = unit_of_work::specialist_sub_call(
                        step_name,
                        spec_name,
                        &spec,
                        &value,
                        usage.get(i),
                        latency,
                    );
                    let seed_output = serde_json::json!({ "output": value });
                    let result = unit_of_work::run_multistep_seeded(
                        op,
                        &steps,
                        &inputs,
                        opts,
                        step_name,
                        seed_output,
                        seed_sub,
                    );
                    out.push((inputs, result));
                }
            }
            Err(e) => {
                for inputs in chunk_inputs {
                    out.push((inputs, fail_work(&format!("specialist '{spec_name}': {e}"))));
                }
            }
        }
    }
    out
}

// ---- Semantic-MV pre-warm ------------------------------------------------

/// Pre-warm the operator cache for a semantic MV refresh.
///
/// Parses the MV's projection expression; if it is a single plain
/// `rvbbit.<op>(...)` call, the pending source rows (those not yet in the
/// MV) are collected and run through `warm` — batched + concurrent — so the
/// per-row anti-join INSERT that follows resolves entirely from cache.
///
/// Returns None when the projection is not a plain operator call (a more
/// complex expression, a non-scalar op, an opts arg, …). The caller then
/// runs the un-warmed INSERT: correct, just sequential.
pub fn warm_mv_projection(
    qualified_source: &str,
    mv_ident: &str,
    pk_ident: &str,
    projection_sql: &str,
) -> Option<WarmStats> {
    let (op_name, arg_exprs) = parse_operator_call(projection_sql)?;
    let op = load_op(&op_name)?;
    if op.shape != "scalar" {
        return None;
    }
    let arg_names = load_arg_names(&op_name);
    // Require an exact arity match — no explicit trailing opts arg (v1).
    if arg_names.is_empty() || arg_names.len() != arg_exprs.len() {
        return None;
    }

    // Build the inputs jsonb with jsonb_build_object so PG produces it
    // exactly as the operator's SQL wrapper does — byte-identical jsonb
    // means a cache hash that matches the per-row _exec_op_* path.
    let pairs: Vec<String> = arg_names
        .iter()
        .zip(arg_exprs.iter())
        .map(|(name, expr)| format!("{}, {}", sql_string_literal(name), expr))
        .collect();
    let inputs_expr = format!("jsonb_build_object({})", pairs.join(", "));

    // NOT EXISTS (rather than a LEFT JOIN) keeps the MV table out of the
    // SELECT's name scope, so bare column refs in the projection can never
    // resolve ambiguously.
    let collect_sql = format!(
        "SELECT {inputs_expr} \
         FROM {qualified_source} s \
         WHERE NOT EXISTS ( \
             SELECT 1 FROM rvbbit.{mv_ident} t WHERE t.{pk_ident} = s.{pk_ident})"
    );

    let mut inputs: Vec<Value> = Vec::new();
    let collected: Result<(), pgrx::spi::Error> = Spi::connect(|client| {
        let table = client.select(&collect_sql, None, &[])?;
        for row in table {
            if let Some(j) = row.get::<JsonB>(1)? {
                inputs.push(j.0);
            }
        }
        Ok(())
    });
    if collected.is_err() {
        return None;
    }
    if inputs.is_empty() {
        return Some(WarmStats {
            n_inputs: 0,
            n_cache_hits: 0,
            n_executed: 0,
            n_errors: 0,
        });
    }

    let opts = Value::Object(Default::default());
    Some(warm(&Arc::new(op), &opts, inputs))
}

/// Parse a projection expression of the exact form `rvbbit.<op>(<args>)`.
/// Returns (op_name, arg_exprs). Anything more complex — wrapped in other
/// expressions, multiple calls, missing schema — returns None.
fn parse_operator_call(expr: &str) -> Option<(String, Vec<String>)> {
    let s = expr.trim();
    let after = s.strip_prefix("rvbbit.")?;
    let name_end = after
        .find(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
        .unwrap_or(after.len());
    if name_end == 0 {
        return None;
    }
    let op_name = after[..name_end].to_string();
    let tail = after[name_end..].trim_start();
    let inner = tail.strip_prefix('(')?.trim_end().strip_suffix(')')?;
    let args = split_top_level_args(inner)?;
    Some((op_name, args))
}

/// Split a comma-separated argument list, respecting single-quoted string
/// literals (with '' escapes) and nested parentheses. None on unbalanced
/// nesting or an empty argument slot.
fn split_top_level_args(inner: &str) -> Option<Vec<String>> {
    let trimmed = inner.trim();
    if trimmed.is_empty() {
        return Some(Vec::new());
    }
    let mut args: Vec<String> = Vec::new();
    let mut depth: i32 = 0;
    let mut in_str = false;
    let mut cur = String::new();
    let mut chars = trimmed.chars().peekable();
    while let Some(c) = chars.next() {
        if in_str {
            cur.push(c);
            if c == '\'' {
                if chars.peek() == Some(&'\'') {
                    cur.push(chars.next().unwrap());
                } else {
                    in_str = false;
                }
            }
            continue;
        }
        match c {
            '\'' => {
                in_str = true;
                cur.push(c);
            }
            '(' => {
                depth += 1;
                cur.push(c);
            }
            ')' => {
                depth -= 1;
                if depth < 0 {
                    return None;
                }
                cur.push(c);
            }
            ',' if depth == 0 => {
                args.push(cur.trim().to_string());
                cur.clear();
            }
            _ => cur.push(c),
        }
    }
    if in_str || depth != 0 {
        return None;
    }
    args.push(cur.trim().to_string());
    if args.iter().any(|a| a.is_empty()) {
        return None;
    }
    Some(args)
}

fn sql_string_literal(s: &str) -> String {
    format!("'{}'", s.replace('\'', "''"))
}

// ---- Helpers -------------------------------------------------------------

fn collect_inputs(sql: &str, arg_names: &[String]) -> Vec<Value> {
    // Let PostgreSQL do the same typed JSON conversion as create_operator's
    // wrapper. Reading every SPI datum as String silently discarded JSONB,
    // numeric and array inputs (and therefore all work in those queries).
    // Positional aliases preserve the public API: query columns need not have
    // the operator's argument names. Skip SQL NULLs just like STRICT wrappers.
    let columns: Vec<String> = (0..arg_names.len()).map(|i| format!("_arg_{i}")).collect();
    let pairs: Vec<String> = arg_names
        .iter()
        .zip(&columns)
        .map(|(name, column)| format!("{}, _inputs.{column}", sql_string_literal(name)))
        .collect();
    let aliases = if columns.is_empty() {
        String::new()
    } else {
        format!("({})", columns.join(", "))
    };
    let predicate = if columns.is_empty() {
        "true".to_string()
    } else {
        columns
            .iter()
            .map(|column| format!("_inputs.{column} IS NOT NULL"))
            .collect::<Vec<_>>()
            .join(" AND ")
    };
    let collect_sql = format!(
        "SELECT jsonb_build_object({}) FROM ({}) AS _inputs{} WHERE {}",
        pairs.join(", "),
        sql.trim().trim_end_matches(';'),
        aliases,
        predicate
    );
    let mut out: Vec<Value> = Vec::new();
    let collected: Result<(), pgrx::spi::Error> = Spi::connect(|client| {
        let table = client.select(&collect_sql, None, &[])?;
        for row in table {
            if let Some(value) = row.get::<JsonB>(1)? {
                out.push(value.0);
            }
        }
        Ok(())
    });
    collected.unwrap_or_else(|error| pgrx::error!("rvbbit prewarm: input query failed: {error}"));
    out
}

fn load_arg_names(op_name: &str) -> Vec<String> {
    let escaped = op_name.replace('\'', "''");
    let sql = format!("SELECT arg_names FROM rvbbit.operators WHERE name = '{escaped}'");
    Spi::get_one::<Vec<Option<String>>>(&sql)
        .ok()
        .flatten()
        .unwrap_or_default()
        .into_iter()
        .flatten()
        .collect()
}

fn load_op(name: &str) -> Option<OpDef> {
    let escaped = name.replace('\'', "''");
    let sql = format!(
        "SELECT shape, return_type, model, system_prompt, user_prompt, parser, \
                max_tokens, temperature, steps, retry, wards, takes, cache_policy \
         FROM rvbbit.operators WHERE name = '{escaped}'"
    );
    let mut result: Option<OpDef> = None;
    let _: Result<(), pgrx::spi::Error> = Spi::connect(|client| {
        let table = client.select(&sql, Some(1), &[])?;
        for row in table {
            let shape: Option<String> = row.get(1)?;
            let return_type: Option<String> = row.get(2)?;
            let model: Option<String> = row.get(3)?;
            let system_prompt: Option<String> = row.get(4)?;
            let user_prompt: Option<String> = row.get(5)?;
            let parser: Option<String> = row.get(6)?;
            let max_tokens: Option<i32> = row.get(7)?;
            let temperature: Option<f32> = row.get(8)?;
            let steps: Option<JsonB> = row.get(9)?;
            let retry: Option<JsonB> = row.get(10)?;
            let wards: Option<JsonB> = row.get(11)?;
            let takes: Option<JsonB> = row.get(12)?;
            let cache_policy: Option<String> = row.get(13)?;
            if let (Some(sh), Some(rt), Some(m), Some(sp), Some(up), Some(p), Some(mt)) = (
                shape,
                return_type,
                model,
                system_prompt,
                user_prompt,
                parser,
                max_tokens,
            ) {
                result = Some(OpDef {
                    name: name.to_string(),
                    shape: sh,
                    return_type: rt,
                    model: m,
                    system_prompt: sp,
                    user_prompt: up,
                    parser: p,
                    max_tokens: mt,
                    temperature,
                    steps: steps.map(|j| j.0),
                    retry: retry.map(|j| j.0),
                    wards: wards.map(|j| j.0),
                    takes: takes.map(|j| j.0),
                    cache_policy: cache_policy.unwrap_or_else(|| "memoize".to_string()),
                });
            }
        }
        Ok(())
    });
    result
}

fn build_hash(op: &OpDef, opts: &Value, inputs: &Value) -> Vec<u8> {
    let model_override = opts.get("model").and_then(|v| v.as_str()).unwrap_or("");
    let prompt_seed = crate::operators::compute_prompt_seed(op);
    let mut h = blake3::Hasher::new();
    h.update(op.name.as_bytes());
    h.update(b"\0");
    // Mirror operators::input_hash — fold in op.model so catalog model
    // changes invalidate (RYR-301) and prewarm+exec hashes line up.
    h.update(op.model.as_bytes());
    h.update(b"\0");
    h.update(model_override.as_bytes());
    h.update(b"\0");
    h.update(serde_json::to_string(inputs).unwrap_or_default().as_bytes());
    h.update(b"\0");
    h.update(prompt_seed.as_bytes());
    h.finalize().as_bytes().to_vec()
}

fn lookup_cached_l2(hash: &[u8]) -> Option<String> {
    let hex = bytes_to_hex(hash);
    let sql = format!(
        "SELECT output FROM rvbbit.receipts \
         WHERE inputs_hash = '\\x{hex}'::bytea AND error IS NULL \
         ORDER BY invocation_at DESC LIMIT 1"
    );
    Spi::get_one::<String>(&sql).ok().flatten()
}

fn log_receipt_leader(op: &OpDef, hash: &[u8], res: &WorkResult, inputs: &Value) {
    let record = crate::costs::record_from_work(op, hash, res, inputs);
    crate::costs::flush_receipt_queue_best_effort(64);
    if let Err(e) = crate::costs::write_receipt_now(&record, crate::costs::MissingQueryId::Generate)
    {
        pgrx::warning!("rvbbit prewarm: receipt log failed: {}", e);
    }
}

fn bytes_to_hex(b: &[u8]) -> String {
    let mut s = String::with_capacity(b.len() * 2);
    for byte in b {
        s.push_str(&format!("{:02x}", byte));
    }
    s
}

// Silence unused chat import warning — the indirect path through
// unit_of_work::execute is what actually calls providers, but keeping
// the symbol in scope documents the dependency.
#[allow(dead_code)]
fn _silence(_: ChatRequest) {}

#[cfg(test)]
mod phase4_tests {
    use super::*;
    use serde_json::json;

    fn op_with_steps(steps: serde_json::Value) -> OpDef {
        OpDef {
            name: "t".into(),
            shape: "scalar".into(),
            return_type: "float8".into(),
            model: "m".into(),
            system_prompt: String::new(),
            user_prompt: String::new(),
            parser: "json".into(),
            max_tokens: 0,
            temperature: None,
            steps: Some(steps),
            retry: None,
            wards: None,
            takes: None,
            cache_policy: "memoize".into(),
        }
    }

    #[test]
    fn detects_about_shape() {
        // about: rerank specialist (inputs from op inputs) + json_get code.
        let op = op_with_steps(json!([
            {"kind":"specialist","name":"rerank","specialist":"rerank_bge_m3",
             "inputs":{"text":"{{ inputs.text }}","query":"{{ inputs.topic }}"}},
            {"kind":"code","name":"score","fn":"json_get",
             "inputs":{"value":"{{ steps.rerank.output }}","path":"score"}}
        ]));
        assert_eq!(
            batchable_specialist_step(&op),
            Some(("rerank".to_string(), "rerank_bge_m3".to_string()))
        );
    }

    #[test]
    fn rejects_non_batchable_shapes() {
        // single-step specialist -> handled elsewhere, not here.
        assert!(batchable_specialist_step(&op_with_steps(json!([
            {"kind":"specialist","name":"r","specialist":"x","inputs":{}}
        ])))
        .is_none());
        // specialist whose inputs depend on a prior step -> can't pre-batch.
        assert!(batchable_specialist_step(&op_with_steps(json!([
            {"kind":"code","name":"pre","fn":"trim","inputs":{"text":"{{ inputs.text }}"}},
            {"kind":"specialist","name":"r","specialist":"x",
             "inputs":{"text":"{{ steps.pre.output }}"}}
        ])))
        .is_none());
        // two specialists -> not the simple shape.
        assert!(batchable_specialist_step(&op_with_steps(json!([
            {"kind":"specialist","name":"a","specialist":"x","inputs":{}},
            {"kind":"specialist","name":"b","specialist":"y","inputs":{}}
        ])))
        .is_none());
        // an llm step present -> leave to per-row.
        assert!(batchable_specialist_step(&op_with_steps(json!([
            {"kind":"specialist","name":"a","specialist":"x","inputs":{}},
            {"kind":"llm","name":"b","user":"{{ steps.a.output }}"}
        ])))
        .is_none());
    }
}

#[cfg(any(test, feature = "pg_test"))]
#[pgrx::pg_schema]
mod tests {
    use pgrx::prelude::*;

    /// A stub specialist whose every answer carries a Clover-style usage
    /// block, plus 5 input rows. Batched prewarm must cost each receipt
    /// from that block, like the per-row path does.
    fn costed_stub_setup(op: &str, steps: &str) {
        // rvbbit.get_secret comes from migration 0143, which the pgrx test
        // install doesn't apply; backend loading calls it.
        Spi::run(
            "DO $s$ BEGIN IF to_regprocedure('rvbbit.get_secret(text)') IS NULL THEN \
             CREATE FUNCTION rvbbit.get_secret(text) RETURNS text LANGUAGE sql AS 'SELECT NULL::text'; \
             END IF; END $s$",
        )
        .unwrap();
        Spi::run(
            "SELECT rvbbit.register_backend(backend_name => 'stub_costed', \
             backend_endpoint => 'stub://8', backend_transport => 'stub', backend_batch_size => 4)",
        )
        .unwrap();
        Spi::run("SELECT rvbbit.reload_backends()").unwrap();
        Spi::run(&format!(
            "SELECT rvbbit.create_operator(op_name => '{op}', op_shape => 'scalar', \
             op_arg_names => ARRAY['text'], op_return_type => 'text', op_system => 'unused', \
             op_user => 'unused', op_steps => '{steps}'::jsonb)"
        ))
        .unwrap();
        Spi::run(
            "CREATE TEMP TABLE costed_rows AS SELECT 'row ' || g AS text FROM generate_series(1, 5) g",
        )
        .unwrap();
    }

    const SPECIALIST_STEP: &str = r#"{"name":"decide","kind":"specialist","specialist":"stub_costed","inputs":{"text":"{{ inputs.text }}","echo":{"choice":"yes","usage":{"cost":0.00025,"input_tokens":40,"output_tokens":3}}}}"#;

    fn receipt_totals(op: &str) -> (i64, i64, f64, i64, String) {
        let row = Spi::get_one::<String>(&format!(
            "SELECT concat_ws('|', count(*), count(cost_usd), coalesce(sum(cost_usd), 0), \
                    coalesce(sum(n_tokens_in), 0), \
                    coalesce(string_agg(DISTINCT sub_calls->0->>'step', ','), '')) \
             FROM rvbbit.receipts WHERE operator = '{op}'"
        ))
        .unwrap()
        .unwrap();
        let f: Vec<&str> = row.split('|').collect();
        (
            f[0].parse().unwrap(),
            f[1].parse().unwrap(),
            f[2].parse().unwrap(),
            f[3].parse().unwrap(),
            f[4].to_string(),
        )
    }

    #[pg_test]
    fn batched_specialist_receipts_carry_the_outputs_cost() {
        costed_stub_setup("costed_stub_op", &format!("[{SPECIALIST_STEP}]"));
        Spi::run("SELECT * FROM rvbbit.prewarm_operator('costed_stub_op', 'SELECT text FROM costed_rows')")
            .unwrap();
        let (n, costed, cost, tokens_in, step) = receipt_totals("costed_stub_op");
        assert_eq!((n, costed), (5, 5), "every batched receipt is costed");
        assert!((cost - 5.0 * 0.00025).abs() < 1e-9, "cost {cost}");
        assert_eq!(tokens_in, 5 * 40);
        assert_eq!(step, "decide");
    }

    #[pg_test]
    fn batched_specialist_then_code_receipts_carry_the_outputs_cost() {
        let steps = format!(
            r#"[{SPECIALIST_STEP},{{"name":"g","kind":"code","fn":"json_get","inputs":{{"path":"choice","value":"{{{{steps.decide.output}}}}"}}}}]"#
        );
        costed_stub_setup("costed_stub_label", &steps);
        Spi::run("SELECT * FROM rvbbit.prewarm_operator('costed_stub_label', 'SELECT text FROM costed_rows')")
            .unwrap();
        let (n, costed, cost, tokens_in, _) = receipt_totals("costed_stub_label");
        assert_eq!((n, costed), (5, 5), "the label op loses the usage block, the receipt must not");
        assert!((cost - 5.0 * 0.00025).abs() < 1e-9, "cost {cost}");
        assert_eq!(tokens_in, 5 * 40);
        let label = Spi::get_one::<String>("SELECT rvbbit.costed_stub_label('row 1')").unwrap();
        assert_eq!(label.as_deref(), Some("yes"));
    }
}
