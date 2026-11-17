//! rvbbit-hutch — the managed-warren gateway (docs/HUTCH_PLAN.md).
//!
//! A smart reverse-proxy with auth: pg_rvbbit's specialist dispatch speaks
//! its native predict contract (POST {url}, Bearer key, {"inputs":[...]} →
//! {"outputs":[...]}) to routes here; the hutch authenticates the key to a
//! tenant, checks entitlements, enforces per-tenant lanes, forwards to the
//! model backend (the zoo) and meters the call. Client install is metadata
//! only: rvbbit.backends rows pointing at /b/{name}/predict with the key in
//! an env var — no extension changes, no heartbeats (external backends are
//! callable by definition in warren_backend_status).
//!
//!   rvbbit-hutch --config hutch.yaml         run the gateway
//!   rvbbit-hutch hash-key <raw-key>          print sha256 for tenants.yaml
//!
//! Middleware order is the trust spine: key → tenant → entitlement → lane →
//! forward → meter. Every non-200 is a stable code + human message because
//! those bodies land in customer receipts.

mod capabilities;
mod capability_health;
mod config;
mod credentials;
mod error;
mod lanes;
mod ledger;
mod llm_stream;
mod media;
mod meter;
mod proxy;
mod speech;
mod tenants;

use axum::extract::{DefaultBodyLimit, Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use base64::Engine as _;
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, RwLock};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use config::{Adapter, HutchConfig, Upstream};
use error::HutchError;
use lanes::LaneRegistry;
use meter::{Meter, MeterRow, ProviderAttempt, ProviderPriceSnapshot};
use proxy::{forward, ForwardErr};
use tenants::{TenantStatus, TenantStore};

const OPENROUTER_APP_URL: &str = "https://rvbbit.ai";
const OPENROUTER_APP_TITLE: &str = "Clover (RVBBIT)";
const MAX_SSE_OBSERVER_BUFFER: usize = 1024 * 1024;
static INVOCATION_SEQUENCE: AtomicU64 = AtomicU64::new(1);

struct AppState {
    cfg: HutchConfig,
    /// Fully resolved at startup (env override + config-relative rules).
    tenants_path: String,
    tenants: RwLock<TenantStore>,
    lanes: LaneRegistry,
    meter: Meter,
    http: reqwest::Client,
}

fn new_invocation_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    let sequence = INVOCATION_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    format!("hutch-{nanos:x}-{sequence:x}")
}

#[derive(Debug, Clone, Default, PartialEq)]
struct ProviderUsageObservation {
    request_id: Option<String>,
    upstream_model: Option<String>,
    prompt_tokens: Option<i64>,
    completion_tokens: Option<i64>,
    provider_cost_nanousd: Option<i64>,
}

fn numeric_value(value: &Value) -> Option<f64> {
    value
        .as_f64()
        .or_else(|| value.as_str()?.parse::<f64>().ok())
        .filter(|number| number.is_finite())
}

fn usd_to_nanousd(value: &Value) -> Option<i64> {
    let usd = numeric_value(value)?;
    if usd < 0.0 {
        return None;
    }
    let nanos = (usd * 1_000_000_000.0).round();
    (nanos <= i64::MAX as f64).then_some(nanos as i64)
}

fn provider_usage_from_response(value: &Value) -> ProviderUsageObservation {
    ProviderUsageObservation {
        request_id: value.get("id").and_then(Value::as_str).map(str::to_string),
        upstream_model: value
            .get("model")
            .and_then(Value::as_str)
            .map(str::to_string),
        prompt_tokens: value
            .pointer("/usage/prompt_tokens")
            .and_then(Value::as_i64),
        completion_tokens: value
            .pointer("/usage/completion_tokens")
            .and_then(Value::as_i64),
        provider_cost_nanousd: value.pointer("/usage/cost").and_then(usd_to_nanousd),
    }
}

fn merge_provider_usage(target: &mut ProviderUsageObservation, update: ProviderUsageObservation) {
    if update.request_id.is_some() {
        target.request_id = update.request_id;
    }
    if update.upstream_model.is_some() {
        target.upstream_model = update.upstream_model;
    }
    if update.prompt_tokens.is_some() {
        target.prompt_tokens = update.prompt_tokens;
    }
    if update.completion_tokens.is_some() {
        target.completion_tokens = update.completion_tokens;
    }
    if update.provider_cost_nanousd.is_some() {
        target.provider_cost_nanousd = update.provider_cost_nanousd;
    }
}

fn llm_provider(llm: &config::LlmCfg) -> String {
    if let Some(provider) = llm
        .provider
        .as_deref()
        .map(str::trim)
        .filter(|provider| !provider.is_empty())
    {
        return provider.to_ascii_lowercase();
    }
    reqwest::Url::parse(&llm.upstream_base)
        .ok()
        .and_then(|url| url.host_str().map(str::to_ascii_lowercase))
        .map(|host| {
            if host == "openrouter.ai" || host.ends_with(".openrouter.ai") {
                "openrouter".to_string()
            } else {
                host
            }
        })
        .unwrap_or_else(|| "generic-openai".to_string())
}

fn reference_cost_nanousd(
    llm: &config::LlmCfg,
    prompt_tokens: Option<i64>,
    completion_tokens: Option<i64>,
) -> i64 {
    // A configured micro-USD / 1k-token rate is numerically identical to
    // nano-USD / token, so this multiplication stays exact and integer-only.
    prompt_tokens
        .unwrap_or_default()
        .max(0)
        .saturating_mul(llm.prompt_microusd_per_1k)
        .saturating_add(
            completion_tokens
                .unwrap_or_default()
                .max(0)
                .saturating_mul(llm.completion_microusd_per_1k),
        )
}

struct ProviderAttemptContext<'a> {
    tenant: &'a str,
    invocation_id: &'a str,
    attempt_no: usize,
    mode: &'a str,
    status: u16,
    cost_source: &'a str,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum LlmUpstreamRoute {
    Primary,
    Overflow,
}

impl LlmUpstreamRoute {
    fn as_str(self) -> &'static str {
        match self {
            Self::Primary => "primary",
            Self::Overflow => "overflow",
        }
    }
}

#[derive(Debug, Clone)]
struct LlmRouteDecision {
    route: LlmUpstreamRoute,
    prompt_tokens: Option<u64>,
    context_budget_tokens: Option<u64>,
    reason: &'static str,
}

impl LlmRouteDecision {
    fn primary() -> Self {
        Self {
            route: LlmUpstreamRoute::Primary,
            prompt_tokens: None,
            context_budget_tokens: None,
            reason: "primary",
        }
    }
}

fn record_provider_attempt(
    state: &AppState,
    llm: &config::LlmCfg,
    observation: &ProviderUsageObservation,
    context: ProviderAttemptContext<'_>,
) {
    let provider = llm_provider(llm);
    state.meter.record_provider_attempt(ProviderAttempt {
        invocation_id: context.invocation_id,
        tenant: context.tenant,
        canonical_model: &llm.name,
        provider: &provider,
        upstream_model: observation
            .upstream_model
            .as_deref()
            .unwrap_or(&llm.upstream_model),
        provider_request_id: observation.request_id.as_deref(),
        attempt_no: context.attempt_no,
        mode: context.mode,
        status: context.status,
        prompt_tokens: observation.prompt_tokens,
        completion_tokens: observation.completion_tokens,
        provider_cost_nanousd: observation.provider_cost_nanousd,
        cost_source: context.cost_source,
        reconcile: is_openrouter_upstream(llm),
    });
}

#[derive(Default)]
struct OpenAiSseUsageObserver {
    buffer: Vec<u8>,
    usage: ProviderUsageObservation,
}

impl OpenAiSseUsageObserver {
    fn observe(&mut self, bytes: &[u8]) {
        self.buffer.extend_from_slice(bytes);
        while let Some(newline) = self.buffer.iter().position(|byte| *byte == b'\n') {
            let mut line: Vec<u8> = self.buffer.drain(..=newline).collect();
            line.pop();
            if line.last() == Some(&b'\r') {
                line.pop();
            }
            self.observe_line(&line);
        }
        if self.buffer.len() > MAX_SSE_OBSERVER_BUFFER {
            tracing::warn!(
                bytes = self.buffer.len(),
                "discarding oversized SSE accounting line"
            );
            self.buffer.clear();
        }
    }

    fn observe_line(&mut self, line: &[u8]) {
        let Some(data) = line.strip_prefix(b"data:") else {
            return;
        };
        let data = data.strip_prefix(b" ").unwrap_or(data);
        if data.is_empty() || data == b"[DONE]" {
            return;
        }
        if let Ok(value) = serde_json::from_slice::<Value>(data) {
            merge_provider_usage(&mut self.usage, provider_usage_from_response(&value));
        }
    }

    fn finish(&mut self) {
        if !self.buffer.is_empty() {
            let line = std::mem::take(&mut self.buffer);
            self.observe_line(&line);
        }
    }
}

struct StreamingMeterGuard {
    state: Arc<AppState>,
    llm: config::LlmCfg,
    tenant: String,
    invocation_id: String,
    label: String,
    attempt_no: usize,
    started: Instant,
    observer: OpenAiSseUsageObserver,
    error_code: Option<&'static str>,
    recorded: bool,
}

impl StreamingMeterGuard {
    fn observe(&mut self, bytes: &[u8]) {
        self.observer.observe(bytes);
    }

    fn mark_transport_error(&mut self) {
        self.error_code = Some("stream_error");
    }

    fn finish(&mut self) {
        self.observer.finish();
        self.record(None);
    }

    fn record(&mut self, fallback_error: Option<&'static str>) {
        if self.recorded {
            return;
        }
        self.recorded = true;
        let error_code = self.error_code.or(fallback_error);
        let duration_ms = self.started.elapsed().as_secs_f64() * 1000.0;
        let cost_source = if self.observer.usage.provider_cost_nanousd.is_some() {
            "inline_stream"
        } else {
            "pending"
        };
        record_provider_attempt(
            &self.state,
            &self.llm,
            &self.observer.usage,
            ProviderAttemptContext {
                tenant: &self.tenant,
                invocation_id: &self.invocation_id,
                attempt_no: self.attempt_no,
                mode: "stream",
                status: if error_code.is_some() { 499 } else { 200 },
                cost_source,
            },
        );
        self.state.meter.record(MeterRow {
            invocation_id: Some(&self.invocation_id),
            tenant: &self.tenant,
            backend: &self.label,
            stream: true,
            n_inputs: 1,
            status: 200,
            error_code,
            duration_ms,
            upstream_ms: Some(duration_ms),
            model_version: &self.llm.model_version,
            reference_cost_nanousd: reference_cost_nanousd(
                &self.llm,
                self.observer.usage.prompt_tokens,
                self.observer.usage.completion_tokens,
            ),
            prompt_tokens: self.observer.usage.prompt_tokens,
            completion_tokens: self.observer.usage.completion_tokens,
        });
    }
}

impl Drop for StreamingMeterGuard {
    fn drop(&mut self) {
        self.record(Some("stream_interrupted"));
    }
}

