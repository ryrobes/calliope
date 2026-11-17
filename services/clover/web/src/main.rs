mod config;
mod extract;
mod fetch;

use axum::{
    extract::{DefaultBodyLimit, State},
    http::{header, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use config::Config;
use extract::ScrapeInput;
use futures_util::{stream, StreamExt};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
    time::Instant,
};
use tokio::sync::Semaphore;
use tracing::{info, warn};

const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Clone)]
struct AppState {
    config: Config,
    slots: Arc<Semaphore>,
    metrics: Arc<Metrics>,
}

#[derive(Default)]
struct Metrics {
    requests: AtomicU64,
    inputs: AtomicU64,
    successes: AtomicU64,
    failures: AtomicU64,
    fetched_bytes: AtomicU64,
    duration_micros: AtomicU64,
}

#[derive(Deserialize)]
struct ScrapeRequest {
    inputs: Vec<Value>,
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "clover_web=info,tower_http=info".into()),
        )
        .init();

    let config = Config::from_env().unwrap_or_else(|error| {
        eprintln!("clover-web configuration error: {error}");
        std::process::exit(2);
    });
    let bind = config.bind;
    let state = AppState {
        slots: Arc::new(Semaphore::new(config.max_concurrency)),
        config,
        metrics: Arc::new(Metrics::default()),
    };
    let app = Router::new()
        .route("/", get(root))
        .route("/health", get(health))
        .route("/metrics", get(metrics))
        .route("/v1/scrape", post(scrape))
        .route("/internal/audio", post(audio))
        .layer(DefaultBodyLimit::max(256 * 1024))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind(bind)
        .await
        .unwrap_or_else(|error| panic!("cannot bind {bind}: {error}"));
    info!(%bind, version = VERSION, "Clover Web ready");
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .expect("Clover Web server failed");
}

async fn root(State(state): State<AppState>) -> Json<Value> {
    Json(json!({
        "service": "clover-web",
        "version": VERSION,
        "endpoint": "POST /v1/scrape",
        "contract": {"inputs": ["https://example.com", {"url": "https://example.com/report.pdf"}]},
        "limits": {
            "max_inputs": state.config.max_inputs,
            "max_response_bytes": state.config.max_response_bytes,
            "timeout_ms": state.config.request_timeout.as_millis(),
            "max_redirects": state.config.max_redirects,
            "allowed_ports": sorted_ports(&state.config),
        },
        "renderers": ["firecrawl/html-extractor", "firecrawl/anydoc"],
        "javascript": false,
    }))
}

async fn health() -> Json<Value> {
    Json(json!({"status": "ok", "service": "clover-web", "version": VERSION}))
}

// Internal-only byte fetch for the SQL transcription bridge. Reuses the same
// DNS-pinned public-network boundary and concurrency/size caps as scraping.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AudioInput {
    url: String,
}

async fn audio(State(state): State<AppState>, Json(input): Json<AudioInput>) -> Response {
    let Ok(_permit) = state.slots.clone().try_acquire_owned() else {
        return (StatusCode::TOO_MANY_REQUESTS, "Fetch capacity is busy").into_response();
    };
    match tokio::time::timeout(
        state.config.request_timeout,
        fetch::fetch(
            &input.url,
            &state.config,
            Some(16 * 1024 * 1024),
            None,
            None,
            None,
        ),
    )
    .await
    {
        Ok(Ok(result)) if !result.not_modified && !result.body.is_empty() => (
            [(header::CONTENT_TYPE, "application/octet-stream")],
            result.body,
        )
            .into_response(),
        Ok(Err(error)) => (
            StatusCode::BAD_REQUEST,
            Json(json!({"error":{"code":error.code,"message":error.message}})),
        )
            .into_response(),
        _ => (StatusCode::BAD_GATEWAY, "Audio fetch failed or timed out").into_response(),
    }
}

