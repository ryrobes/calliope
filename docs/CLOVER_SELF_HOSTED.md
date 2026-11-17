# Self-hosted Clover

Clover can run next to Calliope with a locally issued service key, no account
or billing service, and no GPU. Hutch remains the common inference gateway for
Postgres, Hermes, memory, speech, and other Calliope consumers. Provider keys
stay at that gateway and its workers. This package does not change an existing
Hermes OAuth/provider selection.

The default is a **working subset**, not a GPU-free implementation of every
specialist in the hosted catalog:

| Capability | Default implementation | GPU on Calliope host |
| --- | --- | --- |
| Chat/tool calling | OpenRouter through the existing Hutch OpenAI proxy | None |
| SQL decisions, `choose`, `truth`, decision trees | CPU adapter for OpenRouter Decisions; Jev by default | None |
| SQL sentiment, classification, entailment, moderation | Typed questions through the same Decisions adapter | None |
| SQL/memory embeddings | OpenRouter Voyage 4, 1,024-dimensional float vectors | None |
| SQL/memory reranking | OpenRouter Voyage rerank-3-lite; native scores | None |
| Language detection | Lingua CPU detector, compact model tables | None |
| SQL audio transcription | OpenRouter transcription with public URL/base64/data-URI adapter | None |
| Public-web/document scraping | Bundled Clover Web (Firecrawl extractors, no API key) | None |
| Voice, live transcription, image generation | Existing Hutch OpenRouter media routes | None |
| Exact Qwen/Cua action scorer | Optional separate GPU worker or compatible remote worker | Optional |
| Forecasting, time-series, NuExtract, other specialist operations | Explicitly unconfigured until a compatible worker is supplied | Depends on worker |

The SQL transcription adapter is separate from the interactive media route.
A generic chat model does not replace NuExtract, TabPFN, Chronos,
or Cua-S1 semantics. Unconfigured `/b/.../predict` specialist requests return HTTP 503 with
`capability_unavailable`; no fabricated answer or silent model substitution.

## Standalone CPU installation

Requires Docker with Compose 2.30+ and Python 3 for configuration. Run from the
repository root. The generated configuration should stay outside Git.

```bash
export CLOVER_CONFIG_DIR="$HOME/.config/calliope/clover"
read -rs -p 'OpenRouter key: ' OPENROUTER_API_KEY
export OPENROUTER_API_KEY
python scripts/clover/configure.py --output "$CLOVER_CONFIG_DIR"

docker compose -p clover \
  -f docker/docker-compose.clover-selfhosted.yml \
  up -d --build --wait --wait-timeout 600

python scripts/clover/smoke.py --config "$CLOVER_CONFIG_DIR"
# These flags make small paid requests, including a speech/transcription round trip:
python scripts/clover/smoke.py --config "$CLOVER_CONFIG_DIR" --inference --audio
```

Only `127.0.0.1:18090` is published. Containers share `http://clover:8090`,
with `/v1` for OpenAI-compatible clients. Override `CLOVER_BIND` if needed.
Workers do not publish ports. The default needs no GPU and downloads no Hugging Face model weights. Lingua
ships its language tables inside the worker image. `--provider-key-file /private/openrouter.key` can supply the key without
putting it in shell history or command arguments.

**Models.** SQL LLM operators (`summarize`, `extract`, `clover_llm_*`,
`clover_extract_json`) use the `clover` route, set by `--sql-model` (default
`openai/gpt-6-luna`, temperature 0, reasoning off for Luna). The Calliope agent's
`calliope` route is set separately by `--chat-model`. A strong agent model no
longer makes every row-level SQL call that expensive: on one install Luna answered
the same short SQL operators at ~1/18 of `openai/gpt-6.1-sol`'s cost. An operator
step can still ask for `"model":"calliope"` when it needs the stronger model.

**Host networking.** `--service-host 127.0.0.1` writes every worker URL as
`http://127.0.0.1:<port>` instead of a Compose service name, and binds the gateway
to `127.0.0.1:8090` (override with `--bind`). Use it when the workers run with
`network_mode: host` or as plain processes on one machine. Worker ports are
decisions 8093, utilities 8094, web 8091, research 8092 and CPU embeddings 8085;
pass the analytics origin with `--analytics-worker`.

The self-hosted gateway defaults to **4,096 concurrent lane-gated requests per
instance**, replacing the old eight-request shared-GPU quota. `--lanes N` changes
that ceiling when generating configuration. Local worker capacity and upstream
provider rate limits remain separate. Existing installations can change `lanes`
in `tenants.json` and call the authenticated `POST /admin/reload-tenants`; no
gateway restart or configuration regeneration is required. If this file is an
individual bind mount, update it in place so the container sees the new contents.

Generated files:

- `hutch.json`: routing, limits, and the private admin token.
- `tenants.json`: the local service key's hash and entitlements. No billing sync.
- `providers.env`: bootstrap credentials, read as raw environment values by Compose.
- `credentials/openrouter.key`: the shared runtime OpenRouter key, rotatable in System.
- `client.env`: local `RVBBIT_CLOVER_KEY` and gateway URLs for Calliope.
- `local-keys.json`: private service/admin/computer-worker keys.
- `computer.env`: only the optional computer-worker key and HF download token.
- `catalog.sql`: non-secret SQL provenance and profile updates, readable by Postgres.
- `inference.json`: physical embedding/rerank/decision models and vector dimensions.
- `capabilities.json`: route status and optional-worker/install guidance, without secrets.

