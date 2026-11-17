//! Hutch service configuration — one YAML file describing the listener, the
//! upstream (mock or a real zoo), and the backend menu. Tenants live in a
//! SEPARATE file (tenants.yaml) so key rotation never touches service config.

use serde::Deserialize;
use serde_json::Value;
use std::collections::BTreeMap;

fn default_bind() -> String {
    "0.0.0.0:8090".into()
}
fn default_tenants_file() -> String {
    "tenants.yaml".into()
}
fn default_meter_db() -> String {
    "hutch_meter.sqlite".into()
}
fn default_max_body() -> usize {
    2 * 1024 * 1024
}
fn default_lane_grace_ms() -> u64 {
    250
}
fn default_provider_cost_reconcile_secs() -> u64 {
    15
}
fn default_provider_price_refresh_secs() -> u64 {
    6 * 60 * 60
}
fn default_timeout_ms() -> u64 {
    30_000
}

fn default_upstream_retries() -> u32 {
    2
}

fn default_rerank_batch() -> usize {
    100
}

#[derive(Debug, Clone, Deserialize)]
pub struct HutchConfig {
    #[serde(default = "default_bind")]
    pub bind: String,
    #[serde(default = "default_tenants_file")]
    pub tenants_file: String,
    #[serde(default = "default_meter_db")]
    pub meter_db: String,
    /// Postgres replaces SQLite when configured. Credentials stay in the environment.
    #[serde(default)]
    pub ledger_postgres: Option<crate::ledger::LedgerConfig>,
    /// Bearer token required for /metrics and /admin/*. None = open (dev only).
    #[serde(default)]
    pub admin_token: Option<String>,
    #[serde(default = "default_max_body")]
    pub max_body_bytes: usize,
    /// How long a request may wait for a free lane before 429. Small on
    /// purpose: the client (pg_rvbbit) already throttles via max_concurrent,
    /// so this only smooths bursts, it is not a queue.
    #[serde(default = "default_lane_grace_ms")]
    pub lane_grace_ms: u64,
    /// Generic background cadence for provider-attempt cost reconciliation.
    #[serde(default = "default_provider_cost_reconcile_secs")]
    pub provider_cost_reconcile_secs: u64,
    /// Advisory provider catalog refresh; never blocks Hutch startup.
    #[serde(default = "default_provider_price_refresh_secs")]
    pub provider_price_refresh_secs: u64,
    pub upstream: Upstream,
    pub backends: Vec<BackendCfg>,
    /// Hosted LLMs on the OpenAI-compatible surface. Empty = specialist-only.
    #[serde(default)]
    pub llms: Vec<LlmCfg>,
    /// Hosted speech routes, separate from the chat model catalog.
    #[serde(default)]
    pub speech: Vec<crate::speech::SpeechCfg>,
    #[serde(default)]
    pub media: Vec<crate::media::MediaCfg>,
    /// Tenants come from tenants.yaml only. The old Polar billing section is
    /// still parsed so a config that has one is refused instead of silently
    /// rejecting its subscribers' keys.
    #[serde(default)]
    pub polar: Option<serde_yaml::Value>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "mode", rename_all = "lowercase")]
pub enum Upstream {
    /// Deterministic canned outputs — proves the whole middleware chain
    /// (auth → entitlement → lanes → meter) with no GPU anywhere.
    Mock,
    /// Forward {"inputs": [...]} to base_url + backend.upstream_path and
    /// expect {"outputs": [...]} back (the zoo, or anything predict-shaped).
    Proxy { base_url: String },
}

