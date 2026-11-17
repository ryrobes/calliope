# Private Jev retrieval trial

For the separate live **specialist model selection shadow** experiment, see
[`SPECIALIST_MODEL_SHADOW.md`](../../services/calliope/SPECIALIST_MODEL_SHADOW.md).
It observes fresh specialist assignments without changing execution models or
prompts. `specialist_model_shadow_report.py` joins predictions to actual run
traces for later review; it performs no inference itself.

This is an offline experiment, not a change to live chat context. Freeze a small
set of existing Page questions through the caller-scoped `ask_brain` path, then
compare ordinary retrieval order with a Jev-selected subset. Both answer
branches use the same model, question, standing brief, source text and limits.
No sessions, tools, memories, Page revisions or notifications are generated.
The usual retrieval embedding cache can be populated.

Run within the Calliope container (or a matching environment with access to its
configured database). Keep snapshots and output in a private directory outside
the source repository. They contain real customer text. Credentials are read
from the existing environment and are never included in receipts.

```sh
PYTHONPATH=/app python freeze_brain_retrieval.py \
  --owner USER_EMAIL --page PAGE_UUID --output /app/data/experiments/TRIAL/frozen.json
python jev_retrieval_trial.py \
  --snapshot /app/data/experiments/TRIAL/frozen.json \
  --output /app/data/experiments/TRIAL/run-1 \
  --model MODEL_ID
```

Choose the deployed answering model explicitly. The first trial used the
configured `openai/gpt-5.6-sol` via OpenRouter for both answer branches and the
blinded reviewer, and `typesafe/jev-1.13` for selection. This is a bounded
**evidence-only answer pass**, not the full Hermes system prompt, SOUL, memory,
tool loop, or reconstruction of a historical corpus. It is not a whole-thread
speed benchmark.

Each case retrieves 24 candidates once. Both answer branches get at most eight
complete chunks and 18,000 serialized evidence characters; there is no hidden
chunk truncation. Selection can change actual token counts within that shared
cap. Presentation order is held in original retrieval order. Jev uses fixed
criteria, including material qualifications and contradictions. It does not
ask an agent to invent a decision problem. Invalid selector responses fail the
experiment rather than silently dropping evidence.

The private HTML report shows answers side by side, selected IDs, actual
provider usage/cost, observed timings, the full candidate pool and a blinded
review. Full requests and responses are saved as resumable receipts. Completed
calls are reused only when the request hash matches. Use a fresh output folder
for changed prompts/settings or after a failed call.

The automated reviewer sees the full pool, but is not external ground truth.
It uses the same model family as the answerer. Check its claims manually,
especially when it prefers a more detailed answer. Evaluation inference is
separate from the experimental answer path. Retrieval embedding cost is not
included; unknown provider costs remain unknown. Concurrent branch latency is
noisy and prefix caching may differ: inspect token cache usage before making
speed/cost claims. Three convenience-sampled questions cannot establish a
quality-neutral production improvement.

Next steps depend on evidence: test more source-appropriate questions, add
human evidence labels, repeat/control branch order and cache conditions, and
only then reduce the Jev branch's context budget. No production rollout is
implied by running this script.

## Second experiment: evidence-aware routing

`freeze_routing_sources.py` reads a bounded runtime telemetry snapshot and
registered campaign-tool input schemas under a read-only transaction, and
opens two permitted documents from the original project question using the
existing caller ACL. It never invokes those integrations, refreshes an
observer, or changes a Page. This particular collector is an admin-only
fixture: its SQL is fixed, its query-usage aggregates are caller-scoped, and
its first Page must be the project-status question from the first trial.

`jev_routing_trial.py` compares a fixed source-kind/keyword heuristic, Jev,
and the configured main model as routers. Each selects one enumerated next
step: answer, expand a known document, search documents, inspect runtime
telemetry, or inspect integration contracts. No route synthesizes arguments,
SQL, or permissions. Full-document effects can only expand IDs already
present in the case. The proposed source packets are never shown to routers
before they choose.

```sh
PYTHONPATH=/app python freeze_routing_sources.py \
  --snapshot /PRIVATE/TRIAL/frozen.json --output /PRIVATE/ROUTING/sources.json
python jev_routing_trial.py \
  --snapshot /PRIVATE/TRIAL/frozen.json --sources /PRIVATE/ROUTING/sources.json \
  --output /PRIVATE/ROUTING/run --model MODEL_ID
```

Keep `jev_retrieval_trial.py` beside the routing runner. That module supplies
the existing authenticated provider client and private, resumable receipts.

