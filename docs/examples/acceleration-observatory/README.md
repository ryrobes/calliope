# Acceleration observatory

One set of definitions for "how healthy is acceleration", consumed from three places.

| Piece | Where | Notes |
|---|---|---|
| **Views** (the truth) | `crates/pg_rvbbit/sql/migrations/0329_accel_observatory_views.sql` — `rvbbit.accel_observatory_{tables,coverage,strategy,target_distance,layouts,layout_summary,live,attention,rebuild_cost,autopilot_events}` + `accel_observatory_timeline(hours,limit)` | eligible = past first baseline; current = authoritative ∧ clean ∧ no replacement pending; fresh (child layout) = parent current ∧ child ready, row‑complete, refreshed after newest visible parent row group; distance‑to‑target = seconds dirty ÷ target, `min_interval` is the implicit target for `scheduled`, clean tables are `current`. |
| **DataRabbit tab** | `datarabbit/src/components/desktop/routing-observatory-tab.tsx`, data in `fetchAccelObservatory` / `OBSERVATORY_QUERIES` (`datarabbit/src/lib/rvbbit/routing.ts`) | Routing window → *Observatory*. Auto‑refresh 30 s; actions: refresh / rebuild… per table, autopilot pass, reconcile stale layouts. |
| **System dashboard** | `services/calliope/system-dashboards/acceleration-health.html`, seeded by `_seed_system_dashboards()` (`services/calliope/calliope.py`) at Warehouse startup | slug `rvbbit-acceleration-health`, owner `calliope@system`, Everyone view grant, new version only when the file's sha256 changes, skipped when the views are absent. Its `Q` map queries the views using the result aliases the page's render code expects. |

To change a definition, change the view; both front ends follow. To add a panel, add a view (or a column), then read it from `OBSERVATORY_QUERIES` and the page's `Q` map.
