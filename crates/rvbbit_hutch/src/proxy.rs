//! Upstream dispatch — mock (deterministic, GPU-free) or proxy with
//! per-backend adapters translating the predict contract to the zoo's
//! route shapes (captured live 2026-07-12; see Adapter in config.rs).

use crate::config::{Adapter, BackendCfg, Upstream};
use serde_json::{json, Value};
use std::time::Instant;

pub struct ForwardOk {
    pub outputs: Vec<Value>,
    pub upstream_ms: f64,
    /// Provider-supplied OpenAI usage metadata, when that adapter exposes it.
    pub usage: Option<Value>,
}

#[derive(Debug)]
pub enum ForwardErr {
    /// Upstream answered non-2xx: propagate enough detail to diagnose.
    Status { status: u16, body_head: String },
    /// Transport-level failure (connect, timeout, bad JSON).
    Transport(String),
}

pub async fn forward(
    http: &reqwest::Client,
    upstream: &Upstream,
    backend: &BackendCfg,
    inputs: &[Value],
    tenant_scope: Option<&str>,
) -> Result<ForwardOk, ForwardErr> {
    let t0 = Instant::now();
    match upstream {
        Upstream::Mock => {
            if let Some(ms) = backend.mock_delay_ms {
                tokio::time::sleep(std::time::Duration::from_millis(ms)).await;
            }
            let one = backend
                .mock_output
                .clone()
                .unwrap_or_else(|| json!({"mock": true}));
            Ok(ForwardOk {
                outputs: vec![one; inputs.len()],
                upstream_ms: t0.elapsed().as_secs_f64() * 1000.0,
                usage: None,
            })
        }
        Upstream::Proxy { base_url } => {
            let path = backend.upstream_path.as_deref().unwrap_or("/predict");
            let base_url = backend.upstream_base.as_deref().unwrap_or(base_url);
            let url = format!("{}{}", base_url.trim_end_matches('/'), path);
            let mut usage = None;
            let outputs = match backend.adapter {
                Adapter::Predict => {
                    let parsed = post_json_scoped(
                        http,
                        &url,
                        backend,
                        &json!({ "inputs": inputs }),
                        if backend.forward_tenant_scope {
                            tenant_scope
                        } else {
                            None
                        },
                    )
                    .await?;
                    // Some SQL outputs must remain arrays/scalars. Preserve their
                    // batch-level usage separately instead of requiring an object per row.
                    usage = parsed.get("usage").filter(|u| u.is_object()).cloned();
                    json_array(&parsed, "outputs")?
                }
                Adapter::OpenaiEmbeddings => {
                    let texts = input_texts(inputs)?;
                    let mut body = json!({ "input": texts });
                    merge_params(&mut body, backend);
                    // Inputs belong to this request, model/dimensions to the route.
                    body["input"] = json!(texts);
                    let parsed = post_json(http, &url, backend, &body).await?;
                    usage = parsed.get("usage").cloned();
                    let items = json_array(&parsed, "data")?;
                    if items.len() != texts.len() {
                        return Err(ForwardErr::Transport(
                            "Embedding result count differs from input".into(),
                        ));
                    }
                    let indexed = items.iter().any(|item| item.get("index").is_some());
                    let mut vectors = vec![Value::Null; texts.len()];
                    for (position, item) in items.into_iter().enumerate() {
                        let index = if indexed {
                            item.get("index")
                                .and_then(Value::as_u64)
                                .map(|n| n as usize)
                        } else {
                            Some(position)
                        }
                        .filter(|i| *i < texts.len())
                        .ok_or_else(|| ForwardErr::Transport("Invalid embedding index".into()))?;
                        let vector = item
                            .get("embedding")
                            .and_then(Value::as_array)
                            .filter(|v| !v.is_empty() && v.iter().all(Value::is_number))
                            .ok_or_else(|| {
                                ForwardErr::Transport("Invalid embedding vector".into())
                            })?;
                        if let Some(expected) = backend
                            .upstream_params
                            .as_ref()
                            .and_then(|p| p.get("dimensions"))
                            .and_then(Value::as_u64)
                        {
                            if vector.len() != expected as usize {
                                return Err(ForwardErr::Transport(
                                    "Embedding dimensions differ from configured profile".into(),
                                ));
                            }
                        }
                        if !vectors[index].is_null() {
                            return Err(ForwardErr::Transport("Duplicate embedding index".into()));
                        }
                        vectors[index] = json!(vector);
                    }
                    vectors
                }
                Adapter::ZooSentiment => {
                    let texts = input_texts(inputs)?;
                    let parsed = post_json(http, &url, backend, &json!({ "texts": texts })).await?;
                    let scores = json_array(&parsed, "scores")?;
                    let labels = json_array(&parsed, "labels")?;
                    scores
                        .iter()
                        .zip(labels.iter())
                        .map(|(s, l)| json!({"score": s, "label": l}))
                        .collect()
                }
                Adapter::ZooRerank => rerank_grouped(http, &url, backend, inputs).await?,
                Adapter::HostedRerank => {
                    let (scores, billed) =
                        hosted_rerank_grouped(http, &url, backend, inputs).await?;
                    usage = billed;
                    scores
                }
                Adapter::HostedExtractions => {
                    let (answers, billed) = hosted_extractions(http, &url, backend, inputs).await?;
                    usage = billed;
                    answers
                }
                Adapter::ZooNli => {
                    let pairs: Vec<Value> = inputs
                        .iter()
                        .enumerate()
                        .map(|(i, v)| {
                            let p = field_str(v, "premise", i)?;
                            let h = field_str(v, "hypothesis", i)?;
                            Ok(json!({"premise": p, "hypothesis": h}))
                        })
                        .collect::<Result<_, ForwardErr>>()?;
                    let mut body = json!({ "pairs": pairs });
                    merge_params(&mut body, backend);
                    let parsed = post_json(http, &url, backend, &body).await?;
                    json_array(&parsed, "results")?
                        .iter()
                        .map(|r| {
                            let scores = r.get("scores").cloned().unwrap_or(json!({}));
                            json!({"label": argmax_label(&scores), "scores": scores})
                        })
                        .collect()
                }
                Adapter::ZooClassify => {
                    let mut outs = Vec::with_capacity(inputs.len());
                    for (i, v) in inputs.iter().enumerate() {
                        let text = field_str(v, "text", i)?;
                        let labels = labels_input(v, i)?;
                        let mut body = json!({ "text": text, "labels": labels });
                        merge_params(&mut body, backend);
                        let parsed = post_json(http, &url, backend, &body).await?;
                        outs.push(json!({
                            "label": parsed.get("label").cloned().unwrap_or(Value::Null),
                            "score": parsed.get("score").cloned().unwrap_or(Value::Null),
                            "scores": parsed.get("scores").cloned().unwrap_or(json!({})),
                        }));
                    }
                    outs
                }
                Adapter::ZooToxicity => {
                    let texts = input_texts(inputs)?;
                    let mut body = json!({ "texts": texts });
                    merge_params(&mut body, backend);
                    let parsed = post_json(http, &url, backend, &body).await?;
                    json_array(&parsed, "results")?
                        .iter()
                        .map(|r| {
                            json!({
                                "toxic": r.get("is_toxic").cloned().unwrap_or(Value::Null),
                                "score": r.get("overall").cloned().unwrap_or(Value::Null),
                                "scores": r.get("scores").cloned().unwrap_or(json!({})),
                            })
                        })
                        .collect()
                }
                Adapter::ZooLanguage => {
                    let texts = input_texts(inputs)?;
                    let mut body = json!({ "texts": texts });
                    merge_params(&mut body, backend);
                    let parsed = post_json(http, &url, backend, &body).await?;
                    json_array(&parsed, "results")?
                        .iter()
                        .map(|r| {
                            json!({
                                "language": r.get("language").cloned().unwrap_or(Value::Null),
                                "confidence": r.get("confidence").cloned().unwrap_or(Value::Null),
                            })
                        })
                        .collect()
                }
                Adapter::ZooExtract => {
                    let mut outs = Vec::with_capacity(inputs.len());
                    for (i, v) in inputs.iter().enumerate() {
                        let text = field_str(v, "text", i)?;
                        let labels = labels_input(v, i)?;
                        let mut body = json!({ "texts": [text], "labels": labels });
                        merge_params(&mut body, backend);
                        let parsed = post_json(http, &url, backend, &body).await?;
                        let first = json_array(&parsed, "results")?
                            .into_iter()
                            .next()
                            .unwrap_or(json!([]));
                        outs.push(first);
                    }
                    outs
                }
                Adapter::ZooJson => {
                    let mut outs = Vec::with_capacity(inputs.len());
                    for (i, v) in inputs.iter().enumerate() {
                        let mut body = v.clone();
                        if !body.is_object() {
                            return Err(ForwardErr::Transport(format!(
                                "input[{i}] must be a JSON object for this backend"
                            )));
                        }
                        coerce_json_strings(&mut body);
                        merge_params(&mut body, backend);
                        outs.push(post_json(http, &url, backend, &body).await?);
                    }
                    outs
                }
                Adapter::ZooRelations => {
                    let texts = input_texts(inputs)?;
                    let mut body = json!({ "texts": texts });
                    merge_params(&mut body, backend);
                    let parsed = post_json(http, &url, backend, &body).await?;
                    json_array(&parsed, "results")?
                }
                Adapter::ZooImageEmbeddings => {
                    let items: Vec<String> = inputs
                        .iter()
                        .enumerate()
                        .map(|(i, v)| input_text(v, i))
                        .collect::<Result<_, _>>()?;
                    let mut body = json!({ "input": items });
                    merge_params(&mut body, backend);
                    let parsed = post_json(http, &url, backend, &body).await?;
                    json_array(&parsed, "data")?
                        .iter()
                        .map(|item| {
                            item.get("embedding").cloned().ok_or_else(|| {
                                ForwardErr::Transport(
                                    "image_embeddings item missing 'embedding'".into(),
                                )
                            })
                        })
                        .collect::<Result<Vec<_>, _>>()?
                }
            };
            if outputs.len() != inputs.len() {
                return Err(ForwardErr::Transport(format!(
                    "adapter produced {} outputs for {} inputs",
                    outputs.len(),
                    inputs.len()
                )));
            }
            Ok(ForwardOk {
                outputs,
                upstream_ms: t0.elapsed().as_secs_f64() * 1000.0,
                usage,
            })
        }
    }
}