The three original questions are exploratory. Eight constructed controls pair
the same question with sufficient and insufficient real evidence; their
expected routes are frozen before calls and excluded from model inputs. They
are deliberately simple scope checks, not an independent benchmark. The
keyword heuristic also checks source kinds and narrow ticket status fields,
so it is more than a question-only baseline.

For the original questions, generate fresh baseline and routed answers with
the same model and whole-item budget: 24 items / 18,000 evidence characters.
The router and baseline answer see the **same** evidence. This differs from
the first experiment's eight-chunk subset test. Alternative source packets
take priority within the same cap. An answer route has identical evidence
in both branches; differences there are generation noise, not routing gains.
Search-document choices are recorded but have no frozen effect packet and
therefore do not produce a fabricated follow-up answer.

Routing calls run Jev first, then the main model, once per case. Record cache
usage as well as cost and latency. This is not a full chat latency comparison:
all candidate source packets are prefetched once, and main-agent planning,
authorization, retrieval retries, and cache economics are not simulated.
No production behavior changes merely by running the experiment.

## Third experiment: natural retrieval boundaries

`freeze_retrieval_traces.py` freezes the latest eight owner-linked main Calliope
Hermes sessions with a recorded `ask_brain` call in the preceding 30 days. It
reads the PostgreSQL mirror only, with a read-only transaction and bounded
queries. No new searches, integrations or agent sessions run.

`jev_retrieval_shadow.py` replays at most the last two completed retrieval
batches per session. Compaction copies are deduplicated by call ID. Every
issued sibling tool result must be present before the boundary is accepted;
already-issued parallel calls cannot become predictions of a future action.

```sh
PYTHONPATH=/app python freeze_retrieval_traces.py --owner USER_EMAIL \
  --output /PRIVATE/SHADOW/traces.json
python jev_retrieval_shadow.py --snapshot /PRIVATE/SHADOW/traces.json \
  --output /PRIVATE/SHADOW/run --model MODEL_ID
```

The selector receives only the user requests available at the boundary, the
issuing assistant message, completed sibling results, and bounded earlier
evidence. Subsequent actions are stored separately and never enter the Jev
request. A blinded reviewer sees the subsequent three assistant decisions
and tool results, but not Jev's prediction. That review assesses observed
evidence utility, not counterfactual savings or proof that an alternative
route would succeed. Its inference cost is evaluation overhead.

Whole tool packets can be omitted by budget; omission IDs and scope are
recorded. The state is not a reconstruction of Hermes's full prompt, hidden
reasoning, SOUL or tool-output file contents. Raw spilled-file references are
not opened; later reads appear only when recorded in the continuation.

The natural-trace selector adds data discovery, business querying, and normal
agent continuation to the prior trial's five routes. These are advisory enums,
not instructions to run SQL, delegate, change permissions or answer a user.
`continue_agent` explicitly preserves the main model's judgment. Agreement
with a coarse next-tool category is behavioral overlap, not route accuracy.
All results are retrospective, not a live observer or deployed routing path.

The runner and provider receipts can be reused on future snapshots, but this
experiment installs no watcher, poller, cron job, or production hook.

## Fourth experiment: enrich search results locally

`freeze_search_expansions.py` takes the natural-trace snapshot above and
reissues each thread's last valid `ask_brain` query with its filters and k=8.
It freezes caller-permitted neighboring chunks and document heads for every
returned source, purely as offline comparison fixtures. No agent sessions,
source documents or memories are created or updated.

```sh
PYTHONPATH=/app python freeze_search_expansions.py \
  --traces /PRIVATE/SHADOW/traces.json --output /PRIVATE/EXPANSION/fixtures
python jev_search_expansion_trial.py \
  --snapshot /PRIVATE/EXPANSION/fixtures/frozen.json \
  --output /PRIVATE/EXPANSION/run-1 --model MODEL_ID
```

Keep the earlier experiment modules alongside these scripts. The selector
receives only the tool query, filters and returned source excerpts. It can
abstain or pick up to two existing sources. Expanded text and conversation
history never enter its request. Baselines are unchanged search and expansion
of the first two distinct sources. Every output retains the full original
search result and only adds bounded, source-tagged chunks.

The blinded evaluator judges new evidence relevant to the query, not a
counterfactual answer or saved agent turn. Citation validation checks that
claimed new facts refer to actual added chunks; manual inspection is still
required to establish relevance and novelty. Prefetched lookup timings do
not measure production wall-clock latency. Private receipts and HTML reports
must stay outside git. No live search middleware is installed.

See `RESULTS-EXPANSION-2026-09-18.md` for results and the section-selection,
duplicate-source and empty-expansion limitations found in the first run.