/// How the predict contract translates to the upstream's wire shape.
/// The hutch adapts to the zoo — never the reverse.
#[derive(Debug, Clone, Copy, PartialEq, Default, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Adapter {
    /// Passthrough: upstream already speaks {"inputs":[...]}→{"outputs":[...]}.
    #[default]
    Predict,
    /// OpenAI-shaped embeddings ({"input":[texts]} → data[].embedding),
    /// emitted as bare float arrays (what pg_rvbbit's embed path parses).
    OpenaiEmbeddings,
    /// Zoo /sentiment: {"texts":[...]} → parallel {scores[],labels[]} →
    /// per-item {"score","label"}.
    ZooSentiment,
    /// Zoo /rerank: inputs are {"query","text"} pairs, grouped by query
    /// (one upstream call per distinct query) → per-position score.
    ZooRerank,
    /// OpenRouter/Cohere-shaped rerank: indexed relevance scores, unchanged.
    HostedRerank,
    /// OpenRouter Extractions API (typed-field models such as Cloudflare
    /// Clairic): each input is one request ({state, fields, reference_date?,
    /// images?, options?}); the output is the response's typed answers with
    /// their confidences, null probabilities and spans. Inputs are sent
    /// concurrently (`parallel_requests`).
    HostedExtractions,
    /// Zoo /nli: {"premise","hypothesis"} pairs → {"label", "scores"}.
    ZooNli,
    /// Zoo /classify (single-text zero-shot): {"text","labels"} →
    /// {"label","score","scores"}. One upstream call per input.
    ZooClassify,
    /// Zoo /toxicity: texts → {"toxic","score","scores"}.
    ZooToxicity,
    /// Zoo /language: texts → {"language","confidence"}.
    ZooLanguage,
    /// Zoo /extract (GLiNER): {"text","labels"} → entity array. One
    /// upstream call per input (labels may differ per row).
    ZooExtract,
    /// Generic structured route: each input object IS the upstream request
    /// body (after upstream_params merge + JSON-string coercion for array/
    /// object fields that arrive as SQL text), and the WHOLE upstream
    /// response object is the output. One upstream call per input. Covers
    /// /document/ocr, /transcribe, /forecast, /cluster, /tabular/fit,
    /// /tabular/predict, /tabular/explain, /anomaly/fit, /anomaly/score —
    /// anything single-shot request/response.
    ZooJson,
    /// Zoo /relations (REBEL): {"texts":[...]} → results[] aligned per text.
    ZooRelations,
    /// Zoo /v1/image_embeddings (SigLIP 2 dual-tower): inputs are bare
    /// strings (image URL / data URI / b64 / plain text — the zoo
    /// classifies which tower) → bare float arrays, same convention as
    /// OpenaiEmbeddings so embedding parse paths work unchanged.
    ZooImageEmbeddings,
}

fn enabled() -> bool {
    true
}