async fn scrape(
    State(state): State<AppState>,
    body: Result<Json<ScrapeRequest>, axum::extract::rejection::JsonRejection>,
) -> Response {
    let started = Instant::now();
    state.metrics.requests.fetch_add(1, Ordering::Relaxed);
    let inputs = match body {
        Ok(Json(body)) => body.inputs,
        Err(error) => {
            state.metrics.failures.fetch_add(1, Ordering::Relaxed);
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": {"code": "invalid_request", "message": error.body_text()}})),
            )
                .into_response();
        }
    };
    if inputs.len() > state.config.max_inputs {
        state.metrics.failures.fetch_add(1, Ordering::Relaxed);
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({
                "error": {
                    "code": "too_many_inputs",
                    "message": format!("{} inputs exceed the per-request limit of {}", inputs.len(), state.config.max_inputs),
                }
            })),
        )
            .into_response();
    }
    if inputs.is_empty() {
        return Json(json!({"outputs": []})).into_response();
    }
    state
        .metrics
        .inputs
        .fetch_add(inputs.len() as u64, Ordering::Relaxed);

    let concurrency = state.config.max_concurrency.min(inputs.len());
    let mut outputs = stream::iter(inputs.into_iter().enumerate())
        .map(|(index, value)| {
            let state = state.clone();
            async move {
                let visible_url = input_url(&value);
                let parsed = ScrapeInput::from_value(value);
                let mut output = match parsed {
                    Ok(input) => {
                        let permit = state.slots.clone().acquire_owned().await;
                        match permit {
                            Ok(_permit) => match extract::scrape(input, &state.config).await {
                                Ok(result) => {
                                    state.metrics.successes.fetch_add(1, Ordering::Relaxed);
                                    state
                                        .metrics
                                        .fetched_bytes
                                        .fetch_add(result.bytes as u64, Ordering::Relaxed);
                                    serde_json::to_value(result).unwrap_or_else(|error| {
                                        error_output(
                                            visible_url,
                                            "serialization_error",
                                            error.to_string(),
                                        )
                                    })
                                }
                                Err(error) => {
                                    state.metrics.failures.fetch_add(1, Ordering::Relaxed);
                                    error_output(visible_url, error.code, error.message)
                                }
                            },
                            Err(_) => {
                                state.metrics.failures.fetch_add(1, Ordering::Relaxed);
                                error_output(
                                    visible_url,
                                    "shutting_down",
                                    "service is shutting down",
                                )
                            }
                        }
                    }
                    Err(error) => {
                        state.metrics.failures.fetch_add(1, Ordering::Relaxed);
                        error_output(visible_url, error.code, error.message)
                    }
                };
                // CPU fetch/extraction has no model-provider charge.
                output["usage"] =
                    json!({"cost":0,"model":"clover-web-v0.3","contract":"clover-web-v0.3"});
                (index, output)
            }
        })
        .buffer_unordered(concurrency)
        .collect::<Vec<_>>()
        .await;
    outputs.sort_by_key(|(index, _)| *index);
    let outputs = outputs
        .into_iter()
        .map(|(_, output)| output)
        .collect::<Vec<_>>();
    state.metrics.duration_micros.fetch_add(
        started.elapsed().as_micros().min(u64::MAX as u128) as u64,
        Ordering::Relaxed,
    );
    Json(json!({"outputs": outputs})).into_response()
}

async fn metrics(State(state): State<AppState>) -> impl IntoResponse {
    let metrics = &state.metrics;
    let body = format!(
        concat!(
            "# HELP clover_web_up Whether the Clover Web process is serving.\n",
            "# TYPE clover_web_up gauge\n",
            "clover_web_up 1\n",
            "# TYPE clover_web_requests_total counter\n",
            "clover_web_requests_total {}\n",
            "# TYPE clover_web_inputs_total counter\n",
            "clover_web_inputs_total {}\n",
            "# TYPE clover_web_successes_total counter\n",
            "clover_web_successes_total {}\n",
            "# TYPE clover_web_failures_total counter\n",
            "clover_web_failures_total {}\n",
            "# TYPE clover_web_fetched_bytes_total counter\n",
            "clover_web_fetched_bytes_total {}\n",
            "# TYPE clover_web_duration_seconds_total counter\n",
            "clover_web_duration_seconds_total {:.6}\n",
        ),
        metrics.requests.load(Ordering::Relaxed),
        metrics.inputs.load(Ordering::Relaxed),
        metrics.successes.load(Ordering::Relaxed),
        metrics.failures.load(Ordering::Relaxed),
        metrics.fetched_bytes.load(Ordering::Relaxed),
        metrics.duration_micros.load(Ordering::Relaxed) as f64 / 1_000_000.0,
    );
    ([(header::CONTENT_TYPE, "text/plain; version=0.0.4")], body)
}

fn input_url(value: &Value) -> Option<String> {
    value
        .as_str()
        .map(str::to_string)
        .or_else(|| value.get("url").and_then(Value::as_str).map(str::to_string))
}

fn error_output(url: Option<String>, code: &'static str, message: impl Into<String>) -> Value {
    json!({
        "ok": false,
        "url": url,
        "error": {"code": code, "message": message.into()},
    })
}

fn sorted_ports(config: &Config) -> Vec<u16> {
    let mut ports = config.allowed_ports.iter().copied().collect::<Vec<_>>();
    ports.sort_unstable();
    ports
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
    warn!("Clover Web shutting down");
}

#[cfg(test)]
mod audio_tests {
    use super::*;

    #[tokio::test]
    async fn audio_fetch_blocks_internal_and_non_http_sources() {
        let state = AppState {
            config: Config::from_env().unwrap(),
            slots: Arc::new(Semaphore::new(1)),
            metrics: Arc::new(Metrics::default()),
        };
        for url in [
            "http://127.0.0.1/a.wav",
            "http://169.254.169.254/latest/meta-data",
            "file:///etc/passwd",
        ] {
            let response = audio(State(state.clone()), Json(AudioInput { url: url.into() })).await;
            assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        }
        let _permit = state.slots.clone().acquire_owned().await.unwrap();
        let response = audio(
            State(state),
            Json(AudioInput {
                url: "https://example.com/a.wav".into(),
            }),
        )
        .await;
        assert_eq!(response.status(), StatusCode::TOO_MANY_REQUESTS);
    }
}
