//! Binary speech transport. Scripts and audio never enter the usage ledger.
use crate::{
    authenticate, new_invocation_id, AppState, HutchError, MeterRow, ProviderAttempt, TenantStatus,
};
use axum::{
    body::Body,
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    sync::Arc,
    time::{Duration, Instant},
};

fn sample_rate() -> u32 {
    44100
}
fn max_characters() -> usize {
    10000
}
fn max_concurrent() -> usize {
    2
}
fn global_concurrent() -> usize {
    16
}
fn timeout_ms() -> u64 {
    120000
}
fn max_audio_bytes() -> usize {
    64 * 1024 * 1024
}
fn pcm() -> String {
    "pcm".into()
}
fn expression() -> String {
    "bracket-tags".into()
}
fn provider() -> String {
    "openrouter".into()
}
fn voice_pattern() -> String {
    "^[a-f0-9]{32}$".into()
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct SpeechCfg {
    pub name: String,
    pub entitlement: String,
    #[serde(default = "provider")]
    pub provider: String,
    pub upstream_base: String,
    pub upstream_bearer_token_env: String,
    pub upstream_model: String,
    pub default_voice: String,
    pub model_version: String,
    #[serde(default = "pcm")]
    pub response_format: String,
    #[serde(default = "sample_rate")]
    pub sample_rate: u32,
    #[serde(default = "expression")]
    pub expression_style: String,
    #[serde(default = "max_characters")]
    pub max_input_characters: usize,
    #[serde(default = "max_concurrent")]
    pub max_concurrent: usize,
    #[serde(default = "global_concurrent")]
    pub global_concurrent: usize,
    #[serde(default = "timeout_ms")]
    pub timeout_ms: u64,
    #[serde(default = "max_audio_bytes")]
    pub max_audio_bytes: usize,
    /// Reference tariff only; actual provider charges remain independently unknown.
    #[serde(default)]
    pub character_nanousd: i64,
    /// Operator tuning: let callers name a voice other than the default, as long as it
    /// matches `voice_pattern`. Skipped from the revision: it does not change the default
    /// voice's output, and turning it on must not invalidate every cached render.
    #[serde(default, skip_serializing)]
    pub allow_voice_override: bool,
    #[serde(default = "voice_pattern", skip_serializing)]
    pub voice_pattern: String,
}

impl SpeechCfg {
    fn revision(&self) -> String {
        hex::encode(Sha256::digest(
            serde_json::to_vec(self).expect("speech config serializes"),
        ))[..24]
            .into()
    }
    fn profile(&self) -> Value {
        json!({"id":self.name,"object":"speech.model","profile_revision":self.revision(),
            "model_version":self.model_version,"upstream_model":self.upstream_model,
            "provider":self.provider,"voice":self.default_voice,"response_format":self.response_format,
            "encoding":"s16le","sample_rate":self.sample_rate,"channels":1,
            "expression_style":self.expression_style,"alignment":"none",
            "max_input_characters":self.max_input_characters,
            "voice_override":self.allow_voice_override,"voice_pattern":self.voice_pattern})
    }
}

pub fn validate_configs(configs: &[SpeechCfg]) -> Result<(), String> {
    let mut names = HashSet::new();
    for c in configs {
        if c.name.trim().is_empty()
            || !names.insert(&c.name)
            || c.entitlement.is_empty()
            || c.default_voice.is_empty()
            || c.upstream_model.is_empty()
            || c.model_version.is_empty()
            || c.upstream_bearer_token_env.is_empty()
            || c.max_concurrent == 0
            || c.global_concurrent == 0
            || c.timeout_ms == 0
            || c.max_input_characters == 0
            || c.max_audio_bytes < 2
            || c.character_nanousd < 0
            || !(8000..=192000).contains(&c.sample_rate)
            || c.response_format != "pcm"
            || !["bracket-tags", "plain"].contains(&c.expression_style.as_str())
        {
            return Err(format!(
                "invalid speech configuration for '{}' (requires mono s16le PCM)",
                c.name
            ));
        }
        if regex::Regex::new(&c.voice_pattern).is_err() || c.voice_pattern.len() > 200 {
            return Err(format!("invalid speech voice_pattern for '{}'", c.name));
        }
        let url =
            reqwest::Url::parse(&c.upstream_base).map_err(|_| "invalid speech upstream URL")?;
        let local = matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "::1"));
        if (url.scheme() != "https" && !(local && url.scheme() == "http"))
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err("speech upstream must use HTTPS (HTTP allowed only on loopback) without URL credentials/query".into());
        }
    }
    Ok(())
}