#[derive(Debug, Clone, Deserialize)]
pub struct BackendCfg {
    /// Route name: POST /b/{name}/predict
    pub name: String,
    /// Entitlement group a tenant must hold (e.g. "clover", "gemma").
    pub entitlement: String,
    /// Explicitly absent capabilities remain discoverable without sending requests.
    #[serde(default = "enabled")]
    pub enabled: bool,
    #[serde(default)]
    pub unavailable_reason: Option<String>,
    /// Name of a server-owned credential; never forward the caller's key.
    #[serde(default)]
    pub upstream_bearer_token_env: Option<String>,
    /// Optional non-inference worker readiness endpoint for capability probes.
    #[serde(default)]
    pub health_url: Option<String>,
    /// Path on the upstream in proxy mode (e.g. "/sentiment").
    #[serde(default)]
    pub upstream_path: Option<String>,
    /// Optional base URL for this backend. When absent, the shared
    /// `upstream.base_url` remains the default. This keeps the model zoo
    /// configuration terse while allowing CPU sidecars (for example a web
    /// fetch/extract service) to stay isolated behind the same Hutch door.
    #[serde(default)]
    pub upstream_base: Option<String>,
    #[serde(default)]
    pub adapter: Adapter,
    /// Extra fields merged into the upstream request body (e.g. NLI model
    /// variant, extract threshold). Server-side tunables — bump
    /// model_version when these change: they move verdicts.
    #[serde(default)]
    pub upstream_params: Option<Value>,
    /// Echoed in every response (header + body) — the verdict-stability
    /// breadcrumb that lands in client receipts.
    pub model_version: String,
    /// Would-have-been à la carte price per input, micro-USD. Metering
    /// records it so receipts can show utilization value under flat subs.
    #[serde(default)]
    pub unit_microusd: i64,
    /// Skip the per-tenant lane semaphore for this backend. For cheap
    /// batched encoders lane-gating adds friction but controls no real
    /// cost — lanes price generation (LLM / heavy media), not
    /// classification. Metering still records every call.
    #[serde(default)]
    pub unlaned: bool,
    /// Forward a stable, one-way tenant scope to trusted local sidecars that
    /// maintain durable per-customer state. The raw tenant ID and bearer key
    /// are never forwarded.
    #[serde(default)]
    pub forward_tenant_scope: bool,
    #[serde(default = "default_timeout_ms")]
    pub timeout_ms: u64,
    /// Extra attempts when the upstream certainly did not process the request:
    /// a rate limit (429), 503, or a connection that never got through. Hosted
    /// providers rate limit shared projects; a short backoff usually clears it.
    #[serde(default = "default_upstream_retries")]
    pub upstream_retries: u32,
    /// Hosted rerank: the most documents sent upstream in one call. Larger
    /// requests are split and scored concurrently; a cross-encoder scores each
    /// pair on its own, so the split does not change the ranking. Smaller calls
    /// fit under a shared tokens-per-minute limit far more often.
    #[serde(default = "default_rerank_batch")]
    pub rerank_batch: usize,
    /// Hosted extractions: upstream requests in flight at once for one batch.
    #[serde(default = "default_parallel_requests")]
    pub parallel_requests: usize,
    /// Mock mode: the output emitted per input (default {"mock": true}).
    #[serde(default)]
    pub mock_output: Option<Value>,
    /// Mock mode: per-request artificial latency — lets lane saturation be
    /// exercised end-to-end without real models.
    #[serde(default)]
    pub mock_delay_ms: Option<u64>,
}

fn default_parallel_requests() -> usize {
    8
}

fn default_llm_upstream() -> String {
    "http://localhost:8000".into()
}
fn default_llm_timeout_ms() -> u64 {
    120_000
}
fn default_llm_overflow_concurrency() -> usize {
    2
}
fn default_llm_overflow_tokenize_min_body_bytes() -> usize {
    32 * 1024
}

/// Optional second upstream for requests that would crowd or exceed the
/// primary model's useful context window. The public model id stays stable;
/// only Hutch knows which serving route handled the request.
#[derive(Debug, Clone, Deserialize)]
pub struct LlmOverflowCfg {
    /// Route when exact prompt tokens plus the requested output budget exceed
    /// this value. Keep this below the primary server's hard model limit so a
    /// normal completion still has headroom.
    pub context_threshold_tokens: u64,
    /// Primary-upstream path used for exact chat-template tokenization. When
    /// absent, Hutch uses a conservative body-size estimate and still catches
    /// an explicit primary context-length rejection.
    #[serde(default)]
    pub tokenize_path: Option<String>,
    /// Do not pay for a tokenization round trip on ordinary short requests.
    #[serde(default = "default_llm_overflow_tokenize_min_body_bytes")]
    pub tokenize_min_body_bytes: usize,
    /// Per-tenant overflow concurrency. This is deliberately independent from
    /// the normal tenant lane pool so a slow hosted request cannot consume a
    /// local GPU slot.
    #[serde(default = "default_llm_overflow_concurrency")]
    pub max_concurrent: usize,
    /// Retry the overflow upstream when the primary cannot be reached or
    /// returns a server-side availability failure. This is opt-in because it
    /// can turn a free/local request into a metered hosted request.
    #[serde(default)]
    pub fallback_on_unavailable: bool,
    pub upstream_base: String,
    #[serde(default)]
    pub provider: Option<String>,
    #[serde(default)]
    pub upstream_bearer_token_env: Option<String>,
    pub upstream_model: String,
    pub model_version: String,
    #[serde(default)]
    pub request_overrides: BTreeMap<String, Value>,
    #[serde(default)]
    pub request_defaults: BTreeMap<String, Value>,
    #[serde(default)]
    pub prompt_microusd_per_1k: i64,
    #[serde(default)]
    pub completion_microusd_per_1k: i64,
    #[serde(default = "default_llm_timeout_ms")]
    pub timeout_ms: u64,
}