The configuration directory is private (0700) and env/key JSON files are 0600.
The mounted `credentials` directory is 0755 and `openrouter.key` is 0644 so the
workers' different unprivileged UIDs can read it; its private host parent protects
it outside those explicit mounts. Only Calliope receives a writable mount. Do not
source `providers.env` as shell code. In Calliope, System → Settings → Instance AI
checks and atomically replaces the OpenRouter key. Clover workers reread it on new requests
through `OPENROUTER_API_KEY_FILE`, without a restart or stale env fallback. Hermes
mounts the same directory read-only through `CALLIOPE_OPENROUTER_KEY_FILE`. Its
supervisor validates and registers that key in the native provider pool on first
boot. Saving a replacement synchronizes the pool and restarts Hermes, then refreshes
Calliope's model inventory. Repeating synchronization with the same key does not
restart a healthy gateway. The existing onboarding/provider-key API uses this
same path; OpenRouter cannot be disconnected separately from the instance key.
The supervisor retries missed synchronization on its existing reconciliation loop.
A saved key with a failed Hermes restart is reported as pending, not connected.
`configure.py --force` preserves this rotated file; `--provider-key-file` explicitly
replaces it. Other env credentials still require recreating their consumers. It also
replaces manual edits to generated files, so pass your chosen options again.

## One text embedding space and replaceable decision models

The installation defaults are `voyageai/voyage-4` (1,024 float dimensions),
`voyageai/rerank-3-lite`, and `typesafe/jev-1.13`. SQL, catalog search, brain
chunks, Hermes reference docs and Hindsight all use the same neutral text
embedding representation. Vectors are not decodable records: preserve source
text and the embedding profile. Equal dimensions alone do not make two models
compatible. Image and time-series specialist embeddings remain separate tasks.

Configuration options:

```bash
python scripts/clover/configure.py --output "$CLOVER_CONFIG_DIR" --force \
  --embedding-model voyageai/voyage-4 --embedding-dimensions 1024 \
  --rerank-model voyageai/rerank-3-lite --decision-model typesafe/jev-1.13
```

`scripts/clover/retrieval_eval.py` runs a small paid retrieval smoke comparison
of candidate embedding models; it is an integration check, not a quality benchmark.

The decision model can also be tested per SQL call without changing the default:

```sql
SELECT rvbbit.clover_decide_model(
  '{"message":"Please cancel tomorrow"}'::jsonb,
  '{"intent":{"type":"choice","instructions":"Classify the request",
    "criteria":{"cancel":"Cancel an appointment","book":"Book an appointment"}}}'::jsonb,
  'openai/gpt-6-luna-decisions'
);
```

The chosen model participates in SQL receipt/cache identity. Reapply the
profile and recreate Calliope/the adapter after changing its installation-wide
default. API consumers use authenticated `/v1/decisions`; Hindsight uses
`/v1/rerank`. The SQL reranker keeps original input order while the standard
rerank API returns sorted results. Scores are the provider's native scores.

For an existing installation, pause app/memory writers and embedding cron jobs,
apply `rvbbit.apply_inference_profile`, then run `scripts/clover/reembed.py`
with `WAREHOUSE_DSN` set and `--output /private/reembed-report.json`. It rebuilds
known stored text vectors and refreshes the catalog/registered vector mirrors.
Each table commits atomically and retries reuse the SQL cache. Resume writers
only after the complete rebuild succeeds. Profile changes clear the SQL embedding
cache; they do **not** automatically migrate stored vectors. Dimension changes
also require compatible destination columns/indexes before this script runs.
Custom vector tables or external consumers need their own explicit migration.
The Hermes bundle importer re-embeds a shipped corpus if its model differs from
the installation, avoiding an imported vector island.

Local Arctic remains available with `--embedding cpu` and the Compose
`--profile cpu-embeddings` option. This is an explicit alternative embedding
space, not a silent fallback when the hosted service is down.

## Hosted text judgments

The CPU package also implements these existing SQL functions through typed
[OpenRouter Decisions](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request):

| Backend | SQL functions | Result semantics |
| --- | --- | --- |
| `sentiment` | `clover_sentiment`, `clover_sentiment_score` | Positive/neutral/negative; continuous score is P(positive) − P(negative) |
| `classify` | `clover_classify`, `clover_classify_scores` | Winning supplied label and all label scores |
| `nli` | `clover_entails` | Entailment versus not-entailment; existing SQL threshold remains 0.5 |
| `toxicity` | `clover_toxic`, `clover_moderate` | Six independent moderation scores; existing toxic threshold remains 0.5 |

```sql
SELECT rvbbit.clover_classify(
  'The rooftop unit is leaking refrigerant',
  'repair, billing, installation, scheduling'
);
SELECT rvbbit.clover_entails(
  'Several invoices were paid yesterday', 'Some invoices were paid'
);
SELECT rvbbit.clover_sentiment_score('Excellent repair and quick service');
```

These are explicit model replacements, not reproductions of the old Twitter
sentiment, DeBERTa or Toxic-BERT calibration. Their contract/model identity is
`clover-text-judgments-v1/<decision-model>` and follows `--decision-model`.
Regenerating configuration and applying `catalog.sql` updates operator cache
identity as well as backend provenance. Changing only a worker environment
variable is insufficient. Route overrides can retain the original worker.

The bridge accepts the existing `{"inputs":[...]}` SQL transport and returns
ordered outputs. Each result includes actual provider model, contract and usage;
model/contract are also in `usage` so scalar wrappers retain them in SQL sub-call
receipts. Provider scores and costs are not invented on errors. Empty/malformed
input fails validation before the batch makes any paid calls; text is bounded
by the Decisions 32 KiB state limit, not silently truncated.

