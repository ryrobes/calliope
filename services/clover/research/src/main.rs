mod credentials;
mod config;
mod memory;
mod openrouter;
mod research;

use axum::{
    extract::{DefaultBodyLimit, State},
    http::{header, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use config::Config;
use futures_util::{stream, StreamExt};
use memory::MemoryStore;
use openrouter::OpenRouter;
use research::ResearchInput;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    sync::{
        atomic::{AtomicI64, AtomicU64, Ordering},
        Arc,
    },
    time::Instant,
};
use tokio::sync::Semaphore;
use tracing::info;

const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Clone)]
struct AppState {
    config: Config,
    model: OpenRouter,
    http: reqwest::Client,
    slots: Arc<Semaphore>,
    metrics: Arc<Metrics>,
    memory: MemoryStore,
}

#[derive(Default)]
struct Metrics {
    requests: AtomicU64,
    inputs: AtomicU64,
    successes: AtomicU64,
    failures: AtomicU64,
    prompt_tokens: AtomicI64,
    completion_tokens: AtomicI64,
    search_requests: AtomicI64,
    cost_microusd: AtomicI64,
    duration_micros: AtomicU64,
    cache_reuses: AtomicU64,
    snapshots_written: AtomicU64,
    synthesis_skips: AtomicU64,
}

#[derive(Debug)]
pub struct ServiceError {
    pub code: &'static str,
    pub message: String,
}

impl ServiceError {
    pub fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

#[derive(Deserialize)]
struct ResearchRequest {
    inputs: Vec<Value>,
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "clover_research=info".into()),
        )
        .init();

    let config = Config::from_env().unwrap_or_else(|error| {
        eprintln!("clover-research configuration error: {error}");
        std::process::exit(2);
    });
    let model = OpenRouter::new(
        config.openrouter_base.clone(),
        config.openrouter_api_key.clone(),
        config.model.clone(),
        config.request_timeout,
    )
    .unwrap_or_else(|error| {
        eprintln!("clover-research client error: {error}");
        std::process::exit(2);
    });
    let http = reqwest::Client::builder()
        .timeout(config.request_timeout)
        .user_agent("CloverResearch/0.1 (+https://github.com/ryrobes/calliope)")
        .build()
        .unwrap_or_else(|error| {
            eprintln!("clover-research HTTP client error: {error}");
            std::process::exit(2);
        });
    let bind = config.bind;
    let memory = MemoryStore::open(&config.postgres_dsn)
        .await
        .unwrap_or_else(|error| {
            eprintln!("clover-research memory error: {}", error.message);
            std::process::exit(2);
        });
    let state = AppState {
        slots: Arc::new(Semaphore::new(config.max_concurrency)),
        config,
        model,
        http,
        metrics: Arc::new(Metrics::default()),
        memory,
    };
    let app = Router::new()
        .route("/", get(root))
        .route("/health", get(health))
        .route("/metrics", get(metrics))
        .route("/v1/research", post(research))
        .layer(DefaultBodyLimit::max(256 * 1024))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind(bind)
        .await
        .unwrap_or_else(|error| panic!("cannot bind {bind}: {error}"));
    info!(%bind, version = VERSION, "Clover Research ready");
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .expect("Clover Research server failed");
}

async fn root(State(state): State<AppState>) -> Json<Value> {
    Json(json!({
        "service": "clover-research",
        "version": VERSION,
        "endpoint": "POST /v1/research",
        "contract": {
            "inputs": [
                "What changed in PostgreSQL 19?",
                {"question": "Compare these sources", "seed_urls": ["https://example.com/a"], "search": false}
            ]
        },
        "model": state.model.model(),
        "discovery": "openrouter:web_search/exa",
        "extractor": "clover-web",
        "memory": "tenant-scoped Postgres snapshots and claim lineage",
        "limits": {
            "max_inputs": state.config.max_inputs,
            "max_sources": state.config.max_sources,
            "max_search_uses": state.config.max_search_uses,
            "max_source_chars": state.config.max_source_chars,
            "max_evidence_chars": state.config.max_evidence_chars,
            "max_snapshot_chars": state.config.max_snapshot_chars,
            "max_followup_sources": state.config.max_followup_sources,
            "timeout_ms": state.config.request_timeout.as_millis(),
        },
        "side_effects": false,
    }))
}