/// HUTCH_TENANTS env wins; otherwise a relative tenants_file resolves
/// against the config file's own directory.
fn resolve_tenants_path(config_path: &str, tenants_file: &str) -> String {
    if let Ok(env_path) = std::env::var("HUTCH_TENANTS") {
        return env_path;
    }
    let p = std::path::Path::new(tenants_file);
    if p.is_absolute() {
        return tenants_file.to_string();
    }
    std::path::Path::new(config_path)
        .parent()
        .unwrap_or_else(|| std::path::Path::new("."))
        .join(p)
        .to_string_lossy()
        .into_owned()
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "rvbbit_hutch=info,info".into()),
        )
        .init();

    let mut args = std::env::args().skip(1);
    let mut config_path =
        std::env::var("HUTCH_CONFIG").unwrap_or_else(|_| "hutch.yaml".to_string());
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "hash-key" => {
                let raw = args.next().unwrap_or_else(|| {
                    eprintln!("usage: rvbbit-hutch hash-key <raw-key>");
                    std::process::exit(2);
                });
                println!("{}", tenants::hash_key(&raw));
                return;
            }
            "--config" => {
                config_path = args.next().unwrap_or_else(|| {
                    eprintln!("--config requires a path");
                    std::process::exit(2);
                });
            }
            other => {
                eprintln!("unknown arg: {other}");
                std::process::exit(2);
            }
        }
    }

    let cfg = HutchConfig::load(&config_path).unwrap_or_else(|e| {
        eprintln!("{e}");
        std::process::exit(1);
    });
    // tenants_file is config, so a relative path resolves against the config
    // file's directory (meter_db is data and stays relative to the workdir —
    // that's where the volume mounts).
    let tenants_path = resolve_tenants_path(&config_path, &cfg.tenants_file);
    let store = TenantStore::load(&tenants_path).unwrap_or_else(|e| {
        eprintln!("{e}");
        std::process::exit(1);
    });
    let meter = match &cfg.ledger_postgres {
        Some(config) => Meter::open_postgres(config),
        None => Meter::open(&cfg.meter_db),
    }
    .unwrap_or_else(|e| {
        eprintln!("{e}");
        std::process::exit(1);
    });
    tracing::info!(
        backends = cfg.backends.len(),
        tenants = store.len(),
        upstream = match &cfg.upstream {
            Upstream::Mock => "mock".to_string(),
            Upstream::Proxy { base_url } => base_url.clone(),
        },
        "hutch starting on {}",
        cfg.bind
    );

    let max_body = cfg.max_body_bytes;
    let bind = cfg.bind.clone();
    let state = Arc::new(AppState {
        cfg,
        tenants_path,
        tenants: RwLock::new(store),
        lanes: LaneRegistry::default(),
        meter,
        http: reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .expect("HTTP client configuration"),
    });
    let maintenance = tokio::spawn(provider_maintenance_loop(state.clone()));

    let app = Router::new()
        .route("/", get(health))
        .route("/healthz", get(health))
        .route("/metrics", get(metrics))
        .route("/admin/ledger", get(ledger_status))
        .route("/admin/reload-tenants", post(reload_tenants))
        .route("/b/{backend}/predict", post(predict))
        .route("/v1/models", get(list_models))
        .route("/v1/capabilities", get(capabilities::list))
        .route("/v1/embeddings", post(embeddings))
        .route("/v1/rerank", post(rerank))
        .route("/v1/decisions", post(decisions))
        .route("/v1/audio/models", get(speech::models))
        .route("/v1/audio/speech", post(speech::synthesize))
        .route("/v1/media/models", get(media::models))
        .route("/v1/images/generations", post(media::images))
        .route(
            "/v1/audio/transcriptions",
            post(media::transcribe).layer(DefaultBodyLimit::max(26 * 1024 * 1024)),
        )
        .route("/v1/chat/completions", post(chat_completions))
        .layer(DefaultBodyLimit::max(max_body))
        .with_state(state.clone());

    let listener = tokio::net::TcpListener::bind(&bind)
        .await
        .unwrap_or_else(|e| {
            eprintln!("cannot bind {bind}: {e}");
            std::process::exit(1);
        });
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            #[cfg(unix)]
            {
                let mut term =
                    tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
                        .expect("SIGTERM handler");
                tokio::select! { _ = tokio::signal::ctrl_c() => {}, _ = term.recv() => {} }
            }
            #[cfg(not(unix))]
            let _ = tokio::signal::ctrl_c().await;
            tracing::info!("shutting down");
        })
        .await
        .expect("server error");
    maintenance.abort();
    state.meter.shutdown().await;
}

async fn health(State(state): State<Arc<AppState>>) -> Json<Value> {
    Json(json!({
        "ok": true,
        "service": "rvbbit-hutch",
        "version": env!("CARGO_PKG_VERSION"),
        "backends": state.cfg.backends.iter().map(|b| b.name.clone()).collect::<Vec<_>>(),
    }))
}

/// Bearer key from Authorization or X-Rvbbit-Token (the hare lesson: some
/// front doors eat Authorization, so both are first-class).
fn extract_key(headers: &HeaderMap) -> Option<String> {
    if let Some(auth) = headers.get("authorization").and_then(|v| v.to_str().ok()) {
        if let Some(k) = auth
            .strip_prefix("Bearer ")
            .or_else(|| auth.strip_prefix("bearer "))
        {
            if !k.trim().is_empty() {
                return Some(k.trim().to_string());
            }
        }
    }
    headers
        .get("x-rvbbit-token")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// key → tenant from the tenants file. An expired tenant is returned as-is so
/// route handlers refuse it with the metered subscription_expired path.
async fn authenticate(
    state: &AppState,
    headers: &HeaderMap,
) -> Result<tenants::Tenant, HutchError> {
    let key = extract_key(headers).ok_or_else(HutchError::invalid_key)?;
    {
        let store = state.tenants.read().expect("tenant store poisoned");
        if let Some(t) = store.lookup(&key) {
            return Ok(t.clone());
        }
    }
    Err(HutchError::invalid_key())
}

#[allow(clippy::result_large_err)]
fn admin_gate(state: &AppState, headers: &HeaderMap) -> Result<(), Response> {
    match &state.cfg.admin_token {
        None => Ok(()),
        Some(want) => match extract_key(headers) {
            Some(got) if &got == want => Ok(()),
            _ => Err((StatusCode::UNAUTHORIZED, "admin token required\n").into_response()),
        },
    }
}

async fn metrics(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    if let Err(resp) = admin_gate(&state, &headers) {
        return resp;
    }
    let body = state.meter.render_prometheus(&state.lanes.snapshot());
    ([("content-type", "text/plain; version=0.0.4")], body).into_response()
}

async fn ledger_status(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    if let Err(resp) = admin_gate(&state, &headers) {
        return resp;
    }
    Json(state.meter.ledger_status()).into_response()
}

async fn reload_tenants(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    if let Err(resp) = admin_gate(&state, &headers) {
        return resp;
    }
    match TenantStore::load(&state.tenants_path) {
        Ok(store) => {
            let n = store.len();
            *state.tenants.write().expect("tenant store poisoned") = store;
            tracing::info!(tenants = n, "tenants reloaded");
            Json(json!({"ok": true, "tenants": n})).into_response()
        }
        Err(e) => {
            tracing::error!("tenants reload failed, keeping previous store: {e}");
            (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"ok": false, "error": e})),
            )
                .into_response()
        }
    }
}

#[derive(Deserialize)]
struct PredictIn {
    inputs: Vec<Value>,
}

#[derive(Deserialize)]
struct EmbeddingsIn {
    #[serde(default)]
    model: Option<String>,
    input: Value,
    #[serde(default)]
    encoding_format: Option<String>,
    #[serde(default)]
    dimensions: Option<usize>,
}

#[derive(Deserialize)]
struct RerankIn {
    #[serde(default)]
    model: Option<String>,
    query: String,
    documents: Vec<String>,
    #[serde(default)]
    top_n: Option<usize>,
}

async fn rerank(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<RerankIn>,
) -> Response {
    if body.model.as_deref().is_some_and(|m| m != "rerank") {
        return HutchError::unknown_model(body.model.as_deref().unwrap()).into_response();
    }
    let documents = body.documents;
    let inputs = documents
        .iter()
        .map(|t| json!({"query":body.query,"text":t}))
        .collect();
    let response = predict(
        State(state),
        Path("rerank".into()),
        headers,
        Ok(Json(PredictIn { inputs })),
    )
    .await;
    if !response.status().is_success() {
        return response;
    }
    let bytes = match axum::body::to_bytes(response.into_body(), 8_000_000).await {
        Ok(b) => b,
        Err(_) => return HutchError::upstream("rerank", "Invalid response".into()).into_response(),
    };
    let value: Value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    let Some(scores) = value["outputs"].as_array() else {
        return HutchError::upstream("rerank", "Missing scores".into()).into_response();
    };
    let mut results: Vec<Value> = scores
        .iter()
        .enumerate()
        .map(|(i, s)| {
            json!({
        "index":i,"relevance_score":s,"document":{"text":documents[i]}})
        })
        .collect();
    results.sort_by(|a, b| {
        b["relevance_score"]
            .as_f64()
            .unwrap_or(0.0)
            .total_cmp(&a["relevance_score"].as_f64().unwrap_or(0.0))
    });
    results.truncate(body.top_n.unwrap_or(results.len()));
    Json(json!({"model":"rerank","results":results,"usage":value["usage"],"hutch":value["hutch"]}))
        .into_response()
}

async fn decisions(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Response {
    let response = predict(
        State(state),
        Path("decisions".into()),
        headers,
        Ok(Json(PredictIn { inputs: vec![body] })),
    )
    .await;
    if !response.status().is_success() {
        return response;
    }
    let bytes = match axum::body::to_bytes(response.into_body(), 8_000_000).await {
        Ok(b) => b,
        Err(_) => {
            return HutchError::upstream("decisions", "Invalid response".into()).into_response()
        }
    };
    let value: Value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    Json(value["outputs"][0].clone()).into_response()
}

#[derive(Debug, Default, PartialEq)]
struct SpecialistUsage {
    seen: bool,
    prompt_tokens: i64,
    completion_tokens: i64,
    cost_microusd: i64,
}

/// Predict-shaped specialist responses may carry their own nested model
/// usage. This lets composed backends such as web_research preserve the
/// actual token/provider-cost receipt while ordinary fixed-cost specialists
/// continue using unit_microusd unchanged.
///
/// Two usage dialects are accepted: the OpenAI-style block
/// (`prompt_tokens` / `completion_tokens` / `cost_microusd`) and the Clover
/// decisions block (`input_tokens` / `output_tokens` / `cost` in USD). The
/// decisions service only ever spoke the second, so every Jev call was metered
/// at zero until 2026-09-18 even though its receipt carried the real charge.
fn specialist_usage(outputs: &[Value]) -> SpecialistUsage {
    let mut total = SpecialistUsage::default();
    for output in outputs {
        let Some(usage) = output.get("usage").and_then(Value::as_object) else {
            continue;
        };
        total.seen = true;
        // Composed CPU analytics preserves its embedding cost in the SQL
        // result, but that child call already passed through this gateway.
        // Do not charge/count those tokens a second time in Hutch's ledger.
        if usage.get("already_metered").and_then(Value::as_bool) == Some(true) {
            continue;
        }
        let tokens = |keys: &[&str]| {
            keys.iter()
                .find_map(|k| usage.get(*k).and_then(Value::as_i64))
                .unwrap_or_default()
                .max(0)
        };
        total.prompt_tokens = total
            .prompt_tokens
            .saturating_add(tokens(&["prompt_tokens", "input_tokens"]));
        total.completion_tokens = total
            .completion_tokens
            .saturating_add(tokens(&["completion_tokens", "output_tokens"]));
        let cost_microusd = usage
            .get("cost_microusd")
            .and_then(Value::as_i64)
            .or_else(|| {
                // Fractional USD → micro-USD, rounded, never negative.
                usage
                    .get("cost")
                    .or_else(|| usage.get("cost_usd"))
                    .and_then(Value::as_f64)
                    .filter(|c| c.is_finite() && *c >= 0.0)
                    .map(|c| (c * 1_000_000.0).round() as i64)
            })
            .unwrap_or_default();
        total.cost_microusd = total.cost_microusd.saturating_add(cost_microusd);
    }
    total
}

/// Provider-reported specialist charge, with nano-USD precision. An absent cost
/// stays unknown. Composed child calls already metered here are excluded.
fn reported_specialist_cost(outputs: &[Value]) -> Option<i64> {
    let mut total = None;
    for output in outputs {
        let usage = output.get("usage")?;
        if usage.get("already_metered").and_then(Value::as_bool) == Some(true) {
            // Explicitly no additional charge here; child invocations hold the cost.
            total = Some(total.unwrap_or(0_i64));
            continue;
        }
        let cost = usage
            .get("cost_microusd")
            .and_then(Value::as_i64)
            .filter(|c| *c >= 0)
            .map(|c| c.saturating_mul(1000))
            .or_else(|| {
                usage
                    .get("cost")
                    .or_else(|| usage.get("cost_usd"))
                    .and_then(Value::as_f64)
                    .filter(|c| c.is_finite() && *c >= 0.)
                    .map(|c| (c * 1_000_000_000.).round() as i64)
            })?;
        total = Some(total.unwrap_or(0_i64).saturating_add(cost));
    }
    total
}
fn record_specialist_charge(
    state: &AppState,
    backend: &config::BackendCfg,
    tenant: &str,
    id: &str,
    usage: &SpecialistUsage,
    cost: Option<i64>,
) {
    if let Some(cost) = cost {
        let provider = if backend
            .upstream_base
            .as_deref()
            .is_some_and(|b| b.starts_with("https://openrouter.ai/"))
        {
            "openrouter"
        } else {
            "worker"
        };
        state.meter.record_provider_attempt(ProviderAttempt {
            invocation_id: id,
            tenant,
            canonical_model: &backend.name,
            provider,
            upstream_model: &backend.model_version,
            provider_request_id: None,
            attempt_no: 1,
            mode: "specialist",
            status: 200,
            prompt_tokens: Some(usage.prompt_tokens),
            completion_tokens: Some(usage.completion_tokens),
            provider_cost_nanousd: Some(cost),
            cost_source: "worker_reported",
            reconcile: false,
        });
    }
}

async fn predict(
    State(state): State<Arc<AppState>>,
    Path(backend_name): Path<String>,
    headers: HeaderMap,
    body: Result<Json<PredictIn>, axum::extract::rejection::JsonRejection>,
) -> Response {
    let t0 = Instant::now();

    // 1. key → tenant
    let tenant = match authenticate(&state, &headers).await {
        Ok(t) => t,
        Err(e) => return e.into_response(),
    };

    // 2. backend + entitlement — checked before touching the body so the
    //    error story is stable regardless of payload.
    let backend = match state.cfg.backend(&backend_name) {
        Some(b) => b.clone(),
        None => return HutchError::unknown_backend(&backend_name).into_response(),
    };
    let refuse = |err: HutchError, code: &'static str, state: &AppState| {
        state.meter.record(MeterRow {
            invocation_id: None,
            tenant: &tenant.id,
            backend: &backend_name,
            stream: false,
            n_inputs: 0,
            status: err.status.as_u16(),
            error_code: Some(code),
            duration_ms: t0.elapsed().as_secs_f64() * 1000.0,
            upstream_ms: None,
            model_version: &backend.model_version,
            reference_cost_nanousd: 0,
            prompt_tokens: None,
            completion_tokens: None,
        });
        err.into_response()
    };
    if tenant.status == TenantStatus::Expired {
        return refuse(
            HutchError::subscription_expired(&tenant.id),
            "subscription_expired",
            &state,
        );
    }
    if !tenant
        .entitlements
        .iter()
        .any(|e| e == &backend.entitlement)
    {
        return refuse(
            HutchError::not_entitled(&tenant.id, &backend.name, &backend.entitlement),
            "not_entitled",
            &state,
        );
    }

    if !backend.enabled {
        return refuse(
            HutchError::unavailable_backend(&backend),
            "capability_unavailable",
            &state,
        );
    }

    // 3. body
    let inputs = match body {
        Ok(Json(p)) => p.inputs,
        Err(e) => return HutchError::bad_request(e.body_text()).into_response(),
    };
    if inputs.is_empty() {
        return Json(json!({"outputs": []})).into_response();
    }
    let n = inputs.len();

    // 4. lane — skipped for unlaned backends (cheap encoders: batching is
    // already the throttle; lanes price generation, not classification).
    let _permit = if backend.unlaned {
        None
    } else {
        match state
            .lanes
            .acquire(&tenant.id, tenant.lanes, state.cfg.lane_grace_ms)
            .await
        {
            Some(p) => Some(p),
            None => {
                return refuse(
                    HutchError::lanes_saturated(&tenant.id, tenant.lanes),
                    "lanes_saturated",
                    &state,
                )
            }
        }
    };

    // 5. forward
    let tenant_scope = tenants::hash_key(&format!("clover-research:{}", tenant.id));
    let fwd = forward(
        &state.http,
        &state.cfg.upstream,
        &backend,
        &inputs,
        Some(&tenant_scope),
    )
    .await;
    let duration_ms = t0.elapsed().as_secs_f64() * 1000.0;
    match fwd {
        Ok(ok) => {
            let observations = ok.usage.as_ref().map(|u| vec![json!({"usage":u})]);
            let observations = observations.as_deref().unwrap_or(&ok.outputs);
            let usage = specialist_usage(observations);
            let invocation_id = uuid::Uuid::new_v4().to_string();
            record_specialist_charge(
                &state,
                &backend,
                &tenant.id,
                &invocation_id,
                &usage,
                reported_specialist_cost(observations),
            );
            state.meter.record(MeterRow {
                invocation_id: Some(&invocation_id),
                tenant: &tenant.id,
                backend: &backend.name,
                stream: false,
                n_inputs: n,
                status: 200,
                error_code: None,
                duration_ms,
                upstream_ms: Some(ok.upstream_ms),
                model_version: &backend.model_version,
                reference_cost_nanousd: backend
                    .unit_microusd
                    .saturating_mul(n as i64)
                    .saturating_mul(1000),
                prompt_tokens: usage.seen.then_some(usage.prompt_tokens),
                completion_tokens: usage.seen.then_some(usage.completion_tokens),
            });
            // Extra fields are ignored by pg_rvbbit's PredictResponse parse;
            // humans with curl get the provenance breadcrumb in-band.
            let mut resp = Json(json!({
                "outputs": ok.outputs,
                "usage": ok.usage,
                "hutch": {"backend": backend.name, "model_version": backend.model_version, "n": n},
            }))
            .into_response();
            if let Ok(v) = axum::http::HeaderValue::from_str(&backend.model_version) {
                resp.headers_mut().insert("x-hutch-model-version", v);
            }
            resp
        }
        Err(e) => {
            let (err, code) = match e {
                ForwardErr::Status { status, body_head } => (
                    HutchError::upstream(&backend.name, format!("HTTP {status}: {body_head}")),
                    "upstream_error",
                ),
                ForwardErr::Transport(detail) => (
                    HutchError::upstream(&backend.name, detail),
                    "upstream_error",
                ),
            };
            state.meter.record(MeterRow {
                invocation_id: None,
                tenant: &tenant.id,
                backend: &backend.name,
                stream: false,
                n_inputs: n,
                status: err.status.as_u16(),
                error_code: Some(code),
                duration_ms,
                upstream_ms: None,
                model_version: &backend.model_version,
                reference_cost_nanousd: 0,
                prompt_tokens: None,
                completion_tokens: None,
            });
            err.into_response()
        }
    }
}

