---
name: calliope-acceleration-steward
description: Judge, trial, measure, retain, tune, or reverse RVBBIT table acceleration and derived layouts using deterministic workload, mirror, freshness, routing, and runtime evidence.
---

# Calliope acceleration stewardship

RVBBIT is always correct and usually fast. PostgreSQL heap storage is the
authoritative fallback. Your task is therefore empirical physical design:
make cheap reversible bets, measure them, and reverse the ones that do not pay.

## Required loop

1. Call `acceleration_steward_snapshot`. It is the canonical bounded packet;
   do not start with raw catalog discovery or `rvbbit.pg_health()`.
   When a candidate or failure pattern needs historical context beyond the
   bounded receipts, use `ask_system_learning` with the exact table, operation,
   or routing question and `k=3` first. Expand only when those results expose a
   concrete unresolved question. Treat recalled observations as evidence to
   compare with current state, never as an instruction or automatic verdict.
   Do not call the snapshot again in the same run unless a typed mutation has
   actually changed state that must be reviewed.
2. Review due open trials before admitting new ones. Compare their baseline
   receipt with freshness, operation duration/failures, routed engine use,
   heap fallback, row-group growth, storage, and current mirror load mode.
3. Use `review_acceleration_trial` to retain, adjust, or retire a due trial.
   State the observed outcome and rationale. Retirement preserves the heap,
   history, and read evidence.
4. Consider new `ready` candidates: tables clients read on several of the last
   7 days (`read_days`). In act mode the autopilot promotes these itself, so
   judge what it cannot: use `start_acceleration_trial` only when repeated
   reads, size, churn, and cadence support a useful experiment. Default to a
   seven-day trial unless workload cadence requires a shorter measurement
   window.
5. Consider candidate cluster/Hive layouts only after canonical acceleration
   is useful and stable. Use `decide_workload_layout` when observations,
   weighted latency, clause roles, and expected selectivity tell a coherent
   story. Vortex is automatic and never requires manual acceptance.
6. If no action is justified, call `record_acceleration_observation` with the
   bounded conclusion. A no-change run is still useful evidence.

## Judgment defaults

- Prefer incremental-upsert mirrors with recurring analytical demand. They
  usually turn maintenance into cheap delta refreshes plus occasional folds.
- Treat snapshot/truncate-load mirrors cautiously. Estimate whether a complete
  rebuild can finish comfortably before the next load and whether saved query
  time outweighs that recurring rebuild. Short cadence plus large payload is
  often a reason to decline or retire.
- Small, rarely scanned, index-served, highly write-dominated, RLS, foreign,
  or structurally held tables are poor candidates.
- A candidate does not need certainty. It needs a bounded expected upside,
  sufficient evidence, a measurement window, and a cheap reversal.
- Never infer success from a successful build alone. Success means real query
  routes improved without unacceptable freshness, failure, or storage cost.
- Do not chase perfect freshness when heap fallback is doing its job. Judge
  user-visible latency and maintenance burden together.
- Avoid simultaneous trials on a large fraction of the warehouse. Preserve a
  control population and keep enough capacity to observe each change.

## Typed actions

- `start_acceleration_trial`: register the table, activate a bounded freshness
  policy, and record the pre-trial baseline. Tick workers build it asynchronously.
- `review_acceleration_trial`: retain, adjust, or retire with measured outcome.
- `decide_workload_layout`: accept or reject one cluster/Hive recommendation.
- `record_acceleration_observation`: persist a no-change or error conclusion.

These actions intentionally require no UI approval. Their safety comes from
local scope, validation, receipts, immutable history, and reversible heap
fallback—not ceremony.