async fn health(State(state): State<AppState>) -> Response {
    match state.memory.ready().await {
        Ok(()) => Json(json!({"status":"ok","service":"clover-research","version":VERSION,"storage":"postgres"})).into_response(),
        Err(_) => (StatusCode::SERVICE_UNAVAILABLE,Json(json!({"status":"unavailable","storage":"postgres"}))).into_response(),
    }
}

async fn research(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: Result<Json<ResearchRequest>, axum::extract::rejection::JsonRejection>,
) -> Response {
    let started = Instant::now();
    state.metrics.requests.fetch_add(1, Ordering::Relaxed);
    let inputs = match body {
        Ok(Json(body)) => body.inputs,
        Err(error) => {
            state.metrics.failures.fetch_add(1, Ordering::Relaxed);
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error":{"code":"invalid_request", "message":error.body_text()}})),
            )
                .into_response();
        }
    };
    if inputs.len() > state.config.max_inputs {
        state.metrics.failures.fetch_add(1, Ordering::Relaxed);
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error":{
                "code":"too_many_inputs",
                "message":format!("{} inputs exceed the per-request limit of {}", inputs.len(), state.config.max_inputs)
            }})),
        )
            .into_response();
    }
    if inputs.is_empty() {
        return Json(json!({"outputs":[]})).into_response();
    }
    state
        .metrics
        .inputs
        .fetch_add(inputs.len() as u64, Ordering::Relaxed);
    let Some(tenant_scope) = tenant_scope(&headers) else {
        return (StatusCode::UNAUTHORIZED, Json(json!({"error":{"code":"tenant_required","message":"Use the authenticated Clover gateway"}}))).into_response();
    };

    let concurrency = state.config.max_concurrency.min(inputs.len());
    let mut outputs = stream::iter(inputs.into_iter().enumerate())
        .map(|(index, value)| {
            let state = state.clone();
            let tenant_scope = tenant_scope.clone();
            async move {
                let output = match ResearchInput::from_value(value, &state.config) {
                    Ok(input) => match state.slots.clone().acquire_owned().await {
                        Ok(_permit) => {
                            match research::run(
                                input,
                                &tenant_scope,
                                &state.config,
                                &state.model,
                                &state.http,
                                &state.memory,
                            )
                            .await
                            {
                                Ok(output) => {
                                    state.metrics.successes.fetch_add(1, Ordering::Relaxed);
                                    record_usage(&state.metrics, &output);
                                    output
                                }
                                Err(error) => {
                                    state.metrics.failures.fetch_add(1, Ordering::Relaxed);
                                    error_output(error)
                                }
                            }
                        }
                        Err(_) => {
                            state.metrics.failures.fetch_add(1, Ordering::Relaxed);
                            error_output(ServiceError::new(
                                "shutting_down",
                                "service is shutting down",
                            ))
                        }
                    },
                    Err(error) => {
                        state.metrics.failures.fetch_add(1, Ordering::Relaxed);
                        error_output(error)
                    }
                };
                (index, output)
            }
        })
        .buffer_unordered(concurrency)
        .collect::<Vec<_>>()
        .await;
    outputs.sort_by_key(|(index, _)| *index);
    let outputs = outputs
        .into_iter()
        .map(|(_, value)| value)
        .collect::<Vec<_>>();
    state.metrics.duration_micros.fetch_add(
        started.elapsed().as_micros().min(u64::MAX as u128) as u64,
        Ordering::Relaxed,
    );
    Json(json!({"outputs":outputs})).into_response()
}

fn tenant_scope(headers: &HeaderMap) -> Option<String> {
    headers
        .get("x-clover-tenant-scope")
        .and_then(|value| value.to_str().ok())
        .map(str::trim)
        .filter(|value| value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit()))
        .map(str::to_ascii_lowercase)
}

fn error_output(error: ServiceError) -> Value {
    json!({"ok":false, "error":{"code":error.code, "message":error.message}})
}