fn normalize_embedding_inputs(input: Value) -> Result<Vec<Value>, String> {
    match input {
        Value::String(_) => Ok(vec![input]),
        Value::Array(items) if items.iter().all(Value::is_string) => Ok(items),
        Value::Array(_) => Err(
            "embeddings input must be a string or an array of strings; token arrays are not supported"
                .into(),
        ),
        _ => Err("embeddings input must be a string or an array of strings".into()),
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum EmbeddingEncoding {
    Float,
    Base64,
}

fn embedding_encoding(value: Option<&str>) -> Result<EmbeddingEncoding, String> {
    match value.map(str::trim).filter(|value| !value.is_empty()) {
        None | Some("float") => Ok(EmbeddingEncoding::Float),
        Some("base64") => Ok(EmbeddingEncoding::Base64),
        Some(other) => Err(format!(
            "embeddings encoding_format must be 'float' or 'base64', got '{other}'"
        )),
    }
}

fn encode_embedding(value: Value, encoding: EmbeddingEncoding) -> Result<Value, String> {
    if encoding == EmbeddingEncoding::Float {
        return Ok(value);
    }
    let values = value
        .as_array()
        .ok_or_else(|| "embedding backend returned a non-array vector".to_string())?;
    let mut bytes = Vec::with_capacity(values.len() * std::mem::size_of::<f32>());
    for item in values {
        let number = item
            .as_f64()
            .filter(|number| number.is_finite())
            .ok_or_else(|| "embedding backend returned a non-numeric vector value".to_string())?;
        bytes.extend_from_slice(&(number as f32).to_le_bytes());
    }
    Ok(Value::String(BASE64_STANDARD.encode(bytes)))
}

fn embedding_data(outputs: Vec<Value>, encoding: EmbeddingEncoding) -> Result<Vec<Value>, String> {
    outputs
        .into_iter()
        .enumerate()
        .map(|(index, embedding)| {
            Ok(json!({
                "object": "embedding",
                "embedding": encode_embedding(embedding, encoding)?,
                "index": index,
            }))
        })
        .collect()
}

/// OpenAI-compatible embedding surface for managed consumers such as
/// Hindsight. It is the same authenticated, entitled and metered `embed`
/// backend used by semantic SQL; this is only a second wire shape.
async fn embeddings(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: Result<Json<EmbeddingsIn>, axum::extract::rejection::JsonRejection>,
) -> Response {
    let t0 = Instant::now();
    let tenant = match authenticate(&state, &headers).await {
        Ok(t) => t,
        Err(e) => return e.into_response(),
    };
    let body = match body {
        Ok(Json(value)) => value,
        Err(e) => return HutchError::bad_request(e.body_text()).into_response(),
    };
    let backend_name = body
        .model
        .as_deref()
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .unwrap_or("embed")
        .to_string();
    let backend = match state.cfg.backend(&backend_name) {
        Some(backend) if backend.adapter == Adapter::OpenaiEmbeddings => backend.clone(),
        _ => return HutchError::unknown_model(&backend_name).into_response(),
    };
    let refuse = |err: HutchError, code: &'static str, state: &AppState| {
        state.meter.record(MeterRow {
            invocation_id: None,
            tenant: &tenant.id,
            backend: &backend.name,
            stream: false,
            n_inputs: 0,
            status: err.status.as_u16(),
            error_code: Some(code),
            duration_ms: t0.elapsed().as_secs_f64() * 1000.0,
            upstream_ms: None,
            model_version: &backend.model_version,
            reference_cost_nanousd: 0,
            prompt_tokens: None,
            completion_tokens: None,
        });
        err.into_response()
    };
    if tenant.status == TenantStatus::Expired {
        return refuse(
            HutchError::subscription_expired(&tenant.id),
            "subscription_expired",
            &state,
        );
    }
    if !tenant
        .entitlements
        .iter()
        .any(|entitlement| entitlement == &backend.entitlement)
    {
        return refuse(
            HutchError::not_entitled(&tenant.id, &backend.name, &backend.entitlement),
            "not_entitled",
            &state,
        );
    }
    if !backend.enabled {
        return refuse(
            HutchError::unavailable_backend(&backend),
            "capability_unavailable",
            &state,
        );
    }
    let encoding = match embedding_encoding(body.encoding_format.as_deref()) {
        Ok(encoding) => encoding,
        Err(detail) => return refuse(HutchError::bad_request(detail), "bad_request", &state),
    };
    if let Some(requested) = body.dimensions {
        let configured = backend
            .upstream_params
            .as_ref()
            .and_then(|p| p.get("dimensions"))
            .and_then(Value::as_u64);
        if configured.is_some_and(|d| d as usize != requested) {
            return refuse(
                HutchError::bad_request(
                    "Dimensions must match the installation embedding profile".into(),
                ),
                "bad_request",
                &state,
            );
        }
    }
    let inputs = match normalize_embedding_inputs(body.input) {
        Ok(inputs) => inputs,
        Err(detail) => return refuse(HutchError::bad_request(detail), "bad_request", &state),
    };
    if inputs.is_empty() {
        return Json(json!({
            "object": "list",
            "data": [],
            "model": backend.name.clone(),
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }))
        .into_response();
    }
    let n = inputs.len();
    let _permit = if backend.unlaned {
        None
    } else {
        match state
            .lanes
            .acquire(&tenant.id, tenant.lanes, state.cfg.lane_grace_ms)
            .await
        {
            Some(permit) => Some(permit),
            None => {
                return refuse(
                    HutchError::lanes_saturated(&tenant.id, tenant.lanes),
                    "lanes_saturated",
                    &state,
                )
            }
        }
    };
    let forwarded = forward(&state.http, &state.cfg.upstream, &backend, &inputs, None).await;
    let duration_ms = t0.elapsed().as_secs_f64() * 1000.0;
    match forwarded {
        Ok(ok) => {
            let data = match embedding_data(ok.outputs, encoding) {
                Ok(data) => data,
                Err(detail) => {
                    return refuse(
                        HutchError::upstream(&backend.name, detail),
                        "upstream_error",
                        &state,
                    )
                }
            };
            let observations = [json!({"usage":ok.usage})];
            let usage = specialist_usage(&observations);
            let invocation_id = uuid::Uuid::new_v4().to_string();
            record_specialist_charge(
                &state,
                &backend,
                &tenant.id,
                &invocation_id,
                &usage,
                reported_specialist_cost(&observations),
            );
            state.meter.record(MeterRow {
                invocation_id: Some(&invocation_id),
                tenant: &tenant.id,
                backend: &backend.name,
                stream: false,
                n_inputs: n,
                status: 200,
                error_code: None,
                duration_ms,
                upstream_ms: Some(ok.upstream_ms),
                model_version: &backend.model_version,
                reference_cost_nanousd: backend
                    .unit_microusd
                    .saturating_mul(n as i64)
                    .saturating_mul(1000),
                prompt_tokens: usage.seen.then_some(usage.prompt_tokens),
                completion_tokens: usage.seen.then_some(usage.completion_tokens),
            });
            let mut response = Json(json!({
                "object": "list",
                "data": data,
                "model": backend.name.clone(),
                "usage": ok.usage.unwrap_or_else(|| {
                    json!({"prompt_tokens": 0, "total_tokens": 0})
                }),
            }))
            .into_response();
            if let Ok(value) = axum::http::HeaderValue::from_str(&backend.model_version) {
                response
                    .headers_mut()
                    .insert("x-hutch-model-version", value);
            }
            response
        }
        Err(error) => {
            let detail = match error {
                ForwardErr::Status { status, body_head } => format!("HTTP {status}: {body_head}"),
                ForwardErr::Transport(detail) => detail,
            };
            refuse(
                HutchError::upstream(&backend.name, detail),
                "upstream_error",
                &state,
            )
        }
    }
}

// ---------------------------------------------------------------------------
// OpenAI-compatible LLM surface — one wire format serves pg_rvbbit's
// openai_chat transport, agent()/flow steps, AND raw OpenAI SDKs. Routing is
// by the request's `model` field; the hutch rewrites it to the upstream's
// served name on the way through.
// ---------------------------------------------------------------------------

async fn list_models(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let tenant = match authenticate(&state, &headers).await {
        Ok(t) => t,
        Err(e) => return e.into_response(),
    };
    let chat_models = state
        .cfg
        .llms
        .iter()
        .filter(|llm| {
            tenant
                .entitlements
                .iter()
                .any(|entitlement| entitlement == &llm.entitlement)
        })
        .map(|llm| {
            json!({
                "id": llm.name,
                "object": "model",
                "owned_by": "rvbbit-hutch",
                "meta": {"managed": true, "capability": "chat.completions"},
            })
        });
    let embedding_models = state
        .cfg
        .backends
        .iter()
        .filter(|backend| {
            backend.enabled
                && backend.adapter == Adapter::OpenaiEmbeddings
                && tenant
                    .entitlements
                    .iter()
                    .any(|entitlement| entitlement == &backend.entitlement)
        })
        .map(|backend| {
            json!({
                "id": backend.name,
                "object": "model",
                "owned_by": "rvbbit-hutch",
                "meta": {
                    "managed": true,
                    "capability": "embeddings",
                    "model_version": backend.model_version,
                },
            })
        });
    let data: Vec<Value> = chat_models.chain(embedding_models).collect();
    Json(json!({"object": "list", "data": data})).into_response()
}

fn requested_output_tokens(payload: &Value) -> u64 {
    payload
        .get("max_completion_tokens")
        .and_then(Value::as_u64)
        .or_else(|| payload.get("max_tokens").and_then(Value::as_u64))
        .unwrap_or_default()
}

fn materialize_overflow_llm(llm: &config::LlmCfg) -> Option<config::LlmCfg> {
    let overflow = llm.overflow.as_ref()?;
    let mut routed = llm.clone();
    routed.upstream_base = overflow.upstream_base.clone();
    routed.provider = overflow.provider.clone();
    routed.upstream_bearer_token_env = overflow.upstream_bearer_token_env.clone();
    routed.upstream_model = overflow.upstream_model.clone();
    routed.model_version = overflow.model_version.clone();
    for (key, value) in &overflow.request_defaults {
        routed.request_defaults.insert(key.clone(), value.clone());
    }
    for (key, value) in &overflow.request_overrides {
        routed.request_overrides.insert(key.clone(), value.clone());
    }
    routed.prompt_microusd_per_1k = overflow.prompt_microusd_per_1k;
    routed.completion_microusd_per_1k = overflow.completion_microusd_per_1k;
    routed.timeout_ms = overflow.timeout_ms;
    routed.overflow = None;
    Some(routed)
}

async fn exact_chat_prompt_tokens(
    state: &AppState,
    llm: &config::LlmCfg,
    payload: &Value,
    tokenize_path: &str,
) -> Result<u64, String> {
    let mut token_payload = payload.clone();
    route_llm_payload(&mut token_payload, llm, "token-preflight");
    let url = format!(
        "{}{}",
        llm.upstream_base.trim_end_matches('/'),
        tokenize_path
    );
    let mut request = state
        .http
        .post(url)
        .timeout(std::time::Duration::from_secs(30));
    match resolve_upstream_bearer_token(llm, crate::credentials::resolve) {
        Ok(Some(token)) => request = request.bearer_auth(token),
        Ok(None) => {}
        Err(detail) => return Err(detail),
    }
    let response = request
        .json(&token_payload)
        .send()
        .await
        .map_err(|error| error.to_string())?;
    let status = response.status();
    let value: Value = response
        .json()
        .await
        .map_err(|error| format!("tokenizer HTTP {status} returned bad JSON: {error}"))?;
    if !status.is_success() {
        let detail = value
            .pointer("/error/message")
            .and_then(Value::as_str)
            .unwrap_or("tokenizer rejected the request");
        return Err(format!("tokenizer HTTP {}: {detail}", status.as_u16()));
    }
    value
        .get("count")
        .and_then(Value::as_u64)
        .ok_or_else(|| "tokenizer response omitted count".to_string())
}

async fn choose_llm_route(
    state: &AppState,
    llm: &config::LlmCfg,
    payload: &Value,
) -> LlmRouteDecision {
    let Some(overflow) = llm.overflow.as_ref() else {
        return LlmRouteDecision::primary();
    };
    let output_tokens = requested_output_tokens(payload);
    let serialized_bytes = serde_json::to_vec(payload).map_or(0, |body| body.len());
    let rough_prompt_tokens = (serialized_bytes as u64).div_ceil(3);
    let rough_budget = rough_prompt_tokens.saturating_add(output_tokens);
    let should_tokenize = overflow.tokenize_path.is_some()
        && (serialized_bytes >= overflow.tokenize_min_body_bytes
            || rough_budget > overflow.context_threshold_tokens);

    if should_tokenize {
        let path = overflow.tokenize_path.as_deref().expect("checked above");
        match exact_chat_prompt_tokens(state, llm, payload, path).await {
            Ok(prompt_tokens) => {
                let budget = prompt_tokens.saturating_add(output_tokens);
                return LlmRouteDecision {
                    route: if budget > overflow.context_threshold_tokens {
                        LlmUpstreamRoute::Overflow
                    } else {
                        LlmUpstreamRoute::Primary
                    },
                    prompt_tokens: Some(prompt_tokens),
                    context_budget_tokens: Some(budget),
                    reason: if budget > overflow.context_threshold_tokens {
                        "context-budget"
                    } else {
                        "within-context-budget"
                    },
                };
            }
            Err(error) => tracing::warn!(
                model = llm.name,
                error,
                "LLM context token preflight failed; using conservative estimate"
            ),
        }
    }

    LlmRouteDecision {
        route: if rough_budget > overflow.context_threshold_tokens {
            LlmUpstreamRoute::Overflow
        } else {
            LlmUpstreamRoute::Primary
        },
        prompt_tokens: None,
        context_budget_tokens: Some(rough_budget),
        reason: if rough_budget > overflow.context_threshold_tokens {
            "estimated-context-budget"
        } else {
            "primary"
        },
    }
}

enum LlmSendError {
    NotConfigured(String),
    Transport(String),
}

async fn send_llm_upstream(
    state: &AppState,
    llm: &config::LlmCfg,
    payload: &Value,
) -> Result<reqwest::Response, LlmSendError> {
    let url = format!(
        "{}/v1/chat/completions",
        llm.upstream_base.trim_end_matches('/')
    );
    let mut request = state.http.post(&url);
    if is_openrouter_upstream(llm) {
        request = request
            .header("HTTP-Referer", OPENROUTER_APP_URL)
            .header("X-OpenRouter-Title", OPENROUTER_APP_TITLE)
            .header("X-Title", OPENROUTER_APP_TITLE);
    }
    match resolve_upstream_bearer_token(llm, crate::credentials::resolve) {
        Ok(Some(token)) => request = request.bearer_auth(token),
        Ok(None) => {}
        Err(detail) => return Err(LlmSendError::NotConfigured(detail)),
    }
    llm_stream::send(
        request.json(payload),
        payload
            .get("stream")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        llm.timeout_ms,
        &llm.stream_timeouts,
    )
    .await
    .map_err(LlmSendError::Transport)
}

fn is_context_length_rejection(status: u16, body: &str) -> bool {
    if status != 400 && status != 413 && status != 422 {
        return false;
    }
    let body = body.to_ascii_lowercase();
    [
        "maximum context length",
        "max_model_len",
        "context length exceeded",
        "context window exceeded",
        "too many tokens",
        "input is too long",
        "prompt is too long",
    ]
    .iter()
    .any(|needle| body.contains(needle))
}

fn availability_overflow_enabled(llm: &config::LlmCfg) -> bool {
    llm.overflow
        .as_ref()
        .is_some_and(|overflow| overflow.fallback_on_unavailable)
}

fn primary_http_overflow_reason(
    llm: &config::LlmCfg,
    status: u16,
    body: &str,
) -> Option<&'static str> {
    llm.overflow.as_ref()?;
    if is_context_length_rejection(status, body) {
        return Some("context-rejection");
    }
    if availability_overflow_enabled(llm) && matches!(status, 500 | 502 | 503 | 504) {
        return Some("primary-unavailable");
    }
    None
}

