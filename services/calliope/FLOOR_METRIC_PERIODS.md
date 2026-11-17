# Floor metric time semantics

Core tiles show two independent things: the latest materialized value of a
metric, and a sampled history of that metric. Gallery keeps its existing UI and
business-date windows.

Floor's Daily / Weekly / Monthly / Quarterly control now selects the **chart's
calendar grain**, covering at most twelve periods including the current partial
period. Each point is the last available observation in that period. Periods
use the metric's declared `labels.time_zone` (UTC if absent/invalid); weeks start
Monday. There is no averaging, summing, or SQL re-execution. Missing periods are
not fabricated, and sparse history does not silently switch to a finer grain.
Readings mode still shows the last 24 observations.

The caption explicitly says these are snapshots, not period totals. For example,
a weekly point for a default-today metric remains the daily count observed at
that point in time. It is **not** the week's enrollment total. True weekly counts,
rates, and medians require period-parameterized materialization using each
metric's own SQL, not aggregation of previously observed scalar values.

The headline has a separate measurement-period label. Authors can declare
`labels.display.measurement_period` as a short string or an object with `label`
and optional `detail`. The server also supports existing explicit declarations:
omitted parameters defaulting to today (grain/description), latest-N-date cohorts
(grain), and `labels.cohort_start`. Metric names are never used to infer periods.
Unrecognized definitions display “Latest reading.”

Default-today values are labeled Today only if recorded on the current day in
the declared metric timezone; older readings show their date. Chart date labels,
an endpoint dot, a dated comparison baseline, and an “Updated …” footer separate
measurement window, plotted history, and freshness. A zero comparison baseline
shows absolute change instead of an unexplained arrow or invented percentage.

API: `/api/gallery/metrics?category=Core&floor_period=week`. The new parameter is
optional and incompatible with `ticks`; ordinary Gallery callers are unchanged.
No migration or live metric-definition changes are required.