/// A hosted LLM behind the OpenAI-compatible surface (/v1/chat/completions
/// with model-name routing). One entry per canonical public service id; the same
/// surface serves pg_rvbbit's openai_chat transport, agent()/flow steps,
/// and raw OpenAI SDKs — no per-consumer plumbing.
#[derive(Debug, Clone, Deserialize)]
pub struct LlmCfg {
    /// Canonical public model id — what new subscribers put in the request's
    /// `model`. This is a stable Clover service contract, not the upstream
    /// implementation model.
    pub name: String,
    /// Previously advertised public ids accepted for backwards compatibility.
    /// Aliases route to this entry but are intentionally omitted from
    /// `/v1/models`, so new clients only discover the canonical name.
    #[serde(default)]
    pub aliases: Vec<String>,
    pub entitlement: String,
    #[serde(default = "default_llm_upstream")]
    pub upstream_base: String,
    /// Explicit provider accounting adapter. When absent, Hutch recognizes
    /// known provider hosts for backwards compatibility and otherwise uses a
    /// generic OpenAI-compatible adapter.
    #[serde(default)]
    pub provider: Option<String>,
    /// Optional environment variable containing the upstream API token.
    /// When set, Hutch adds `Authorization: Bearer <token>` to the upstream
    /// request.  The variable name is configuration; the credential itself
    /// never belongs in YAML (useful for OpenRouter and other hosted,
    /// OpenAI-compatible backends).
    #[serde(default)]
    pub upstream_bearer_token_env: Option<String>,
    /// Served model name at the upstream (e.g. the vLLM --model id).
    pub upstream_model: String,
    pub model_version: String,
    /// Server-owned fields recursively merged into every upstream request.
    /// These win over subscriber input so privacy policy and hosted-model
    /// quirks stay behind the stable public model alias without replacing
    /// unrelated nested preferences. `model` is reserved and always comes
    /// from upstream_model.
    #[serde(default)]
    pub request_overrides: BTreeMap<String, Value>,
    /// Fields recursively supplied only when the subscriber did not send
    /// them. This lets deterministic semantic operators get stable hosted-
    /// model defaults while callers can still opt into different values.
    #[serde(default)]
    pub request_defaults: BTreeMap<String, Value>,
    /// Auto-enable caching for resolved Claude/OpenRouter routes. False
    /// disables Hutch's default; explicit caller cache controls are preserved.
    #[serde(default)]
    pub prompt_caching: Option<bool>,
    /// Would-be à la carte rates for receipts, micro-USD per 1k tokens.
    #[serde(default)]
    pub prompt_microusd_per_1k: i64,
    #[serde(default)]
    pub completion_microusd_per_1k: i64,
    #[serde(default = "default_llm_timeout_ms")]
    pub timeout_ms: u64,
    /// Streaming body limits; timeout_ms remains the header and nonstream deadline.
    #[serde(default)]
    pub stream_timeouts: crate::llm_stream::StreamTimeouts,
    /// Optional long-context route. This is an overflow for the same canonical
    /// service, not a separately advertised model.
    #[serde(default)]
    pub overflow: Option<LlmOverflowCfg>,
}

fn validate_endpoint(value: &str) -> Result<(), String> {
    let url = reqwest::Url::parse(value).map_err(|_| "Invalid backend URL")?;
    if !["https", "http"].contains(&url.scheme())
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(
            "Backend URLs need HTTP(S), without embedded credentials, query or fragment".into(),
        );
    }
    Ok(())
}

