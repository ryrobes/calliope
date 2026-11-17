//! Cheap readiness probes, not inference. Deduplicated within each inventory read.
use futures_util::{stream, StreamExt};
use serde_json::{json, Value};
use std::{
    collections::{HashMap, HashSet},
    time::Duration,
};

#[derive(Clone, Hash, PartialEq, Eq)]
pub struct Request {
    url: String,
    key: Option<String>,
}

#[derive(Clone)]
pub enum Check {
    Worker(Request),
    OpenRouter {
        key: Request,
        models: Request,
        model: String,
        modality: String,
    },
    Gateway {
        request: Request,
        backend: String,
    },
    Missing,
}

impl Check {
    pub fn worker(url: &str) -> Self {
        Self::Worker(Request {
            url: url.into(),
            key: None,
        })
    }

    pub fn provider(base: &str, key: Option<&str>, model: &str, modality: &str) -> Self {
        // Only use OpenRouter's documented authenticated endpoints on that host.
        if reqwest::Url::parse(base)
            .ok()
            .and_then(|u| u.host_str().map(str::to_owned))
            .as_deref()
            != Some("openrouter.ai")
        {
            return Self::Missing;
        }
        let root = base.trim_end_matches('/').trim_end_matches("/v1");
        let request = |path: &str| Request {
            url: format!("{root}/v1/{path}"),
            key: key.map(str::to_owned),
        };
        Self::OpenRouter {
            key: request("key"),
            models: request("models/user?output_modalities=all"),
            model: model.into(),
            modality: modality.into(),
        }
    }

    pub fn gateway(base: &str, key: Option<&str>, backend: &str) -> Self {
        Self::Gateway {
            request: Request {
                url: format!("{}/", base.trim_end_matches('/')),
                key: key.map(str::to_owned),
            },
            backend: backend.into(),
        }
    }

    fn requests(&self) -> Vec<Request> {
        match self {
            Self::Worker(r) | Self::Gateway { request: r, .. } => vec![r.clone()],
            Self::OpenRouter { key, models, .. } => vec![key.clone(), models.clone()],
            Self::Missing => vec![],
        }
    }
}

#[derive(Clone, Debug)]
pub struct Failure {
    code: &'static str,
    detail: &'static str,
}
type Evidence = HashMap<Request, Result<Value, Failure>>;

async fn fetch(client: &reqwest::Client, r: &Request) -> Result<Value, Failure> {
    let mut request = client.get(&r.url).timeout(Duration::from_secs(4));
    if let Some(key) = &r.key {
        let value = crate::credentials::resolve(key)
            .filter(|s| !s.is_empty())
            .ok_or(Failure {
                code: "missing_credentials",
                detail: "Provider credential is missing or unreadable.",
            })?;
        request = request.bearer_auth(value);
    }
    let response = request.send().await.map_err(|_| Failure {
        code: "unreachable",
        detail: "Health endpoint could not be reached within four seconds.",
    })?;
    if !response.status().is_success() {
        let (code, detail) = match response.status().as_u16() {
            401 | 403 => (
                "credentials_rejected",
                "Provider rejected the configured credential or its permissions.",
            ),
            404 => (
                "health_endpoint_missing",
                "The configured health endpoint does not exist.",
            ),
            429 => (
                "rate_limited",
                "Health endpoint is rate limited; retry the check.",
            ),
            _ => (
                "not_ready",
                "Health endpoint reported that the service is not ready.",
            ),
        };
        return Err(Failure { code, detail });
    }
    response.json().await.map_err(|_| Failure {
        code: "invalid_health_response",
        detail: "Health endpoint did not return the expected JSON.",
    })
}

pub async fn collect(checks: impl Iterator<Item = Check>) -> Evidence {
    let requests: HashSet<_> = checks.flat_map(|c| c.requests()).collect();
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .unwrap();
    stream::iter(requests)
        .map(|r| {
            let client = client.clone();
            async move {
                let result = fetch(&client, &r).await;
                (r, result)
            }
        })
        .buffer_unordered(12)
        .collect()
        .await
}