## Fifth experiment: select sections and test bounded follow-up lookups

`freeze_section_expansions.py` reuses the fourth experiment's frozen hits.
It reads chunk indices and content hashes for those documents under
`brain_visible_docs` ACLs. Original excerpt hashes must still match the
current chunks, or collection fails rather than mixing source revisions.
This is metadata-only planning; no expansion body enters the selector.

```sh
PYTHONPATH=/app python freeze_section_expansions.py \
  --snapshot /PRIVATE/EXPANSION/fixtures/frozen.json \
  --output /PRIVATE/SECTIONS/fixtures
python jev_section_expansion_trial.py \
  --snapshot /PRIVATE/SECTIONS/fixtures/frozen.json \
  --output /PRIVATE/SECTIONS/run --model MODEL_ID
```

Each returned excerpt is a possible +/-2 chunk window. Windows with no
unseen text are excluded deterministically. Identical candidate windows are
collapsed with their source references retained. The selector can choose
zero to two windows, including different sections of the same document.
There is no automatic document-head expansion. Whole added chunks are
deduplicated against original hits and previous additions; all original
result fields remain unchanged. Rank-first uses the same preprocessing,
deduplication and size limits as Jev, so it is a stronger baseline than in
the fourth experiment.

Two answer branches receive either the unchanged or Jev-enriched result.
They can request up to two known sections per round for at most two rounds,
then answer with source citations and explicit evidence gaps. This is a
bounded query-answer task, **not** a replay of the real user conversation or
Hermes system prompt. No source synthesis, new search, SQL or integration
calls are allowed; lookups read the private frozen fixtures. Both branches
see the same available-section metadata. Original queries can be broad
search phrases rather than standalone business questions.

Different initial results are evaluated concurrently using the same model.
Identical inputs reuse one continuation rather than inventing a difference
from sampling noise. Costs in branch totals therefore represent separate
hypothetical paths, not the actual combined invoice. Full prompts/responses
remain private; matching completed receipts can be resumed. A protocol
failure must be recorded, not silently repaired into an apparent success.

The blinded reviewer assesses initial enrichment separately from final
answers. Follow-up lookup counts alone are not success: check unsupported
claims, missing qualifications, total automatic plus requested reads, and
main-model calls/cost. These are the same development queries as before,
not an independent holdout or a measured live-chat improvement. Nothing is
installed in the production retrieval path.

See `RESULTS-SECTIONS-2026-09-18.md` for the completed eight-case comparison,
protocol correction, manual checks and limits on interpreting the savings.

## Sixth experiment: judge actual new text after bounded fetching

`jev_fact_expansion_trial.py` uses the same frozen section fixtures. A fixed
policy chooses up to four nonredundant windows in retrieval order, then builds
a round-robin pool of at most 12 whole unseen chunks / 12,000 serialized chunk
characters. Chunks already present in original hits or earlier pool entries
are excluded. The script never inspects unfetched windows to choose fetches.

```sh
python jev_fact_expansion_trial.py \
  --snapshot /PRIVATE/SECTIONS/run/frozen.json --prior /PRIVATE/SECTIONS/run \
  --output /PRIVATE/FACTS/run --model MODEL_ID
```

Jev sees the actual candidate text plus the original excerpts and tool query.
It labels new direct evidence, material qualifications, redundancy, unrelated
content, or unclear fragments. Only direct evidence and qualifications are
eligible for attachment: at most four whole chunks / 6,000 serialized chunk
characters. No generated summaries or extracted claims replace source text.
The unchanged and first-eligible-chunks baselines retain the same original
result. The latter uses identical prefetch, pool and attachment limits.

All three branches run the fifth experiment's bounded answer/lookup task.
Original-branch receipts are reused only when the frozen corpus and every
request hash match. Identical initial outputs share one continuation. Only
fully delivered windows are removed from follow-up options: filtering text
does not make it unavailable to the agent.

Read accounting includes all upfront windows, even if Jev attaches nothing.
Subsequent requests into the prefetched windows are counted as agent lookups
but cache hits, not extra physical window reads. This models a request-local
fetch cache, not a deployed cache or a measured database benchmark. The
previously collected fixtures make live DB latency and cost unmeasured.

The blinded review compares initial additions and final answers separately,
with citations checked against each actual addition. Cost totals include Jev
for its branch and exclude evaluation, unpriced database work, and original
fixture collection. The eight development queries and single recorded
continuations do not establish general or quality-neutral savings. No live
search or conversation hook is installed.

See `RESULTS-FACTS-2026-09-18.md` for the three-way comparison and its explicit
prefetch/read accounting, quality checks and comparison with the prior trial.