impl HutchConfig {
    pub fn load(path: &str) -> Result<Self, String> {
        let raw = std::fs::read_to_string(path)
            .map_err(|e| format!("cannot read config '{path}': {e}"))?;
        let cfg: HutchConfig =
            serde_yaml::from_str(&raw).map_err(|e| format!("bad config '{path}': {e}"))?;
        if cfg.polar.is_some() {
            return Err(format!(
                "config '{path}' has a `polar:` billing section, which this gateway no longer \
                 supports; manage keys in the tenants file (an external billing service can \
                 rewrite it and POST /admin/reload-tenants)"
            ));
        }
        if cfg.backends.is_empty() && cfg.llms.is_empty() {
            return Err("config has no backends or chat routes".into());
        }
        if let Upstream::Proxy { .. } = cfg.upstream {
            for b in &cfg.backends {
                if b.enabled && b.upstream_path.is_none() {
                    return Err(format!(
                        "backend '{}' has no upstream_path but upstream.mode is proxy",
                        b.name
                    ));
                }
            }
        }
        let mut backend_names = std::collections::HashSet::new();
        for b in &cfg.backends {
            if !backend_names.insert(&b.name) || b.name.trim().is_empty() || b.timeout_ms == 0 {
                return Err("Backend names must be unique/nonempty and timeouts positive".into());
            }
            if let Some(name) = &b.upstream_bearer_token_env {
                if name.is_empty() || !name.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_')
                {
                    return Err(format!(
                        "Invalid credential environment name for '{}'",
                        b.name
                    ));
                }
            }
            if let Some(url) = &b.health_url {
                validate_endpoint(url)?;
            }
            if b.enabled {
                if let Upstream::Proxy { base_url } = &cfg.upstream {
                    let base = b.upstream_base.as_ref().unwrap_or(base_url);
                    validate_endpoint(base)?;
                    if !b
                        .upstream_path
                        .as_ref()
                        .is_some_and(|p| p.starts_with('/') && !p.starts_with("//"))
                    {
                        return Err(format!("Backend '{}' needs an absolute URL path", b.name));
                    }
                }
            }
        }
        crate::speech::validate_configs(&cfg.speech)?;
        crate::media::validate_configs(&cfg.media)?;
        let mut llm_routes: BTreeMap<&str, &str> = BTreeMap::new();
        for llm in &cfg.llms {
            if llm.timeout_ms == 0
                || llm.stream_timeouts.idle_ms == 0
                || llm.stream_timeouts.total_ms == 0
            {
                return Err(format!("llm '{}' timeouts must be positive", llm.name));
            }
            if llm
                .provider
                .as_deref()
                .is_some_and(|provider| provider.is_empty() || provider.trim() != provider)
            {
                return Err(format!(
                    "llm '{}' has a blank or whitespace-padded provider id",
                    llm.name
                ));
            }
            if llm.request_overrides.contains_key("model")
                || llm.request_defaults.contains_key("model")
            {
                return Err(format!(
                    "llm '{}' request policy cannot replace the routed model",
                    llm.name
                ));
            }
            if let Some(overflow) = &llm.overflow {
                if overflow.context_threshold_tokens == 0 {
                    return Err(format!(
                        "llm '{}' overflow context_threshold_tokens must be positive",
                        llm.name
                    ));
                }
                if overflow.max_concurrent == 0 {
                    return Err(format!(
                        "llm '{}' overflow max_concurrent must be positive",
                        llm.name
                    ));
                }
                if overflow.tokenize_min_body_bytes == 0 {
                    return Err(format!(
                        "llm '{}' overflow tokenize_min_body_bytes must be positive",
                        llm.name
                    ));
                }
                if overflow.upstream_base.trim().is_empty()
                    || overflow.upstream_model.trim().is_empty()
                    || overflow.model_version.trim().is_empty()
                {
                    return Err(format!(
                        "llm '{}' overflow upstream_base, upstream_model, and model_version must be non-blank",
                        llm.name
                    ));
                }
                if overflow
                    .provider
                    .as_deref()
                    .is_some_and(|provider| provider.is_empty() || provider.trim() != provider)
                {
                    return Err(format!(
                        "llm '{}' overflow has a blank or whitespace-padded provider id",
                        llm.name
                    ));
                }
                if overflow.request_overrides.contains_key("model")
                    || overflow.request_defaults.contains_key("model")
                {
                    return Err(format!(
                        "llm '{}' overflow request policy cannot replace the routed model",
                        llm.name
                    ));
                }
                if overflow.tokenize_path.as_deref().is_some_and(|path| {
                    path.trim() != path || path.is_empty() || !path.starts_with('/')
                }) {
                    return Err(format!(
                        "llm '{}' overflow tokenize_path must be an absolute HTTP path",
                        llm.name
                    ));
                }
            }
            for route in
                std::iter::once(llm.name.as_str()).chain(llm.aliases.iter().map(String::as_str))
            {
                if route.is_empty() || route.trim() != route {
                    return Err(format!(
                        "llm '{}' has a blank or whitespace-padded public model route",
                        llm.name
                    ));
                }
                if let Some(owner) = llm_routes.insert(route, llm.name.as_str()) {
                    return Err(format!(
                        "public model route '{}' is claimed by both '{}' and '{}'",
                        route, owner, llm.name
                    ));
                }
            }
        }
        Ok(cfg)
    }