Classification accepts comma-separated labels or arrays. With 2–16 labels it
uses a choice question. With one or 17–200 labels it scores each independently
and normalizes the scores, marking `scoring=normalized_independent`; it never
silently drops labels. Those relative scores are not cross-label-set confidence
calibration. Moderation scores remain independent rather than summing to one.

The shared worker runs at most eight provider requests, with room for 24 queued
inputs. Excess work receives a capacity error. Specialist batches are bounded at
32 inputs and internally chunked; generated SQL backends use batches of eight.
This lets normal decisions overlap bulk text work without an unbounded queue.

**Three-way NLI stays opt-in.** Its adapter exists at
`/specialists/nli3/predict`, but both Jev and Luna Decisions classify “The
meeting is at 9am” versus “The meeting is cancelled” as neutral, while the
existing `clover_contradicts` test expects contradiction. Fresh CPU installs
therefore mark the `nli3` route unconfigured; supply a compatible three-way NLI
worker through `--routes` to enable it.

Run the paid HTTP checks and, optionally, the seven operators' existing SQL tests:

```bash
python scripts/clover/text_judgments_smoke.py --config "$CLOVER_CONFIG_DIR" \
  --psql-command 'docker exec -i calliope-postgres psql -XAt -U postgres -d rvbbit' \
  --output /tmp/clover-text-judgments.json
```

The SQL checks temporarily bypass result caches in their own connections; they
do not change operator definitions.

## Language, SQL audio, and public-web bridges

The default Compose now includes two small services: `clover-utilities` for
language/audio adapters and `clover-web` for public fetching and extraction.
Neither uses a GPU, a separate database, or host network access.

| SQL | Implementation and boundary |
| --- | --- |
| `clover_language(text)`, `clover_language_info(text)` | Lingua 2.1.1 compact mode; ISO code and native relative confidence. Confidence differs from XLM-R's old calibration. |
| `clover_transcribe(audio)` | OpenRouter `openai/gpt-transcribe`; public HTTP(S) URL, base64 audio, or base64 audio data URI. Returns text using the existing scalar wrapper. |
| `clover_web_scrape(url)`, `clover_web_markdown(url)` | Original Clover Web HTML/document extraction, packaged here; source hash, metadata, validators and per-input failures preserved. No JavaScript browser. |

```sql
SELECT rvbbit.clover_language('bonjour mes amis');
SELECT rvbbit.clover_language_info('The compressor needs repair');
SELECT rvbbit.clover_transcribe(
  'https://raw.githubusercontent.com/ggerganov/whisper.cpp/master/samples/jfk.wav');
SELECT rvbbit.clover_web_markdown('https://example.com');
```

Audio URL fetching reuses Clover Web's public-IP validation, pinned DNS,
redirect checks, concurrency and download caps. Provider credentials never go
to the source URL. Local filesystem paths are unsupported. Limits are 16 MiB
per fetched audio and 8 MB for the gateway's complete JSON request (so embedded
base64 has a smaller effective limit). WAV, MP3, FLAC, Ogg, M4A and WebM are
recognized. The SQL bridge supports transcription and optional ISO language;
translation, chunk controls and requested timestamps fail explicitly. Returned
`chunks` is empty and unknown duration/language stays null. Use a compatible
Whisper worker override if those specialist options are needed.

Language and static scraping report zero model-provider cost. Transcription
preserves reported provider cost, including fractional cents, in SQL receipts
and the Clover Postgres ledger. Missing provider cost stays unknown. A SQL
memoization hit does not create a second Clover call. Mutable-web operators
retain `cache_policy='never'`; HTTP validators remain the page-cache boundary.

`--transcription-model provider/model` changes the SQL worker and interactive
STT profile together, including the SQL cache identity. Regenerate configuration, apply `catalog.sql`,
then recreate Clover and the utilities worker. Changing only the environment
would leave the SQL model identity stale. Generated files still preserve the
Postgres ledger configuration and locally issued keys.

Run the paid audio and live SQL checks:

```bash
python scripts/clover/utilities_smoke.py --config "$CLOVER_CONFIG_DIR" \
  --psql-command 'docker exec -i calliope-postgres psql -XAt -U postgres -d rvbbit' \
  --output /tmp/clover-utilities.json
```

## Remaining specialists and Warren

[`scripts/clover/capability_options.json`](../scripts/clover/capability_options.json)
is the machine-readable installation map, also emitted with selected routes
as `capabilities.json`. Disabled routes expose a specific reason through
`/v1/capabilities`. An install candidate is not an inference-verified worker.

- **Available Warren packs:** `builtin/clover-analytics` for clustering/anomaly
  fit and score, `builtin/clover-gliner` for Clover-compatible entity spans,
  and `builtin/clover-tspulse` for four time-series operations. All run on CPU.
  The original `extract/gliner-medium-v2.1` and language detector packs remain
  independent alternatives; the Clover GLiNER pack avoids overwriting their
  operator names and preserves the existing Clover threshold/output contract.
- **Compatible workers still need packaging:** three-way NLI, Granite-Docling
  OCR/structured documents, Chronos-2 forecasts, SigLIP2 image embeddings, REBEL
  relations, and real TabPFN fit/predict/SHAP. The Warren seasonal baseline and
  TabPFN-shaped reference pack are not replacements for those contracts.
- **Qwen/Cua:** optional GPU Compose worker already packaged; target-GPU startup
  and inference remain necessary before calling that image release-ready.
- **Web research:** packaged CPU/OpenRouter service with Postgres snapshots,
  exact selected evidence, watch history and claim lineage. Enable `--research`
  and the `research` Compose profile after preparing the evidence schema below.
  It uses the same hosted model key and local Clover Web extractor; no GPU,
  external account or research SQLite volume.