fn worker_ready(value: &Value) -> bool {
    // A 200 with {ok:false}, an error, or missing dependency isn't readiness.
    let negative = [
        "ok",
        "ready",
        "loaded",
        "weights_present",
        "credential_present",
        "embedding_configured",
        "cuda_available",
    ]
    .iter()
    .any(|k| value.get(k) == Some(&json!(false)));
    if negative || value.get("error").is_some_and(|e| !e.is_null()) {
        return false;
    }
    value.get("ok") == Some(&json!(true))
        || value.get("ready") == Some(&json!(true))
        || value.get("loaded") == Some(&json!(true))
        || matches!(
            value.get("status").and_then(Value::as_str),
            Some("ok" | "ready" | "healthy")
        )
}

fn verify(
    check: &Check,
    evidence: &Evidence,
) -> Result<(&'static str, &'static str, &'static str), Failure> {
    let read = |r: &Request| {
        evidence
            .get(r)
            .expect("all planned requests collected")
            .as_ref()
            .map_err(Clone::clone)
    };
    match check {
        Check::Missing => Ok((
            "configured",
            "not_checked",
            "No readiness contract is configured for this endpoint.",
        )),
        Check::Worker(r) => {
            let data = read(r)?;
            if !worker_ready(data) {
                return Err(Failure {
                    code: "worker_not_ready",
                    detail:
                        "Worker JSON does not confirm readiness, or reports a missing dependency.",
                });
            }
            Ok(("ready", "worker", "Worker reports readiness and its declared prerequisites. Inference is tested separately in Checks."))
        }
        Check::OpenRouter {
            key,
            models,
            model,
            modality,
        } => {
            let account = read(key)?;
            if !account.get("data").is_some_and(Value::is_object) {
                return Err(Failure {
                    code: "invalid_health_response",
                    detail: "Provider returned an invalid credential check.",
                });
            }
            if account["data"]["limit_remaining"]
                .as_f64()
                .is_some_and(|n| n <= 0.0)
            {
                return Err(Failure {
                    code: "provider_limit_exhausted",
                    detail: "The configured provider key has exhausted its spending limit.",
                });
            }
            let catalogue = read(models)?;
            let rows = catalogue["data"].as_array().ok_or(Failure {
                code: "invalid_health_response",
                detail: "Provider did not return a model catalogue.",
            })?;
            let found = rows.iter().find(|v| v["id"] == *model).ok_or(Failure {
                code: "model_unavailable",
                detail: "The configured model is absent from this key's available model catalogue.",
            })?;
            if !found["architecture"]["output_modalities"]
                .as_array()
                .is_some_and(|a| a.iter().any(|v| v == modality))
            {
                return Err(Failure {
                    code: "modality_unavailable",
                    detail: "The configured model does not advertise the required output type.",
                });
            }
            Ok(("ready", "provider", "Provider accepted the key and lists this model for the required output type. No inference request was made."))
        }
        Check::Gateway { request, backend } => {
            let data = read(request)?;
            if data["service"] != "rvbbit-hutch"
                || data["ok"] != true
                || !data["backends"].is_array()
            {
                return Err(Failure {
                    code: "invalid_health_response",
                    detail: "Remote gateway did not return its capability registry.",
                });
            }
            if !data["backends"]
                .as_array()
                .unwrap()
                .iter()
                .any(|v| v == backend)
            {
                return Err(Failure {
                    code: "route_missing",
                    detail: "This capability is not registered on the remote gateway.",
                });
            }
            Ok(("configured", "route_only", "Remote gateway lists this route but exposes no worker readiness check. Run its SQL tests in Checks to verify execution."))
        }
    }
}