fn failure(status: StatusCode, code: &'static str, message: &str) -> HutchError {
    HutchError {
        status,
        code,
        message: message.into(),
        retry_after_ms: if status == StatusCode::TOO_MANY_REQUESTS {
            Some(2000)
        } else {
            None
        },
    }
}

pub async fn models(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let tenant = match authenticate(&state, &headers).await {
        Ok(t) => t,
        Err(e) => return e.into_response(),
    };
    if tenant.status == TenantStatus::Expired {
        return HutchError::subscription_expired(&tenant.id).into_response();
    }
    let data: Vec<_> = state
        .cfg
        .speech
        .iter()
        .filter(|c| tenant.entitlements.contains(&c.entitlement))
        .map(SpeechCfg::profile)
        .collect();
    (
        [("cache-control", "no-store")],
        Json(json!({"object":"list","data":data})),
    )
        .into_response()
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SpeechRequest {
    model: String,
    input: String,
    voice: Option<String>,
    response_format: Option<String>,
}

struct UsageGuard {
    state: Arc<AppState>,
    cfg: SpeechCfg,
    tenant: String,
    id: String,
    started: Instant,
    characters: usize,
    bytes: usize,
    first_ms: Option<f64>,
    generation: Option<String>,
    status: u16,
    error: Option<&'static str>,
    attempted: bool,
}
impl Drop for UsageGuard {
    fn drop(&mut self) {
        let elapsed = self.started.elapsed().as_secs_f64() * 1000.0;
        self.state.meter.record(MeterRow {
            invocation_id: Some(&self.id),
            tenant: &self.tenant,
            backend: &format!("speech:{}", self.cfg.name),
            stream: true,
            n_inputs: 1,
            status: self.status,
            error_code: self.error,
            duration_ms: elapsed,
            upstream_ms: self.attempted.then_some(elapsed),
            model_version: &self.cfg.model_version,
            reference_cost_nanousd: if self.attempted {
                (self.characters as i64).saturating_mul(self.cfg.character_nanousd)
            } else {
                0
            },
            prompt_tokens: None,
            completion_tokens: None,
        });
        self.state.meter.record_speech(
            &self.id,
            self.characters,
            self.bytes,
            Some(self.bytes as f64 / (self.cfg.sample_rate as f64 * 2.0)),
            self.first_ms,
            &self.cfg.revision(),
        );
        if self.attempted {
            self.state.meter.record_provider_attempt(ProviderAttempt {
                invocation_id: &self.id,
                tenant: &self.tenant,
                canonical_model: &self.cfg.name,
                provider: &self.cfg.provider,
                upstream_model: &self.cfg.upstream_model,
                provider_request_id: self.generation.as_deref(),
                attempt_no: 1,
                mode: "speech",
                status: self.status,
                prompt_tokens: None,
                completion_tokens: None,
                provider_cost_nanousd: None,
                cost_source: if self.generation.is_some() {
                    "pending"
                } else {
                    "unavailable"
                },
                reconcile: self.cfg.provider == "openrouter",
            });
        }
    }
}

fn validate_payload(cfg: &SpeechCfg, request: &SpeechRequest) -> Result<(), HutchError> {
    if request.input.trim().is_empty() || request.input.chars().count() > cfg.max_input_characters {
        return Err(HutchError::bad_request(format!(
            "speech input must contain 1–{} characters",
            cfg.max_input_characters
        )));
    }
    if request
        .response_format
        .as_ref()
        .is_some_and(|v| v != &cfg.response_format)
    {
        return Err(HutchError::bad_request(
            "unsupported speech voice or format".into(),
        ));
    }
    if let Some(voice) = request.voice.as_ref().filter(|v| *v != &cfg.default_voice) {
        let allowed = cfg.allow_voice_override
            && voice.len() <= 128
            && regex::Regex::new(&cfg.voice_pattern).is_ok_and(|re| re.is_match(voice));
        if !allowed {
            return Err(HutchError::bad_request(
                "unsupported speech voice or format".into(),
            ));
        }
    }
    Ok(())
}

fn effective_voice(cfg: &SpeechCfg, request: &SpeechRequest) -> String {
    request
        .voice
        .clone()
        .filter(|v| !v.trim().is_empty())
        .unwrap_or_else(|| cfg.default_voice.clone())
}

fn validate_audio_type(value: &str, cfg: &SpeechCfg) -> bool {
    let parts: Vec<_> = value.split(';').map(str::trim).collect();
    if parts.first().map(|s| s.to_ascii_lowercase()).as_deref() != Some("audio/pcm") {
        return false;
    }
    // Some providers omit parameters. Only an operator-verified profile can supply defaults.
    for part in &parts[1..] {
        if let Some((k, v)) = part.split_once('=') {
            match k.trim().to_ascii_lowercase().as_str() {
                "rate" if v.trim().parse::<u32>().ok() != Some(cfg.sample_rate) => return false,
                "channels" if v.trim() != "1" => return false,
                _ => {}
            }
        }
    }
    true
}

pub async fn synthesize(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: Result<Json<Value>, axum::extract::rejection::JsonRejection>,
) -> Response {
    let tenant = match authenticate(&state, &headers).await {
        Ok(t) => t,
        Err(e) => return e.into_response(),
    };
    if tenant.status == TenantStatus::Expired {
        return HutchError::subscription_expired(&tenant.id).into_response();
    }
    let payload = match body {
        Ok(Json(v)) => v,
        Err(_) => return HutchError::bad_request("invalid speech JSON".into()).into_response(),
    };
    let request: SpeechRequest = match serde_json::from_value(payload) {
        Ok(v) => v,
        Err(_) => return HutchError::bad_request("invalid speech fields".into()).into_response(),
    };
    let cfg = match state.cfg.speech.iter().find(|c| c.name == request.model) {
        Some(c) => c.clone(),
        None => {
            return failure(
                StatusCode::NOT_FOUND,
                "speech_model_not_found",
                "Unknown speech alias.",
            )
            .into_response()
        }
    };
    if !tenant.entitlements.contains(&cfg.entitlement) {
        return HutchError::not_entitled(&tenant.id, &cfg.name, &cfg.entitlement).into_response();
    }
    if let Err(e) = validate_payload(&cfg, &request) {
        return e.into_response();
    }
    if headers
        .get("x-speech-profile-revision")
        .is_some_and(|v| v.to_str().ok() != Some(cfg.revision().as_str()))
    {
        return failure(
            StatusCode::CONFLICT,
            "speech_profile_changed",
            "Speech configuration changed. Prepare a new render.",
        )
        .into_response();
    }
    let mut usage = UsageGuard {
        state: state.clone(),
        cfg: cfg.clone(),
        tenant: tenant.id.clone(),
        id: new_invocation_id(),
        started: Instant::now(),
        characters: request.input.chars().count(),
        bytes: 0,
        first_ms: None,
        generation: None,
        status: 499,
        error: Some("stream_interrupted"),
        attempted: false,
    };
    let tenant_permit = state
        .lanes
        .acquire(
            &format!("{}::speech::{}", tenant.id, cfg.name),
            cfg.max_concurrent,
            state.cfg.lane_grace_ms,
        )
        .await;
    let global_permit = state
        .lanes
        .acquire(
            &format!("speech-global::{}", cfg.name),
            cfg.global_concurrent,
            state.cfg.lane_grace_ms,
        )
        .await;
    let permits = match (tenant_permit, global_permit) {
        (Some(t), Some(g)) => (t, g),
        _ => {
            usage.status = 429;
            usage.error = Some("lanes_saturated");
            return HutchError::lanes_saturated(&tenant.id, cfg.max_concurrent).into_response();
        }
    };
    let key = match crate::credentials::resolve(&cfg.upstream_bearer_token_env) {
        Some(k) if !k.trim().is_empty() => k,
        _ => {
            usage.status = 503;
            usage.error = Some("speech_not_configured");
            return failure(
                StatusCode::SERVICE_UNAVAILABLE,
                "speech_not_configured",
                "Speech credentials are not configured.",
            )
            .into_response();
        }
    };
    usage.attempted = true;
    // Dedicated no-redirect client: provider credentials must stay on the configured origin.
    let client = match reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(12))
        .build()
    {
        Ok(c) => c,
        Err(_) => {
            return failure(
                StatusCode::BAD_GATEWAY,
                "speech_transport",
                "Cannot initialize speech connection.",
            )
            .into_response()
        }
    };
    let upstream = client.post(format!("{}/audio/speech",cfg.upstream_base.trim_end_matches('/')))
        .bearer_auth(key).header("HTTP-Referer",crate::OPENROUTER_APP_URL).header("X-Title",crate::OPENROUTER_APP_TITLE)
        .timeout(Duration::from_millis(cfg.timeout_ms))
        .json(&json!({"model":cfg.upstream_model,"input":request.input,"voice":effective_voice(&cfg,&request),"response_format":cfg.response_format})).send().await;
    let mut upstream = match upstream {
        Ok(r) => r,
        Err(_) => {
            usage.status = 502;
            usage.error = Some("speech_transport");
            return failure(
                StatusCode::BAD_GATEWAY,
                "speech_transport",
                "Speech provider could not be reached.",
            )
            .into_response();
        }
    };
    usage.generation = upstream
        .headers()
        .get("x-generation-id")
        .and_then(|v| v.to_str().ok())
        .map(|v| v.chars().take(160).collect());
    if !upstream.status().is_success() {
        let code = upstream.status().as_u16();
        usage.status = code;
        usage.error = Some("speech_upstream_error");
        let (status, message) = match code {
            429 => (
                StatusCode::TOO_MANY_REQUESTS,
                "Speech provider is temporarily at capacity.",
            ),
            401..=403 => (
                StatusCode::SERVICE_UNAVAILABLE,
                "Speech provider credentials or quota need operator attention.",
            ),
            404 => (
                StatusCode::SERVICE_UNAVAILABLE,
                "The configured speech model is unavailable.",
            ),
            _ => (
                StatusCode::BAD_GATEWAY,
                "Speech provider could not synthesize this response.",
            ),
        };
        return failure(status, "speech_upstream_error", message).into_response();
    }
    if !validate_audio_type(
        upstream
            .headers()
            .get("content-type")
            .and_then(|v| v.to_str().ok())
            .unwrap_or(""),
        &cfg,
    ) {
        usage.status = 502;
        usage.error = Some("speech_format");
        return failure(
            StatusCode::BAD_GATEWAY,
            "speech_format",
            "Speech provider returned an unexpected audio format.",
        )
        .into_response();
    }
    let first = match tokio::time::timeout(Duration::from_secs(60), upstream.chunk()).await {
        Ok(Ok(Some(b))) if !b.is_empty() && b.len() <= cfg.max_audio_bytes => b,
        _ => {
            usage.status = 502;
            usage.error = Some("speech_empty");
            return failure(
                StatusCode::BAD_GATEWAY,
                "speech_empty",
                "Speech provider returned no playable audio.",
            )
            .into_response();
        }
    };
    usage.first_ms = Some(usage.started.elapsed().as_secs_f64() * 1000.0);
    let revision = cfg.revision();
    let generation = usage.generation.clone();
    let stream = async_stream::try_stream! {
        let _permits=permits;
        // Move the guard into the body even if it is dropped before its first poll.
        let mut usage=usage;
        usage.bytes+=first.len(); yield first;
        let mut bytes=upstream.bytes_stream();
        loop {
            match tokio::time::timeout(Duration::from_secs(60),bytes.next()).await {
                Ok(Some(Ok(chunk))) => {
                    if usage.bytes.saturating_add(chunk.len())>cfg.max_audio_bytes {
                        usage.status=502;usage.error=Some("speech_too_large");
                        Err(std::io::Error::other("speech output limit exceeded"))?;
                    }
                    usage.bytes+=chunk.len(); yield chunk;
                }
                Ok(None) => break,
                _ => {usage.status=502;usage.error=Some("speech_stream_error");Err(std::io::Error::other("speech stream interrupted"))?;}
            }
        }
        if !usage.bytes.is_multiple_of(2) {usage.status=502;usage.error=Some("speech_partial_sample");Err(std::io::Error::other("incomplete PCM sample"))?;}
        usage.status=200;usage.error=None;
    };
    let mut response = Response::new(Body::from_stream(
        stream.map(|item: Result<_, std::io::Error>| item),
    ));
    let h = response.headers_mut();
    h.insert(
        "content-type",
        format!("audio/pcm;rate={};channels=1", cfg.sample_rate)
            .parse()
            .unwrap(),
    );
    h.insert("cache-control", "no-store".parse().unwrap());
    h.insert("x-speech-profile-revision", revision.parse().unwrap());
    if let Some(id) = generation.and_then(|g| g.parse().ok()) {
        h.insert("x-generation-id", id);
    }
    response
}