- **NuExtract structured extraction:** uses the separate `clover-extract` chat
  alias, not `/b/extract` (GLiNER). It is not yet provided by this CPU package.
  `clover_extract_json` has its existing validated generalist recovery route;
  specialist-only policy requires a real NuExtract-compatible service. Do not
  point its custom chat-template request at an ordinary model unchanged.

### GLiNER and TSPulse CPU workers

Each worker is independently installable through Warren. Both use CPU-only
PyTorch wheels, two inference threads by default, an unprivileged process, and
persistent `model_cache` volumes. First startup downloads public Hugging Face
weights; subsequent starts reuse them. `/health` succeeds only after the actual
models load. Keep the services internal to the appliance Docker network.

```sql
SELECT rvbbit.deploy_catalog_capability(
  'builtin/clover-gliner', '{"docker":true,"capability":true,"gpu":false}'::jsonb,
  'Clover GLiNER CPU', 'build');
SELECT rvbbit.deploy_catalog_capability(
  'builtin/clover-tspulse', '{"docker":true,"capability":true,"gpu":false}'::jsonb,
  'Clover TSPulse CPU', 'build');
SELECT name, callable FROM rvbbit.warren_backend_status
WHERE name IN ('clover_gliner', 'clover_tspulse');
```

For a new installation, include these optional origins when generating Clover:

```bash
python scripts/clover/configure.py --output /private/clover \
  --provider-key-file /private/openrouter.key \
  --gliner-worker http://rvbbit-clover-gliner:8080 \
  --tspulse-worker http://rvbbit-clover-tspulse:8080
```

On an existing installation preserve its other routes, keys, ledger and provider
settings. Merge just these five generated routes, apply their `catalog.sql`
updates, then recreate Clover. `--force` regenerates configuration: retain all
previous flags and route overrides if using it; remove the five replaced
legacy entries from those overrides. SQL model identities change
with the workers so old cached results cannot masquerade as new inference.

Existing functions keep working:

```sql
SELECT rvbbit.clover_extract(
  'Alice at Acme in Boston; alice@example.com',
  'person,organization,location,email');
SELECT rvbbit.clover_pii('Contact Alice at alice@example.com');

WITH series AS (
  SELECT jsonb_agg(sin(i / 10.0) ORDER BY i)::text AS values
  FROM generate_series(1, 2048) i
)
SELECT rvbbit.clover_series_embed(values),
       rvbbit.clover_series_anomalies(values),
       rvbbit.clover_series_similarity(values, values)
FROM series;
```

- GLiNER uses **medium v2.1**, explicitly different from the old large model.
  Checkpoint, DeBERTa backbone/tokenizer and GLiNER version are pinned. Output
  remains an array of `text`, `label`, `start`, `end`, `score`, ordered by score.
  Default threshold is 0.3; explicit threshold/flat-NER settings are forwarded.
  This is still a short-context span extractor, not arbitrary long-document
  extraction. Long document chunking remains the caller's responsibility.
- TSPulse retains the Zoo v2 CPU algorithms and both checkpoints: anomaly main
  `2e64fcdc…` and reconstruction/register embeddings `b1216457…`.
  Embedding/similarity use the **last 512 points**. Imputation uses overlapping
  512-point windows across the whole series and preserves observed values.
  Anomaly detection needs **at least 1536 points**; its default threshold is 0.6.
  It is far slower on CPU than the other operations, so run large anomaly
  workloads as background jobs. These are time-series vectors, not Voyage text
  vectors.
- Direct SQL worker helpers are `clover_gliner_extract(text, labels)` and
  `clover_tspulse(operation, payload_json_text)`. The latter accepts
  `timeseries_anomalies`, `timeseries_impute`, `timeseries_embed`,
  `timeseries_similarity`, and `health`; use it for optional threshold/stride
  controls. Normal `clover_*` calls through the gateway retain its call ledger.
- Local CPU inference has no upstream API charge. TSPulse reports zero provider
  cost in `usage`; GLiNER's required array output has no usage field, so Clover's
  current ledger records its cost as unknown rather than fabricating a charge.
  Neither reports the hardware/hosting cost of running the worker.

Real-model smoke checks and optional migration comparison:

```bash
python scripts/clover/model_workers_smoke.py \
  --config /private/clover --output /private/model-workers-report.json
# Before cutover, --legacy records the old bridge (including its ignored
# GLiNER threshold). After cutover, --compare /private/legacy-report.json
# checks TSPulse embeddings, imputation and anomaly scores within 1e-4.
```

## Add to the Calliope appliance

Use the normal Calliope environment file for database, authentication, and other
appliance settings. The generated `client.env` supplies Clover's local key:

```bash
docker compose -p calliope \
  --env-file /path/to/calliope.env --env-file "$CLOVER_CONFIG_DIR/client.env" \
  -f docker/docker-compose.calliope.yml \
  -f docker/docker-compose.clover-selfhosted.yml \
  -f docker/docker-compose.calliope-clover.yml \
  up -d --build
```

The overlay routes Postgres, default Hermes inference, Hindsight, Calliope
memory, speech, and the optional computer chooser to local Clover. It mounts
the current registration/bootstrap scripts so the overlay also works with an
existing compatible Postgres image. Fresh installation and subsequent bootstrap
reapply the local origin **after** catalog installation. Only backends using
`RVBBIT_CLOVER_KEY` are rebased; other service URLs are preserved.

The gateway starts even with missing provider credentials, so its inventory
remains inspectable. Appliance bootstrap waits for the optional CPU embeddings
worker before its embedding probe. The managed catalog still registers other
SQL functions; registration alone does not imply their workers are available.

This overlay is not an automatic migration of a running instance's credentials,
Hermes OAuth selection, cached results, or vectors. Use an isolated deployment
first. The hosted Clover service is retired; this gateway is the only Clover.