    pub fn backend(&self, name: &str) -> Option<&BackendCfg> {
        self.backends.iter().find(|b| b.name == name)
    }

    pub fn llm(&self, name: &str) -> Option<&LlmCfg> {
        self.llms
            .iter()
            .find(|l| l.name == name || l.aliases.iter().any(|alias| alias == name))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_config_with_a_polar_billing_section_is_refused() {
        let base = include_str!("../hutch.clover.example.yaml");
        let dir = std::env::temp_dir().join(format!("hutch-polar-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let plain = dir.join("plain.yaml");
        let with_polar = dir.join("polar.yaml");
        std::fs::write(&plain, base).unwrap();
        std::fs::write(&with_polar, format!("{base}\npolar:\n  organization_id: org\n")).unwrap();

        assert!(HutchConfig::load(plain.to_str().unwrap()).is_ok());
        let err = HutchConfig::load(with_polar.to_str().unwrap()).unwrap_err();
        assert!(err.contains("`polar:` billing section"), "{err}");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn production_clover_example_covers_heavy_zoo_routes() {
        let cfg: HutchConfig = serde_yaml::from_str(include_str!("../hutch.clover.example.yaml"))
            .expect("production-shaped Clover example should parse");

        assert_eq!(cfg.backends.len(), 28);
        assert_eq!(cfg.llms.len(), 2);

        let cluster = cfg.backend("cluster").expect("cluster backend");
        assert_eq!(cluster.adapter, Adapter::ZooJson);
        assert_eq!(cluster.upstream_path.as_deref(), Some("/cluster"));
        assert_eq!(cluster.timeout_ms, 300_000);

        let explain = cfg
            .backend("tabular_explain")
            .expect("tabular_explain backend");
        assert_eq!(explain.adapter, Adapter::ZooJson);
        assert_eq!(explain.upstream_path.as_deref(), Some("/tabular/explain"));
        assert_eq!(explain.timeout_ms, 600_000);

        let document_parse = cfg
            .backend("document_parse")
            .expect("structured document backend");
        assert_eq!(
            document_parse.upstream_path.as_deref(),
            Some("/document/parse")
        );
        assert_eq!(
            document_parse.model_version,
            "clover-v2/granite-docling-258m-structured"
        );

        let forecast = cfg.backend("forecast").expect("forecast backend");
        assert_eq!(forecast.upstream_path.as_deref(), Some("/forecast"));
        assert_eq!(forecast.model_version, "clover-v2/chronos-2");

        let anomalies = cfg
            .backend("timeseries_anomalies")
            .expect("TSPulse anomaly backend");
        assert_eq!(
            anomalies.upstream_path.as_deref(),
            Some("/timeseries/anomalies")
        );
        assert!(anomalies.unlaned);

        let research = cfg.backend("web_research").expect("research backend");
        assert!(!research.unlaned);
        assert!(research.forward_tenant_scope);
        assert_eq!(
            research.upstream_base.as_deref(),
            Some("http://127.0.0.1:8092")
        );
        assert_eq!(research.upstream_path.as_deref(), Some("/v1/research"));
        assert_eq!(research.timeout_ms, 180_000);

        let clover = cfg.llm("clover").expect("canonical Clover LLM");
        assert_eq!(clover.name, "clover");
        assert_eq!(clover.upstream_model, "nvidia/Gemma-4-31B-IT-NVFP4");
        assert!(clover.upstream_bearer_token_env.is_none());
        assert_eq!(clover.prompt_microusd_per_1k, 100);
        assert_eq!(clover.completion_microusd_per_1k, 200);
        assert_eq!(
            cfg.llm("gemma4").expect("legacy public alias").name,
            "clover"
        );

        let calliope = cfg.llm("calliope").expect("Calliope LLM");
        assert_eq!(calliope.name, "calliope");
        assert_eq!(calliope.provider.as_deref(), Some("openrouter"));
        assert_eq!(calliope.upstream_base, "https://openrouter.ai/api");
        assert_eq!(
            calliope.upstream_bearer_token_env.as_deref(),
            Some("OPENROUTER_API_KEY")
        );
        assert_eq!(calliope.upstream_model, "openai/gpt-6-luna");
        assert_eq!(calliope.model_version, "openrouter/openai/gpt-6-luna");
        assert!(calliope.request_defaults.is_empty());
        assert_eq!(calliope.request_overrides["provider"]["zdr"], true);
        assert_eq!(
            calliope.request_overrides["provider"]["data_collection"],
            "deny"
        );
        assert_eq!(calliope.prompt_microusd_per_1k, 1000);
        assert_eq!(calliope.completion_microusd_per_1k, 6000);
        assert_eq!(calliope.timeout_ms, 180_000);

        let embed = cfg.backend("embed").expect("OpenAI embedding alias");
        assert_eq!(embed.adapter, Adapter::OpenaiEmbeddings);
        assert_eq!(embed.entitlement, "clover");
        assert_eq!(embed.upstream_path.as_deref(), Some("/v1/embeddings"));
    }

    #[test]
    fn hosted_llm_credentials_are_referenced_by_environment_name() {
        let cfg: HutchConfig = serde_yaml::from_str(
            r#"
            upstream: { mode: mock }
            backends:
              - name: embed
                entitlement: clover
                model_version: mock
            llms:
              - name: clover
                aliases: [gemma4]
                entitlement: clover_llm
                upstream_base: https://openrouter.ai/api
                upstream_bearer_token_env: OPENROUTER_API_KEY
                upstream_model: ~deepseek/deepseek-v4-flash-latest
                model_version: openrouter/deepseek-v4-flash-latest
                request_defaults:
                  reasoning: { enabled: false }
                  temperature: 0
                  seed: 0
                request_overrides:
                  provider:
                    zdr: true
                    data_collection: deny
            "#,
        )
        .expect("hosted OpenAI-compatible LLM config should parse");

        let clover = cfg.llm("clover").expect("canonical Clover LLM");
        assert_eq!(clover.upstream_base, "https://openrouter.ai/api");
        assert_eq!(
            clover.upstream_bearer_token_env.as_deref(),
            Some("OPENROUTER_API_KEY")
        );
        assert_eq!(clover.upstream_model, "~deepseek/deepseek-v4-flash-latest");
        assert_eq!(clover.request_defaults["reasoning"]["enabled"], false);
        assert_eq!(clover.request_defaults["temperature"], 0);
        assert_eq!(clover.request_defaults["seed"], 0);
        assert_eq!(clover.request_overrides["provider"]["zdr"], true);
        assert_eq!(
            clover.request_overrides["provider"]["data_collection"],
            "deny"
        );
        assert_eq!(cfg.llm("gemma4").expect("legacy alias").name, "clover");
    }

    #[test]
    fn long_context_overflow_is_private_to_the_canonical_model_route() {
        let cfg: HutchConfig = serde_yaml::from_str(
            r#"
            upstream: { mode: mock }
            backends:
              - name: embed
                entitlement: clover
                model_version: mock
            llms:
              - name: clover
                aliases: [gemma4, clover-bulk, gemma4-bulk]
                entitlement: clover_llm
                upstream_base: http://10.0.0.6:8000
                upstream_bearer_token_env: GEMMA_BULK_API_KEY
                upstream_model: google/gemma-4-26B-A4B-it
                model_version: local-awq
                overflow:
                  context_threshold_tokens: 45000
                  tokenize_path: /tokenize
                  max_concurrent: 2
                  fallback_on_unavailable: true
                  upstream_base: https://openrouter.ai/api
                  provider: openrouter
                  upstream_bearer_token_env: OPENROUTER_API_KEY
                  upstream_model: google/gemma-4-26b-a4b-it
                  model_version: openrouter/gemma-4-26b
                  request_overrides:
                    provider:
                      zdr: true
                      data_collection: deny
                      sort: throughput
                  prompt_microusd_per_1k: 100
                  completion_microusd_per_1k: 400
            "#,
        )
        .expect("long-context overflow config should parse");

        let llm = cfg.llm("clover").expect("canonical route");
        assert_eq!(llm.name, "clover");
        assert_eq!(
            cfg.llm("clover-bulk").expect("compatibility alias").name,
            "clover"
        );
        let overflow = llm.overflow.as_ref().expect("overflow config");
        assert_eq!(overflow.context_threshold_tokens, 45_000);
        assert_eq!(overflow.tokenize_path.as_deref(), Some("/tokenize"));
        assert_eq!(overflow.tokenize_min_body_bytes, 32 * 1024);
        assert_eq!(overflow.max_concurrent, 2);
        assert!(overflow.fallback_on_unavailable);
        assert_eq!(overflow.provider.as_deref(), Some("openrouter"));
        assert_eq!(overflow.request_overrides["provider"]["zdr"], true);
        assert_eq!(overflow.request_overrides["provider"]["sort"], "throughput");
    }

    #[test]
    fn invalid_overflow_policy_is_rejected_at_load_time() {
        let dir = std::env::temp_dir();
        let path = dir.join(format!(
            "hutch-invalid-overflow-{}-{}.yaml",
            std::process::id(),
            std::thread::current().name().unwrap_or("test")
        ));
        std::fs::write(
            &path,
            r#"
            upstream: { mode: mock }
            backends:
              - name: embed
                entitlement: clover
                model_version: mock
            llms:
              - name: clover
                entitlement: clover_llm
                upstream_model: local
                model_version: local
                overflow:
                  context_threshold_tokens: 0
                  tokenize_path: tokenize
                  max_concurrent: 0
                  upstream_base: ""
                  upstream_model: ""
                  model_version: ""
            "#,
        )
        .expect("write invalid overflow fixture");
        let error = HutchConfig::load(path.to_str().expect("utf-8 fixture path"))
            .expect_err("invalid overflow must be rejected");
        let _ = std::fs::remove_file(path);
        assert!(error.contains("context_threshold_tokens must be positive"));
    }

    #[test]
    fn specialist_can_override_the_shared_upstream_base() {
        let cfg: HutchConfig = serde_yaml::from_str(
            r#"
            upstream:
              mode: proxy
              base_url: http://127.0.0.1:8085
            backends:
              - name: web_scrape
                entitlement: clover
                upstream_base: http://127.0.0.1:8091
                upstream_path: /v1/scrape
                model_version: clover-web-v0.2
            "#,
        )
        .expect("per-backend upstream should parse");

        let web = cfg.backend("web_scrape").expect("web backend");
        assert_eq!(web.upstream_base.as_deref(), Some("http://127.0.0.1:8091"));
        assert_eq!(web.upstream_path.as_deref(), Some("/v1/scrape"));
    }
}