fn add_llm_route_headers(response: &mut Response, decision: &LlmRouteDecision) {
    response.headers_mut().insert(
        "x-hutch-upstream-route",
        axum::http::HeaderValue::from_static(decision.route.as_str()),
    );
    if decision.route == LlmUpstreamRoute::Overflow {
        response.headers_mut().insert(
            "x-hutch-overflow-reason",
            axum::http::HeaderValue::from_static(decision.reason),
        );
    }
    if let Some(tokens) = decision.context_budget_tokens {
        if let Ok(value) = axum::http::HeaderValue::from_str(&tokens.to_string()) {
            response
                .headers_mut()
                .insert("x-hutch-context-budget-tokens", value);
        }
    }
}

async fn chat_completions(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: Result<Json<Value>, axum::extract::rejection::JsonRejection>,
) -> Response {
    let t0 = Instant::now();
    let tenant = match authenticate(&state, &headers).await {
        Ok(t) => t,
        Err(e) => return e.into_response(),
    };
    let original_payload = match body {
        Ok(Json(v)) => v,
        Err(e) => return HutchError::bad_request(e.body_text()).into_response(),
    };
    let model = original_payload
        .get("model")
        .and_then(|m| m.as_str())
        .unwrap_or_default()
        .to_string();
    let llm = match state.cfg.llm(&model) {
        Some(l) => l.clone(),
        None => return HutchError::unknown_model(&model).into_response(),
    };
    let invocation_id = new_invocation_id();
    let label = format!("llm:{}", llm.name);
    let is_stream = original_payload
        .get("stream")
        .and_then(|s| s.as_bool())
        .unwrap_or(false);

    let refuse = |err: HutchError, code: &'static str, model_version: &str| {
        state.meter.record(MeterRow {
            invocation_id: Some(&invocation_id),
            tenant: &tenant.id,
            backend: &label,
            stream: is_stream,
            n_inputs: 1,
            status: err.status.as_u16(),
            error_code: Some(code),
            duration_ms: t0.elapsed().as_secs_f64() * 1000.0,
            upstream_ms: None,
            model_version,
            reference_cost_nanousd: 0,
            prompt_tokens: None,
            completion_tokens: None,
        });
        err.into_response()
    };
    if tenant.status == TenantStatus::Expired {
        return refuse(
            HutchError::subscription_expired(&tenant.id),
            "subscription_expired",
            &llm.model_version,
        );
    }
    if !tenant.entitlements.iter().any(|e| e == &llm.entitlement) {
        return refuse(
            HutchError::not_entitled(&tenant.id, &llm.name, &llm.entitlement),
            "not_entitled",
            &llm.model_version,
        );
    }

    let mut decision = choose_llm_route(&state, &llm, &original_payload).await;
    let mut active_llm = if decision.route == LlmUpstreamRoute::Overflow {
        materialize_overflow_llm(&llm).expect("overflow decision requires overflow config")
    } else {
        llm.clone()
    };
    if decision.route == LlmUpstreamRoute::Overflow {
        tracing::info!(
            model = llm.name,
            reason = decision.reason,
            prompt_tokens = decision.prompt_tokens,
            context_budget_tokens = decision.context_budget_tokens,
            "routing long-context LLM request to overflow upstream"
        );
    }
    let mut payload = original_payload.clone();
    route_llm_payload(&mut payload, &active_llm, &tenant.id);
    let mut cache_affinity = route_cache_identity(&mut payload, &active_llm, &tenant.id, &headers);

    let mut lane_scope = if decision.route == LlmUpstreamRoute::Overflow {
        format!("{}::overflow::{}", tenant.id, llm.name)
    } else {
        tenant.id.clone()
    };
    let mut lane_limit = if decision.route == LlmUpstreamRoute::Overflow {
        llm.overflow
            .as_ref()
            .map(|overflow| overflow.max_concurrent)
            .unwrap_or(tenant.lanes)
    } else {
        tenant.lanes
    };
    let permit = state
        .lanes
        .acquire(&lane_scope, lane_limit, state.cfg.lane_grace_ms)
        .await;
    let mut lane_permit = match permit {
        Some(p) => p,
        None => {
            return refuse(
                HutchError::lanes_saturated(&tenant.id, lane_limit),
                "lanes_saturated",
                &active_llm.model_version,
            )
        }
    };

    let mut attempt_no = 1usize;
    let mut resp = match send_llm_upstream(&state, &active_llm, &payload).await {
        Ok(response) => response,
        Err(error) => {
            record_provider_attempt(
                &state,
                &active_llm,
                &ProviderUsageObservation::default(),
                ProviderAttemptContext {
                    tenant: &tenant.id,
                    invocation_id: &invocation_id,
                    attempt_no,
                    mode: if is_stream { "stream" } else { "response" },
                    status: 0,
                    cost_source: "unavailable",
                },
            );
            let (detail, code, transport_failure) = match error {
                LlmSendError::NotConfigured(detail) => (detail, "upstream_not_configured", false),
                LlmSendError::Transport(detail) => (detail, "upstream_error", true),
            };
            if decision.route == LlmUpstreamRoute::Primary
                && transport_failure
                && availability_overflow_enabled(&llm)
            {
                drop(lane_permit);
                decision.route = LlmUpstreamRoute::Overflow;
                decision.reason = "primary-transport";
                active_llm = materialize_overflow_llm(&llm)
                    .expect("availability fallback requires overflow config");
                payload = original_payload.clone();
                route_llm_payload(&mut payload, &active_llm, &tenant.id);
                cache_affinity =
                    route_cache_identity(&mut payload, &active_llm, &tenant.id, &headers);
                lane_scope = format!("{}::overflow::{}", tenant.id, llm.name);
                lane_limit = llm
                    .overflow
                    .as_ref()
                    .map(|overflow| overflow.max_concurrent)
                    .unwrap_or(tenant.lanes);
                lane_permit = match state
                    .lanes
                    .acquire(&lane_scope, lane_limit, state.cfg.lane_grace_ms)
                    .await
                {
                    Some(permit) => permit,
                    None => {
                        return refuse(
                            HutchError::lanes_saturated(&tenant.id, lane_limit),
                            "overflow_lanes_saturated",
                            &active_llm.model_version,
                        )
                    }
                };
                attempt_no += 1;
                tracing::warn!(
                    model = llm.name,
                    error = detail,
                    "primary LLM transport unavailable; retrying through overflow upstream"
                );
                match send_llm_upstream(&state, &active_llm, &payload).await {
                    Ok(response) => response,
                    Err(error) => {
                        record_provider_attempt(
                            &state,
                            &active_llm,
                            &ProviderUsageObservation::default(),
                            ProviderAttemptContext {
                                tenant: &tenant.id,
                                invocation_id: &invocation_id,
                                attempt_no,
                                mode: if is_stream { "stream" } else { "response" },
                                status: 0,
                                cost_source: "unavailable",
                            },
                        );
                        let (detail, code) = match error {
                            LlmSendError::NotConfigured(detail) => {
                                (detail, "upstream_not_configured")
                            }
                            LlmSendError::Transport(detail) => (detail, "upstream_error"),
                        };
                        return refuse(
                            HutchError::upstream(&llm.name, detail),
                            code,
                            &active_llm.model_version,
                        );
                    }
                }
            } else {
                return refuse(
                    HutchError::upstream(&llm.name, detail),
                    code,
                    &active_llm.model_version,
                );
            }
        }
    };

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body = resp.text().await.unwrap_or_default();
        let observation = serde_json::from_str::<Value>(&body)
            .ok()
            .map(|value| provider_usage_from_response(&value))
            .unwrap_or_default();
        record_provider_attempt(
            &state,
            &active_llm,
            &observation,
            ProviderAttemptContext {
                tenant: &tenant.id,
                invocation_id: &invocation_id,
                attempt_no,
                mode: if is_stream { "stream" } else { "response" },
                status,
                cost_source: if observation.provider_cost_nanousd.is_some() {
                    "inline_error"
                } else {
                    "unavailable"
                },
            },
        );
        let fallback_reason = (decision.route == LlmUpstreamRoute::Primary)
            .then(|| primary_http_overflow_reason(&llm, status, &body))
            .flatten();
        if let Some(fallback_reason) = fallback_reason {
            drop(lane_permit);
            decision.route = LlmUpstreamRoute::Overflow;
            decision.reason = fallback_reason;
            active_llm =
                materialize_overflow_llm(&llm).expect("context fallback requires overflow config");
            payload = original_payload.clone();
            route_llm_payload(&mut payload, &active_llm, &tenant.id);
            cache_affinity = route_cache_identity(&mut payload, &active_llm, &tenant.id, &headers);
            lane_scope = format!("{}::overflow::{}", tenant.id, llm.name);
            lane_limit = llm
                .overflow
                .as_ref()
                .map(|overflow| overflow.max_concurrent)
                .unwrap_or(tenant.lanes);
            lane_permit = match state
                .lanes
                .acquire(&lane_scope, lane_limit, state.cfg.lane_grace_ms)
                .await
            {
                Some(permit) => permit,
                None => {
                    return refuse(
                        HutchError::lanes_saturated(&tenant.id, lane_limit),
                        "overflow_lanes_saturated",
                        &active_llm.model_version,
                    )
                }
            };
            attempt_no += 1;
            tracing::warn!(
                model = llm.name,
                status,
                reason = fallback_reason,
                "primary LLM request failed; retrying through overflow upstream"
            );
            resp = match send_llm_upstream(&state, &active_llm, &payload).await {
                Ok(response) => response,
                Err(error) => {
                    record_provider_attempt(
                        &state,
                        &active_llm,
                        &ProviderUsageObservation::default(),
                        ProviderAttemptContext {
                            tenant: &tenant.id,
                            invocation_id: &invocation_id,
                            attempt_no,
                            mode: if is_stream { "stream" } else { "response" },
                            status: 0,
                            cost_source: "unavailable",
                        },
                    );
                    let (detail, code) = match error {
                        LlmSendError::NotConfigured(detail) => (detail, "upstream_not_configured"),
                        LlmSendError::Transport(detail) => (detail, "upstream_error"),
                    };
                    return refuse(
                        HutchError::upstream(&llm.name, detail),
                        code,
                        &active_llm.model_version,
                    );
                }
            };
        } else {
            let head: String = body.chars().take(300).collect();
            return refuse(
                HutchError::upstream(&llm.name, format!("HTTP {status}: {head}")),
                "upstream_error",
                &active_llm.model_version,
            );
        }
    }

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body = resp.text().await.unwrap_or_default();
        let observation = serde_json::from_str::<Value>(&body)
            .ok()
            .map(|value| provider_usage_from_response(&value))
            .unwrap_or_default();
        record_provider_attempt(
            &state,
            &active_llm,
            &observation,
            ProviderAttemptContext {
                tenant: &tenant.id,
                invocation_id: &invocation_id,
                attempt_no,
                mode: if is_stream { "stream" } else { "response" },
                status,
                cost_source: if observation.provider_cost_nanousd.is_some() {
                    "inline_error"
                } else {
                    "unavailable"
                },
            },
        );
        let head: String = body.chars().take(300).collect();
        return refuse(
            HutchError::upstream(&llm.name, format!("HTTP {status}: {head}")),
            "upstream_error",
            &active_llm.model_version,
        );
    }

    if is_stream {
        // OpenRouter includes usage and actual cost in the final SSE event.
        // Observe complete data lines while yielding every upstream byte
        // unchanged. The guard also persists a pending reconciliation row if
        // the browser disconnects before the final event arrives.
        let content_type = resp
            .headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("text/event-stream")
            .to_string();
        let upstream_stream = resp.bytes_stream();
        let stream_state = state.clone();
        let stream_llm = active_llm.clone();
        let stream_tenant = tenant.id.clone();
        let stream_invocation_id = invocation_id.clone();
        let stream_label = label.clone();
        let metered_stream = async_stream::stream! {
            let _lane_permit = lane_permit;
            let mut upstream_stream = Box::pin(upstream_stream);
            let mut guard = StreamingMeterGuard {
                state: stream_state,
                llm: stream_llm,
                tenant: stream_tenant,
                invocation_id: stream_invocation_id,
                label: stream_label,
                attempt_no,
                started: t0,
                observer: OpenAiSseUsageObserver::default(),
                error_code: None,
                recorded: false,
            };
            loop {
                let item = match llm_stream::next(&mut upstream_stream, guard.llm.stream_timeouts.idle_ms).await {
                    Ok(Some(item)) => item,
                    Ok(None) => break,
                    Err(_) => {
                        guard.error_code = Some("stream_idle_timeout");
                        yield Err(std::io::Error::new(std::io::ErrorKind::TimedOut, "upstream stream inactive"));
                        break;
                    }
                };
                match item {
                    Ok(bytes) => {
                        guard.observe(&bytes);
                        yield Ok::<_, std::io::Error>(bytes);
                    }
                    Err(error) => {
                        guard.mark_transport_error();
                        if error.is_timeout() {
                            guard.error_code = Some("stream_total_timeout");
                        }
                        yield Err(std::io::Error::other(error));
                        break;
                    }
                }
            }
            guard.finish();
        };
        let mut builder = axum::http::Response::builder()
            .status(StatusCode::OK)
            .header("content-type", content_type);
        if let Ok(v) = axum::http::HeaderValue::from_str(&active_llm.model_version) {
            builder = builder.header("x-hutch-model-version", v);
        }
        let mut response = builder
            .body(axum::body::Body::from_stream(metered_stream))
            .expect("stream response build");
        add_llm_route_headers(&mut response, &decision);
        response.headers_mut().insert(
            "x-hutch-cache-affinity",
            axum::http::HeaderValue::from_static(cache_affinity),
        );
        return response;
    }

    let mut out: Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => {
            record_provider_attempt(
                &state,
                &active_llm,
                &ProviderUsageObservation::default(),
                ProviderAttemptContext {
                    tenant: &tenant.id,
                    invocation_id: &invocation_id,
                    attempt_no,
                    mode: "response",
                    status: 502,
                    cost_source: "unavailable",
                },
            );
            return refuse(
                HutchError::upstream(&llm.name, format!("bad upstream JSON: {e}")),
                "upstream_error",
                &active_llm.model_version,
            );
        }
    };
    let first_observation = provider_usage_from_response(&out);
    record_provider_attempt(
        &state,
        &active_llm,
        &first_observation,
        ProviderAttemptContext {
            tenant: &tenant.id,
            invocation_id: &invocation_id,
            attempt_no,
            mode: "response",
            status: 200,
            cost_source: if first_observation.provider_cost_nanousd.is_some() {
                "inline_response"
            } else {
                "pending"
            },
        },
    );
    let mut final_observation = first_observation;
    if !llm_response_has_answer(&out) {
        attempt_no += 1;
        let retried = match send_llm_upstream(&state, &active_llm, &payload).await {
            Ok(response) => response,
            Err(error) => {
                record_provider_attempt(
                    &state,
                    &active_llm,
                    &ProviderUsageObservation::default(),
                    ProviderAttemptContext {
                        tenant: &tenant.id,
                        invocation_id: &invocation_id,
                        attempt_no,
                        mode: "response",
                        status: 0,
                        cost_source: "unavailable",
                    },
                );
                let (detail, code) = match error {
                    LlmSendError::NotConfigured(detail) => (detail, "upstream_not_configured"),
                    LlmSendError::Transport(detail) => (detail, "upstream_error"),
                };
                return refuse(
                    HutchError::upstream(&llm.name, format!("retry failed: {detail}")),
                    code,
                    &active_llm.model_version,
                );
            }
        };
        if !retried.status().is_success() {
            let status = retried.status().as_u16();
            let body = retried.text().await.unwrap_or_default();
            let observation = serde_json::from_str::<Value>(&body)
                .ok()
                .map(|value| provider_usage_from_response(&value))
                .unwrap_or_default();
            record_provider_attempt(
                &state,
                &active_llm,
                &observation,
                ProviderAttemptContext {
                    tenant: &tenant.id,
                    invocation_id: &invocation_id,
                    attempt_no,
                    mode: "response",
                    status,
                    cost_source: if observation.provider_cost_nanousd.is_some() {
                        "inline_error"
                    } else {
                        "unavailable"
                    },
                },
            );
            let head: String = body.chars().take(300).collect();
            return refuse(
                HutchError::upstream(&llm.name, format!("retry HTTP {status}: {head}")),
                "upstream_error",
                &active_llm.model_version,
            );
        }
        out = match retried.json().await {
            Ok(v) => v,
            Err(e) => {
                record_provider_attempt(
                    &state,
                    &active_llm,
                    &ProviderUsageObservation::default(),
                    ProviderAttemptContext {
                        tenant: &tenant.id,
                        invocation_id: &invocation_id,
                        attempt_no,
                        mode: "response",
                        status: 502,
                        cost_source: "unavailable",
                    },
                );
                return refuse(
                    HutchError::upstream(&llm.name, format!("bad retry JSON: {e}")),
                    "upstream_error",
                    &active_llm.model_version,
                );
            }
        };
        final_observation = provider_usage_from_response(&out);
        record_provider_attempt(
            &state,
            &active_llm,
            &final_observation,
            ProviderAttemptContext {
                tenant: &tenant.id,
                invocation_id: &invocation_id,
                attempt_no,
                mode: "response",
                status: 200,
                cost_source: if final_observation.provider_cost_nanousd.is_some() {
                    "inline_response"
                } else {
                    "pending"
                },
            },
        );
        if !llm_response_has_answer(&out) {
            return refuse(
                HutchError::upstream(&llm.name, "upstream returned no answer after retry".into()),
                "upstream_error",
                &active_llm.model_version,
            );
        }
    }
    let duration_ms = t0.elapsed().as_secs_f64() * 1000.0;
    state.meter.record(MeterRow {
        invocation_id: Some(&invocation_id),
        tenant: &tenant.id,
        backend: &label,
        stream: false,
        n_inputs: 1,
        status: 200,
        error_code: None,
        duration_ms,
        upstream_ms: Some(duration_ms),
        model_version: &active_llm.model_version,
        reference_cost_nanousd: reference_cost_nanousd(
            &active_llm,
            final_observation.prompt_tokens,
            final_observation.completion_tokens,
        ),
        prompt_tokens: final_observation.prompt_tokens,
        completion_tokens: final_observation.completion_tokens,
    });
    // The OpenAI-compatible client contract names the managed Clover service,
    // not whichever upstream model currently implements it. Exact upstream
    // provenance remains in the private meter and x-hutch-model-version.
    if let Some(object) = out.as_object_mut() {
        object.insert("model".into(), Value::String(llm.name.clone()));
        object.insert("hutch_upstream_model".into(), json!(llm.upstream_model));
    }
    let mut resp_out = Json(out).into_response();
    if let Ok(v) = axum::http::HeaderValue::from_str(&active_llm.model_version) {
        resp_out.headers_mut().insert("x-hutch-model-version", v);
    }
    add_llm_route_headers(&mut resp_out, &decision);
    resp_out.headers_mut().insert(
        "x-hutch-cache-affinity",
        axum::http::HeaderValue::from_static(cache_affinity),
    );
    resp_out
}