async fn post_json(
    http: &reqwest::Client,
    url: &str,
    backend: &BackendCfg,
    body: &Value,
) -> Result<Value, ForwardErr> {
    post_json_scoped(http, url, backend, body, None).await
}

async fn post_json_scoped(
    http: &reqwest::Client,
    url: &str,
    backend: &BackendCfg,
    body: &Value,
    tenant_scope: Option<&str>,
) -> Result<Value, ForwardErr> {
    let token = match &backend.upstream_bearer_token_env {
        Some(name) => Some(
            crate::credentials::resolve(name)
                .filter(|v| !v.trim().is_empty())
                .ok_or_else(|| {
                    ForwardErr::Transport(format!("Missing upstream credential {name}"))
                })?,
        ),
        None => None,
    };
    let mut attempt = 0;
    loop {
        let mut request = http
            .post(url)
            .timeout(std::time::Duration::from_millis(backend.timeout_ms));
        if let Some(token) = &token {
            request = request.bearer_auth(token);
        }
        if let Some(tenant_scope) = tenant_scope {
            request = request.header("x-clover-tenant-scope", tenant_scope);
        }
        // Some(wait hint) when the request may be sent again.
        let (err, retry) = match request.json(body).send().await {
            Ok(resp) if resp.status().is_success() => {
                return resp
                    .json()
                    .await
                    .map_err(|e| ForwardErr::Transport(format!("bad upstream JSON: {e}")));
            }
            Ok(resp) => {
                let status = resp.status();
                let hinted = retry_after(resp.headers());
                let body = resp.text().await.unwrap_or_default();
                let err = ForwardErr::Status {
                    status: status.as_u16(),
                    body_head: if backend.upstream_bearer_token_env.is_some() {
                        "Authenticated upstream rejected the request".into()
                    } else {
                        body.chars().take(300).collect()
                    },
                };
                let unprocessed = matches!(status.as_u16(), 429 | 503);
                (err, unprocessed.then_some(hinted))
            }
            // A refused or failed connection never reached the upstream; a
            // timeout may have, so it is not retried.
            Err(e) => {
                let retry = e.is_connect().then_some(None);
                (ForwardErr::Transport(e.to_string()), retry)
            }
        };
        let Some(hint) = retry.filter(|_| attempt < backend.upstream_retries) else {
            tracing::warn!(backend = %backend.name, attempts = attempt + 1, error = %describe(&err),
                "upstream request failed");
            return Err(err);
        };
        let wait = hint.unwrap_or_else(|| backoff(attempt));
        tracing::warn!(backend = %backend.name, attempt = attempt + 1, wait_ms = wait.as_millis() as u64,
            error = %describe(&err), "upstream did not take the request; retrying");
        tokio::time::sleep(wait).await;
        attempt += 1;
    }
}