fn record_usage(metrics: &Metrics, output: &Value) {
    let usage = output.get("usage").unwrap_or(&Value::Null);
    metrics.prompt_tokens.fetch_add(
        usage
            .get("prompt_tokens")
            .and_then(Value::as_i64)
            .unwrap_or_default(),
        Ordering::Relaxed,
    );
    metrics.completion_tokens.fetch_add(
        usage
            .get("completion_tokens")
            .and_then(Value::as_i64)
            .unwrap_or_default(),
        Ordering::Relaxed,
    );
    metrics.search_requests.fetch_add(
        usage
            .get("search_requests")
            .and_then(Value::as_i64)
            .unwrap_or_default(),
        Ordering::Relaxed,
    );
    metrics.cost_microusd.fetch_add(
        usage
            .get("cost_microusd")
            .and_then(Value::as_i64)
            .unwrap_or_default(),
        Ordering::Relaxed,
    );
    let memory = output.get("memory").unwrap_or(&Value::Null);
    metrics.cache_reuses.fetch_add(
        memory
            .get("cache_reuses")
            .and_then(Value::as_u64)
            .unwrap_or_default(),
        Ordering::Relaxed,
    );
    metrics.snapshots_written.fetch_add(
        memory
            .get("snapshots_written")
            .and_then(Value::as_u64)
            .unwrap_or_default(),
        Ordering::Relaxed,
    );
    if memory.get("synthesis_reused").and_then(Value::as_bool) == Some(true) {
        metrics.synthesis_skips.fetch_add(1, Ordering::Relaxed);
    }
}

async fn metrics(State(state): State<AppState>) -> impl IntoResponse {
    let metrics = &state.metrics;
    let body = format!(
        concat!(
            "# HELP clover_research_up Whether the Clover Research process is serving.\n",
            "# TYPE clover_research_up gauge\n",
            "clover_research_up 1\n",
            "# TYPE clover_research_requests_total counter\n",
            "clover_research_requests_total {}\n",
            "# TYPE clover_research_inputs_total counter\n",
            "clover_research_inputs_total {}\n",
            "# TYPE clover_research_successes_total counter\n",
            "clover_research_successes_total {}\n",
            "# TYPE clover_research_failures_total counter\n",
            "clover_research_failures_total {}\n",
            "# TYPE clover_research_prompt_tokens_total counter\n",
            "clover_research_prompt_tokens_total {}\n",
            "# TYPE clover_research_completion_tokens_total counter\n",
            "clover_research_completion_tokens_total {}\n",
            "# TYPE clover_research_search_requests_total counter\n",
            "clover_research_search_requests_total {}\n",
            "# TYPE clover_research_cost_microusd_total counter\n",
            "clover_research_cost_microusd_total {}\n",
            "# TYPE clover_research_duration_seconds_total counter\n",
            "clover_research_duration_seconds_total {:.6}\n",
            "# TYPE clover_research_cache_reuses_total counter\n",
            "clover_research_cache_reuses_total {}\n",
            "# TYPE clover_research_snapshots_written_total counter\n",
            "clover_research_snapshots_written_total {}\n",
            "# TYPE clover_research_synthesis_skips_total counter\n",
            "clover_research_synthesis_skips_total {}\n"
        ),
        metrics.requests.load(Ordering::Relaxed),
        metrics.inputs.load(Ordering::Relaxed),
        metrics.successes.load(Ordering::Relaxed),
        metrics.failures.load(Ordering::Relaxed),
        metrics.prompt_tokens.load(Ordering::Relaxed),
        metrics.completion_tokens.load(Ordering::Relaxed),
        metrics.search_requests.load(Ordering::Relaxed),
        metrics.cost_microusd.load(Ordering::Relaxed),
        metrics.duration_micros.load(Ordering::Relaxed) as f64 / 1_000_000.0,
        metrics.cache_reuses.load(Ordering::Relaxed),
        metrics.snapshots_written.load(Ordering::Relaxed),
        metrics.synthesis_skips.load(Ordering::Relaxed),
    );
    ([(header::CONTENT_TYPE, "text/plain; version=0.0.4")], body)
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("failed to install Ctrl+C handler");
    };
    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("failed to install SIGTERM handler")
            .recv()
            .await;
    };
    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();
    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
}