fn route_cache_identity(
    payload: &mut Value,
    llm: &config::LlmCfg,
    tenant: &str,
    headers: &HeaderMap,
) -> &'static str {
    // Only this upstream documents these fields. A fallback to a local/strict
    // compat endpoint must not receive injected cache metadata.
    if !is_openrouter_upstream(llm) {
        return "unsupported";
    }
    let Some(object) = payload.as_object_mut() else {
        return "none";
    };
    if object.contains_key("session_id") {
        return "caller";
    }
    let bounded_header = |name: &str| {
        headers
            .get(name)
            .and_then(|v| v.to_str().ok())
            .filter(|s| !s.is_empty() && s.len() <= 256 && !s.chars().any(char::is_control))
    };
    if let Some(id) = bounded_header("x-session-id") {
        object.insert("session_id".into(), json!(id));
        return "caller";
    }
    if object.contains_key("prompt_cache_key") {
        return "caller";
    }
    let Some(scope) = bounded_header("x-hutch-cache-scope") else {
        return "none";
    };
    // Scope from Hermes is already opaque. Include the AUTHENTICATED tenant,
    // not the body user label, so shared upstream credentials cannot combine
    // independent customers into the same generated routing bucket.
    use sha2::{Digest, Sha256};
    let key = hex::encode(Sha256::digest(
        serde_json::to_vec(&json!(["hutch-cache-v1", tenant, scope])).unwrap(),
    ));
    object.insert("session_id".into(), json!(key));
    object.insert("prompt_cache_key".into(), json!(key));
    "scoped"
}