/// 300 ms, then 900 ms: a shared per-minute limit usually clears within a second.
fn backoff(attempt: u32) -> std::time::Duration {
    std::time::Duration::from_millis(300 * 3_u64.pow(attempt.min(4)))
}

/// A Retry-After of a few seconds is honoured; a longer one is not worth holding the caller for.
fn retry_after(headers: &reqwest::header::HeaderMap) -> Option<std::time::Duration> {
    headers
        .get(reqwest::header::RETRY_AFTER)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.trim().parse::<f64>().ok())
        .filter(|s| s.is_finite() && *s >= 0.0 && *s <= 5.0)
        .map(std::time::Duration::from_secs_f64)
}

fn describe(err: &ForwardErr) -> String {
    match err {
        ForwardErr::Status { status, body_head } => format!("HTTP {status}: {body_head}"),
        ForwardErr::Transport(detail) => detail.clone(),
    }
}

fn json_array(v: &Value, key: &str) -> Result<Vec<Value>, ForwardErr> {
    v.get(key)
        .and_then(|o| o.as_array())
        .cloned()
        .ok_or_else(|| ForwardErr::Transport(format!("upstream response missing '{key}' array")))
}

/// A text input is either a bare string or {"text": "..."} (the shape
/// pg_rvbbit's embed path sends).
fn input_text(v: &Value, i: usize) -> Result<String, ForwardErr> {
    if let Some(s) = v.as_str() {
        return Ok(s.to_string());
    }
    v.get("text")
        .and_then(|t| t.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| {
            ForwardErr::Transport(format!(
                "input[{i}] is neither a string nor an object with a 'text' field"
            ))
        })
}

/// SQL operator args are text, so array/object-valued fields (tabular X/y,
/// forecast context, feature_names) often arrive as JSON *strings*. Any
/// top-level string value that looks like a JSON array/object and parses
/// cleanly is replaced with the parsed value; everything else (URLs, b64
/// documents, labels) passes through untouched.
fn coerce_json_strings(body: &mut Value) {
    if let Some(obj) = body.as_object_mut() {
        for (_, v) in obj.iter_mut() {
            if let Some(s) = v.as_str() {
                let t = s.trim_start();
                if t.starts_with('[') || t.starts_with('{') {
                    if let Ok(parsed) = serde_json::from_str::<Value>(s) {
                        *v = parsed;
                    }
                }
            }
        }
    }
}

fn merge_params(body: &mut Value, backend: &BackendCfg) {
    if let (Some(Value::Object(params)), Some(obj)) =
        (backend.upstream_params.as_ref(), body.as_object_mut())
    {
        for (k, v) in params {
            obj.entry(k.clone()).or_insert_with(|| v.clone());
        }
    }
}

fn field_str(v: &Value, key: &str, i: usize) -> Result<String, ForwardErr> {
    v.get(key)
        .and_then(|s| s.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| ForwardErr::Transport(format!("input[{i}] missing '{key}'")))
}