#[cfg(test)]
mod tests {
    use super::*;
    fn config() -> SpeechCfg {
        serde_json::from_value(json!({"name":"voice","entitlement":"clover","upstream_base":"https://openrouter.ai/api/v1","upstream_bearer_token_env":"OPENROUTER_API_KEY","upstream_model":"fish-audio/s2.1-pro-free:free","default_voice":"fish-voice","model_version":"v1"})).unwrap()
    }
    #[test]
    fn revision_changes_with_voice_model_and_encoding() {
        let c = config();
        let mut changed = c.clone();
        changed.default_voice = "another".into();
        assert_ne!(c.revision(), changed.revision());
        changed = c.clone();
        changed.upstream_model = "paid".into();
        assert_ne!(c.revision(), changed.revision());
        changed = c.clone();
        changed.sample_rate = 24000;
        assert_ne!(c.revision(), changed.revision());
    }
    #[test]
    fn rejects_credential_redirect_targets_and_invalid_limits() {
        let c = config();
        assert!(validate_configs(std::slice::from_ref(&c)).is_ok());
        for url in [
            "http://example.com",
            "https://user:secret@example.com",
            "https://example.com?key=secret",
        ] {
            let mut bad = c.clone();
            bad.upstream_base = url.into();
            assert!(validate_configs(&[bad]).is_err());
        }
        let mut bad = c;
        bad.max_concurrent = 0;
        assert!(validate_configs(&[bad]).is_err());
    }
    #[test]
    fn rejects_wrong_rate_channels_and_json_audio() {
        let c = config();
        assert!(validate_audio_type("audio/pcm;rate=44100;channels=1", &c));
        for mime in [
            "audio/pcm;rate=24000;channels=1",
            "audio/pcm;rate=44100;channels=2",
            "application/json",
            "audio/mpeg",
        ] {
            assert!(!validate_audio_type(mime, &c));
        }
    }
    #[test]
    fn preserves_tags_but_rejects_voice_override_and_overlong_input() {
        let c = config();
        let mut r = SpeechRequest {
            model: c.name.clone(),
            input: "[excited] Hello!".into(),
            voice: None,
            response_format: None,
        };
        assert!(validate_payload(&c, &r).is_ok());
        r.voice = Some("unapproved".into());
        assert!(validate_payload(&c, &r).is_err());
        r.voice = Some("0123456789abcdef0123456789abcdef".into());
        assert!(validate_payload(&c, &r).is_err());          // shape is right, override is off
        let mut tuning = c.clone();
        tuning.allow_voice_override = true;
        assert!(validate_payload(&tuning, &r).is_ok());
        assert_eq!(effective_voice(&tuning, &r), "0123456789abcdef0123456789abcdef");
        r.voice = Some("../not-a-voice".into());
        assert!(validate_payload(&tuning, &r).is_err());     // pattern still gates
        assert_eq!(tuning.revision(), c.revision());          // policy is not part of the output identity
        r.voice = None;
        assert_eq!(effective_voice(&c, &r), c.default_voice);
        r.input = "x".repeat(c.max_input_characters + 1);
        assert!(validate_payload(&c, &r).is_err());
    }
    #[tokio::test]
    async fn gateway_auth_profile_binary_stream_and_cancellation() {
        use axum::{
            routing::{get, post},
            Router,
        };
        use std::sync::{
            atomic::{AtomicUsize, Ordering},
            RwLock,
        };
        let calls = Arc::new(AtomicUsize::new(0));
        let calls_clone = calls.clone();
        let upstream=Router::new().route("/audio/speech",post(move |headers:HeaderMap,Json(body):Json<Value>| {
            let calls=calls_clone.clone(); async move {
                calls.fetch_add(1,Ordering::SeqCst);
                assert_eq!(headers["authorization"],"Bearer fixture-provider-key");
                assert_eq!(body["model"],"fish-audio/s2.1-pro-free:free");
                assert_eq!(body["voice"],"fish-voice");
                if body["input"]=="busy" {return (StatusCode::TOO_MANY_REQUESTS,"secret upstream message").into_response();}
                if body["input"]=="wrong-format" {return Json(json!({"secret":"not audio"})).into_response();}
                let slow=body["input"]=="slow";
                let stream=async_stream::stream! {
                    yield Ok::<_,std::io::Error>(axum::body::Bytes::from_static(b"\x01"));
                    if slow { tokio::time::sleep(Duration::from_secs(30)).await; }
                    yield Ok::<_,std::io::Error>(axum::body::Bytes::from_static(b"\x02\x03\x04"));
                };
                let mut r=Response::new(Body::from_stream(stream));
                r.headers_mut().insert("content-type","audio/pcm;rate=44100;channels=1".parse().unwrap());
                r.headers_mut().insert("x-generation-id","gen-tts-fixture".parse().unwrap()); r
            }
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let upstream_task =
            tokio::spawn(async move { axum::serve(listener, upstream).await.unwrap() });
        let mut c = config();
        c.upstream_base = format!("http://{addr}");
        c.upstream_bearer_token_env = "HUTCH_TEST_SPEECH_KEY".into();
        c.max_concurrent = 1;
        std::env::set_var("HUTCH_TEST_SPEECH_KEY", "fixture-provider-key");
        let mut cfg = crate::config::HutchConfig::load("hutch.example.yaml").unwrap();
        cfg.speech = vec![c.clone()];
        let db_path = std::env::temp_dir().join(format!("{}.sqlite", new_invocation_id()));
        let state = Arc::new(AppState {
            cfg,
            tenants_path: "tenants.example.yaml".into(),
            tenants: RwLock::new(
                crate::tenants::TenantStore::load("tenants.example.yaml").unwrap(),
            ),
            lanes: crate::lanes::LaneRegistry::default(),
            meter: crate::meter::Meter::open(db_path.to_str().unwrap()).unwrap(),
            http: reqwest::Client::new(),
        });
        let app = Router::new()
            .route("/v1/audio/models", get(models))
            .route("/v1/audio/speech", post(synthesize))
            .with_state(state.clone());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let task = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        let http = reqwest::Client::new();
        let base = format!("http://{addr}/v1/audio");
        assert_eq!(
            http.get(format!("{base}/models"))
                .send()
                .await
                .unwrap()
                .status(),
            401
        );
        assert_eq!(
            http.get(format!("{base}/models"))
                .bearer_auth("hutch_dev_lapsed")
                .send()
                .await
                .unwrap()
                .status(),
            403
        );
        let profiles: Value = http
            .get(format!("{base}/models"))
            .bearer_auth("hutch_dev_acme")
            .send()
            .await
            .unwrap()
            .json()
            .await
            .unwrap();
        assert_eq!(profiles["data"][0]["profile_revision"], c.revision());
        assert!(!profiles.to_string().contains("fixture-provider-key"));
        let send = |input: &str| {
            http.post(format!("{base}/speech"))
                .bearer_auth("hutch_dev_acme")
                .json(&json!({"model":"voice","input":input}))
        };
        assert_eq!(
            send("hello")
                .header("x-speech-profile-revision", "old")
                .send()
                .await
                .unwrap()
                .status(),
            409
        );
        assert_eq!(calls.load(Ordering::SeqCst), 0);
        let r = send("[excited] hello").send().await.unwrap();
        assert_eq!(r.status(), 200);
        assert_eq!(r.headers()["x-generation-id"], "gen-tts-fixture");
        assert_eq!(r.bytes().await.unwrap().as_ref(), b"\x01\x02\x03\x04");
        let r = send("busy").send().await.unwrap();
        assert_eq!(r.status(), 429);
        assert!(!r.text().await.unwrap().contains("secret"));
        assert_eq!(send("wrong-format").send().await.unwrap().status(), 502);
        let mut slow = send("slow").send().await.unwrap();
        assert!(slow.chunk().await.unwrap().is_some());
        assert_eq!(send("blocked").send().await.unwrap().status(), 429);
        drop(slow);
        for _ in 0..100 {
            if state
                .lanes
                .snapshot()
                .iter()
                .all(|(_, in_flight, _)| *in_flight == 0)
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert!(state.lanes.snapshot().iter().all(|(_, n, _)| *n == 0));
        let db = rusqlite::Connection::open(&db_path).unwrap();
        assert_eq!(db.query_row("SELECT count(*) FROM invocations WHERE backend='speech:voice' AND error_code='stream_interrupted'",[],|r|r.get::<_,i64>(0)).unwrap(),1);
        assert_eq!(db.query_row("SELECT output_bytes FROM speech_usage JOIN invocations USING(invocation_id) WHERE status=200",[],|r|r.get::<_,i64>(0)).unwrap(),4);
        assert_eq!(db.query_row("SELECT count(*) FROM upstream_attempts WHERE mode='speech' AND prompt_tokens IS NULL AND provider_cost_nanousd IS NULL",[],|r|r.get::<_,i64>(0)).unwrap(),4);
        task.abort();
        upstream_task.abort();
        drop(db);
        drop(state);
        let _ = std::fs::remove_file(db_path);
    }
}