pub fn apply(entry: &mut Value, check: &Check, evidence: &Evidence) {
    entry["probe"] = json!(match check {
        Check::Worker(_) => "worker-health",
        Check::OpenRouter { .. } => "provider-readiness",
        Check::Gateway { .. } => "gateway-registry",
        Check::Missing => "none",
    });
    match verify(check, evidence) {
        Ok((state, scope, detail)) => {
            entry["state"] = json!(state);
            entry["health_scope"] = json!(scope);
            entry["health_detail"] = json!(detail);
        }
        Err(error) => {
            entry["state"] = json!("unavailable");
            entry["health_code"] = json!(error.code);
            entry["health_detail"] = json!(error.detail);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn readiness_requires_positive_evidence_and_dependencies() {
        assert!(worker_ready(&json!({"ok":true,"loaded":true})));
        assert!(worker_ready(&json!({"status":"ready"})));
        for value in [
            json!({}),
            json!({"status":"missing_provider_key"}),
            json!({"ok":true,"loaded":false}),
            json!({"status":"ready","embedding_configured":false}),
            json!({"ok":false}),
            json!({"ok":true,"error":"failed"}),
        ] {
            assert!(!worker_ready(&value), "{value}");
        }
    }

    #[test]
    fn hosted_model_requires_authentication_and_correct_modality() {
        let check = Check::provider(
            "https://openrouter.ai/api/v1",
            Some("OR_TEST"),
            "test/model",
            "embeddings",
        );
        let Check::OpenRouter { key, models, .. } = &check else {
            panic!()
        };
        let mut evidence = HashMap::from([
            (key.clone(), Ok(json!({"data":{"limit_remaining":null}}))),
            (
                models.clone(),
                Ok(
                    json!({"data":[{"id":"test/model","architecture":{"output_modalities":["embeddings"]}}]}),
                ),
            ),
        ]);
        assert_eq!(verify(&check, &evidence).unwrap().1, "provider");
        evidence.insert(
            models.clone(),
            Ok(json!({"data":[{"id":"test/model","architecture":{"output_modalities":["text"]}}]})),
        );
        assert_eq!(
            verify(&check, &evidence).unwrap_err().code,
            "modality_unavailable"
        );
        evidence.insert(models.clone(), Ok(json!({"data":[]})));
        assert_eq!(
            verify(&check, &evidence).unwrap_err().code,
            "model_unavailable"
        );
        evidence.insert(
            key.clone(),
            Err(Failure {
                code: "credentials_rejected",
                detail: "Rejected",
            }),
        );
        assert_eq!(
            verify(&check, &evidence).unwrap_err().code,
            "credentials_rejected"
        );
    }

    #[test]
    fn registry_does_not_claim_worker_health_and_detects_missing_routes() {
        let check = Check::gateway("https://legacy.test", Some("KEY"), "ocr");
        let Check::Gateway { request, .. } = &check else {
            panic!()
        };
        let mut evidence = HashMap::from([(
            request.clone(),
            Ok(json!({"ok":true,"service":"rvbbit-hutch","backends":["ocr"]})),
        )]);
        assert_eq!(verify(&check, &evidence).unwrap().0, "configured");
        assert_eq!(verify(&check, &evidence).unwrap().1, "route_only");
        evidence.insert(
            request.clone(),
            Ok(json!({"ok":true,"service":"rvbbit-hutch","backends":[]})),
        );
        assert_eq!(verify(&check, &evidence).unwrap_err().code, "route_missing");
    }

    #[tokio::test]
    async fn deduplicates_network_probes_and_rejects_http200_failure_bodies() {
        use axum::{routing::get, Json, Router};
        use std::sync::{
            atomic::{AtomicUsize, Ordering},
            Arc,
        };
        let count = Arc::new(AtomicUsize::new(0));
        let observed = count.clone();
        let app = Router::new().route(
            "/health",
            get(move || {
                let count = observed.clone();
                async move {
                    count.fetch_add(1, Ordering::SeqCst);
                    Json(json!({"ok":false}))
                }
            }),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}/health", listener.local_addr().unwrap());
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        let checks = vec![Check::worker(&url); 8];
        let evidence = collect(checks.clone().into_iter()).await;
        assert_eq!(count.load(Ordering::SeqCst), 1);
        let mut entry = json!({"inference_verified":false});
        apply(&mut entry, &checks[0], &evidence);
        assert_eq!(entry["state"], "unavailable");
        assert_eq!(entry["health_code"], "worker_not_ready");
        assert_eq!(entry["inference_verified"], false);
        server.abort();
    }
}