## Hosted specialists and Hugging Face

Pass a JSON route map to `configure.py --routes`. For example, a dedicated HF
endpoint can run a contract-compatible worker:

```json
{
  "forecast": {
    "upstream_base": "https://YOUR-ENDPOINT.endpoints.huggingface.cloud",
    "upstream_path": "/predict",
    "upstream_bearer_token_env": "HF_TOKEN",
    "model_version": "chronos-2/YOUR-PINNED-REVISION",
    "timeout_ms": 120000
  }
}
```

The default adapter sends `{"inputs":[...]}` and expects `{"outputs":[...]}`
in the same order. Use an existing compatible adapter where appropriate, e.g.
`openai-embeddings` plus `upstream_params.model` for an embedding endpoint.
Generic HF task APIs are not automatically interchangeable with these contracts.
Worker credentials are explicit server-side environment references; caller
credentials are not forwarded. Generic forwarding disables redirects, and
authenticated upstream error bodies are suppressed.

## Optional Qwen/Cua worker

`services/clover/computer` packages the previously exercised Qwen3.5-4B scorer
with Cua-S1 0.2's separate text and multimodal LoRAs. Base and adapter revisions
are pinned, and loading verifies adapter tensors. This worker needs NVIDIA
CUDA; it is never part of the default CPU deployment. It ranks supplied action
candidates; the existing Calliope computer-use harness still executes actions.

On a GPU worker host, add `docker/docker-compose.clover-computer.yml`. Build,
download weights once with online mode, then start the offline worker:

```bash
clover_computer() {
  docker compose -p clover -f docker/docker-compose.clover-selfhosted.yml \
    -f docker/docker-compose.clover-computer.yml \
    --profile computer-gpu "$@"
}
clover_computer build clover-computer
clover_computer run --rm --no-deps \
  -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 clover-computer python3 models.py
clover_computer up -d clover-computer

python scripts/clover/configure.py --output "$CLOVER_CONFIG_DIR" --force \
  --routes docker/clover/computer.routes.json
clover_computer up -d clover
```

Add `--profile cpu-embeddings` to that helper when using local Arctic embeddings.
For a remote GPU machine, change the example's upstream URL and provision the
same worker bearer key on both ends; use HTTPS outside a private network.
The HF model download location is not an inference endpoint. Hosting the exact
LoRA remotely removes the GPU requirement from the Calliope machine, not from
the infrastructure serving that model. Jev remains a different, available
computer-choice option and is never mislabeled as Cua-S1. `cua.act()`, `cua.work()`,
and `choose()` default to Jev; Qwen/Cua requires `provider="cua-s1"`. The desktop
VM, Cua Driver, screenshots, accessibility, input and recording run without a GPU.
The optional scorer health check requires complete weight shards, adapters, a
worker key, and CUDA with BF16 support; it does not load a model to score an action.

The worker packaging has CPU contract/auth tests. Check GPU image startup and
real inference on your target GPU before relying on it.

## Health, receipts, and System

`GET /v1/capabilities?probe=true` requires the local service key. It returns
per-route `disabled`, `missing_credentials`, `configured`, `ready`, or
`unavailable`, plus model identity and an explanation for disabled routes.
Readiness probes now check the actual serving destination:

- Worker health must return positive JSON readiness. HTTP 200 with `ok:false`,
  unloaded weights, missing credentials, or missing declared dependencies fails.
- Direct OpenRouter routes validate the key and find the exact model and required
  output modality in the authenticated user's full model catalogue. The default
  text-only catalogue would incorrectly omit embeddings, rerank, decisions, speech,
  transcription and image models.
- Legacy Hutch routes check the remote registry. These are explicitly
  `health_scope: route_only`: the remote gateway has no worker readiness API.
  Missing remote routes fail; present routes still require SQL execution tests.
- Disabled capabilities are not probed. Missing credentials require setup.

Checks share duplicate requests within one inventory read, reject redirects,
use four-second request timeouts, and return sanitized failure categories. They
make no paid inference calls and keep `inference_verified: false`. This is
readiness evidence, not an accuracy benchmark or a reservation of provider capacity.

System distinguishes unavailable capabilities from configured intent and shows
exactly what was checked. The agent's capability catalog omits SQL operators
whose registered dependencies are disabled or missing, and temporarily omits
routes that a System probe found unavailable within the last two minutes.
Old failures expire; successful rechecks immediately restore discovery. SQL
functions, embedded tests and installable capability packs remain inspectable.
This is discovery filtering, not a new SQL authorization or execution policy.
Recorded invocation success/failure and provider costs remain separate evidence.

Calliope installations can keep Clover telemetry in the same Postgres database,
using the dedicated `clover_telemetry` schema and the Postgres ledger overlay below.
Standalone Clover defaults to its existing SQLite ledger. Choose one active sink;
there is no database pair to synchronize. Back up routing/keys and Postgres (or the
standalone SQLite volume). Model weights can be downloaded again. No external
account, billing service or webhook is involved.

## CPU analytics capability

`builtin/clover-analytics` packages clustering and tabular anomaly detection as a
Warren capability. It downloads Python wheels, not model weights, and needs no
GPU. Install on a CPU Docker node:

```sql
SELECT rvbbit.deploy_catalog_capability(
  'builtin/clover-analytics',
  '{"capability":true,"docker":true,"gpu":false}'::jsonb
);
```

The Warren process needs `RVBBIT_CLOVER_BASE_URL` and `RVBBIT_CLOVER_KEY` for
clustering. The Calliope/Clover Compose overlay supplies these. Only the local
service key reaches this worker; OpenRouter credentials remain at the gateway.
Check the job and backend probe before enabling its three Clover routes:

