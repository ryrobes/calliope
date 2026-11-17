# Specialist model selection: shadow experiment

Observe the assignment Calliope already assembled and ask whether it looks
suitable for a Sol, Terra, or Luna execution lane. **No models are switched.**
These names describe experiment candidates, not measured capability rankings.
In particular, Terra is not presumed to be a better value than Luna. We retain
the underlying judgments so that policy can be reconsidered after real work.

## Enable after deployment

No database migration or Hermes change is needed. The server image includes
`specialist_model_shadow.py`; the Compose environment exposes:

```dotenv
CALLIOPE_SPECIALIST_MODEL_SHADOW=shadow
CALLIOPE_SPECIALIST_MODEL_SHADOW_OWNERS=
CALLIOPE_SPECIALIST_MODEL_SHADOW_SESSIONS=
```

Default is `off`. Only the literal `shadow` value enables observation; there
is no live routing mode. Blank scopes include all users and source chat sessions.
Optional comma-separated owner emails/session UUIDs narrow collection. Uses the
server's existing `OPENROUTER_API_KEY` and `typesafe/jev-1.13` Decisions endpoint.
Set the mode to `off` to stop new observations; existing receipts remain.

To run the experiment on an install, enable `shadow` in its environment and
restart calliope-server. Local source changes alone do not enable it on a
running install. No model profile/settings writes are needed.

## What happens at dispatch

After the normal handoff is assembled, a retained background task observes it
while the normal Hermes launch proceeds. Nothing awaits the selector on the
launch path. No extra agent turn, retrieval, document expansion, visual capture,
image-model call, or memory write is performed.

The input consists of bounded assignment text (8k characters), provided context
(8k), parent request (2k), reference descriptors (4k), image count, and design
profile presence. No image bytes, SOUL, historical specialist context, or future
results are included. Agent identity is saved in the receipt but is not a
classifier input. This avoids a policy that simply learns "Iris is easy" or
"Cassandra is difficult." Names can still occur naturally in assignment text.

One batched request classifies five dimensions:

- **Scope:** specified execution, mixed decisions, open investigation, unknown.
- **Evidence:** supplied inputs, bounded lookup, investigation, unknown.
- **Verification:** concrete checks, reference-based judgment, unclear, unknown.
- **Breadth:** one coherent region, bounded integration, broad ownership, unknown.
- **Visual work:** none, follow a reference, invent a composition, unknown.

The initial, versioned policy maps these to candidate lanes:

| Conditions | Candidate |
| --- | --- |
| Truncated inputs, unknown dimensions, or selected probability below .75 | Keep current |
| Open investigation, uncertain semantics to investigate, broad ownership, unclear verification, or invention of visual composition | Sol |
| Bounded local implementation with supplied inputs and concrete checks or a reference review | Luna |
| Remaining bounded work with implementation decisions/lookups/integration | Terra |

Provider probabilities are **not calibrated success probabilities**. The .75
cutoff is an explicit experimental abstention policy. Claimed validated SQL and
references are not independently verified. The selector cannot establish that
image references are usable or that an apparently complete brief is correct.

Only round one makes a provider call. Later rounds receive a deterministic
`keep_current / continuation_affinity` observation, so the proposed policy does
not imply switching a model halfway through a persistent specialist thread.
Each new round still retains its task and actual execution trace for analysis.

## Persistence, cost, and failure behavior

The observation is an internal JSON key,
`calliope_specialist_runs.context._model_selection_shadow`. An atomic write-once
claim prevents duplicate inference for the same run, including concurrent
launch attempts. Completion uses its observation token, so a quick specialist
can finish before the independent selector saves its result. Other context
keys and execution state are preserved.

The snapshot contains the version, exact text-handoff hash, bounded classifier
input, selected judgments/probabilities, candidate lane/reason, selector model,
request ID, provider-reported usage/cost, and elapsed/provider time. `applied`
is always false. Actual model identity and usage come from the specialist's
existing runtime/usage receipt, not from current profile settings guessed later.
Selector charges remain in this observation, separate from the specialist's bill.

The key is explicitly stripped when constructing Hermes input. It does not
appear in the specialist result sent back to Calliope, nor as a Stage artifact,
notification, prompt hint, or Hindsight entry. Main-thread model/context and
specialist cache prefixes remain unchanged by the observation.

Provider deadline: 3 seconds, no retries. Maximum 12 pending observations per
server process. Missing credentials, malformed answers, or timeouts leave the
candidate as `keep_current`. Provider error bodies are not logged or saved.
Over-capacity observations are skipped and logged by run ID; no additional
unbounded queue is created. Database operations have short statement timeouts.

Collection is best-effort: a restart can leave `pending` receipts or no receipt.
We do not replay missing observations into a live run. This may bias coverage
toward quieter periods and must be considered when reviewing the sample. Unknown
provider cost (including an interrupted request) is not treated as zero cost.

## Read the results after real builds and data investigations

Run the report with server database access and the script from this repository
(the experiment scripts are not automatically copied into the runtime image):

```sh
PYTHONPATH=/app python specialist_model_shadow_report.py \
  --owner USER_EMAIL --days 7 --limit 200 \
  --output /app/data/experiments/model-shadow-YYYYMMDD
```

Optionally pass `--session CHAT_UUID`. It reads an explicit read-only transaction
and writes a private JSON + Markdown report, refusing an existing output
directory. Keep reports outside the repository: they contain client assignments
and result excerpts.

Each prediction is joined to actual model/provider, run status, elapsed time,
raw usage (including whatever cache/cost data Hermes reported), tool counts and
failures, later-round count, and outcome excerpt. Chat, turn and specialist
thread IDs allow inspection of the full original traces. Missing observations
and selection-window limits are visible. This is an admin CLI, not a new public
API or UI surface.

Start with several independent builds and investigations across specialists.
Review candidate Luna/Terra assignments for undisclosed ambiguity and work that
expanded beyond its brief. Follow-up rounds and tool failures are review flags,
**not automatic evidence of bad work**: follow-ups may add legitimate scope.
An execution marked complete is not proof the user accepted the result.

Do not infer savings or quality parity from this shadow experiment. It observes
only the configured model's execution, not a cheaper model's counterfactual.
The next step, if the eligible cohort looks sensible, is isolated paired replay
with shared acceptance criteria, including parent correction effort and context
cache costs. Do not let alternate outputs publish or enter agent memory.