/// Labels arrive as a JSON array or a comma-separated string (SQL args are
/// text, so 'billing, shipping, returns' must Just Work).
fn labels_input(v: &Value, i: usize) -> Result<Vec<String>, ForwardErr> {
    match v.get("labels") {
        Some(Value::Array(a)) => Ok(a
            .iter()
            .filter_map(|x| x.as_str().map(|s| s.trim().to_string()))
            .filter(|s| !s.is_empty())
            .collect()),
        Some(Value::String(s)) => Ok(s
            .split(',')
            .map(|p| p.trim().to_string())
            .filter(|p| !p.is_empty())
            .collect()),
        _ => Err(ForwardErr::Transport(format!(
            "input[{i}] missing 'labels' (array or comma-separated string)"
        ))),
    }
}

fn argmax_label(scores: &Value) -> Value {
    scores
        .as_object()
        .and_then(|m| {
            m.iter()
                .max_by(|a, b| {
                    let fa = a.1.as_f64().unwrap_or(f64::MIN);
                    let fb = b.1.as_f64().unwrap_or(f64::MIN);
                    fa.partial_cmp(&fb).unwrap_or(std::cmp::Ordering::Equal)
                })
                .map(|(k, _)| Value::String(k.clone()))
        })
        .unwrap_or(Value::Null)
}

fn input_texts(inputs: &[Value]) -> Result<Vec<String>, ForwardErr> {
    inputs
        .iter()
        .enumerate()
        .map(|(i, v)| input_text(v, i))
        .collect()
}

/// Rerank inputs are {"query","text"} pairs. The zoo scores one query
/// against many passages per call, so group by query (the common case —
/// one criterion, many rows — is exactly one upstream call) and scatter
/// scores back to original positions.
async fn rerank_grouped(
    http: &reqwest::Client,
    url: &str,
    backend: &BackendCfg,
    inputs: &[Value],
) -> Result<Vec<Value>, ForwardErr> {
    let mut groups: Vec<(String, Vec<(usize, String)>)> = Vec::new();
    for (i, v) in inputs.iter().enumerate() {
        let query = v
            .get("query")
            .and_then(|q| q.as_str())
            .ok_or_else(|| ForwardErr::Transport(format!("input[{i}] missing 'query'")))?;
        let text = v
            .get("text")
            .and_then(|t| t.as_str())
            .ok_or_else(|| ForwardErr::Transport(format!("input[{i}] missing 'text'")))?;
        match groups.iter_mut().find(|(q, _)| q == query) {
            Some((_, members)) => members.push((i, text.to_string())),
            None => groups.push((query.to_string(), vec![(i, text.to_string())])),
        }
    }
    let mut outputs = vec![Value::Null; inputs.len()];
    for (query, members) in &groups {
        let passages: Vec<&str> = members.iter().map(|(_, t)| t.as_str()).collect();
        let parsed = post_json(
            http,
            url,
            backend,
            &json!({ "query": query, "passages": passages }),
        )
        .await?;
        let scores = json_array(&parsed, "scores")?;
        if scores.len() != members.len() {
            return Err(ForwardErr::Transport(format!(
                "rerank returned {} scores for {} passages",
                scores.len(),
                members.len()
            )));
        }
        for ((idx, _), score) in members.iter().zip(scores.iter()) {
            outputs[*idx] = score.clone();
        }
    }
    Ok(outputs)
}

/// Preserve SQL row order: upstream rerank results arrive in relevance order.
/// Scores are the provider's native values, without BGE-specific calibration.
async fn hosted_rerank_grouped(
    http: &reqwest::Client,
    url: &str,
    backend: &BackendCfg,
    inputs: &[Value],
) -> Result<(Vec<Value>, Option<Value>), ForwardErr> {
    let mut groups: Vec<(String, Vec<(usize, String)>)> = Vec::new();
    for (i, input) in inputs.iter().enumerate() {
        let query = field_str(input, "query", i)?;
        let text = field_str(input, "text", i)?;
        if let Some((_, members)) = groups.iter_mut().find(|(q, _)| q == &query) {
            members.push((i, text));
        } else {
            groups.push((query, vec![(i, text)]));
        }
    }
    let mut outputs = vec![Value::Null; inputs.len()];
    let (mut cost, mut tokens, mut has_usage) = (0.0, 0_u64, false);
    let batches: Vec<(String, Vec<(usize, String)>)> = groups
        .into_iter()
        .flat_map(|(query, members)| {
            members
                .chunks(backend.rerank_batch.max(1))
                .map(|chunk| (query.clone(), chunk.to_vec()))
                .collect::<Vec<_>>()
        })
        .collect();
    let answers = futures_util::future::try_join_all(batches.iter().map(|(query, members)| {
        let mut body = json!({"query":query,
            "documents":members.iter().map(|(_, text)| text).collect::<Vec<_>>(),
            "top_n":members.len()});
        merge_params(&mut body, backend);
        async move { post_json(http, url, backend, &body).await }
    }))
    .await?;
    for ((_, members), parsed) in batches.iter().zip(answers) {
        let results = json_array(&parsed, "results")?;
        if results.len() != members.len() {
            return Err(ForwardErr::Transport(
                "Rerank result count differs from input".into(),
            ));
        }
        for result in results {
            let index = result
                .get("index")
                .and_then(Value::as_u64)
                .filter(|n| (*n as usize) < members.len())
                .ok_or_else(|| ForwardErr::Transport("Invalid rerank index".into()))?
                as usize;
            let score = result
                .get("relevance_score")
                .and_then(Value::as_f64)
                .filter(|s| s.is_finite())
                .ok_or_else(|| ForwardErr::Transport("Invalid rerank score".into()))?;
            let slot = &mut outputs[members[index].0];
            if !slot.is_null() {
                return Err(ForwardErr::Transport("Duplicate rerank index".into()));
            }
            *slot = json!(score);
        }
        if let Some(u) = parsed.get("usage") {
            has_usage = true;
            tokens += u.get("total_tokens").and_then(Value::as_u64).unwrap_or(0);
            cost += u.get("cost").and_then(Value::as_f64).unwrap_or(0.0);
        }
    }
    Ok((
        outputs,
        has_usage.then(|| json!({"prompt_tokens":tokens,"total_tokens":tokens,"cost":cost})),
    ))
}