```bash
python scripts/clover/configure.py --output "$CLOVER_CONFIG_DIR" --force \
  --analytics-worker http://rvbbit-clover-analytics:8080
```

Include your existing model options and any remaining `--routes` overrides when
regenerating. Apply `catalog.sql` and recreate Clover. The manifest's default
name produces the worker hostname above. A separate standalone installation can
instead add `docker/docker-compose.clover-analytics.yml` and configure
`--analytics-worker http://clover-analytics:8080`. Install one of these two ways.

Existing SQL remains unchanged:

```sql
SELECT rvbbit.clover_cluster(
  '["compressor repair","refrigerant leak","invoice overdue","invoice copy"]',
  '2'
);

WITH model AS MATERIALIZED (
  SELECT rvbbit.clover_anomaly_fit(
    '[[1,1],[1.1,0.9],[0.9,1.1],[1.05,1],[0.95,0.98],[1.02,1.03],[1,0.97]]'
  ) AS artifact
)
SELECT rvbbit.clover_anomaly_score(artifact->>'blob_b64', '[[1,1],[9,9]]')
FROM model;
```

- Clustering uses the installation's `embed` backend, including its physical
  model and vector dimensions. A positive cluster count selects seeded KMeans;
  zero/empty selects HDBSCAN. Noise keeps the legacy final-bucket behavior and
  now carries `noise: true`. `criterion` remains advisory, not an extra model
  instruction. Changing from Arctic to Voyage can change memberships; numeric
  cluster IDs have no persistent business meaning.
- Anomaly detection preserves Isolation Forest, LOF and the MLP autoencoder,
  categorical encoding, and **higher means more anomalous** scores. The generic
  `rvbbit.clover_analytics(operation, payload_text)` helper exposes algorithm,
  feature-name and categorical-column options. This helper directly probes the
  Warren backend and bypasses SQL result caching; use the ordinary Clover
  functions for gateway accounting and model-aware result reuse.
- Save the returned artifact in Postgres. `blob_b64` plus its SHA is sufficient
  after restart, reinstall or another compatible CPU worker. The in-memory SHA
  cache is disposable and returns 412 on a miss without a blob. There is no
  separate model database or required model volume to synchronize.
- Model blobs retain the existing pickle wire format and **scikit-learn 1.6.1**
  contract, including `main.MLPAutoencoder`, for interoperability with the old
  anomaly/explain service. Loading checks the hash first, restricts pickle
  globals to supported pipeline classes, and rejects mismatched sklearn
  versions. Treat these as trusted model artifacts, not arbitrary Python files;
  future sklearn upgrades need an explicit compatibility test or refit.
- The worker runs one CPU job at a time, with room for three waiting requests,
  two numerical threads, and up to eight inputs per request. Default bounds are
  10,000 clustering texts, 16,000 characters per text, one million matrix cells,
  256 features, 16 MiB model artifacts and 32 MiB HTTP bodies. Gateway body limits
  may be lower. Limits fail explicitly; rows are never silently dropped.
  `ANALYTICS_THREADS`, `ANALYTICS_MAX_VALUES` and `ANALYTICS_MAX_CELLS` are worker
  settings. Larger requests may also require gateway/SQL timeout adjustments.

The generated cluster SQL cache identity includes the embedding profile. Anomaly
identity includes the analytics contract and sklearn version. Stopping/removing
the Warren worker leaves the SQL definitions and receipts intact but makes fresh
calls unavailable; reinstall restores execution. Gateway route configuration is
explicit and survives removal, so its probe reports the missing worker.

```bash
python scripts/clover/analytics_smoke.py --config "$CLOVER_CONFIG_DIR" \
  --output /tmp/analytics-check.json --saved-model /tmp/analytics-model.json \
  --psql-command 'docker exec -i calliope-postgres psql -XAt -U postgres -d rvbbit'
# After worker restart/reinstall:
python scripts/clover/analytics_smoke.py --config "$CLOVER_CONFIG_DIR" \
  --reuse-model --saved-model /tmp/analytics-model.json \
  --output /tmp/analytics-reuse.json
```

## Clover's call ledger and Postgres reporting

Use **Postgres as the sole active ledger for Calliope installations**. Hutch
queues metadata in bounded memory and commits it on a background thread; inference
never waits for database connection, writes, reconciliation queries or metrics
queries. SQLite remains the standalone default, with its existing synchronous
best-effort write behavior. Postgres mode does not open a SQLite database.

Clover dispatch is accounted for whether the worker is a local container,
OpenRouter, another Clover, or a dedicated endpoint. These are service receipts,
not a full HTTP access log: early authentication/validation failures are not all
recorded. SQL cache hits never reach Clover and remain in RVBBIT's receipts;
direct provider/worker calls also bypass this ledger.

| `clover_telemetry` relation | Contents |
| --- | --- |
| `invocations` | Logical call ID, instance, tenant, backend/model, batch size, status, latency, tokens and reference tariff |
| `upstream_attempts` | Chat/media attempts and specialist reported charges; provider IDs where available, actual cost, reconciliation status |
| `calls` | One row per invocation joined to aggregated attempts and speech/media metadata |
| `daily_usage` | UTC day/tenant/backend/model totals, known actual cost, coverage, errors and latency sums |
| `provider_price_snapshots` | Advisory model prices, distinct from settled charges |
| `speech_usage`, `media_usage` | Numeric accounting; no transcript, image, audio, or model output |
| `writers` | Per-process heartbeat and queue/write/drop status, for a future Clover settings UI |

