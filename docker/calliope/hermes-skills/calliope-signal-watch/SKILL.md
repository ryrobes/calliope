---
name: calliope-signal-watch
description: Detect and investigate material business changes using governed semantics, change graphs, structured data, and Company Brain context.
---

# Calliope signal watch

1. Start with recent `business_context_changes`, `business_metric_change`,
   governed `metric_history`, open specialist findings, and value-system
   context. Prefer a handful of high-value signals over a warehouse-wide scan.
2. Validate period completeness, source freshness, mirror status, seasonality,
   and known cadence before treating movement as business change.
3. Decompose a credible signal through its value system, contributors,
   segments, and relevant entities. Use Company Brain only for corroborating
   context; it does not prove causality.
4. Compare at least two plausible explanations when evidence permits. State
   the decisive follow-up test when it does not.
5. Record only material signals with `record_specialist_finding`. Use stable
   dedupe keys so an ongoing signal becomes one evolving case instead of daily
   duplicates. Set a next review time when monitoring is appropriate.
6. If there is no material new information, return exactly `[SILENT]` during a
   scheduled run.

Never report a mirror failure as a business event or a partial period as a
completed-period decline.