fn route_llm_payload(payload: &mut Value, llm: &config::LlmCfg, tenant_id: &str) {
    let supplied_user = payload
        .get("user")
        .and_then(Value::as_str)
        .and_then(bounded_tracking_user);
    if let Some(object) = payload.as_object_mut() {
        for (key, value) in &llm.request_defaults {
            if key != "model" {
                match object.get_mut(key) {
                    Some(current) => merge_json_defaults(current, value),
                    None => {
                        object.insert(key.clone(), value.clone());
                    }
                }
            }
        }
        for (key, value) in &llm.request_overrides {
            if key != "model" {
                match object.get_mut(key) {
                    Some(current) => merge_json_overrides(current, value),
                    None => {
                        object.insert(key.clone(), value.clone());
                    }
                }
            }
        }
        object.insert("model".into(), json!(llm.upstream_model));
        // Preserve pg_rvbbit's human caller when present; direct Hutch clients
        // still get a stable tenant identifier. This field is provider cost
        // telemetry only and is never an entitlement/auth input in Hutch.
        if is_openrouter_upstream(llm) {
            let tracking_user = supplied_user.unwrap_or_else(|| {
                bounded_tracking_user(&format!("clover-tenant:{tenant_id}"))
                    .unwrap_or_else(|| "clover-tenant".to_string())
            });
            object.insert("user".into(), Value::String(tracking_user));
        }
    }
    // Resolve the alias FIRST, including overflow routing. Hermes may only
    // know a name such as `calliope-sonnet` and cannot infer cache capability.
    // OpenRouter translates this automatic trailing breakpoint for Claude
    // providers (including Claude Platform on AWS). Preserve explicit plans.
    // https://openrouter.ai/docs/guides/best-practices/prompt-caching
    let model = llm
        .upstream_model
        .trim_start_matches('~')
        .to_ascii_lowercase();
    if llm.prompt_caching != Some(false)
        && is_openrouter_upstream(llm)
        && model.starts_with("anthropic/claude-")
        && !has_cache_policy(payload)
    {
        if let Some(object) = payload.as_object_mut() {
            object.insert("cache_control".into(), json!({"type": "ephemeral"}));
        }
    }
}

fn has_cache_policy(value: &Value) -> bool {
    fn marker(v: &Value) -> bool {
        v.get("cache_control").is_some() || v.get("prompt_cache_breakpoint").is_some()
    }
    // Inspect wire locations only: a tool's JSON schema may legitimately
    // describe a property named cache_control without setting cache policy.
    marker(value)
        || value.get("prompt_cache_options").is_some()
        || value
            .get("messages")
            .and_then(Value::as_array)
            .is_some_and(|messages| {
                messages.iter().any(|message| {
                    marker(message)
                        || message
                            .get("content")
                            .and_then(Value::as_array)
                            .is_some_and(|blocks| blocks.iter().any(marker))
                })
            })
        || value
            .get("tools")
            .and_then(Value::as_array)
            .is_some_and(|tools| {
                tools
                    .iter()
                    .any(|tool| marker(tool) || tool.get("function").is_some_and(marker))
            })
}

fn bounded_tracking_user(value: &str) -> Option<String> {
    let value = value.trim();
    if value.is_empty() || value.chars().count() > 254 || value.chars().any(char::is_control) {
        return None;
    }
    Some(value.to_string())
}

fn is_openrouter_upstream(llm: &config::LlmCfg) -> bool {
    llm_provider(llm) == "openrouter"
}

async fn provider_maintenance_loop(state: Arc<AppState>) {
    if state.cfg.llms.is_empty() {
        return;
    }
    let reconcile_every =
        std::time::Duration::from_secs(state.cfg.provider_cost_reconcile_secs.max(1));
    let price_every = std::time::Duration::from_secs(state.cfg.provider_price_refresh_secs.max(60));
    let mut last_price_refresh: Option<Instant> = None;
    loop {
        reconcile_pending_provider_costs(&state).await;
        if last_price_refresh.is_none_or(|last| last.elapsed() >= price_every) {
            refresh_provider_prices(&state).await;
            last_price_refresh = Some(Instant::now());
        }
        tokio::time::sleep(reconcile_every).await;
    }
}

fn configured_provider_route(
    state: &AppState,
    canonical_model: &str,
    provider: &str,
    upstream_model: &str,
) -> Option<config::LlmCfg> {
    let primary = state.cfg.llm(canonical_model)?.clone();
    let mut candidates = vec![primary.clone()];
    if let Some(overflow) = materialize_overflow_llm(&primary) {
        candidates.push(overflow);
    }
    candidates
        .iter()
        .find(|candidate| {
            llm_provider(candidate) == provider
                && candidate.upstream_model.trim_start_matches('~')
                    == upstream_model.trim_start_matches('~')
        })
        .cloned()
        .or_else(|| {
            candidates
                .into_iter()
                .find(|candidate| llm_provider(candidate) == provider)
        })
}

async fn reconcile_pending_provider_costs(state: &AppState) {
    for pending in state.meter.pending_provider_costs(25) {
        if pending.provider != "openrouter" {
            state.meter.defer_provider_cost(
                pending.row_id,
                "no reconciliation adapter is configured for this provider",
            );
            continue;
        }
        let speech = state.cfg.speech.iter().find(|route| {
            route.name == pending.canonical_model && route.provider == pending.provider
        });
        let resolved = if let Some(route) = speech {
            crate::credentials::resolve(&route.upstream_bearer_token_env)
                .filter(|v| !v.trim().is_empty())
                .map(|token| {
                    (
                        format!("{}/generation", route.upstream_base.trim_end_matches('/')),
                        token,
                    )
                })
        } else {
            configured_provider_route(
                state,
                &pending.canonical_model,
                &pending.provider,
                &pending.upstream_model,
            )
            .and_then(|llm| {
                resolve_upstream_bearer_token(&llm, crate::credentials::resolve)
                    .ok()
                    .flatten()
                    .map(|token| {
                        (
                            format!("{}/v1/generation", llm.upstream_base.trim_end_matches('/')),
                            token,
                        )
                    })
            })
        };
        let Some((url, token)) = resolved else {
            state.meter.defer_provider_cost(
                pending.row_id,
                "provider route or credential is not configured",
            );
            continue;
        };
        let response = state
            .http
            .get(url)
            .bearer_auth(token)
            .query(&[("id", pending.provider_request_id.as_str())])
            .timeout(std::time::Duration::from_secs(30))
            .send()
            .await;
        let response = match response {
            Ok(response) => response,
            Err(error) => {
                state
                    .meter
                    .defer_provider_cost(pending.row_id, &error.to_string());
                continue;
            }
        };
        let status = response.status();
        let value: Value = match response.json().await {
            Ok(value) => value,
            Err(error) => {
                state.meter.defer_provider_cost(
                    pending.row_id,
                    &format!("generation metadata HTTP {status}: {error}"),
                );
                continue;
            }
        };
        if !status.is_success() {
            let detail = value
                .pointer("/error/message")
                .and_then(Value::as_str)
                .unwrap_or("generation metadata unavailable");
            state.meter.defer_provider_cost(
                pending.row_id,
                &format!("generation metadata HTTP {status}: {detail}"),
            );
            continue;
        }
        let data = value.get("data").unwrap_or(&value);
        let cost = data
            .get("total_cost")
            .or_else(|| data.get("usage"))
            .and_then(usd_to_nanousd);
        let Some(cost) = cost else {
            state
                .meter
                .defer_provider_cost(pending.row_id, "generation metadata omitted total cost");
            continue;
        };
        let prompt_tokens = data
            .get("native_tokens_prompt")
            .or_else(|| data.get("tokens_prompt"))
            .and_then(Value::as_i64);
        let completion_tokens = data
            .get("native_tokens_completion")
            .or_else(|| data.get("tokens_completion"))
            .and_then(Value::as_i64);
        state.meter.reconcile_provider_cost(
            &pending,
            if speech.is_some() {
                None
            } else {
                prompt_tokens
            },
            if speech.is_some() {
                None
            } else {
                completion_tokens
            },
            cost,
        );
        tracing::debug!(
            provider = pending.provider,
            upstream_model = pending.upstream_model,
            "reconciled provider cost"
        );
    }
}

async fn refresh_provider_prices(state: &AppState) {
    let provider_routes: Vec<config::LlmCfg> = state
        .cfg
        .llms
        .iter()
        .flat_map(|llm| std::iter::once(llm.clone()).chain(materialize_overflow_llm(llm)))
        .filter(is_openrouter_upstream)
        .collect();
    let Some(anchor) = provider_routes.first() else {
        return;
    };
    let token = match resolve_upstream_bearer_token(anchor, crate::credentials::resolve) {
        Ok(Some(token)) => token,
        Ok(None) => return,
        Err(detail) => {
            tracing::warn!("provider price refresh skipped: {detail}");
            return;
        }
    };
    let url = format!("{}/v1/models", anchor.upstream_base.trim_end_matches('/'));
    let response = state
        .http
        .get(url)
        .bearer_auth(token)
        .query(&[("limit", "500")])
        .timeout(std::time::Duration::from_secs(30))
        .send()
        .await;
    let response = match response {
        Ok(response) => response,
        Err(error) => {
            tracing::warn!("provider price refresh failed: {error}");
            return;
        }
    };
    let status = response.status();
    let value: Value = match response.json().await {
        Ok(value) => value,
        Err(error) => {
            tracing::warn!("provider price refresh HTTP {status} returned bad JSON: {error}");
            return;
        }
    };
    if !status.is_success() {
        let detail = value
            .pointer("/error/message")
            .and_then(Value::as_str)
            .unwrap_or("catalog unavailable");
        tracing::warn!("provider price refresh HTTP {status}: {detail}");
        return;
    }
    let Some(models) = value.get("data").and_then(Value::as_array) else {
        tracing::warn!("provider price refresh omitted the model list");
        return;
    };
    for llm in &provider_routes {
        let public_upstream_model = llm.upstream_model.trim_start_matches('~');
        let Some((model, alias_resolved)) = openrouter_catalog_model(models, public_upstream_model)
        else {
            tracing::warn!(
                model = public_upstream_model,
                "provider price catalog has no exact model entry"
            );
            continue;
        };
        let prompt = model.pointer("/pricing/prompt").and_then(numeric_value);
        let completion = model.pointer("/pricing/completion").and_then(numeric_value);
        let (Some(prompt), Some(completion)) = (prompt, completion) else {
            tracing::warn!(
                model = public_upstream_model,
                "provider price catalog entry omitted token rates"
            );
            continue;
        };
        state.meter.record_provider_price(ProviderPriceSnapshot {
            provider: "openrouter",
            upstream_model: public_upstream_model,
            prompt_usd_per_token: prompt,
            completion_usd_per_token: completion,
            source: if alias_resolved {
                "openrouter-models-alias"
            } else {
                "openrouter-models"
            },
        });
        if alias_resolved {
            tracing::debug!(
                model = public_upstream_model,
                resolved_model = model
                    .get("id")
                    .and_then(|value| value.as_str())
                    .unwrap_or("unknown"),
                "resolved provider price alias"
            );
        }
    }
}