`reference_cost_nanousd` is a tariff, not a provider charge. In `calls`,
`known_provider_cost_nanousd` sums the known subset of attempts;
`provider_cost_nanousd` is NULL until **all recorded attempts** have a known cost.
Zero is an explicitly reported zero. `provider_cost_status` is complete, pending,
partial, or unavailable. A late provider reconciliation updates the same attempt
and the views immediately; retries do not inflate invocation counts or tariffs.
Missing/dropped telemetry cannot be reconstructed by a view; also inspect writer
health before treating reporting as complete.

New specialist calls keep worker-reported charges separate from reference tariffs,
including sub-micro-dollar precision. A batch with incomplete cost reporting stays
unknown. Specialist receipts generally lack the provider IDs needed to reconcile
missing costs later. Historic SQLite specialist costs were a mixture of tariffs
and reported charges: imports label them `cost_basis='legacy_specialist_mixed'`
and do not invent settled provider costs. Chat history retains its distinct costs.

Clustering's child embedding request is metered separately. The parent keeps
`usage.already_metered=true` for its SQL receipt and does not charge those tokens
or provider cost again. Anomaly workers report zero provider cost; this does not
mean CPU infrastructure is free. Never add reference tariff and actual provider
charge together, or infer SQL-cache savings from this ledger alone.

### Enable Postgres telemetry

After generating ordinary Clover config, prepare the ledger separately. This
preserves routes, provider credentials, and existing local service keys.
Re-running `configure.py --force` also preserves the selected Postgres ledger.

```bash
python scripts/clover/ledger.py prepare --config "$CLOVER_CONFIG_DIR" \
  --instance-id my-calliope --host postgres --database rvbbit

# Use your existing appliance Compose files and database administrator here.
# The private SQL contains a generated password; do not log it or commit it.
docker compose exec -T postgres psql -X -U postgres -d rvbbit \
  -v ON_ERROR_STOP=1 --single-transaction < "$CLOVER_CONFIG_DIR/ledger-bootstrap.sql"
```

This creates a dedicated login `clover_ledger` with read/write access only to
`clover_telemetry`. The administrator owns and installs the schema; runtime
connections never execute DDL.
Clover receives its DSN from private `ledger.env`, not the administrator DSN.
The worker has no privileges on application tables. On RVBBIT databases it also
receives read access to the acceleration registry's `table_oid` and
`acceleration_enabled` columns, which current relation hooks consult even in
plain heap mode; it receives no other registry columns. To grant reporting access
later, grant a chosen reporting role USAGE on this schema and SELECT on its
relations; do not share the writer credentials with a browser.

Layer `docker/docker-compose.clover-ledger.yml` after the existing Clover/Calliope
Compose files, and recreate **only** `clover` (`up -d --no-deps clover`). It adds
the private env file and removes the SQLite volume mount. Other workers do not
receive the ledger credentials. Existing installations must explicitly apply the
bootstrap SQL first; merely setting an environment variable does not grant schema
access. The writer role uses `rvbbit.force_heap_scan=on` so bookkeeping uses
ordinary PostgreSQL without involving RVBBIT acceleration or catalog permissions.
This setting applies only to the telemetry writer, not application queries.
External databases can use an edited DSN with TLS and a trusted CA.

Config in `hutch.json`:

```json
"ledger_postgres": {
  "dsn_env": "CLOVER_LEDGER_DSN",
  "instance_id": "my-calliope",
  "queue_capacity": 4096
}
```

Keep the instance ID stable across restarts, imports and gateway replicas.
Each process gets a separate writer ID. Removing `ledger_postgres` explicitly
returns to standalone SQLite mode; it does not automatically export Postgres back.

### Import existing history once

Stop the old SQLite-writing gateway for the final cutover snapshot, enable the
Postgres writer, then import the old file. The importer takes a SQLite backup
(including WAL) before reading, so it can also be used for a baseline import while
the old gateway is running. Repeat against the final snapshot with the same
instance/source IDs. Final provider costs in Postgres are never replaced by an
older pending record.

Use Python with `psycopg[binary]` installed and `CLOVER_LEDGER_DSN` in its private
environment (same writer credentials as Clover):

```bash
python scripts/clover/ledger.py import-sqlite \
  --sqlite /private/history/hutch_meter.sqlite \
  --instance-id my-calliope --source-id legacy-sqlite
```

Imports are idempotent and tolerate absent optional media tables. The old file
can remain an inactive migration archive; no exporter tails it after cutover.
The importer reads only known ledger tables and never contacts a provider.

### Operational behavior and reporting

The default queue holds **4,096 events**, including the batch being retried,
with a 16 KiB metadata limit per event. When full, new telemetry is dropped and
counted. A process crash can lose queued events; a prolonged DB outage can exceed
the queue. This is an explicit telemetry tradeoff, not an outage guarantee.
SIGTERM/SIGINT drains serving requests and then gives the ledger up to six seconds
to flush. Docker's overall stop grace period is 30 seconds; longer in-flight
inference still needs an appropriately longer deployment grace period.

Admin-authenticated `GET /admin/ledger` provides mode, connection status, queue
capacity/depth, successful writes, dropped events, failures, last write and last
snapshot time. `/metrics` exposes `hutch_ledger_*` counters/gauges. The five-second
background snapshot supplies reconciliation and metrics reads. `writers.seen_at`
provides a heartbeat in Postgres; an old row means that writer stopped reporting,
not that the DB can still observe an offline process. Both status surfaces omit
DSNs and credentials. Prompts, responses, datasets and model blobs are excluded.

