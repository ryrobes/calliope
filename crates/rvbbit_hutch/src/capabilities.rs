//! Authenticated configuration/readiness inventory. No paid inference probes.
use crate::{authenticate, AppState, HutchError, TenantStatus};
use crate::{
    capability_health::{self, Check},
    config::{Adapter, Upstream},
};
use axum::{
    extract::{Query, State},
    http::HeaderMap,
    response::{IntoResponse, Response},
    Json,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::Arc;

#[derive(Deserialize, Default)]
pub struct Options {
    #[serde(default)]
    probe: bool,
}

fn configured(name: &str, kind: &str, version: &str, key: Option<&str>) -> Value {
    let present =
        key.is_none_or(|k| crate::credentials::resolve(k).is_some_and(|v| !v.trim().is_empty()));
    json!({"id":name,"kind":kind,"model_version":version,"enabled":true,
        "state":if present {"configured"} else {"missing_credentials"},
        "credential_env":key,"inference_verified":false})
}

pub async fn list(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Query(options): Query<Options>,
) -> Response {
    let tenant = match authenticate(&state, &headers).await {
        Ok(t) => t,
        Err(e) => return e.into_response(),
    };
    if tenant.status == TenantStatus::Expired {
        return HutchError::subscription_expired(&tenant.id).into_response();
    }
    let allowed = |entitlement: &String| tenant.entitlements.contains(entitlement);
    let mut entries = Vec::new();
    for b in state
        .cfg
        .backends
        .iter()
        .filter(|b| allowed(&b.entitlement))
    {
        let mut entry = configured(
            &b.name,
            "specialist",
            &b.model_version,
            b.upstream_bearer_token_env.as_deref(),
        );
        if let Some(params) = &b.upstream_params {
            entry["model"] = params.get("model").cloned().unwrap_or(Value::Null);
            if b.adapter == crate::config::Adapter::OpenaiEmbeddings {
                entry["dimensions"] = params.get("dimensions").cloned().unwrap_or(Value::Null);
            }
        }
        if !b.enabled {
            entry["enabled"] = json!(false);
            entry["state"] = json!("disabled");
            entry["reason"] = json!(b.unavailable_reason);
        }
        let base = b
            .upstream_base
            .as_deref()
            .or(match &state.cfg.upstream {
                Upstream::Proxy { base_url } => Some(base_url.as_str()),
                Upstream::Mock => None,
            })
            .unwrap_or("");
        let check = if let Some(url) = &b.health_url {
            Check::worker(url)
        } else if matches!(
            b.adapter,
            Adapter::OpenaiEmbeddings | Adapter::HostedRerank | Adapter::HostedExtractions
        ) {
            Check::provider(
                base,
                b.upstream_bearer_token_env.as_deref(),
                entry["model"].as_str().unwrap_or(""),
                match b.adapter {
                    Adapter::OpenaiEmbeddings => "embeddings",
                    // OpenRouter lists typed-field extraction models under
                    // the decisions output modality.
                    Adapter::HostedExtractions => "decisions",
                    _ => "rerank",
                },
            )
        } else if b.adapter == Adapter::Predict
            && b.upstream_path
                .as_deref()
                .is_some_and(|p| p.starts_with("/b/"))
        {
            let backend = b
                .upstream_path
                .as_deref()
                .unwrap()
                .trim_start_matches("/b/")
                .trim_end_matches("/predict");
            Check::gateway(base, b.upstream_bearer_token_env.as_deref(), backend)
        } else {
            Check::Missing
        };
        entries.push((entry, check));
    }
    for b in state.cfg.llms.iter().filter(|b| allowed(&b.entitlement)) {
        entries.push((
            configured(
                &b.name,
                "chat",
                &b.model_version,
                b.upstream_bearer_token_env.as_deref(),
            ),
            Check::provider(
                &b.upstream_base,
                b.upstream_bearer_token_env.as_deref(),
                &b.upstream_model,
                "text",
            ),
        ));
    }
    for b in state.cfg.speech.iter().filter(|b| allowed(&b.entitlement)) {
        entries.push((
            configured(
                &b.name,
                "speech",
                &b.model_version,
                Some(&b.upstream_bearer_token_env),
            ),
            Check::provider(
                &b.upstream_base,
                Some(&b.upstream_bearer_token_env),
                &b.upstream_model,
                "speech",
            ),
        ));
    }
    for b in state.cfg.media.iter().filter(|b| allowed(&b.entitlement)) {
        entries.push((
            configured(
                &b.name,
                &b.capability,
                &b.model_version,
                Some(&b.upstream_bearer_token_env),
            ),
            Check::provider(
                &b.upstream_base,
                Some(&b.upstream_bearer_token_env),
                &b.upstream_model,
                &b.capability,
            ),
        ));
    }
    if options.probe {
        let evidence = capability_health::collect(
            entries
                .iter()
                .filter(|(v, _)| v["state"] == "configured")
                .map(|(_, check)| check.clone()),
        )
        .await;
        for (entry, check) in &mut entries {
            if entry["state"] == "configured" {
                capability_health::apply(entry, check, &evidence);
            }
        }
    }
    let mut entries: Vec<Value> = entries.into_iter().map(|(entry, _)| entry).collect();
    entries.sort_by_key(|v| v["id"].as_str().unwrap_or("").to_string());
    ([("cache-control","no-store")],Json(json!({"object":"capability.list","billing":"static",
        "note":"Configured credentials and worker health do not prove successful inference.","data":entries}))).into_response()
}