/// OpenRouter's convenience `-latest` aliases are accepted for generation but
/// are not separate `/models` records. Resolve those only when the catalog has
/// one unambiguous newest model in the same named family. The configured alias
/// remains the gauge key; this is advisory pricing, never invoice accounting.
fn openrouter_catalog_model<'a>(models: &'a [Value], requested: &str) -> Option<(&'a Value, bool)> {
    if let Some(exact) = models.iter().find(|model| {
        model.get("id").and_then(Value::as_str) == Some(requested)
            || model.get("canonical_slug").and_then(Value::as_str) == Some(requested)
    }) {
        return Some((exact, false));
    }
    let family_prefix = requested.strip_suffix("latest")?;
    models
        .iter()
        .filter(|model| {
            model
                .get("id")
                .and_then(Value::as_str)
                .is_some_and(|id| id.starts_with(family_prefix))
        })
        .max_by_key(|model| model.get("created").and_then(Value::as_i64).unwrap_or(0))
        .map(|model| (model, true))
}

fn llm_response_has_answer(out: &Value) -> bool {
    let Some(message) = out.pointer("/choices/0/message") else {
        return false;
    };
    let has_content = match message.get("content") {
        Some(Value::String(_)) => true,
        Some(Value::Array(parts)) => !parts.is_empty(),
        _ => false,
    };
    let has_tool_calls = message
        .get("tool_calls")
        .and_then(Value::as_array)
        .is_some_and(|calls| !calls.is_empty());
    let has_refusal = message
        .get("refusal")
        .and_then(Value::as_str)
        .is_some_and(|refusal| !refusal.is_empty());
    has_content || has_tool_calls || has_refusal
}

fn merge_json_defaults(target: &mut Value, defaults: &Value) {
    let (Some(target), Some(defaults)) = (target.as_object_mut(), defaults.as_object()) else {
        return;
    };
    for (key, value) in defaults {
        match target.get_mut(key) {
            Some(current) => merge_json_defaults(current, value),
            None => {
                target.insert(key.clone(), value.clone());
            }
        }
    }
}

fn merge_json_overrides(target: &mut Value, overrides: &Value) {
    match (target.as_object_mut(), overrides.as_object()) {
        (Some(target), Some(overrides)) => {
            for (key, value) in overrides {
                match target.get_mut(key) {
                    Some(current) => merge_json_overrides(current, value),
                    None => {
                        target.insert(key.clone(), value.clone());
                    }
                }
            }
        }
        _ => *target = overrides.clone(),
    }
}

/// Resolve a hosted LLM credential without ever putting its value in config,
/// logs, or error messages.  The injected lookup keeps the behavior directly
/// testable without mutating process-global environment variables.
fn resolve_upstream_bearer_token<F>(
    llm: &config::LlmCfg,
    lookup: F,
) -> Result<Option<String>, String>
where
    F: FnOnce(&str) -> Option<String>,
{
    let Some(raw_env_name) = llm.upstream_bearer_token_env.as_deref() else {
        return Ok(None);
    };
    let env_name = raw_env_name.trim();
    if env_name.is_empty() {
        return Err("upstream_bearer_token_env is configured but blank".into());
    }
    let token = lookup(env_name)
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| format!("credential environment variable '{env_name}' is unset or empty"))?;
    Ok(Some(token))
}

#[cfg(test)]
mod llm_upstream_tests {
    use super::*;

    fn llm(env_name: Option<&str>) -> config::LlmCfg {
        config::LlmCfg {
            name: "clover".into(),
            aliases: vec!["gemma4".into()],
            entitlement: "clover_llm".into(),
            upstream_base: "https://openrouter.ai/api".into(),
            provider: Some("openrouter".into()),
            upstream_bearer_token_env: env_name.map(str::to_string),
            upstream_model: "~deepseek/deepseek-v4-flash-latest".into(),
            model_version: "openrouter/deepseek-v4-flash-latest".into(),
            request_overrides: std::collections::BTreeMap::new(),
            request_defaults: std::collections::BTreeMap::new(),
            prompt_caching: None,
            prompt_microusd_per_1k: 90,
            completion_microusd_per_1k: 180,
            timeout_ms: 120_000,
            stream_timeouts: llm_stream::StreamTimeouts::default(),
            overflow: None,
        }
    }

    #[test]
    fn local_llm_needs_no_upstream_credential() {
        let got = resolve_upstream_bearer_token(&llm(None), |_| None).unwrap();
        assert_eq!(got, None);
    }

    #[test]
    fn cache_scope_is_stable_private_and_separate_per_tenant_and_agent() {
        let cfg = llm(None);
        let mut headers = HeaderMap::new();
        headers.insert("x-hutch-cache-scope", "opaque-agent-a".parse().unwrap());
        let original = json!({"messages":[{"role":"user","content":"task"}]});
        let mut a = original.clone();
        assert_eq!(
            route_cache_identity(&mut a, &cfg, "tenant-a", &headers),
            "scoped"
        );
        assert_eq!(a["session_id"], a["prompt_cache_key"]);
        assert_eq!(a["prompt_cache_key"].as_str().unwrap().len(), 64);
        assert_eq!(a["messages"], original["messages"]);
        let mut again = original.clone();
        route_cache_identity(&mut again, &cfg, "tenant-a", &headers);
        assert_eq!(a, again);
        let mut other = original.clone();
        route_cache_identity(&mut other, &cfg, "tenant-b", &headers);
        assert_ne!(a["session_id"], other["session_id"]);
        headers.insert("x-hutch-cache-scope", "opaque-agent-b".parse().unwrap());
        let mut child = original.clone();
        route_cache_identity(&mut child, &cfg, "tenant-a", &headers);
        assert_ne!(a["session_id"], child["session_id"]);
    }

    #[test]
    fn cache_identity_preserves_explicit_policy_and_does_not_leak_into_fallback() {
        let mut cfg = llm(None);
        let mut headers = HeaderMap::new();
        headers.insert("x-hutch-cache-scope", "opaque".parse().unwrap());
        for original in [
            json!({"session_id":"mine"}),
            json!({"prompt_cache_key":"mine"}),
        ] {
            let mut payload = original.clone();
            assert_eq!(
                route_cache_identity(&mut payload, &cfg, "tenant", &headers),
                "caller"
            );
            assert_eq!(payload, original);
        }
        headers.insert("x-session-id", "header-scope".parse().unwrap());
        let mut payload = json!({});
        assert_eq!(
            route_cache_identity(&mut payload, &cfg, "tenant", &headers),
            "caller"
        );
        assert_eq!(payload["session_id"], "header-scope");
        cfg.provider = Some("generic".into());
        cfg.upstream_base = "http://local:8000".into();
        let mut fallback = json!({}); // routing always starts from original request
        assert_eq!(
            route_cache_identity(&mut fallback, &cfg, "tenant", &headers),
            "unsupported"
        );
        assert_eq!(fallback, json!({}));
    }

    #[test]
    fn absent_or_invalid_scope_does_not_create_a_shared_cache_bucket() {
        let cfg = llm(None);
        let mut headers = HeaderMap::new();
        let mut payload = json!({});
        assert_eq!(
            route_cache_identity(&mut payload, &cfg, "tenant", &headers),
            "none"
        );
        headers.insert("x-hutch-cache-scope", "x".repeat(257).parse().unwrap());
        assert_eq!(
            route_cache_identity(&mut payload, &cfg, "tenant", &headers),
            "none"
        );
        assert_eq!(payload, json!({}));
    }

    #[test]
    fn claude_alias_enables_cache_after_route_resolution_without_changing_messages() {
        let mut cfg = llm(None);
        cfg.name = "calliope".into(); // no model family in the public alias
        cfg.upstream_model = "~anthropic/claude-sonnet-latest".into();
        let messages = json!([{"role":"system","content":"stable instructions"},
            {"role":"user","content":"task"}]);
        let mut payload = json!({"model":"calliope", "messages":messages, "stream":true});
        route_llm_payload(&mut payload, &cfg, "tenant");
        assert_eq!(payload["cache_control"], json!({"type":"ephemeral"}));
        assert_eq!(payload["messages"], messages);
        assert_eq!(payload["stream"], true);
        let once = payload.clone();
        route_llm_payload(&mut payload, &cfg, "tenant");
        assert_eq!(once, payload);
    }

    #[test]
    fn caching_respects_explicit_controls_and_operator_opt_out() {
        let mut cfg = llm(None);
        cfg.upstream_model = "anthropic/claude-sonnet-latest".into();
        for mut payload in [
            json!({"cache_control":{"type":"ephemeral","ttl":"1h"}}),
            json!({"messages":[{"role":"user","content":[{"type":"text","text":"cached", "cache_control":{"type":"ephemeral"}}]}]}),
            json!({"tools":[{"type":"function","function":{"name":"sql"},"cache_control":{"type":"ephemeral"}}]}),
            json!({"prompt_cache_options":{"mode":"explicit"}}),
        ] {
            let explicit = payload.get("cache_control").cloned();
            route_llm_payload(&mut payload, &cfg, "tenant");
            assert_eq!(payload.get("cache_control").cloned(), explicit);
        }
        cfg.prompt_caching = Some(false);
        let mut payload = json!({"messages":[]});
        route_llm_payload(&mut payload, &cfg, "tenant");
        assert!(payload.get("cache_control").is_none());
    }

    #[test]
    fn caching_does_not_guess_from_public_alias_or_tool_parameter_names() {
        let mut cfg = llm(None);
        cfg.name = "calliope-sonnet".into();
        let mut payload = json!({"messages":[]});
        route_llm_payload(&mut payload, &cfg, "tenant");
        assert!(payload.get("cache_control").is_none()); // resolved DeepSeek
        cfg.upstream_model = "anthropic/claude-sonnet-latest".into();
        cfg.provider = Some("generic".into());
        cfg.upstream_base = "http://local:8000".into();
        route_llm_payload(&mut payload, &cfg, "tenant");
        assert!(payload.get("cache_control").is_none());
        cfg.provider = Some("openrouter".into());
        let mut payload = json!({"tools":[{"type":"function", "function":{
            "name":"settings", "parameters":{"properties":{"cache_control":{"type":"string"}}}}}]});
        route_llm_payload(&mut payload, &cfg, "tenant");
        assert!(payload.get("cache_control").is_some());
    }

    #[test]
    fn hosted_llm_resolves_and_trims_credential() {
        let got = resolve_upstream_bearer_token(&llm(Some("OPENROUTER_API_KEY")), |name| {
            assert_eq!(name, "OPENROUTER_API_KEY");
            Some("  secret-token  ".into())
        })
        .unwrap();
        assert_eq!(got.as_deref(), Some("secret-token"));
    }

    #[test]
    fn hosted_llm_fails_closed_when_credential_is_missing() {
        let err =
            resolve_upstream_bearer_token(&llm(Some("OPENROUTER_API_KEY")), |_| None).unwrap_err();
        assert!(err.contains("OPENROUTER_API_KEY"));
        assert!(!err.contains("secret-token"));
    }

    #[test]
    fn hosted_llm_fails_closed_when_credential_name_is_blank() {
        let err = resolve_upstream_bearer_token(&llm(Some("  ")), |_| {
            panic!("blank environment name must not be looked up")
        })
        .unwrap_err();
        assert!(err.contains("configured but blank"));
    }

    #[test]
    fn hosted_llm_request_policy_preserves_explicit_values_and_forces_overrides() {
        let mut cfg = llm(Some("OPENROUTER_API_KEY"));
        cfg.request_defaults.insert("temperature".into(), json!(0));
        cfg.request_defaults.insert("seed".into(), json!(0));
        cfg.request_defaults.insert(
            "provider".into(),
            json!({"allow_fallbacks": true, "sort": "price"}),
        );
        cfg.request_overrides
            .insert("reasoning".into(), json!({"enabled": false}));
        cfg.request_overrides.insert(
            "provider".into(),
            json!({"zdr": true, "data_collection": "deny"}),
        );
        cfg.request_overrides
            .insert("model".into(), json!("must-not-win"));
        let mut payload = json!({
            "model": "clover",
            "reasoning": {"enabled": true},
            "temperature": 0.7,
            "provider": {"order": ["DeepInfra"], "allow_fallbacks": false},
            "messages": [{"role": "user", "content": "hello"}]
        });

        route_llm_payload(&mut payload, &cfg, "pilot-company");

        assert_eq!(payload["model"], "~deepseek/deepseek-v4-flash-latest");
        assert_eq!(payload["reasoning"]["enabled"], false);
        assert_eq!(payload["temperature"], 0.7);
        assert_eq!(payload["seed"], 0);
        assert_eq!(payload["provider"]["order"], json!(["DeepInfra"]));
        assert_eq!(payload["provider"]["allow_fallbacks"], false);
        assert_eq!(payload["provider"]["sort"], "price");
        assert_eq!(payload["provider"]["zdr"], true);
        assert_eq!(payload["provider"]["data_collection"], "deny");
        assert_eq!(payload["user"], "clover-tenant:pilot-company");
    }