```sql
SELECT day, backend, model_version, calls, failed_calls,
       known_provider_cost_nanousd / 1e9 AS known_provider_usd,
       calls_with_complete_cost, pending_attempts, cost_basis
FROM clover_telemetry.daily_usage
ORDER BY day DESC, backend;

SELECT ts, backend, status, duration_ms, attempt_count,
       provider_cost_nanousd / 1e9 AS settled_provider_usd,
       provider_cost_status
FROM clover_telemetry.calls
ORDER BY ts DESC LIMIT 100;

SELECT instance_id, writer_id, seen_at, status
FROM clover_telemetry.writers ORDER BY seen_at DESC;
```

Validation: `scripts/clover/test_ledger.py` exercises a real isolated Postgres,
mocked inference during an outage, queue saturation/recovery, SIGTERM flush,
restart reconciliation, duplicate history import, retry-safe totals, metadata
joins and the restricted writer role. It requires an expendable database; never
run its schema-resetting fixtures against an application database.

## Web Research with Postgres evidence

Clover Research is a CPU service, with OpenRouter/Exa discovery and hosted
synthesis. It uses the existing SQL functions and returns the same cited research
objects. `deep` follows evidence to close gaps; `watch` conditionally fetches
stable URLs and reuses synthesis only when the watch's exact evidence, question,
model and evidence budget match. Every call still reaches the worker—SQL cache
policy remains `never`.

For a fresh configuration, include `--research` in `scripts/clover/configure.py`
and optionally `--research-model MODEL`. The default is
`openai/gpt-6-luna` (previously `google/gemma-4-31b-it`, which is only slightly
cheaper and much less capable); replacements must support OpenRouter server-side
search and the structured synthesis contract. For existing configurations, retain the
other model/worker/route arguments when regenerating with `--force`.

Prepare a dedicated, restricted Postgres role and apply the generated bootstrap
as the database administrator:

```bash
python3 scripts/clover/research.py --config "$CLOVER_CONFIG_DIR" \
  --host postgres --database rvbbit
# Apply $CLOVER_CONFIG_DIR/research-bootstrap.sql to that database as its admin.
# Apply the generated catalog.sql through the existing inference setup path.
docker compose -f docker/docker-compose.clover-selfhosted.yml \
  --profile research up -d --build
```

The Compose network must reach `postgres` (use the Calliope network or an
appropriate hostname). This does not create a second database. The private
`research.env` contains only the evidence writer's DSN; do not use an admin DSN.
Runtime does no DDL. `research.py` preserves its password on repeat preparation.
The container exposes no host port, runs read-only/non-root, and `/health`
checks Postgres access. Hutch supplies the authenticated tenant scope; a missing
scope is rejected instead of sharing an anonymous cache. Internal service
network access is trusted, as with the other Clover workers.

```sql
SELECT rvbbit.clover_web_research(
  'What does PostgreSQL documentation say about row-level security?');

SELECT rvbbit.clover_web_research_deep(
  'Compare PostgreSQL row-level security policies and table privileges using official documentation.');

SELECT rvbbit.clover_web_watch(
  'Summarize the row-security rules on this page.',
  '["https://www.postgresql.org/docs/current/ddl-rowsecurity.html"]');
```

The `clover_research` schema holds `research_runs`, `web_snapshots`,
`research_run_sources`, `research_claims`, and `research_watches`. Runs store the
full JSONB receipt; source links store exactly the selected evidence used for
that run, not just the latest URL body. Claims retain their source and snapshot
IDs. All of these, including the final watch pointer, commit together. Include
this schema in normal Calliope Postgres backups.

Administrators can report on research directly:

```sql
SELECT id, mode, question, created_at,
       result_json->'usage' AS usage,
       result_json->'memory' AS evidence_status
FROM clover_research.research_runs
ORDER BY created_at DESC;
```

Aggregate provider cost (discovery, synthesis, follow-up and repair when used)
is included in each successful receipt and Clover's Postgres call ledger.
Missing provider cost remains unknown; an unchanged seed-only watch explicitly
reports zero model tokens and zero model cost. Source refresh failure may reuse
an explicitly marked stale snapshot. A failed final persistence operation is
reported as `memory.stored=false`, never as a durable watch update.

The service retains the existing bounds: public HTTP(S), at most six sources by
default, 10k selected characters per source, 48k total selected evidence and
250k stored characters per source version. It does not add browser sessions,
JavaScript execution or authenticated-page research. Postgres backups now own
new evidence history. This package does not silently import a hosted Clover
account's old SQLite history or reuse its tenant identity.

## SQL wrapper availability and embedded tests

Migration `0473_operator_availability` makes `warren_backend_status.callable`
respect disabled Clover routes. `configure.py` now writes non-secret route facts
into `rvbbit.backends.transport_opts.clover_route` for both enabled and disabled
routes, applied through the existing `catalog.sql` registration path. Regenerate
and apply that file after changing gateway routing. Backend updates invalidate
existing specialist caches through the existing generation trigger.

SQL wrappers and their embedded tests remain installed. An unavailable backend
is no longer presented as callable just because it is external to Warren.
`rvbbit.operator_availability` projects declared specialist/LLM/embed dependencies
from operator steps; opaque code and implicit default providers are not inferred.
Calliope's capability search includes these facts as `sql_availability`.
System distinguishes hosted provider adapters, configured workers, legacy remote
Clover, and unconfigured routes. The obsolete NuExtract `clover-extract` chat
backend is marked unconfigured when that chat route is absent.

System → Checks runs the existing SQL test harness and shares its results ledger
with DataRabbit. Its default batch omits legacy/unconfigured dependencies, with
explicit single-operator runs available for legacy services. It exposes all
installed operators, including those without tests, rather than maintaining a
second list of Clover-specific test definitions. See the System workspace
operations guide for persistence, interruption and cache behavior.