/// One extraction request per input, `parallel_requests` at a time. SQL
/// sends `fields` as a JSON string, and either as a list of named fields
/// (Cloudflare's shape) or as an object keyed by name (OpenRouter's); the
/// upstream gets the object. An absent reference date is left out rather
/// than sent as null.
async fn hosted_extractions(
    http: &reqwest::Client,
    url: &str,
    backend: &BackendCfg,
    inputs: &[Value],
) -> Result<(Vec<Value>, Option<Value>), ForwardErr> {
    use futures_util::{StreamExt, TryStreamExt};
    let bodies = inputs
        .iter()
        .enumerate()
        .map(|(i, input)| extraction_request(input, i, backend))
        .collect::<Result<Vec<_>, _>>()?;
    let answers: Vec<Value> = futures_util::stream::iter(bodies.into_iter().map(|body| async move {
        post_json(http, url, backend, &body).await
    }))
    .buffered(backend.parallel_requests.max(1))
    .try_collect()
    .await?;
    let (mut cost, mut tokens, mut has_usage) = (0.0, 0_u64, false);
    let mut outputs = Vec::with_capacity(answers.len());
    for mut parsed in answers {
        if !parsed.get("answers").is_some_and(Value::is_object) {
            return Err(ForwardErr::Transport(
                "extraction response missing 'answers' object".into(),
            ));
        }
        if let Some(u) = parsed.get("usage") {
            has_usage = true;
            tokens += u.get("input_tokens").and_then(Value::as_u64).unwrap_or(0);
            cost += u.get("cost").and_then(Value::as_f64).unwrap_or(0.0);
        }
        if let Some(obj) = parsed.as_object_mut() {
            obj.remove("user_id");
        }
        outputs.push(parsed);
    }
    Ok((
        outputs,
        has_usage.then(|| json!({"prompt_tokens":tokens,"total_tokens":tokens,"cost":cost})),
    ))
}