    #[test]
    fn hosted_llm_preserves_bounded_human_tracking_user() {
        let cfg = llm(Some("OPENROUTER_API_KEY"));
        let mut payload = json!({
            "model": "gemma4",
            "user": "person@example.com",
            "messages": [{"role": "user", "content": "hello"}]
        });

        route_llm_payload(&mut payload, &cfg, "pilot-company");

        assert_eq!(payload["user"], "person@example.com");
        assert!(is_openrouter_upstream(&cfg));
    }

    #[test]
    fn overflow_materialization_keeps_public_identity_and_replaces_serving_route() {
        let mut cfg = llm(None);
        cfg.request_defaults.insert("temperature".into(), json!(0));
        cfg.overflow = Some(config::LlmOverflowCfg {
            context_threshold_tokens: 45_000,
            tokenize_path: Some("/tokenize".into()),
            tokenize_min_body_bytes: 32 * 1024,
            max_concurrent: 2,
            fallback_on_unavailable: true,
            upstream_base: "https://openrouter.ai/api".into(),
            provider: Some("openrouter".into()),
            upstream_bearer_token_env: Some("OPENROUTER_API_KEY".into()),
            upstream_model: "google/gemma-4-26b-a4b-it".into(),
            model_version: "openrouter/google/gemma-4-26b-a4b-it".into(),
            request_overrides: std::collections::BTreeMap::from([(
                "provider".into(),
                json!({"zdr": true, "sort": "throughput"}),
            )]),
            request_defaults: std::collections::BTreeMap::new(),
            prompt_microusd_per_1k: 100,
            completion_microusd_per_1k: 400,
            timeout_ms: 900_000,
        });

        let routed = materialize_overflow_llm(&cfg).expect("overflow route");
        assert_eq!(routed.name, "clover");
        assert_eq!(routed.aliases, vec!["gemma4"]);
        assert_eq!(routed.upstream_model, "google/gemma-4-26b-a4b-it");
        assert_eq!(routed.provider.as_deref(), Some("openrouter"));
        assert_eq!(routed.request_defaults["temperature"], 0);
        assert_eq!(routed.request_overrides["provider"]["zdr"], true);
        assert_eq!(routed.request_overrides["provider"]["sort"], "throughput");
        assert_eq!(routed.prompt_microusd_per_1k, 100);
        assert_eq!(routed.completion_microusd_per_1k, 400);
        assert!(routed.overflow.is_none());
    }

    #[test]
    fn output_budget_prefers_openai_max_completion_tokens() {
        assert_eq!(
            requested_output_tokens(&json!({
                "max_tokens": 100,
                "max_completion_tokens": 250
            })),
            250
        );
        assert_eq!(requested_output_tokens(&json!({"max_tokens": 100})), 100);
        assert_eq!(requested_output_tokens(&json!({})), 0);
    }

    #[test]
    fn only_structured_context_limit_errors_trigger_overflow_retry() {
        assert!(is_context_length_rejection(
            400,
            "This model's maximum context length is 49152 tokens"
        ));
        assert!(is_context_length_rejection(
            413,
            r#"{"error":{"message":"prompt is too long"}}"#
        ));
        assert!(!is_context_length_rejection(500, "maximum context length"));
        assert!(!is_context_length_rejection(400, "invalid temperature"));
    }

    #[test]
    fn availability_overflow_is_explicit_and_limited_to_server_failures() {
        let mut cfg = llm(None);
        cfg.overflow = Some(config::LlmOverflowCfg {
            context_threshold_tokens: 45_000,
            tokenize_path: None,
            tokenize_min_body_bytes: 32 * 1024,
            max_concurrent: 2,
            fallback_on_unavailable: true,
            upstream_base: "https://openrouter.ai/api".into(),
            provider: Some("openrouter".into()),
            upstream_bearer_token_env: Some("OPENROUTER_API_KEY".into()),
            upstream_model: "google/gemma-4-26b-a4b-it".into(),
            model_version: "openrouter/google/gemma-4-26b-a4b-it".into(),
            request_overrides: std::collections::BTreeMap::new(),
            request_defaults: std::collections::BTreeMap::new(),
            prompt_microusd_per_1k: 100,
            completion_microusd_per_1k: 400,
            timeout_ms: 900_000,
        });

        assert!(availability_overflow_enabled(&cfg));
        assert_eq!(
            primary_http_overflow_reason(&cfg, 503, "service unavailable"),
            Some("primary-unavailable")
        );
        assert_eq!(
            primary_http_overflow_reason(&cfg, 400, "maximum context length exceeded"),
            Some("context-rejection")
        );
        assert_eq!(
            primary_http_overflow_reason(&cfg, 400, "invalid request"),
            None
        );
        assert_eq!(primary_http_overflow_reason(&cfg, 429, "busy"), None);

        cfg.overflow
            .as_mut()
            .expect("overflow config")
            .fallback_on_unavailable = false;
        assert!(!availability_overflow_enabled(&cfg));
        assert_eq!(
            primary_http_overflow_reason(&cfg, 503, "service unavailable"),
            None
        );
    }

    #[test]
    fn hosted_llm_answer_validation_distinguishes_empty_from_tool_calls() {
        assert!(llm_response_has_answer(
            &json!({"choices":[{"message":{"content":"ready"}}]})
        ));
        assert!(llm_response_has_answer(
            &json!({"choices":[{"message":{"content":null,"tool_calls":[{"id":"call_1"}]}}]})
        ));
        assert!(llm_response_has_answer(
            &json!({"choices":[{"message":{"content":null,"refusal":"cannot comply"}}]})
        ));
        assert!(!llm_response_has_answer(&json!({"choices":[]})));
        assert!(!llm_response_has_answer(
            &json!({"choices":[{"message":{"content":null}}]})
        ));
        assert!(!llm_response_has_answer(
            &json!({"error":{"message":"temporary"}})
        ));
    }

    #[test]
    fn aggregates_composed_specialist_usage() {
        let usage = specialist_usage(&[
            json!({"usage":{"prompt_tokens":100,"completion_tokens":20,"cost_microusd":1200}}),
            json!({"usage":{"prompt_tokens":50,"completion_tokens":10,"cost_microusd":700}}),
            json!({"ok":false}),
        ]);
        assert_eq!(
            usage,
            SpecialistUsage {
                seen: true,
                prompt_tokens: 150,
                completion_tokens: 30,
                cost_microusd: 1900,
            }
        );
    }

    #[test]
    fn specialist_reported_cost_is_precise_and_distinguishes_unknown_and_child_cost() {
        assert_eq!(reported_specialist_cost(&[json!({"usage":{"cost":0.000014028}})]),Some(14028));
        assert_eq!(reported_specialist_cost(&[json!({"usage":{"cost":0.0}})]),Some(0));
        assert_eq!(reported_specialist_cost(&[json!({"usage":{"input_tokens":5}})]),None);
        assert_eq!(reported_specialist_cost(&[json!({"usage":{"cost":-1.0}})]),None);
        assert_eq!(reported_specialist_cost(&[json!({"usage":{"cost":0.01,"already_metered":true}})]),Some(0));
    }

    #[test]
    fn decisions_usage_dialect_is_metered() {
        // The Clover decisions service reports input_tokens / output_tokens and
        // a fractional USD cost; before 2026-09-18 this metered as zero.
        let usage = specialist_usage(&[
            json!({"answers":{},"usage":{"cost":0.000014028,"input_tokens":334,"output_tokens":20}}),
            json!({"answers":{},"usage":{"cost":0.000029736,"input_tokens":708,"output_tokens":140}}),
        ]);
        assert_eq!(
            usage,
            SpecialistUsage {
                seen: true,
                prompt_tokens: 1042,
                completion_tokens: 160,
                cost_microusd: 14 + 30,
            }
        );
        // Negative or non-finite costs never become charges.
        let bad = specialist_usage(&[json!({"usage":{"cost":-1.0,"input_tokens":5}})]);
        assert_eq!(bad.cost_microusd, 0);
        assert_eq!(bad.prompt_tokens, 5);
    }

    #[test]
    fn composed_child_cost_is_not_metered_twice() {
        let usage = specialist_usage(&[
            json!({"usage":{"cost":0.01,"input_tokens":200,"already_metered":true,"metered_by":"embed"}}),
            json!({"usage":{"cost":0.002,"input_tokens":10}}),
        ]);
        assert_eq!(usage.cost_microusd, 2000);
        assert_eq!(usage.prompt_tokens, 10);
        assert!(usage.seen);
    }

    #[test]
    fn ordinary_specialists_have_no_composed_usage() {
        assert_eq!(
            specialist_usage(&[json!({"score":0.9})]),
            SpecialistUsage::default()
        );
    }

    #[test]
    fn openai_embedding_input_normalizes_one_or_many_strings() {
        assert_eq!(
            normalize_embedding_inputs(json!("one")).unwrap(),
            vec![json!("one")]
        );
        assert_eq!(
            normalize_embedding_inputs(json!(["one", "two"])).unwrap(),
            vec![json!("one"), json!("two")]
        );
        assert!(normalize_embedding_inputs(json!([[1, 2, 3]])).is_err());
        assert!(normalize_embedding_inputs(json!({"text": "one"})).is_err());
    }

    #[test]
    fn openai_embedding_encoding_supports_float_and_sdk_base64() {
        assert_eq!(embedding_encoding(None).unwrap(), EmbeddingEncoding::Float);
        assert_eq!(
            embedding_encoding(Some("base64")).unwrap(),
            EmbeddingEncoding::Base64
        );
        assert!(embedding_encoding(Some("hex")).is_err());

        let encoded = encode_embedding(json!([1.0, -2.5]), EmbeddingEncoding::Base64).unwrap();
        let bytes = BASE64_STANDARD
            .decode(encoded.as_str().expect("base64 string"))
            .unwrap();
        assert_eq!(
            bytes,
            [1.0_f32.to_le_bytes(), (-2.5_f32).to_le_bytes()].concat()
        );
    }

    #[test]
    fn openai_embedding_data_preserves_batch_indices() {
        let data = embedding_data(
            vec![json!([0.1, 0.2]), json!([0.3, 0.4])],
            EmbeddingEncoding::Float,
        )
        .unwrap();
        assert_eq!(data[0]["object"], "embedding");
        assert_eq!(data[0]["index"], 0);
        assert_eq!(data[1]["index"], 1);
        assert_eq!(data[1]["embedding"], json!([0.3, 0.4]));
    }

    #[test]
    fn provider_usage_extracts_inline_actual_cost_without_float_drift() {
        let usage = provider_usage_from_response(&json!({
            "id": "gen-test",
            "model": "openai/gpt-6-luna",
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 45,
                "cost": 0.001234567
            }
        }));
        assert_eq!(usage.request_id.as_deref(), Some("gen-test"));
        assert_eq!(usage.upstream_model.as_deref(), Some("openai/gpt-6-luna"));
        assert_eq!(usage.prompt_tokens, Some(120));
        assert_eq!(usage.completion_tokens, Some(45));
        assert_eq!(usage.provider_cost_nanousd, Some(1_234_567));
    }

    #[test]
    fn streaming_usage_observer_handles_sse_chunk_boundaries() {
        let mut observer = OpenAiSseUsageObserver::default();
        observer.observe(
            b"data: {\"id\":\"gen-stream\",\"model\":\"openai/gpt-6-luna\",\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}\n\n",
        );
        observer.observe(b"data: {\"id\":\"gen-stream\",\"choices\":[],\"usa");
        observer.observe(
            b"ge\":{\"prompt_tokens\":10,\"completion_tokens\":2,\"cost\":0.000031}}\n\ndata: [DONE]\n\n",
        );
        observer.finish();

        assert_eq!(observer.usage.request_id.as_deref(), Some("gen-stream"));
        assert_eq!(observer.usage.prompt_tokens, Some(10));
        assert_eq!(observer.usage.completion_tokens, Some(2));
        assert_eq!(observer.usage.provider_cost_nanousd, Some(31_000));
    }

    #[test]
    fn reference_tariff_uses_exact_nanodollars_per_token() {
        let cfg = llm(Some("OPENROUTER_API_KEY"));
        assert_eq!(reference_cost_nanousd(&cfg, Some(100), Some(20)), 12_600);
        assert_eq!(llm_provider(&cfg), "openrouter");
    }

    #[test]
    fn provider_catalog_resolves_latest_alias_to_newest_family_member() {
        let models = vec![
            json!({"id":"deepseek/deepseek-v4-flash-0630","created":100}),
            json!({"id":"deepseek/deepseek-v4-flash-0731","created":200}),
            json!({"id":"deepseek/deepseek-v4","created":300}),
        ];
        let (model, alias_resolved) =
            openrouter_catalog_model(&models, "deepseek/deepseek-v4-flash-latest")
                .expect("latest alias");
        assert!(alias_resolved);
        assert_eq!(model["id"], "deepseek/deepseek-v4-flash-0731");

        let (exact, alias_resolved) =
            openrouter_catalog_model(&models, "deepseek/deepseek-v4-flash-0630")
                .expect("exact model");
        assert!(!alias_resolved);
        assert_eq!(exact["created"], 100);
    }
}
