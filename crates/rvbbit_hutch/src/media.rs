//! Buffered image and transcription adapters.
//! Credentials and model policy stay at Hutch. No media, prompts, SDP or transcripts are logged.
use crate::{
    authenticate, new_invocation_id, AppState, HutchError, MeterRow, ProviderAttempt, TenantStatus,
};
use axum::{
    extract::{Multipart, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use futures_util::StreamExt;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::HashSet,
    sync::Arc,
    time::{Duration, Instant},
};

#[derive(Clone, Debug, Deserialize)]
pub struct MediaCfg {
    pub name: String,
    pub capability: String,
    pub entitlement: String,
    pub provider: String,
    pub upstream_base: String,
    pub upstream_path: String,
    pub upstream_bearer_token_env: String,
    pub upstream_model: String,
    pub model_version: String,
    #[serde(default)]
    pub request_overrides: Value,
    #[serde(default = "timeout")]
    pub timeout_ms: u64,
}
fn timeout() -> u64 {
    180_000
}
pub fn validate_configs(configs: &[MediaCfg]) -> Result<(), String> {
    let mut names = HashSet::new();
    for c in configs {
        let u = reqwest::Url::parse(&c.upstream_base).map_err(|_| "Invalid media upstream")?;
        let local = matches!(u.host_str(), Some("127.0.0.1" | "localhost" | "::1"));
        if c.name.is_empty()
            || !names.insert(&c.name)
            || c.entitlement.is_empty()
            || c.provider.is_empty()
            || c.upstream_model.is_empty()
            || c.model_version.is_empty()
            || c.upstream_bearer_token_env.is_empty()
            || c.timeout_ms == 0
            || !["image", "transcription"].contains(&c.capability.as_str())
            || !["/images", "/images/generations", "/audio/transcriptions"]
                .contains(&c.upstream_path.as_str())
            || !match c.capability.as_str() {
                "image" => ["/images", "/images/generations"].contains(&c.upstream_path.as_str()),
                "transcription" => c.upstream_path == "/audio/transcriptions",
                _ => false,
            }
            || !(u.scheme() == "https" || (local && u.scheme() == "http"))
            || !u.username().is_empty()
            || u.password().is_some()
            || u.query().is_some()
            || u.fragment().is_some()
            || !(c.request_overrides.is_null() || c.request_overrides.is_object())
            || c.request_overrides.get("model").is_some()
        {
            return Err(format!("Invalid media route {}", c.name));
        }
    }
    Ok(())
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
        .media
        .iter()
        .filter(|c| tenant.entitlements.contains(&c.entitlement))
        .map(|c| {
            json!({"id":c.name,"capability":c.capability,"model_version":c.model_version,
            "upstream_model":c.upstream_model,"provider":c.provider,"streaming":false,
            "image_editing":false,"usage_scope":"request"})
        })
        .collect();
    Json(json!({"object":"list","data":data})).into_response()
}

struct Upload {
    fields: Vec<(String, String)>,
    file: Option<(String, String, Vec<u8>)>,
}
async fn upload(mut multipart: Multipart, audio: bool) -> Result<Upload, HutchError> {
    let mut out = Upload {
        fields: vec![],
        file: None,
    };
    let mut seen = HashSet::new();
    while let Some(field) = multipart
        .next_field()
        .await
        .map_err(|_| HutchError::bad_request("Invalid multipart upload".into()))?
    {
        let name = field.name().unwrap_or("").to_string();
        if !seen.insert(name.clone()) {
            return Err(HutchError::bad_request("Duplicate multipart field".into()));
        }
        if audio && name == "file" {
            let filename = field.file_name().unwrap_or("audio.wav").to_string();
            let mime = field
                .content_type()
                .unwrap_or("application/octet-stream")
                .to_string();
            let bytes = field
                .bytes()
                .await
                .map_err(|_| HutchError::bad_request("Audio upload exceeds limit".into()))?;
            if bytes.is_empty() || bytes.len() > 25 * 1024 * 1024 {
                return Err(HutchError::bad_request(
                    "Audio must contain 1–25 MiB".into(),
                ));
            }
            out.file = Some((filename, mime, bytes.to_vec()));
        } else {
            let allowed = if audio {
                &[
                    "model",
                    "language",
                    "languages",
                    "prompt",
                    "response_format",
                    "temperature",
                    "stream",
                ][..]
            } else {
                &["sdp", "session"][..]
            };
            if !allowed.contains(&name.as_str()) {
                return Err(HutchError::bad_request(format!(
                    "Unsupported transcription field: {name}"
                )));
            }
            let value = field
                .text()
                .await
                .map_err(|_| HutchError::bad_request("Invalid text field".into()))?;
            if value.len() > 128 * 1024 {
                return Err(HutchError::bad_request("Text field exceeds limit".into()));
            }
            out.fields.push((name, value));
        }
    }
    Ok(out)
}
fn get<'a>(u: &'a Upload, key: &str) -> &'a str {
    u.fields
        .iter()
        .find(|(k, _)| k == key)
        .map(|(_, v)| v.as_str())
        .unwrap_or("")
}
fn bad(message: &str) -> Response {
    HutchError::bad_request(message.into()).into_response()
}
pub async fn images(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(mut body): Json<Value>,
) -> Response {
    let alias = body
        .get("model")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if !body
        .get("prompt")
        .and_then(Value::as_str)
        .is_some_and(|s| !s.trim().is_empty() && s.len() <= 32_000)
        || body.get("n").is_some_and(|n| n.as_u64() != Some(1))
        || body.get("stream").is_some_and(|v| v != false)
        || body.get("response_format").is_some_and(|v| v != "b64_json")
    {
        return bad("Expected prompt, n=1, non-streaming b64_json output");
    }
    let allowed = [
        "model",
        "prompt",
        "n",
        "size",
        "quality",
        "response_format",
        "stream",
        "background",
        "output_format",
    ];
    if body
        .as_object()
        .unwrap()
        .keys()
        .any(|k| !allowed.contains(&k.as_str()))
    {
        return bad("Unsupported image generation parameter");
    }
    body.as_object_mut().unwrap().remove("response_format");
    forward(state, headers, alias, "image", body, None).await
}
pub async fn transcribe(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    multipart: Multipart,
) -> Response {
    let u = match upload(multipart, true).await {
        Ok(u) => u,
        Err(e) => return e.into_response(),
    };
    if u.file.is_none() {
        return bad("Missing audio file");
    }
    if !["", "false"].contains(&get(&u, "stream")) {
        return bad("Streaming uploads are not supported; use live transcription");
    }
    if !["", "json", "verbose_json"].contains(&get(&u, "response_format")) {
        return bad("Unsupported transcription response_format");
    }
    let alias = get(&u, "model").to_string();
    forward(state, headers, alias, "transcription", Value::Null, Some(u)).await
}
async fn forward(
    state: Arc<AppState>,
    headers: HeaderMap,
    alias: String,
    capability: &str,
    mut body: Value,
    upload: Option<Upload>,
) -> Response {
    let tenant = match authenticate(&state, &headers).await {
        Ok(t) => t,
        Err(e) => return e.into_response(),
    };
    if tenant.status == TenantStatus::Expired {
        return HutchError::subscription_expired(&tenant.id).into_response();
    }
    let cfg = match state
        .cfg
        .media
        .iter()
        .find(|c| c.name == alias && c.capability == capability)
    {
        Some(c) => c,
        None => return bad("Unknown media model or unsupported capability"),
    };
    if !tenant.entitlements.contains(&cfg.entitlement) {
        return (StatusCode::FORBIDDEN,Json(json!({"error":{"code":"not_entitled","message":"Media capability is not enabled for this tenant"}}))).into_response();
    }
    let key = match crate::credentials::resolve(&cfg.upstream_bearer_token_env).filter(|s| !s.is_empty()) { Some(k)=>k, None=>return (StatusCode::SERVICE_UNAVAILABLE,Json(json!({"error":{"code":"provider_unconfigured","message":"Media provider is not configured"}}))).into_response() };
    let _permit = match state
        .lanes
        .acquire(&tenant.id, tenant.lanes, state.cfg.lane_grace_ms)
        .await
    {
        Some(p) => p,
        None => return HutchError::lanes_saturated(&tenant.id, tenant.lanes).into_response(),
    };
    let id = new_invocation_id();
    let started = Instant::now();
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .expect("media HTTP client");
    let mut request = client
        .post(format!(
            "{}{}",
            cfg.upstream_base.trim_end_matches('/'),
            cfg.upstream_path
        ))
        .bearer_auth(key)
        .timeout(Duration::from_millis(cfg.timeout_ms));
    let input_bytes;
    if let Some(mut u) = upload {
        let mut form = reqwest::multipart::Form::new();
        {
            let (filename, mime, bytes) = u.file.take().unwrap();
            input_bytes = bytes.len();
            let part = match reqwest::multipart::Part::bytes(bytes)
                .file_name(filename)
                .mime_str(&mime)
            {
                Ok(p) => p,
                Err(_) => return bad("Invalid audio content type"),
            };
            form = form
                .part("file", part)
                .text("model", cfg.upstream_model.clone());
            for (k, v) in u.fields {
                if k == "model" || k == "stream" {
                    continue;
                }
                if cfg.request_overrides.get(&k).is_none() {
                    form = form.text(k, v);
                }
            }
            if let Some(o) = cfg.request_overrides.as_object() {
                for (k, v) in o {
                    form = form.text(
                        k.clone(),
                        v.as_str()
                            .map(str::to_string)
                            .unwrap_or_else(|| v.to_string()),
                    );
                }
            }
        }
        request = request.multipart(form);
    } else {
        crate::merge_json_overrides(&mut body, &cfg.request_overrides);
        body["model"] = json!(cfg.upstream_model);
        input_bytes = body.to_string().len();
        request = request.json(&body);
    }
    if cfg.provider == "openrouter" {
        request = request
            .header("HTTP-Referer", crate::OPENROUTER_APP_URL)
            .header("X-Title", crate::OPENROUTER_APP_TITLE);
    }
    let result = async {
        let response = request.send().await.map_err(|_| "upstream_transport")?;
        let status = response.status();
        let response_headers = response.headers().clone();
        let mut bytes = Vec::new();
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|_| "upstream_transport")?;
            if bytes.len() + chunk.len() > 16 * 1024 * 1024 {
                return Err("response_too_large");
            }
            bytes.extend_from_slice(&chunk);
        }
        Ok((status, response_headers, bytes))
    }
    .await;
    let (status, up_headers, bytes, error) = match result {
        Ok((s, h, b)) => (s, h, b, None),
        Err(code) => (
            StatusCode::BAD_GATEWAY,
            HeaderMap::new(),
            vec![],
            Some(code),
        ),
    };
    let out: Value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    let valid = if capability == "image" {
        out.pointer("/data/0/b64_json")
            .and_then(Value::as_str)
            .is_some_and(|s| !s.is_empty())
    } else {
        out.get("text").is_some()
            || up_headers
                .get("content-type")
                .is_some_and(|h| h.to_str().unwrap_or("").starts_with("text/plain"))
    };
    let error = error.or(if !status.is_success() {
        Some("upstream_rejected")
    } else if !valid {
        Some("invalid_media_response")
    } else {
        None
    });
    let response_status = if error.is_some() {
        StatusCode::BAD_GATEWAY
    } else {
        status
    };
    let usage = out.get("usage").cloned().unwrap_or(Value::Null);
    let input = usage
        .get("prompt_tokens")
        .or_else(|| usage.get("input_tokens"))
        .and_then(Value::as_i64);
    let output = usage
        .get("completion_tokens")
        .or_else(|| usage.get("output_tokens"))
        .and_then(Value::as_i64);
    let cost = usage.get("cost").and_then(crate::usd_to_nanousd);
    let provider_id = up_headers
        .get("x-generation-id").or_else(|| up_headers.get("x-request-id"))
        .and_then(|h| h.to_str().ok())
        .or_else(|| out.get("id").and_then(Value::as_str));
    state.meter.record(MeterRow {
        invocation_id: Some(&id),
        tenant: &tenant.id,
        backend: &format!("media:{}", cfg.name),
        stream: false,
        n_inputs: 1,
        status: response_status.as_u16(),
        error_code: error,
        duration_ms: started.elapsed().as_secs_f64() * 1000.0,
        upstream_ms: Some(started.elapsed().as_secs_f64() * 1000.0),
        model_version: &cfg.model_version,
        reference_cost_nanousd: 0,
        prompt_tokens: input,
        completion_tokens: output,
    });
    state.meter.record_provider_attempt(ProviderAttempt {
        invocation_id: &id,
        tenant: &tenant.id,
        canonical_model: &cfg.name,
        provider: &cfg.provider,
        upstream_model: &cfg.upstream_model,
        provider_request_id: provider_id,
        attempt_no: 1,
        mode: capability,
        status: status.as_u16(),
        prompt_tokens: input,
        completion_tokens: output,
        provider_cost_nanousd: cost,
        cost_source: if cost.is_some() {
            "response"
        } else {
            "unavailable"
        },
        reconcile: false,
    });
    state
        .meter
        .record_media(&id, capability, input_bytes, bytes.len(), &usage);
    let mut response = if let Some(code) = error {
        (response_status,Json(json!({"error":{"code":code,"message":"Media provider failed; retry only after checking the request receipt","upstream_status":status.as_u16()}}))).into_response()
    } else {
        (
            status,
            [(
                "content-type",
                up_headers
                    .get("content-type")
                    .and_then(|h| h.to_str().ok())
                    .unwrap_or("application/json"),
            )],
            bytes,
        )
            .into_response()
    };
    for (k, v) in [
        ("x-hutch-request-id", id),
        ("x-hutch-model-version", cfg.model_version.clone()),
        ("x-hutch-upstream-model", cfg.upstream_model.clone()),
        ("x-hutch-provider", cfg.provider.clone()),
        ("x-hutch-usage-scope", "request".into()),
    ] {
        if let Ok(v) = v.parse() {
            response.headers_mut().insert(k, v);
        }
    }
    if let Some(v) = up_headers.get("x-generation-id").or_else(|| up_headers.get("x-request-id")) {
        response.headers_mut().insert("x-request-id", v.clone());
    }
    response
}