fn extraction_request(input: &Value, i: usize, backend: &BackendCfg) -> Result<Value, ForwardErr> {
    let mut body = input.clone();
    if !body.is_object() {
        return Err(ForwardErr::Transport(format!(
            "input[{i}] must be a JSON object for this backend"
        )));
    }
    coerce_json_strings(&mut body);
    let obj = body.as_object_mut().expect("checked above");
    if let Some(Value::Array(list)) = obj.get("fields") {
        let mut named = serde_json::Map::new();
        for (j, field) in list.iter().enumerate() {
            let mut field = field.as_object().cloned().ok_or_else(|| {
                ForwardErr::Transport(format!("input[{i}].fields[{j}] must be an object"))
            })?;
            let name = field
                .remove("name")
                .and_then(|n| n.as_str().map(str::to_owned))
                .ok_or_else(|| {
                    ForwardErr::Transport(format!("input[{i}].fields[{j}] missing 'name'"))
                })?;
            named.insert(name, Value::Object(field));
        }
        obj.insert("fields".into(), Value::Object(named));
    }
    if !obj.get("fields").is_some_and(|f| f.as_object().is_some_and(|f| !f.is_empty())) {
        return Err(ForwardErr::Transport(format!(
            "input[{i}] needs at least one field"
        )));
    }
    if obj
        .get("reference_date")
        .is_some_and(|d| d.is_null() || d.as_str().is_some_and(|d| d.trim().is_empty()))
    {
        obj.remove("reference_date");
    }
    merge_params(&mut body, backend);
    Ok(body)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{http::HeaderMap, routing::post, Json, Router};

    #[tokio::test]
    async fn predict_preserves_batch_usage_without_changing_array_outputs() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(listener, Router::new().route("/predict", post(|| async {
                Json(json!({"outputs":[[{"text":"Alice","label":"person"}]],"usage":{"cost":0.0}}))
            }))).await.unwrap();
        });
        let backend: BackendCfg = serde_json::from_value(json!({"name":"extract","entitlement":"clover","model_version":"test"})).unwrap();
        let output=forward(&reqwest::Client::new(), &Upstream::Proxy {base_url:format!("http://{address}")},
            &backend, &[json!({"text":"Alice","labels":["person"]})], None).await.unwrap();
        assert_eq!(output.outputs, vec![json!([{"text":"Alice","label":"person"}])]);
        assert_eq!(output.usage, Some(json!({"cost":0.0})));
        server.abort();
    }

    #[tokio::test]
    async fn hosted_rerank_scatter_preserves_rows_and_native_scores() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(listener, Router::new().route("/rerank", post(|Json(body): Json<Value>| async move {
                assert_eq!(body["model"], "provider/ranker");
                assert_eq!(body["top_n"], 2);
                Json(json!({"results":[{"index":1,"relevance_score":0.98},{"index":0,"relevance_score":0.12}],
                    "usage":{"total_tokens":20,"cost":0.00004}}))
            }))).await.unwrap();
        });
        let cfg: BackendCfg = serde_json::from_value(json!({"name":"rerank","entitlement":"clover",
            "adapter":"hosted-rerank","upstream_base":format!("http://{address}"),
            "upstream_path":"/rerank","model_version":"v1","upstream_params":{"model":"provider/ranker"}})).unwrap();
        let inputs = vec![
            json!({"query":"q1","text":"a"}),
            json!({"query":"q2","text":"b"}),
            json!({"query":"q1","text":"c"}),
            json!({"query":"q2","text":"d"}),
        ];
        let output = forward(
            &reqwest::Client::new(),
            &Upstream::Proxy {
                base_url: String::new(),
            },
            &cfg,
            &inputs,
            None,
        )
        .await
        .unwrap();
        assert_eq!(
            output.outputs,
            vec![json!(0.12), json!(0.12), json!(0.98), json!(0.98)]
        );
        assert_eq!(output.usage.unwrap()["total_tokens"], 40);
        server.abort();
    }

    #[tokio::test]
    async fn hosted_extractions_sends_named_fields_and_returns_typed_answers() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(listener, Router::new().route("/alpha/extractions", post(|Json(body): Json<Value>| async move {
                assert_eq!(body["model"], "cloudflare/clairic");
                assert!(body["fields"]["total"].is_object(), "fields keyed by name: {body}");
                assert!(body["fields"]["total"].get("name").is_none());
                let state = body["state"].as_str().unwrap().to_owned();
                if state == "no date" {
                    assert!(body.get("reference_date").is_none());
                } else {
                    assert_eq!(body["reference_date"], "2026-11-07");
                }
                Json(json!({"model":"cloudflare/clairic-20261029","user_id":"user_x",
                    "answers":{"total":{"type":"number","value":state.len(),"p_null":0.01,"confidence":0.99}},
                    "truncated":false,"usage":{"input_tokens":600,"output_tokens":0,"cost":0.00006}}))
            }))).await.unwrap();
        });
        let cfg: BackendCfg = serde_json::from_value(json!({"name":"extractions","entitlement":"clover",
            "adapter":"hosted-extractions","upstream_base":format!("http://{address}"),
            "upstream_path":"/alpha/extractions","model_version":"v1",
            "upstream_params":{"model":"cloudflare/clairic"},"parallel_requests":2})).unwrap();
        let inputs = vec![
            // SQL sends fields as JSON text, in either shape.
            json!({"state":"abc","reference_date":"2026-11-07",
                "fields":"[{\"name\":\"total\",\"type\":\"number\"}]"}),
            json!({"state":"no date","reference_date":"",
                "fields":"{\"total\":{\"type\":\"number\"}}"}),
            json!({"state":"abcdef","reference_date":"2026-11-07","fields":{"total":{"type":"number"}}}),
        ];
        let output = forward(
            &reqwest::Client::new(),
            &Upstream::Proxy { base_url: String::new() },
            &cfg,
            &inputs,
            None,
        )
        .await
        .unwrap();
        let totals: Vec<Value> = output.outputs.iter().map(|o| o["answers"]["total"]["value"].clone()).collect();
        assert_eq!(totals, vec![json!(3), json!(7), json!(6)]);
        assert!(output.outputs.iter().all(|o| o.get("user_id").is_none()));
        let usage = output.usage.unwrap();
        assert_eq!(usage["prompt_tokens"], 1800);
        assert!((usage["cost"].as_f64().unwrap() - 0.00018).abs() < 1e-12);
        server.abort();
    }

    #[tokio::test]
    async fn hosted_extractions_rejects_requests_without_fields() {
        let cfg: BackendCfg = serde_json::from_value(json!({"name":"extractions","entitlement":"clover",
            "adapter":"hosted-extractions","upstream_base":"http://127.0.0.1:9",
            "upstream_path":"/alpha/extractions","model_version":"v1"})).unwrap();
        for input in [json!({"state":"x"}), json!({"state":"x","fields":{}}), json!("x")] {
            let err = forward(
                &reqwest::Client::new(),
                &Upstream::Proxy { base_url: String::new() },
                &cfg,
                &[input],
                None,
            )
            .await;
            assert!(matches!(err, Err(ForwardErr::Transport(_))));
        }
    }

    #[tokio::test]
    async fn hosted_rerank_rejects_missing_or_duplicate_rows() {
        for results in [
            json!([{"index":0,"relevance_score":0.1}]),
            json!([{"index":0,"relevance_score":0.1},{"index":0,"relevance_score":0.2}]),
            json!([{"index":0,"relevance_score":0.1},{"index":9,"relevance_score":0.2}]),
        ] {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let address = listener.local_addr().unwrap();
            let server = tokio::spawn(async move {
                axum::serve(
                    listener,
                    Router::new().route(
                        "/rerank",
                        post(move || {
                            let results = results.clone();
                            async move { Json(json!({"results":results})) }
                        }),
                    ),
                )
                .await
                .unwrap();
            });
            let cfg: BackendCfg =
                serde_json::from_value(json!({"name":"rerank","entitlement":"clover",
                "adapter":"hosted-rerank","upstream_base":format!("http://{address}"),
                "upstream_path":"/rerank","model_version":"v1"}))
                .unwrap();
            assert!(forward(
                &reqwest::Client::new(),
                &Upstream::Proxy {
                    base_url: String::new()
                },
                &cfg,
                &[
                    json!({"query":"q","text":"a"}),
                    json!({"query":"q","text":"b"})
                ],
                None
            )
            .await
            .is_err());
            server.abort();
        }
    }

    /// An upstream that answers `fail` with `status` for the first `failures`
    /// calls, then succeeds; returns the address and the call counter.
    async fn flaky_upstream(
        failures: usize,
        status: u16,
        retry_after: Option<&'static str>,
    ) -> (
        std::net::SocketAddr,
        std::sync::Arc<std::sync::atomic::AtomicUsize>,
        tokio::task::JoinHandle<()>,
    ) {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let calls = std::sync::Arc::new(AtomicUsize::new(0));
        let seen = calls.clone();
        let handler = move |Json(body): Json<Value>| {
            let seen = seen.clone();
            async move {
                let n = seen.fetch_add(1, Ordering::SeqCst);
                if n < failures {
                    let mut resp = axum::response::IntoResponse::into_response((
                        axum::http::StatusCode::from_u16(status).unwrap(),
                        "slow down",
                    ));
                    if let Some(after) = retry_after {
                        resp.headers_mut()
                            .insert("retry-after", after.parse().unwrap());
                    }
                    return resp;
                }
                let count = body["inputs"].as_array().map(Vec::len).unwrap_or_default();
                axum::response::IntoResponse::into_response(Json(
                    json!({"outputs": vec![json!(1); count]}),
                ))
            }
        };
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(listener, Router::new().route("/predict", post(handler)))
                .await
                .unwrap();
        });
        (address, calls, server)
    }

    fn predict_backend(retries: Option<u32>) -> BackendCfg {
        let extra = retries
            .map(|n| format!("upstream_retries: {n}\n"))
            .unwrap_or_default();
        serde_yaml::from_str(&format!(
            "name: rerank\nentitlement: clover\nupstream_path: /predict\nmodel_version: test\n{extra}"
        ))
        .unwrap()
    }

    #[tokio::test]
    async fn a_rate_limited_request_is_sent_again_until_it_is_taken() {
        use std::sync::atomic::Ordering;
        let (address, calls, server) = flaky_upstream(2, 429, Some("0")).await;
        let upstream = Upstream::Proxy {
            base_url: format!("http://{address}"),
        };
        let backend = predict_backend(None);
        let output = forward(
            &reqwest::Client::new(),
            &upstream,
            &backend,
            &[json!({})],
            None,
        )
        .await
        .unwrap();
        assert_eq!(output.outputs, vec![json!(1)]);
        assert_eq!(calls.load(Ordering::SeqCst), 3);
        server.abort();
    }

    #[tokio::test]
    async fn retries_stop_at_the_backend_budget_and_report_the_last_status() {
        use std::sync::atomic::Ordering;
        let (address, calls, server) = flaky_upstream(10, 503, Some("0")).await;
        let upstream = Upstream::Proxy {
            base_url: format!("http://{address}"),
        };
        let err = forward(
            &reqwest::Client::new(),
            &upstream,
            &predict_backend(Some(1)),
            &[json!({})],
            None,
        )
        .await
        .err()
        .unwrap();
        assert!(matches!(err, ForwardErr::Status { status: 503, .. }));
        assert_eq!(calls.load(Ordering::SeqCst), 2);
        server.abort();
    }

    #[tokio::test]
    async fn a_request_the_upstream_may_have_processed_is_not_sent_twice() {
        use std::sync::atomic::Ordering;
        let (address, calls, server) = flaky_upstream(1, 500, None).await;
        let upstream = Upstream::Proxy {
            base_url: format!("http://{address}"),
        };
        let err = forward(
            &reqwest::Client::new(),
            &upstream,
            &predict_backend(None),
            &[json!({})],
            None,
        )
        .await
        .err()
        .unwrap();
        assert!(matches!(err, ForwardErr::Status { status: 500, .. }));
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        server.abort();
    }

    #[tokio::test]
    async fn a_large_rerank_is_split_and_each_score_lands_on_its_row() {
        // The upstream scores a document by its text ("d7" -> 0.07), whatever
        // else is in the call, and reports one token per document.
        let calls = std::sync::Arc::new(std::sync::Mutex::new(Vec::<usize>::new()));
        let seen = calls.clone();
        let handler = move |Json(body): Json<Value>| {
            let seen = seen.clone();
            async move {
                let docs = body["documents"].as_array().unwrap().clone();
                seen.lock().unwrap().push(docs.len());
                assert_eq!(body["top_n"], docs.len());
                let mut results: Vec<Value> = docs
                    .iter()
                    .enumerate()
                    .map(|(i, d)| {
                        let n: f64 = d.as_str().unwrap()[1..].parse().unwrap();
                        json!({"index": i, "relevance_score": n / 100.0})
                    })
                    .collect();
                results.reverse(); // relevance order, not request order
                Json(json!({"results": results, "usage": {"total_tokens": docs.len()}}))
            }
        };
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(listener, Router::new().route("/rerank", post(handler)))
                .await
                .unwrap();
        });
        let cfg: BackendCfg = serde_json::from_value(json!({"name":"rerank","entitlement":"clover",
            "adapter":"hosted-rerank","upstream_base":format!("http://{address}"),
            "upstream_path":"/rerank","model_version":"v1","rerank_batch":2})).unwrap();
        let inputs: Vec<Value> = (0..5)
            .map(|i| json!({"query": "q", "text": format!("d{i}")}))
            .collect();
        let output = forward(
            &reqwest::Client::new(),
            &Upstream::Proxy {
                base_url: String::new(),
            },
            &cfg,
            &inputs,
            None,
        )
        .await
        .unwrap();
        let expected: Vec<Value> = (0..5).map(|i| json!(i as f64 / 100.0)).collect();
        assert_eq!(output.outputs, expected);
        assert_eq!(output.usage.unwrap()["total_tokens"], 5);
        let mut sizes = calls.lock().unwrap().clone();
        sizes.sort();
        assert_eq!(sizes, vec![1, 2, 2]);
        server.abort();
    }

    #[test]
    fn backoff_and_retry_after_stay_short() {
        assert_eq!(backoff(0).as_millis(), 300);
        assert_eq!(backoff(1).as_millis(), 900);
        let mut headers = reqwest::header::HeaderMap::new();
        headers.insert(reqwest::header::RETRY_AFTER, "2".parse().unwrap());
        assert_eq!(
            retry_after(&headers),
            Some(std::time::Duration::from_secs(2))
        );
        headers.insert(reqwest::header::RETRY_AFTER, "60".parse().unwrap());
        assert_eq!(retry_after(&headers), None);
        headers.insert(
            reqwest::header::RETRY_AFTER,
            "Wed, 21 Oct 2026 07:28:00 GMT".parse().unwrap(),
        );
        assert_eq!(retry_after(&headers), None);
    }

    async fn echo_scope(headers: HeaderMap, Json(body): Json<Value>) -> Json<Value> {
        let scope = headers
            .get("x-clover-tenant-scope")
            .and_then(|value| value.to_str().ok())
            .map(str::to_string);
        let count = body
            .get("inputs")
            .and_then(Value::as_array)
            .map(Vec::len)
            .unwrap_or_default();
        Json(json!({"outputs": vec![json!({"scope":scope}); count]}))
    }

    #[tokio::test]
    async fn predict_adapter_forwards_only_the_one_way_tenant_scope() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(listener, Router::new().route("/predict", post(echo_scope)))
                .await
                .unwrap();
        });
        let backend: BackendCfg = serde_yaml::from_str(
            r#"
            name: web_research
            entitlement: clover_llm
            forward_tenant_scope: true
            upstream_path: /predict
            model_version: test
            "#,
        )
        .unwrap();
        let upstream = Upstream::Proxy {
            base_url: format!("http://{address}"),
        };
        let scope = "a".repeat(64);
        let output = forward(
            &reqwest::Client::new(),
            &upstream,
            &backend,
            &[json!({"question":"test"})],
            Some(&scope),
        )
        .await
        .unwrap();
        assert_eq!(output.outputs[0]["scope"], scope);
        server.abort();
    }
    #[tokio::test]
    async fn hosted_embeddings_use_route_credentials_model_and_response_indices() {
        std::env::set_var("HUTCH_TEST_EMBED_KEY", "upstream-key");
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(listener, Router::new().route("/v1/embeddings", post(|headers: HeaderMap, Json(body): Json<Value>| async move {
                assert_eq!(headers["authorization"], "Bearer upstream-key");
                assert_eq!(body["model"], "provider/embed-model");
                assert_eq!(body["input"], json!(["first", "second"]));
                Json(json!({"data":[{"index":1,"embedding":[0.2,0.8]}, {"index":0,"embedding":[0.9,0.1]}],"usage":{"prompt_tokens":2}}))
            }))).await.unwrap();
        });
        let backend: BackendCfg =
            serde_json::from_value(json!({"name":"embed","entitlement":"clover",
            "adapter":"openai-embeddings","upstream_path":"/v1/embeddings","model_version":"v1",
            "upstream_bearer_token_env":"HUTCH_TEST_EMBED_KEY",
            "upstream_params":{"model":"provider/embed-model","input":"must not replace input"}}))
            .unwrap();
        let result = forward(
            &reqwest::Client::new(),
            &Upstream::Proxy {
                base_url: format!("http://{address}"),
            },
            &backend,
            &[json!("first"), json!("second")],
            None,
        )
        .await
        .unwrap();
        assert_eq!(
            result.outputs,
            json!([[0.9, 0.1], [0.2, 0.8]]).as_array().unwrap().clone()
        );
        assert_eq!(result.usage.unwrap()["prompt_tokens"], 2);
        server.abort();
    }

    #[tokio::test]
    async fn missing_specialist_credentials_fail_before_network() {
        let backend: BackendCfg = serde_yaml::from_str("name: hf\nentitlement: clover\nmodel_version: v1\nupstream_bearer_token_env: HUTCH_UNSET_TEST_AUTH\n").unwrap();
        let result = post_json(
            &reqwest::Client::new(),
            "http://127.0.0.1:1/predict",
            &backend,
            &json!({}),
        )
        .await;
        assert!(
            matches!(result,Err(ForwardErr::Transport(e)) if e.contains("Missing upstream credential"))
        );
    }

    #[tokio::test]
    async fn authenticated_upstream_error_cannot_echo_credentials() {
        std::env::set_var("HUTCH_TEST_ERROR_KEY", "never-expose-this");
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(
                listener,
                Router::new().route(
                    "/predict",
                    post(|| async { (axum::http::StatusCode::UNAUTHORIZED, "never-expose-this") }),
                ),
            )
            .await
            .unwrap();
        });
        let backend: BackendCfg = serde_yaml::from_str("name: hf\nentitlement: clover\nmodel_version: v1\nupstream_bearer_token_env: HUTCH_TEST_ERROR_KEY\n").unwrap();
        let result = post_json(
            &reqwest::Client::new(),
            &format!("http://{address}/predict"),
            &backend,
            &json!({}),
        )
        .await;
        assert!(
            matches!(result,Err(ForwardErr::Status{body_head,..}) if !body_head.contains("never-expose-this"))
        );
        server.abort();
    }
}
