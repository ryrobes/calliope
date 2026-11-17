use crate::{
    config::Config,
    memory::{CachedSnapshot, ClaimWrite, MemoryStore, PersistedRun, RunWrite, SnapshotWrite},
    openrouter::OpenRouter,
    openrouter::Usage,
    ServiceError,
};
use regex::Regex;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    time::Instant,
};
use url::Url;

#[derive(Clone, Debug)]
pub struct ResearchInput {
    pub question: String,
    pub seed_urls: Vec<String>,
    pub search: bool,
    pub max_sources: usize,
    pub mode: ResearchMode,
    pub watch_id: Option<String>,
    pub remember: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum ResearchMode {
    Research,
    Deep,
    Watch,
}

impl ResearchMode {
    fn parse(value: Option<&Value>) -> Result<Self, ServiceError> {
        match value.and_then(Value::as_str).unwrap_or("research") {
            "research" | "quick" => Ok(Self::Research),
            "deep" => Ok(Self::Deep),
            "watch" | "refresh" => Ok(Self::Watch),
            _ => Err(ServiceError::new(
                "invalid_input",
                "mode must be research, deep, or watch",
            )),
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::Research => "research",
            Self::Deep => "deep",
            Self::Watch => "watch",
        }
    }
}

impl ResearchInput {
    pub fn from_value(value: Value, config: &Config) -> Result<Self, ServiceError> {
        let (question, seed_urls, search, requested_sources, mode, watch_id, remember) = match value
        {
            Value::String(question) => (
                question,
                Vec::new(),
                true,
                None,
                ResearchMode::Research,
                None,
                true,
            ),
            Value::Object(object) => {
                let question = object
                    .get("question")
                    .or_else(|| object.get("query"))
                    .and_then(Value::as_str)
                    .ok_or_else(|| {
                        ServiceError::new("invalid_input", "input object needs a question")
                    })?
                    .to_string();
                let seed_urls = parse_seed_urls(object.get("seed_urls"))?;
                let mode = ResearchMode::parse(object.get("mode"))?;
                let explicit_search = object
                    .get("search")
                    .map(|value| {
                        value.as_bool().ok_or_else(|| {
                            ServiceError::new("invalid_input", "search must be boolean")
                        })
                    })
                    .transpose()?;
                let search = explicit_search
                    .unwrap_or_else(|| mode != ResearchMode::Watch || seed_urls.is_empty());
                let requested_sources = object
                    .get("max_sources")
                    .map(|value| {
                        value.as_u64().ok_or_else(|| {
                            ServiceError::new(
                                "invalid_input",
                                "max_sources must be a positive integer",
                            )
                        })
                    })
                    .transpose()?
                    .map(|value| value as usize);
                let watch_id = object
                    .get("watch_id")
                    .map(|value| {
                        value.as_str().ok_or_else(|| {
                            ServiceError::new("invalid_input", "watch_id must be text")
                        })
                    })
                    .transpose()?
                    .map(str::trim)
                    .filter(|value| !value.is_empty())
                    .map(str::to_string);
                if watch_id
                    .as_ref()
                    .is_some_and(|value| value.chars().count() > 128)
                {
                    return Err(ServiceError::new(
                        "invalid_input",
                        "watch_id exceeds the 128 character limit",
                    ));
                }
                let remember = object
                    .get("remember")
                    .map(|value| {
                        value.as_bool().ok_or_else(|| {
                            ServiceError::new("invalid_input", "remember must be boolean")
                        })
                    })
                    .transpose()?
                    .unwrap_or(true);
                (
                    question,
                    seed_urls,
                    search,
                    requested_sources,
                    mode,
                    watch_id,
                    remember,
                )
            }
            _ => {
                return Err(ServiceError::new(
                    "invalid_input",
                    "input must be a question string or research options object",
                ))
            }
        };
        let question = question.trim().to_string();
        if question.is_empty() {
            return Err(ServiceError::new(
                "invalid_input",
                "question cannot be blank",
            ));
        }
        if question.chars().count() > config.max_question_chars {
            return Err(ServiceError::new(
                "question_too_long",
                format!(
                    "question exceeds the {} character limit",
                    config.max_question_chars
                ),
            ));
        }
        if seed_urls.is_empty() && !search {
            return Err(ServiceError::new(
                "no_discovery_path",
                "search=false requires at least one seed URL",
            ));
        }
        if mode == ResearchMode::Watch && !remember {
            return Err(ServiceError::new(
                "invalid_input",
                "watch mode requires remember=true",
            ));
        }
        let max_sources = requested_sources
            .unwrap_or(config.max_sources)
            .clamp(1, config.max_sources);
        Ok(Self {
            question,
            seed_urls,
            search,
            max_sources,
            mode,
            watch_id,
            remember,
        })
    }
}

#[derive(Clone, Debug, Serialize)]
struct SourceEvidence {
    id: String,
    url: String,
    title: String,
    content: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Synthesis {
    answer_markdown: String,
    claims: Vec<Claim>,
    contradictions: Vec<String>,
    gaps: Vec<String>,
    #[serde(default)]
    knowledge: Knowledge,
    #[serde(default)]
    follow_up_queries: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Claim {
    claim: String,
    source_ids: Vec<String>,
    confidence: f64,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
struct Knowledge {
    #[serde(default)]
    entities: Vec<Entity>,
    #[serde(default)]
    relationships: Vec<Relationship>,
    #[serde(default)]
    events: Vec<Event>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Entity {
    name: String,
    kind: String,
    #[serde(default)]
    aliases: Vec<String>,
    source_ids: Vec<String>,
    confidence: f64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Relationship {
    subject: String,
    predicate: String,
    object: String,
    source_ids: Vec<String>,
    confidence: f64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Event {
    name: String,
    #[serde(default)]
    date: Option<String>,
    source_ids: Vec<String>,
    confidence: f64,
}

pub async fn run(
    input: ResearchInput,
    tenant_scope: &str,
    config: &Config,
    model: &OpenRouter,
    http: &Client,
    memory: &MemoryStore,
) -> Result<Value, ServiceError> {
    let started = Instant::now();
    let mut usage = Usage::default();
    let mut trail = Vec::new();
    let watch_id = (input.mode == ResearchMode::Watch).then(|| {
        input
            .watch_id
            .clone()
            .unwrap_or_else(|| derived_watch_id(&input.question, &input.seed_urls))
    });
    let prior_watch = match watch_id.as_deref() {
        Some(watch_id) => memory.watch_state(tenant_scope, watch_id).await?,
        None => None,
    };
    let mut candidates = Vec::<Candidate>::new();
    for url in &input.seed_urls {
        push_candidate(&mut candidates, url, None, "seed");
    }

    if input.search {
        let discovery = model
            .discover(
                &input.question,
                config.max_search_uses,
                config.search_results_per_use,
                config.max_search_results,
                config.search_context_chars,
                config.discovery_max_tokens,
                tenant_scope,
            )
            .await?;
        usage.add(&discovery.usage);
        let citation_count = discovery.citations.len();
        for citation in discovery.citations {
            push_candidate(&mut candidates, &citation.url, citation.title, "web_search");
        }
        trail.push(json!({
            "stage": "discover",
            "status": "ok",
            "engine": "openrouter:web_search/exa",
            "search_budget": config.max_search_uses,
            "search_requests": discovery.usage.search_requests,
            "citations": citation_count,
            "duration_ms": round_ms(discovery.duration_ms),
        }));
    } else {
        trail.push(json!({
            "stage": "discover",
            "status": "skipped",
            "reason": "search disabled; using seed URLs",
        }));
    }

    let followup_reserve = if input.mode == ResearchMode::Deep && input.max_sources > 1 {
        config
            .max_followup_sources
            .min(input.max_sources.saturating_sub(1))
    } else {
        0
    };
    candidates.truncate(input.max_sources.saturating_sub(followup_reserve).max(1));
    if candidates.is_empty() {
        return Err(ServiceError::new(
            "no_sources",
            "source discovery returned no cited URLs",
        ));
    }

    let extraction_started = Instant::now();
    let cached = cached_snapshots(memory, tenant_scope, &candidates, input.remember).await?;
    let scrape_outputs = scrape_sources(&candidates, &cached, config, http).await?;
    let mut prepared = prepare_evidence(
        &input.question,
        &candidates,
        scrape_outputs,
        &cached,
        config,
        0,
        config.max_evidence_chars,
    );
    trail.push(json!({
        "stage": "extract",
        "status": if prepared.sources.is_empty() { "failed" } else { "ok" },
        "requested": candidates.len(),
        "usable": prepared.sources.len(),
        "failed": prepared.failed,
        "cache_reuses": prepared.cache_reuses(),
        "renderer": "clover-web",
        "duration_ms": round_ms(extraction_started.elapsed().as_secs_f64() * 1_000.0),
    }));
    if prepared.sources.is_empty() {
        return Err(ServiceError::new(
            "no_usable_sources",
            "none of the discovered sources could be safely fetched and extracted",
        ));
    }

    let evidence_chars = prepared.evidence_chars();
    trail.push(json!({
        "stage": "select_evidence",
        "status": "ok",
        "sources": prepared.sources.len(),
        "selected_chars": evidence_chars,
        "strategy": "query-weighted passages with per-source and total caps",
    }));

    let source_set_unchanged = prior_watch.as_ref().is_some_and(|prior| {
        prior.result["question"] == input.question
            && prior.result["model"] == model.model()
            && prior.result["limits"]["max_source_chars"] == config.max_source_chars
            && prior.result["limits"]["max_evidence_chars"] == config.max_evidence_chars
            && watch_source_set(&prior.result) == prepared.requested_urls()
            && prepared.sources.iter().all(|source| {
                prior.result["sources"].as_array().is_some_and(|sources| {
                    sources
                        .iter()
                        .any(|old| snapshot_matches_receipt(&source.snapshot, old))
                })
            })
            && prepared.sources.iter().all(|source| {
                matches!(
                    source.cache_status.as_str(),
                    "revalidated" | "unchanged" | "stale_fallback"
                )
            })
    });
    let (mut synthesis, synthesis_reused) = if source_set_unchanged {
        let prior = prior_watch.as_ref().expect("checked above");
        let synthesis = synthesis_from_result(&prior.result)?;
        trail.push(json!({
            "stage": "synthesize",
            "status": "skipped",
            "reason": "watch sources are byte-identical to the previous run",
            "reused_run_id": prior.run_id,
        }));
        (synthesis, true)
    } else {
        let (synthesis, repaired, duration_ms) = synthesize_prepared(
            &input.question,
            &prepared,
            config,
            model,
            tenant_scope,
            &mut usage,
        )
        .await?;
        trail.push(json!({
            "stage": "synthesize",
            "status": "ok",
            "model": model.model(),
            "json_repair": repaired,
            "duration_ms": round_ms(duration_ms),
        }));
        (synthesis, false)
    };

    if input.mode == ResearchMode::Deep
        && !synthesis.follow_up_queries.is_empty()
        && prepared.sources.len() < input.max_sources
    {
        let followup_started = Instant::now();
        let followup_query = followup_question(
            &input.question,
            &synthesis.follow_up_queries,
            config.max_followup_queries,
        );
        let remaining_sources = input.max_sources.saturating_sub(prepared.sources.len());
        let mut followup_candidates =
            linked_candidates(&prepared, &followup_query, remaining_sources);
        let linked_count = followup_candidates.len();
        let mut followup_search_requests = 0;
        if input.search && followup_candidates.len() < remaining_sources {
            let discovery = model
                .discover(
                    &followup_query,
                    config.followup_search_uses,
                    config.search_results_per_use,
                    config.max_search_results,
                    config.search_context_chars,
                    config.discovery_max_tokens,
                    tenant_scope,
                )
                .await?;
            usage.add(&discovery.usage);
            followup_search_requests = discovery.usage.search_requests;
            for citation in discovery.citations {
                push_candidate(
                    &mut followup_candidates,
                    &citation.url,
                    citation.title,
                    "gap_search",
                );
            }
        }
        let already = prepared.requested_urls();
        followup_candidates.retain(|candidate| !already.contains(&candidate.url));
        followup_candidates.truncate(remaining_sources);
        let mut usable = 0;
        if !followup_candidates.is_empty() {
            let cached =
                cached_snapshots(memory, tenant_scope, &followup_candidates, input.remember)
                    .await?;
            let outputs = scrape_sources(&followup_candidates, &cached, config, http).await?;
            let selected_chars = prepared.evidence_chars();
            let more = prepare_evidence(
                &input.question,
                &followup_candidates,
                outputs,
                &cached,
                config,
                prepared.sources.len(),
                config.max_evidence_chars.saturating_sub(selected_chars),
            );
            usable = more.sources.len();
            prepared.extend(more);
        }
        if usable > 0 {
            let (final_synthesis, repaired, duration_ms) = synthesize_prepared(
                &input.question,
                &prepared,
                config,
                model,
                tenant_scope,
                &mut usage,
            )
            .await?;
            synthesis = final_synthesis;
            trail.push(json!({
                "stage": "gap_closure",
                "status": "ok",
                "query": followup_query,
                "linked_candidates": linked_count,
                "search_requests": followup_search_requests,
                "new_sources": usable,
                "json_repair": repaired,
                "duration_ms": round_ms(followup_started.elapsed().as_secs_f64() * 1_000.0),
                "synthesis_ms": round_ms(duration_ms),
            }));
        } else {
            trail.push(json!({
                "stage": "gap_closure",
                "status": "no_new_evidence",
                "query": followup_query,
                "linked_candidates": linked_count,
                "search_requests": followup_search_requests,
                "duration_ms": round_ms(followup_started.elapsed().as_secs_f64() * 1_000.0),
            }));
        }
    }

    let mut output = json!({
        "ok": true,
        "question": input.question,
        "mode": input.mode.as_str(),
        "watch_id": watch_id,
        "answer_markdown": synthesis.answer_markdown,
        "claims": synthesis.claims,
        "contradictions": synthesis.contradictions,
        "gaps": synthesis.gaps,
        "knowledge": synthesis.knowledge,
        "follow_up_queries": synthesis.follow_up_queries,
        "sources": prepared.receipts(),
        "changes": watch_changes(
            input.mode == ResearchMode::Watch,
            prior_watch.as_ref(),
            &synthesis,
            &prepared,
        ),
        "trail": trail,
        "usage": usage,
        "model": model.model(),
        "limits": {
            "max_sources": input.max_sources,
            "max_search_uses": config.max_search_uses + if input.mode == ResearchMode::Deep { config.followup_search_uses } else { 0 },
            "max_source_chars": config.max_source_chars,
            "max_evidence_chars": config.max_evidence_chars,
            "max_snapshot_chars": config.max_snapshot_chars,
            "max_followup_sources": config.max_followup_sources,
        },
        "duration_ms": round_ms(started.elapsed().as_secs_f64() * 1_000.0),
    });

    if input.remember {
        let write = RunWrite {
            question: input.question.clone(),
            mode: input.mode.as_str().into(),
            watch_id: watch_id.clone(),
            result: output.clone(),
            sources: prepared
                .sources
                .iter()
                .map(|source| source.snapshot.clone())
                .collect(),
            claims: synthesis
                .claims
                .iter()
                .map(|claim| ClaimWrite {
                    claim: claim.claim.clone(),
                    confidence: claim.confidence,
                    source_ids: claim.source_ids.clone(),
                })
                .collect(),
        };
        match memory
            .persist_run(tenant_scope, write, move |output, persisted| {
                attach_lineage(output, persisted);
                output
                    .as_object_mut()
                    .expect("research output is object")
                    .insert(
                        "memory".into(),
                        json!({
                            "stored": true,
                            "scope": "tenant",
                            "run_id": persisted.run_id,
                            "previous_run_id": persisted.previous_run_id,
                            "snapshots_written": persisted.snapshots_written,
                            "cache_reuses": persisted.cache_reuses,
                            "changed_sources": persisted.changed_sources,
                            "synthesis_reused": synthesis_reused,
                        }),
                    );
                push_output_trail(
                    output,
                    json!({
                        "stage": "remember",
                        "status": "ok",
                        "run_id": persisted.run_id,
                        "snapshots_written": persisted.snapshots_written,
                        "cache_reuses": persisted.cache_reuses,
                    }),
                );
            })
            .await
        {
            Ok(stored) => output = stored,
            Err(error) => {
                output
                    .as_object_mut()
                    .expect("research output is object")
                    .insert(
                        "memory".into(),
                        json!({
                            "stored": false,
                            "scope": "tenant",
                            "status": "degraded",
                            "error": {"code": error.code, "message": error.message},
                            "synthesis_reused": synthesis_reused,
                        }),
                    );
                push_output_trail(&mut output, json!({"stage":"remember", "status":"failed"}));
            }
        }
    } else {
        output
            .as_object_mut()
            .expect("research output is object")
            .insert(
                "memory".into(),
                json!({"stored":false, "status":"disabled", "synthesis_reused":false}),
            );
    }

    output["duration_ms"] = json!(round_ms(started.elapsed().as_secs_f64() * 1_000.0));
    Ok(output)
}

#[derive(Clone, Debug)]
struct Candidate {
    url: String,
    title: Option<String>,
    discovered_by: String,
}

fn push_candidate(
    candidates: &mut Vec<Candidate>,
    url: &str,
    title: Option<String>,
    discovered_by: &str,
) {
    let Ok(parsed) = Url::parse(url) else {
        return;
    };
    if !matches!(parsed.scheme(), "http" | "https") {
        return;
    }
    let normalized = parsed.to_string();
    if candidates.iter().any(|item| item.url == normalized) {
        return;
    }
    candidates.push(Candidate {
        url: normalized,
        title,
        discovered_by: discovered_by.to_string(),
    });
}

fn parse_seed_urls(value: Option<&Value>) -> Result<Vec<String>, ServiceError> {
    let Some(value) = value else {
        return Ok(Vec::new());
    };
    match value {
        Value::String(text) => {
            if let Ok(parsed) = serde_json::from_str::<Vec<String>>(text) {
                Ok(parsed)
            } else {
                Ok(vec![text.clone()])
            }
        }
        Value::Array(values) => values
            .iter()
            .map(|value| {
                value.as_str().map(str::to_string).ok_or_else(|| {
                    ServiceError::new("invalid_input", "seed_urls must contain only strings")
                })
            })
            .collect(),
        _ => Err(ServiceError::new(
            "invalid_input",
            "seed_urls must be a URL string or array of URL strings",
        )),
    }
}

async fn cached_snapshots(
    memory: &MemoryStore,
    tenant_scope: &str,
    candidates: &[Candidate],
    remember: bool,
) -> Result<HashMap<String, CachedSnapshot>, ServiceError> {
    if !remember {
        return Ok(HashMap::new());
    }
    let urls = candidates
        .iter()
        .map(|candidate| candidate.url.clone())
        .collect::<Vec<_>>();
    memory.latest_snapshots(tenant_scope, &urls).await
}

async fn scrape_sources(
    candidates: &[Candidate],
    cached: &HashMap<String, CachedSnapshot>,
    config: &Config,
    http: &Client,
) -> Result<Vec<Value>, ServiceError> {
    let inputs = candidates
        .iter()
        .map(|candidate| {
            let mut input = json!({
                "url": candidate.url,
                "favor_precision": true,
                "include_links": true,
                "include_tables": true,
                "include_images": false,
                "output_text": false,
                "max_bytes": 2 * 1024 * 1024,
            });
            if let Some(snapshot) = cached.get(&candidate.url) {
                if let Some(etag) = &snapshot.etag {
                    input["if_none_match"] = Value::String(etag.clone());
                }
                if let Some(last_modified) = &snapshot.last_modified {
                    input["if_modified_since"] = Value::String(last_modified.clone());
                }
            }
            input
        })
        .collect::<Vec<_>>();
    let response = http
        .post(&config.scrape_url)
        .timeout(config.request_timeout)
        .json(&json!({"inputs": inputs}))
        .send()
        .await
        .map_err(|error| {
            if error.is_timeout() {
                ServiceError::new("scrape_timeout", "Clover Web extraction timed out")
            } else {
                ServiceError::new("scrape_unavailable", "Clover Web extraction is unavailable")
            }
        })?;
    if !response.status().is_success() {
        return Err(ServiceError::new(
            "scrape_error",
            format!("Clover Web returned HTTP {}", response.status().as_u16()),
        ));
    }
    let value: Value = response
        .json()
        .await
        .map_err(|_| ServiceError::new("scrape_response", "Clover Web returned invalid JSON"))?;
    let outputs = value
        .get("outputs")
        .and_then(Value::as_array)
        .cloned()
        .ok_or_else(|| {
            ServiceError::new("scrape_response", "Clover Web response has no outputs")
        })?;
    if outputs.len() != candidates.len() {
        return Err(ServiceError::new(
            "scrape_response",
            "Clover Web response was not aligned with requested sources",
        ));
    }
    Ok(outputs)
}

#[derive(Clone, Debug)]
struct PreparedSource {
    evidence: SourceEvidence,
    snapshot: SnapshotWrite,
    receipt: Value,
    full_markdown: String,
    cache_status: String,
}

#[derive(Clone, Debug, Default)]
struct PreparedBatch {
    sources: Vec<PreparedSource>,
    failed: usize,
    failed_receipts: Vec<Value>,
}

impl PreparedBatch {
    fn evidence(&self) -> Vec<SourceEvidence> {
        self.sources
            .iter()
            .map(|source| source.evidence.clone())
            .collect()
    }

    fn evidence_chars(&self) -> usize {
        self.sources
            .iter()
            .map(|source| source.evidence.content.chars().count())
            .sum()
    }

    fn receipts(&self) -> Vec<Value> {
        let mut receipts = self
            .sources
            .iter()
            .map(|source| source.receipt.clone())
            .collect::<Vec<_>>();
        receipts.extend(self.failed_receipts.iter().cloned());
        receipts
    }

    fn requested_urls(&self) -> HashSet<String> {
        self.sources
            .iter()
            .map(|source| source.snapshot.requested_url.clone())
            .collect()
    }

    fn cache_reuses(&self) -> usize {
        self.sources
            .iter()
            .filter(|source| {
                matches!(
                    source.cache_status.as_str(),
                    "revalidated" | "unchanged" | "stale_fallback"
                )
            })
            .count()
    }

    fn extend(&mut self, mut other: PreparedBatch) {
        self.failed += other.failed;
        self.sources.append(&mut other.sources);
        self.failed_receipts.append(&mut other.failed_receipts);
    }
}

fn prepare_evidence(
    question: &str,
    candidates: &[Candidate],
    outputs: Vec<Value>,
    cached: &HashMap<String, CachedSnapshot>,
    config: &Config,
    id_offset: usize,
    available_evidence_chars: usize,
) -> PreparedBatch {
    let mut batch = PreparedBatch::default();
    let mut remaining_chars = available_evidence_chars;

    for (candidate, output) in candidates.iter().zip(outputs) {
        let previous = cached.get(&candidate.url);
        let output_ok = output.get("ok").and_then(Value::as_bool) == Some(true);
        let not_modified = output.get("not_modified").and_then(Value::as_bool) == Some(true);
        let refresh_error = (!output_ok).then(|| {
            output
                .get("error")
                .cloned()
                .unwrap_or_else(|| json!({"code":"unknown","message":"source extraction failed"}))
        });
        let (
            markdown,
            sha256,
            title,
            final_url,
            format,
            published_date,
            etag,
            last_modified,
            cache_status,
        ) = if let Some(previous) = previous.filter(|_| !output_ok || not_modified) {
            (
                previous.markdown.clone(),
                previous.sha256.clone(),
                previous.title.clone(),
                previous.final_url.clone(),
                previous.format.clone(),
                previous.published_date.clone(),
                output
                    .get("etag")
                    .and_then(Value::as_str)
                    .map(str::to_string)
                    .or_else(|| previous.etag.clone()),
                output
                    .get("last_modified")
                    .and_then(Value::as_str)
                    .map(str::to_string)
                    .or_else(|| previous.last_modified.clone()),
                if not_modified {
                    "revalidated".to_string()
                } else {
                    "stale_fallback".to_string()
                },
            )
        } else if output_ok && !not_modified {
            let markdown = output
                .get("markdown")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let sha256 = output
                .get("sha256")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let status = match previous {
                None => "new",
                Some(previous) if previous.sha256 == sha256 => "unchanged",
                Some(_) => "changed",
            };
            (
                markdown,
                sha256,
                output
                    .pointer("/metadata/title")
                    .and_then(Value::as_str)
                    .map(str::to_string)
                    .or_else(|| candidate.title.clone())
                    .unwrap_or_else(|| candidate.url.clone()),
                output
                    .get("final_url")
                    .and_then(Value::as_str)
                    .unwrap_or(&candidate.url)
                    .to_string(),
                output
                    .get("format")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                output
                    .pointer("/metadata/published_date")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                output
                    .get("etag")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                output
                    .get("last_modified")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                status.to_string(),
            )
        } else {
            batch.failed += 1;
            batch.failed_receipts.push(json!({
                "ok": false,
                "url": candidate.url,
                "discovered_by": candidate.discovered_by,
                "error": refresh_error.unwrap_or_else(|| {
                    if not_modified {
                        json!({"code":"cache_miss","message":"source returned 304 but no tenant snapshot was available"})
                    } else {
                        json!({"code":"unknown","message":"source extraction failed"})
                    }
                }),
            }));
            continue;
        };
        if sha256.is_empty() {
            batch.failed += 1;
            batch.failed_receipts.push(json!({
                "ok": false,
                "url": candidate.url,
                "discovered_by": candidate.discovered_by,
                "error": {"code":"missing_hash","message":"source extraction returned no content hash"},
            }));
            continue;
        }
        let source_limit = config.max_source_chars.min(remaining_chars);
        if source_limit == 0 {
            batch.failed += 1;
            batch.failed_receipts.push(json!({
                "ok": false,
                "url": candidate.url,
                "discovered_by": candidate.discovered_by,
                "error": {"code":"evidence_budget","message":"request evidence budget was exhausted before this source"},
            }));
            continue;
        }
        let selected = select_passages(question, &markdown, source_limit);
        if selected.trim().is_empty() {
            batch.failed += 1;
            batch.failed_receipts.push(json!({
                "ok": false,
                "url": candidate.url,
                "discovered_by": candidate.discovered_by,
                "error": {"code":"empty_content","message":"source contained no usable text"},
            }));
            continue;
        }
        let selected_chars = selected.chars().count();
        remaining_chars = remaining_chars.saturating_sub(selected_chars);
        let id = format!("S{}", id_offset + batch.sources.len() + 1);
        let evidence = SourceEvidence {
            id: id.clone(),
            url: final_url.clone(),
            title: title.clone(),
            content: selected.clone(),
        };
        let evidence_excerpt = truncate_chars(&selected.replace('\n', " "), 360);
        let receipt = json!({
            "ok": true,
            "id": id,
            "url": candidate.url,
            "final_url": final_url,
            "title": title,
            "discovered_by": candidate.discovered_by,
            "format": format,
            "extractor": if cache_status == "revalidated" { json!("http-cache") } else { output.get("source").cloned().unwrap_or(Value::Null) },
            "published_date": published_date,
            "sha256": sha256,
            "etag": etag,
            "last_modified": last_modified,
            "cache_status": cache_status,
            "previous_snapshot_id": previous.map(|snapshot| format!("P{}", snapshot.row_id)),
            "previous_requested_url": previous.map(|snapshot| snapshot.requested_url.clone()),
            "previous_fetched_at": previous.map(|snapshot| snapshot.fetched_at.clone()),
            "previous_checked_at": previous.map(|snapshot| snapshot.last_checked_at.clone()),
            "fetched_bytes": output.get("bytes").cloned().unwrap_or(Value::Null),
            "selected_chars": selected_chars,
            "evidence_excerpt": evidence_excerpt,
            "fetch_ms": output.get("fetch_ms").cloned().unwrap_or(Value::Null),
            "extract_ms": output.get("extract_ms").cloned().unwrap_or(Value::Null),
            "refresh_error": refresh_error,
        });
        let snapshot = SnapshotWrite {
            source_id: id,
            requested_url: candidate.url.clone(),
            final_url,
            title,
            format,
            published_date,
            sha256,
            etag,
            last_modified,
            markdown: truncate_chars(&markdown, config.max_snapshot_chars),
            selected_evidence: selected,
            discovered_by: candidate.discovered_by.clone(),
            cache_status: cache_status.clone(),
            selected_chars,
            evidence_excerpt,
        };
        batch.sources.push(PreparedSource {
            evidence,
            snapshot,
            receipt,
            full_markdown: markdown,
            cache_status,
        });
    }
    batch
}

async fn synthesize_prepared(
    question: &str,
    prepared: &PreparedBatch,
    config: &Config,
    model: &OpenRouter,
    tracking_user: &str,
    usage: &mut Usage,
) -> Result<(Synthesis, bool, f64), ServiceError> {
    let evidence = prepared.evidence();
    let evidence_json = serde_json::to_string(&evidence)
        .map_err(|_| ServiceError::new("internal", "could not serialize selected evidence"))?;
    let call = model
        .synthesize(
            question,
            &evidence_json,
            config.synthesis_max_tokens,
            tracking_user,
        )
        .await?;
    usage.add(&call.usage);
    let valid_ids = evidence
        .iter()
        .map(|item| item.id.clone())
        .collect::<Vec<_>>();
    let (mut synthesis, repaired, duration_ms) =
        match parse_and_validate_synthesis(&call.content, &valid_ids) {
            Ok(synthesis) => (synthesis, false, call.duration_ms),
            Err(_) => {
                let repair = model
                    .repair_json(
                        &call.content,
                        &valid_ids,
                        config.synthesis_max_tokens,
                        tracking_user,
                    )
                    .await?;
                usage.add(&repair.usage);
                let synthesis = parse_and_validate_synthesis(&repair.content, &valid_ids)?;
                (synthesis, true, call.duration_ms + repair.duration_ms)
            }
        };
    normalize_synthesis(&mut synthesis);
    Ok((synthesis, repaired, duration_ms))
}

fn synthesis_from_result(result: &Value) -> Result<Synthesis, ServiceError> {
    serde_json::from_value(json!({
        "answer_markdown": result.get("answer_markdown").cloned().unwrap_or(Value::Null),
        "claims": result.get("claims").cloned().unwrap_or_else(|| json!([])),
        "contradictions": result.get("contradictions").cloned().unwrap_or_else(|| json!([])),
        "gaps": result.get("gaps").cloned().unwrap_or_else(|| json!([])),
        "knowledge": result.get("knowledge").cloned().unwrap_or_else(|| json!({})),
        "follow_up_queries": result.get("follow_up_queries").cloned().unwrap_or_else(|| json!([])),
    }))
    .map_err(|_| {
        ServiceError::new(
            "memory_error",
            "the previous watch result cannot be reused safely",
        )
    })
}

fn followup_question(original: &str, gaps: &[String], max_queries: usize) -> String {
    let gaps = gaps
        .iter()
        .take(max_queries)
        .map(|gap| truncate_chars(gap.trim(), 500))
        .filter(|gap| !gap.is_empty())
        .collect::<Vec<_>>()
        .join("; ");
    format!(
        "Original research question: {}\nResolve only these material evidence gaps using primary sources: {}",
        truncate_chars(original, 2_000),
        gaps
    )
}

fn linked_candidates(
    prepared: &PreparedBatch,
    followup_query: &str,
    limit: usize,
) -> Vec<Candidate> {
    if limit == 0 {
        return Vec::new();
    }
    let link_pattern = Regex::new(r#"\[([^\]]{1,240})\]\(([^\s\)]+)(?:\s+\"[^\"]*\")?\)"#)
        .expect("static markdown link regex");
    let terms = query_terms(followup_query);
    let existing = prepared.requested_urls();
    let mut scored = Vec::<(f64, Candidate)>::new();
    let mut seen = HashSet::new();

    for source in &prepared.sources {
        let Ok(base) = Url::parse(&source.evidence.url) else {
            continue;
        };
        for captures in link_pattern.captures_iter(&source.full_markdown) {
            let anchor = captures.get(1).map(|value| value.as_str()).unwrap_or("");
            let href = captures.get(2).map(|value| value.as_str()).unwrap_or("");
            let Ok(mut url) = base.join(href) else {
                continue;
            };
            if !matches!(url.scheme(), "http" | "https") {
                continue;
            }
            url.set_fragment(None);
            if is_low_value_link(&url) {
                continue;
            }
            let normalized = url.to_string();
            if existing.contains(&normalized) || !seen.insert(normalized.clone()) {
                continue;
            }
            let haystack = format!("{} {}", anchor, normalized).to_lowercase();
            let term_score = terms.iter().filter(|term| haystack.contains(*term)).count() as f64;
            let same_host = base.host_str() == url.host_str();
            let primary_hint = [
                "docs",
                "documentation",
                "report",
                "release",
                "policy",
                "paper",
            ]
            .iter()
            .filter(|term| haystack.contains(**term))
            .count() as f64;
            let score = term_score * 3.0 + primary_hint + if same_host { 0.75 } else { 0.0 };
            if score <= 0.0 {
                continue;
            }
            scored.push((
                score,
                Candidate {
                    url: normalized,
                    title: (!anchor.trim().is_empty()).then(|| anchor.trim().to_string()),
                    discovered_by: "source_link".into(),
                },
            ));
        }
    }
    scored.sort_by(|left, right| {
        right
            .0
            .total_cmp(&left.0)
            .then_with(|| left.1.url.cmp(&right.1.url))
    });
    scored
        .into_iter()
        .take(limit)
        .map(|(_, candidate)| candidate)
        .collect()
}

fn is_low_value_link(url: &Url) -> bool {
    let lower = url.path().to_ascii_lowercase();
    [
        ".avif", ".css", ".gif", ".ico", ".jpeg", ".jpg", ".js", ".mp3", ".mp4", ".png", ".svg",
        ".webm", ".webp", ".woff", ".woff2", ".zip",
    ]
    .iter()
    .any(|suffix| lower.ends_with(suffix))
        || ["/login", "/logout", "/signin", "/signup"]
            .iter()
            .any(|segment| lower.contains(segment))
}

fn derived_watch_id(question: &str, seed_urls: &[String]) -> String {
    let mut urls = seed_urls.to_vec();
    urls.sort();
    let mut digest = Sha256::new();
    digest.update(question.trim().as_bytes());
    digest.update(b"\0");
    for url in urls {
        digest.update(url.as_bytes());
        digest.update(b"\0");
    }
    format!("W{}", &hex::encode(digest.finalize())[..20])
}

// Source IDs are part of the citation contract. The same URLs in another
// order must not reuse an answer whose [S1] would now identify a different page.
fn snapshot_matches_receipt(snapshot: &SnapshotWrite, receipt: &Value) -> bool {
    receipt["id"] == snapshot.source_id
        && receipt["url"] == snapshot.requested_url
        && receipt["sha256"] == snapshot.sha256
}

fn watch_source_set(result: &Value) -> HashSet<String> {
    result
        .get("sources")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|source| source.get("ok").and_then(Value::as_bool) == Some(true))
        .filter_map(|source| source.get("url").and_then(Value::as_str))
        .map(str::to_string)
        .collect()
}

fn watch_changes(
    is_watch: bool,
    prior: Option<&crate::memory::WatchState>,
    synthesis: &Synthesis,
    prepared: &PreparedBatch,
) -> Value {
    if !is_watch {
        return Value::Null;
    }
    let current_claims = synthesis
        .claims
        .iter()
        .map(|claim| claim.claim.trim().to_string())
        .collect::<Vec<_>>();
    let previous_claims = prior
        .and_then(|prior| prior.result.get("claims"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|claim| claim.get("claim").and_then(Value::as_str))
        .map(str::to_string)
        .collect::<Vec<_>>();
    let previous_normalized = previous_claims
        .iter()
        .map(|claim| normalize_claim(claim))
        .collect::<HashSet<_>>();
    let current_normalized = current_claims
        .iter()
        .map(|claim| normalize_claim(claim))
        .collect::<HashSet<_>>();
    let new_claims = current_claims
        .iter()
        .filter(|claim| !previous_normalized.contains(&normalize_claim(claim)))
        .take(20)
        .cloned()
        .collect::<Vec<_>>();
    let resolved_claims = previous_claims
        .iter()
        .filter(|claim| !current_normalized.contains(&normalize_claim(claim)))
        .take(20)
        .cloned()
        .collect::<Vec<_>>();
    let source_changes = prepared
        .sources
        .iter()
        .filter(|source| matches!(source.cache_status.as_str(), "new" | "changed"))
        .map(|source| {
            json!({
                "source_id": source.evidence.id,
                "url": source.snapshot.requested_url,
                "status": source.cache_status,
            })
        })
        .collect::<Vec<_>>();
    let stale_sources = prepared
        .sources
        .iter()
        .filter(|source| source.cache_status == "stale_fallback")
        .count();
    let status = if prior.is_none() {
        "initial"
    } else if stale_sources > 0 {
        "attention"
    } else if source_changes.is_empty() && new_claims.is_empty() && resolved_claims.is_empty() {
        "unchanged"
    } else {
        "changed"
    };
    json!({
        "status": status,
        "source_changes": source_changes,
        "new_claims": new_claims,
        "resolved_claims": resolved_claims,
        "stale_sources": stale_sources,
    })
}

fn normalize_claim(value: &str) -> String {
    value
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase()
}

fn attach_lineage(output: &mut Value, persisted: &PersistedRun) {
    let by_source = persisted
        .source_lineage
        .iter()
        .map(|lineage| (lineage.source_id.clone(), lineage))
        .collect::<HashMap<_, _>>();
    if let Some(sources) = output.get_mut("sources").and_then(Value::as_array_mut) {
        for source in sources {
            let Some(source_id) = source.get("id").and_then(Value::as_str) else {
                continue;
            };
            let Some(lineage) = by_source.get(source_id) else {
                continue;
            };
            source["snapshot_id"] = Value::String(lineage.snapshot_id.clone());
        }
    }
    if let Some(claims) = output.get_mut("claims").and_then(Value::as_array_mut) {
        for (index, claim) in claims.iter_mut().enumerate() {
            let source_ids = claim
                .get("source_ids")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            let evidence = source_ids
                .iter()
                .filter_map(Value::as_str)
                .filter_map(|source_id| by_source.get(source_id))
                .map(|lineage| {
                    json!({
                        "source_id": lineage.source_id,
                        "snapshot_id": lineage.snapshot_id,
                        "sha256": lineage.sha256,
                    })
                })
                .collect::<Vec<_>>();
            claim["claim_id"] = Value::String(format!("{}:C{}", persisted.run_id, index + 1));
            claim["evidence_snapshots"] = json!(evidence);
        }
    }
}

fn push_output_trail(output: &mut Value, item: Value) {
    if let Some(trail) = output.get_mut("trail").and_then(Value::as_array_mut) {
        trail.push(item);
    }
}

#[derive(Clone)]
struct Passage {
    index: usize,
    score: f64,
    text: String,
}

fn select_passages(question: &str, markdown: &str, max_chars: usize) -> String {
    let terms = query_terms(question);
    let chunks = chunk_markdown(markdown, 1_800);
    if chunks.is_empty() {
        return String::new();
    }
    let mut passages = chunks
        .into_iter()
        .enumerate()
        .map(|(index, text)| {
            let lower = text.to_lowercase();
            let matches = terms.iter().filter(|term| lower.contains(*term)).count();
            let heading_bonus = if text.trim_start().starts_with('#') {
                0.75
            } else {
                0.0
            };
            let lead_bonus = if index == 0 { 1.0 } else { 0.0 };
            Passage {
                index,
                score: matches as f64 * 2.0 + heading_bonus + lead_bonus,
                text,
            }
        })
        .collect::<Vec<_>>();
    passages.sort_by(|a, b| {
        b.score
            .total_cmp(&a.score)
            .then_with(|| a.index.cmp(&b.index))
    });

    let mut selected = Vec::new();
    let mut used = 0;
    for passage in passages {
        if used >= max_chars {
            break;
        }
        let separator = if selected.is_empty() { 0 } else { 2 };
        let remaining = max_chars.saturating_sub(used + separator);
        if remaining == 0 {
            break;
        }
        let text = truncate_chars(&passage.text, remaining);
        let count = text.chars().count();
        if count == 0 {
            continue;
        }
        used += separator + count;
        selected.push((passage.index, text));
    }
    selected.sort_by_key(|(index, _)| *index);
    selected
        .into_iter()
        .map(|(_, text)| text)
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn chunk_markdown(markdown: &str, max_chars: usize) -> Vec<String> {
    let mut chunks = Vec::new();
    for block in markdown.split("\n\n") {
        let block = block.trim();
        if block.is_empty() {
            continue;
        }
        let chars = block.chars().collect::<Vec<_>>();
        for part in chars.chunks(max_chars) {
            chunks.push(part.iter().collect());
        }
    }
    chunks
}

fn query_terms(question: &str) -> HashSet<String> {
    const STOP: &[&str] = &[
        "about", "after", "also", "and", "are", "can", "could", "does", "for", "from", "have",
        "how", "into", "its", "most", "not", "that", "the", "their", "this", "was", "what", "when",
        "where", "which", "who", "why", "will", "with", "would",
    ];
    question
        .split(|ch: char| !ch.is_alphanumeric())
        .map(str::to_lowercase)
        .filter(|term| term.len() >= 3 && !STOP.contains(&term.as_str()))
        .collect()
}

fn parse_and_validate_synthesis(
    text: &str,
    valid_source_ids: &[String],
) -> Result<Synthesis, ServiceError> {
    let json_text = isolate_json(text).ok_or_else(|| {
        ServiceError::new("invalid_synthesis", "model did not return a JSON object")
    })?;
    let mut synthesis: Synthesis = serde_json::from_str(json_text).map_err(|_| {
        ServiceError::new(
            "invalid_synthesis",
            "model returned malformed research JSON",
        )
    })?;
    synthesis.answer_markdown = normalize_grouped_citations(&synthesis.answer_markdown);
    if synthesis.answer_markdown.trim().is_empty() {
        return Err(ServiceError::new(
            "invalid_synthesis",
            "model returned an empty answer",
        ));
    }
    let valid = valid_source_ids.iter().cloned().collect::<HashSet<_>>();
    for claim in &synthesis.claims {
        validate_source_ids(&claim.source_ids, &valid, "claim")?;
    }
    for entity in &synthesis.knowledge.entities {
        validate_source_ids(&entity.source_ids, &valid, "entity")?;
    }
    for relationship in &synthesis.knowledge.relationships {
        validate_source_ids(&relationship.source_ids, &valid, "relationship")?;
    }
    for event in &synthesis.knowledge.events {
        validate_source_ids(&event.source_ids, &valid, "event")?;
    }
    let citation_pattern = Regex::new(r"\[(S\d+)\]").expect("static citation regex");
    let answer_citations = citation_pattern
        .captures_iter(&synthesis.answer_markdown)
        .map(|capture| capture[1].to_string())
        .collect::<Vec<_>>();
    if answer_citations.is_empty() {
        return Err(ServiceError::new(
            "invalid_synthesis",
            "model answer contained no source citations",
        ));
    }
    if answer_citations
        .iter()
        .any(|source| !valid.contains(source))
    {
        return Err(ServiceError::new(
            "invalid_synthesis",
            "model cited an unknown source ID",
        ));
    }
    Ok(synthesis)
}

fn validate_source_ids(
    source_ids: &[String],
    valid: &HashSet<String>,
    kind: &str,
) -> Result<(), ServiceError> {
    if source_ids.is_empty() || source_ids.iter().any(|source| !valid.contains(source)) {
        return Err(ServiceError::new(
            "invalid_synthesis",
            format!("model used an unknown or missing {kind} source ID"),
        ));
    }
    Ok(())
}

fn normalize_synthesis(synthesis: &mut Synthesis) {
    synthesis.answer_markdown = synthesis.answer_markdown.trim().to_string();
    for claim in &mut synthesis.claims {
        claim.claim = claim.claim.trim().to_string();
        claim.source_ids.sort();
        claim.source_ids.dedup();
        claim.confidence = claim.confidence.clamp(0.0, 1.0);
    }
    synthesis.claims.retain(|claim| !claim.claim.is_empty());
    synthesis.claims.truncate(40);
    for values in [&mut synthesis.contradictions, &mut synthesis.gaps] {
        for value in values.iter_mut() {
            *value = value.trim().to_string();
        }
        values.retain(|value| !value.is_empty());
        values.truncate(20);
    }
    for entity in &mut synthesis.knowledge.entities {
        entity.name = entity.name.trim().to_string();
        entity.kind = entity.kind.trim().to_string();
        entity.aliases = entity
            .aliases
            .iter()
            .map(|alias| alias.trim().to_string())
            .filter(|alias| !alias.is_empty())
            .take(10)
            .collect();
        normalize_sources_and_confidence(&mut entity.source_ids, &mut entity.confidence);
    }
    synthesis
        .knowledge
        .entities
        .retain(|entity| !entity.name.is_empty() && !entity.kind.is_empty());
    synthesis.knowledge.entities.truncate(40);
    for relationship in &mut synthesis.knowledge.relationships {
        relationship.subject = relationship.subject.trim().to_string();
        relationship.predicate = relationship.predicate.trim().to_string();
        relationship.object = relationship.object.trim().to_string();
        normalize_sources_and_confidence(
            &mut relationship.source_ids,
            &mut relationship.confidence,
        );
    }
    synthesis.knowledge.relationships.retain(|relationship| {
        !relationship.subject.is_empty()
            && !relationship.predicate.is_empty()
            && !relationship.object.is_empty()
    });
    synthesis.knowledge.relationships.truncate(40);
    for event in &mut synthesis.knowledge.events {
        event.name = event.name.trim().to_string();
        event.date = event
            .date
            .take()
            .map(|date| date.trim().to_string())
            .filter(|date| !date.is_empty());
        normalize_sources_and_confidence(&mut event.source_ids, &mut event.confidence);
    }
    synthesis
        .knowledge
        .events
        .retain(|event| !event.name.is_empty());
    synthesis.knowledge.events.truncate(30);
    synthesis.follow_up_queries = synthesis
        .follow_up_queries
        .iter()
        .map(|query| truncate_chars(query.trim(), 500))
        .filter(|query| !query.is_empty())
        .take(4)
        .collect();
}

fn normalize_sources_and_confidence(source_ids: &mut Vec<String>, confidence: &mut f64) {
    source_ids.sort();
    source_ids.dedup();
    *confidence = confidence.clamp(0.0, 1.0);
}

fn isolate_json(text: &str) -> Option<&str> {
    let start = text.find('{')?;
    let end = text.rfind('}')?;
    (start <= end).then_some(&text[start..=end])
}

fn normalize_grouped_citations(text: &str) -> String {
    let grouped = Regex::new(r"\[(S\d+(?:\s*,\s*S\d+)+)\]").expect("static grouped citation regex");
    grouped
        .replace_all(text, |captures: &regex::Captures<'_>| {
            captures[1]
                .split(',')
                .map(|source| format!("[{}]", source.trim()))
                .collect::<String>()
        })
        .into_owned()
}

fn truncate_chars(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

fn round_ms(value: f64) -> f64 {
    (value * 10.0).round() / 10.0
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{extract::State, routing::post, Json, Router};
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    };

    fn config() -> Config {
        Config {
            bind: "127.0.0.1:8092".parse().unwrap(),
            max_inputs: 2,
            max_concurrency: 2,
            max_question_chars: 4_000,
            max_sources: 6,
            max_source_chars: 100,
            max_evidence_chars: 400,
            max_snapshot_chars: 1_000,
            max_search_uses: 2,
            max_followup_sources: 2,
            followup_search_uses: 1,
            max_followup_queries: 2,
            search_results_per_use: 5,
            max_search_results: 8,
            search_context_chars: 3_000,
            discovery_max_tokens: 700,
            synthesis_max_tokens: 2_000,
            request_timeout: std::time::Duration::from_secs(10),
            openrouter_base: "https://example.com".into(),
            openrouter_api_key: "test".into(),
            model: "gemma".into(),
            scrape_url: "http://127.0.0.1:8091/v1/scrape".into(),
            postgres_dsn: "unused-by-input-unit-tests".into(),
        }
    }

    #[test]
    fn watch_reuse_requires_exact_citation_identity() {
        let snapshot = crate::memory::tests::source("unchanged");
        let mut receipt =
            json!({"id":snapshot.source_id,"url":snapshot.requested_url,"sha256":snapshot.sha256});
        assert!(snapshot_matches_receipt(&snapshot, &receipt));
        receipt["id"] = json!("S2");
        assert!(!snapshot_matches_receipt(&snapshot, &receipt));
        receipt["id"] = json!("S1");
        receipt["sha256"] = json!("different");
        assert!(!snapshot_matches_receipt(&snapshot, &receipt));
    }

    #[test]
    fn accepts_sql_friendly_json_seed_urls() {
        let input = ResearchInput::from_value(
            json!({
                "question": "What changed?",
                "seed_urls": "[\"https://example.com/a\",\"https://example.com/b\"]",
                "search": false,
                "max_sources": 99
            }),
            &config(),
        )
        .unwrap();
        assert_eq!(input.seed_urls.len(), 2);
        assert_eq!(input.max_sources, 6);
        assert_eq!(input.mode, ResearchMode::Research);
    }

    #[test]
    fn rejects_no_discovery_path() {
        let error = ResearchInput::from_value(
            json!({"question":"What changed?", "search":false}),
            &config(),
        )
        .unwrap_err();
        assert_eq!(error.code, "no_discovery_path");
    }

    #[test]
    fn passage_selection_prefers_query_terms_and_respects_cap() {
        let markdown = "Generic introduction.\n\n# Release status\nPostgreSQL 19 beta is available.\n\nUnrelated long section about gardening.";
        let selected = select_passages("PostgreSQL 19 release status", markdown, 80);
        assert!(selected.contains("PostgreSQL 19"));
        assert!(selected.chars().count() <= 80);
    }

    #[test]
    fn synthesis_rejects_unknown_source_ids() {
        let text = r#"{"answer_markdown":"Fact [S9]","claims":[{"claim":"Fact","source_ids":["S9"],"confidence":0.8}],"contradictions":[],"gaps":[]}"#;
        assert!(parse_and_validate_synthesis(text, &["S1".into()]).is_err());
    }

    #[test]
    fn synthesis_accepts_fenced_json_and_clamps_confidence() {
        let text = "```json\n{\"answer_markdown\":\"Fact [S1]\",\"claims\":[{\"claim\":\"Fact\",\"source_ids\":[\"S1\"],\"confidence\":2.0}],\"contradictions\":[],\"gaps\":[]}\n```";
        let mut synthesis = parse_and_validate_synthesis(text, &["S1".into()]).unwrap();
        normalize_synthesis(&mut synthesis);
        assert_eq!(synthesis.claims[0].confidence, 1.0);
    }

    #[test]
    fn grouped_citations_become_individually_addressable() {
        assert_eq!(
            normalize_grouped_citations("Fact [S1, S3] and another [S2]."),
            "Fact [S1][S3] and another [S2]."
        );
    }

    #[test]
    fn watch_defaults_to_seed_only_and_has_stable_id() {
        let input = ResearchInput::from_value(
            json!({
                "question":"Did this change?",
                "seed_urls":["https://example.com/a"],
                "mode":"watch"
            }),
            &config(),
        )
        .unwrap();
        assert_eq!(input.mode, ResearchMode::Watch);
        assert!(!input.search);
        assert_eq!(
            derived_watch_id(&input.question, &input.seed_urls),
            derived_watch_id(&input.question, &input.seed_urls)
        );
    }

    #[test]
    fn extracts_ranked_static_links_for_gap_closure() {
        let source = PreparedSource {
            evidence: SourceEvidence {
                id: "S1".into(),
                url: "https://example.com/start".into(),
                title: "Start".into(),
                content: "Evidence".into(),
            },
            snapshot: SnapshotWrite {
                source_id: "S1".into(),
                requested_url: "https://example.com/start".into(),
                final_url: "https://example.com/start".into(),
                title: "Start".into(),
                format: Some("html".into()),
                published_date: None,
                sha256: "abc".into(),
                etag: None,
                last_modified: None,
                markdown: "Evidence".into(),
                selected_evidence: "Evidence".into(),
                discovered_by: "seed".into(),
                cache_status: "new".into(),
                selected_chars: 8,
                evidence_excerpt: "Evidence".into(),
            },
            receipt: json!({"ok":true,"id":"S1","url":"https://example.com/start"}),
            full_markdown:
                "Read the [official release documentation](/docs/release) or [logo](/logo.png)."
                    .into(),
            cache_status: "new".into(),
        };
        let batch = PreparedBatch {
            sources: vec![source],
            failed: 0,
            failed_receipts: Vec::new(),
        };
        let links = linked_candidates(&batch, "release documentation", 2);
        assert_eq!(links.len(), 1);
        assert_eq!(links[0].url, "https://example.com/docs/release");
    }

    #[derive(Clone, Default)]
    struct MockCalls {
        syntheses: Arc<AtomicUsize>,
        conditional_fetches: Arc<AtomicUsize>,
    }

    async fn mock_model(State(calls): State<MockCalls>, Json(_body): Json<Value>) -> Json<Value> {
        calls.syntheses.fetch_add(1, Ordering::Relaxed);
        let content = json!({
            "answer_markdown":"The policy is stable. [S1]",
            "claims":[{"claim":"The policy is stable.","source_ids":["S1"],"confidence":0.95}],
            "contradictions":[],
            "gaps":[],
            "knowledge":{
                "entities":[{"name":"Stable Policy","kind":"document","aliases":[],"source_ids":["S1"],"confidence":0.9}],
                "relationships":[],
                "events":[]
            },
            "follow_up_queries":[]
        })
        .to_string();
        Json(json!({
            "choices":[{"message":{"role":"assistant","content":content,"annotations":[]}}],
            "usage":{"prompt_tokens":100,"completion_tokens":25,"total_tokens":125,"cost":0.0001}
        }))
    }

    async fn mock_scrape(State(calls): State<MockCalls>, Json(body): Json<Value>) -> Json<Value> {
        let outputs = body
            .get("inputs")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .map(|input| {
                let url = input.get("url").and_then(Value::as_str).unwrap();
                if input.get("if_none_match").and_then(Value::as_str) == Some("\"stable\"") {
                    calls.conditional_fetches.fetch_add(1, Ordering::Relaxed);
                    json!({
                        "ok":true,
                        "not_modified":true,
                        "url":url,
                        "final_url":url,
                        "status_code":304,
                        "format":"not_modified",
                        "source":"http-cache",
                        "markdown":"",
                        "bytes":0,
                        "etag":"\"stable\"",
                        "fetch_ms":1.0,
                        "extract_ms":0.0
                    })
                } else {
                    json!({
                        "ok":true,
                        "not_modified":false,
                        "url":url,
                        "final_url":url,
                        "status_code":200,
                        "format":"html",
                        "source":"mock",
                        "markdown":"# Stable Policy\n\nThe policy is stable and remains in force.",
                        "metadata":{"title":"Stable Policy"},
                        "sha256":"stable-sha",
                        "bytes":58,
                        "etag":"\"stable\"",
                        "fetch_ms":1.0,
                        "extract_ms":1.0
                    })
                }
            })
            .collect::<Vec<_>>();
        Json(json!({"outputs":outputs}))
    }

    async fn mock_deep_model(
        State(calls): State<MockCalls>,
        Json(_body): Json<Value>,
    ) -> Json<Value> {
        let synthesis = calls.syntheses.fetch_add(1, Ordering::Relaxed);
        let content = if synthesis == 0 {
            json!({
                "answer_markdown":"The release exists, but its migration details need confirmation. [S1]",
                "claims":[{"claim":"The release exists.","source_ids":["S1"],"confidence":0.85}],
                "contradictions":[],
                "gaps":["Confirm the official migration details."],
                "knowledge":{"entities":[],"relationships":[],"events":[]},
                "follow_up_queries":["official migration details"]
            })
        } else {
            json!({
                "answer_markdown":"The release exists and the official migration requires a staged upgrade. [S1][S2]",
                "claims":[
                    {"claim":"The release exists.","source_ids":["S1"],"confidence":0.9},
                    {"claim":"The migration requires a staged upgrade.","source_ids":["S2"],"confidence":0.95}
                ],
                "contradictions":[],
                "gaps":[],
                "knowledge":{
                    "entities":[{"name":"Staged Upgrade","kind":"process","aliases":[],"source_ids":["S2"],"confidence":0.9}],
                    "relationships":[],
                    "events":[]
                },
                "follow_up_queries":[]
            })
        }
        .to_string();
        Json(json!({
            "choices":[{"message":{"role":"assistant","content":content,"annotations":[]}}],
            "usage":{"prompt_tokens":100,"completion_tokens":25,"total_tokens":125,"cost":0.0001}
        }))
    }

    async fn mock_deep_scrape(Json(body): Json<Value>) -> Json<Value> {
        let outputs = body
            .get("inputs")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .map(|input| {
                let url = input.get("url").and_then(Value::as_str).unwrap();
                let (title, markdown, sha256) = if url.ends_with("/details") {
                    (
                        "Official Migration Details",
                        "# Official Migration Details\n\nThe migration requires a staged upgrade.",
                        "details-sha",
                    )
                } else {
                    (
                        "Release",
                        "# Release\n\nThe release exists. Read the [official migration details](/details).",
                        "release-sha",
                    )
                };
                json!({
                    "ok":true,
                    "not_modified":false,
                    "url":url,
                    "final_url":url,
                    "status_code":200,
                    "format":"html",
                    "source":"mock",
                    "markdown":markdown,
                    "metadata":{"title":title},
                    "sha256":sha256,
                    "bytes":markdown.len(),
                    "fetch_ms":1.0,
                    "extract_ms":1.0
                })
            })
            .collect::<Vec<_>>();
        Json(json!({"outputs":outputs}))
    }

    #[tokio::test]
    #[ignore = "requires CLOVER_RESEARCH_TEST_DSN and schema.sql"]
    async fn deep_mode_follows_static_evidence_and_resynthesizes() {
        let calls = MockCalls::default();
        let model_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let model_address = model_listener.local_addr().unwrap();
        let model_app = Router::new()
            .route("/chat/completions", post(mock_deep_model))
            .with_state(calls.clone());
        let model_server = tokio::spawn(async move {
            axum::serve(model_listener, model_app).await.unwrap();
        });
        let scrape_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let scrape_address = scrape_listener.local_addr().unwrap();
        let scrape_server = tokio::spawn(async move {
            axum::serve(
                scrape_listener,
                Router::new().route("/v1/scrape", post(mock_deep_scrape)),
            )
            .await
            .unwrap();
        });

        let memory = crate::memory::tests::test_store().await;
        let tenant_scope = crate::memory::tests::scope();
        let mut config = config();
        config.max_source_chars = 1_000;
        config.max_evidence_chars = 4_000;
        config.max_snapshot_chars = 5_000;
        config.openrouter_base = format!("http://{model_address}");
        config.scrape_url = format!("http://{scrape_address}/v1/scrape");
        let model = OpenRouter::new(
            config.openrouter_base.clone(),
            "test".into(),
            "gemma".into(),
            config.request_timeout,
        )
        .unwrap();
        let input = ResearchInput::from_value(
            json!({
                "question":"What are the release migration requirements?",
                "seed_urls":["https://example.com/release"],
                "mode":"deep",
                "search":false,
                "max_sources":3
            }),
            &config,
        )
        .unwrap();
        let output = run(
            input,
            &tenant_scope,
            &config,
            &model,
            &Client::new(),
            &memory,
        )
        .await
        .unwrap();

        let gap = output["trail"]
            .as_array()
            .unwrap()
            .iter()
            .find(|entry| entry["stage"] == "gap_closure")
            .unwrap();
        assert_eq!(gap["status"], "ok");
        assert_eq!(gap["new_sources"], 1);
        assert_eq!(output["sources"].as_array().unwrap().len(), 2);
        assert_eq!(output["sources"][1]["discovered_by"], "source_link");
        assert_eq!(output["claims"][1]["source_ids"][0], "S2");
        assert_eq!(
            output["claims"][1]["evidence_snapshots"][0]["snapshot_id"],
            output["sources"][1]["snapshot_id"]
        );
        assert_eq!(calls.syntheses.load(Ordering::Relaxed), 2);

        model_server.abort();
        scrape_server.abort();
    }

    #[tokio::test]
    #[ignore = "requires CLOVER_RESEARCH_TEST_DSN and schema.sql"]
    async fn unchanged_watch_reuses_synthesis_and_preserves_lineage() {
        let calls = MockCalls::default();
        let model_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let model_address = model_listener.local_addr().unwrap();
        let model_app = Router::new()
            .route("/chat/completions", post(mock_model))
            .with_state(calls.clone());
        let model_server = tokio::spawn(async move {
            axum::serve(model_listener, model_app).await.unwrap();
        });
        let scrape_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let scrape_address = scrape_listener.local_addr().unwrap();
        let scrape_app = Router::new()
            .route("/v1/scrape", post(mock_scrape))
            .with_state(calls.clone());
        let scrape_server = tokio::spawn(async move {
            axum::serve(scrape_listener, scrape_app).await.unwrap();
        });

        let memory = crate::memory::tests::test_store().await;
        let tenant_scope = crate::memory::tests::scope();
        let mut config = config();
        config.max_source_chars = 1_000;
        config.max_evidence_chars = 4_000;
        config.max_snapshot_chars = 5_000;
        config.openrouter_base = format!("http://{model_address}");
        config.scrape_url = format!("http://{scrape_address}/v1/scrape");
        let model = OpenRouter::new(
            config.openrouter_base.clone(),
            "test".into(),
            "gemma".into(),
            config.request_timeout,
        )
        .unwrap();
        let http = Client::new();
        let input = ResearchInput::from_value(
            json!({
                "question":"Is the policy still in force?",
                "seed_urls":["https://example.com/policy"],
                "mode":"watch",
                "watch_id":"policy-watch"
            }),
            &config,
        )
        .unwrap();
        let first = run(
            input.clone(),
            &tenant_scope,
            &config,
            &model,
            &http,
            &memory,
        )
        .await
        .unwrap();
        assert_eq!(first["changes"]["status"], "initial");
        assert!(first["memory"]["stored"].as_bool().unwrap());
        assert_eq!(
            first["claims"][0]["evidence_snapshots"][0]["snapshot_id"],
            first["sources"][0]["snapshot_id"]
        );

        let second = run(
            input.clone(),
            &tenant_scope,
            &config,
            &model,
            &http,
            &memory,
        )
        .await
        .unwrap();
        assert_eq!(second["changes"]["status"], "unchanged");
        assert_eq!(second["sources"][0]["cache_status"], "revalidated");
        assert_eq!(
            second["memory"]["previous_run_id"],
            first["memory"]["run_id"]
        );
        assert_ne!(second["memory"]["run_id"], first["memory"]["run_id"]);
        assert_eq!(second["memory"]["synthesis_reused"], true);
        assert_eq!(second["usage"]["prompt_tokens"], 0);
        assert_eq!(calls.syntheses.load(Ordering::Relaxed), 1);
        assert_eq!(calls.conditional_fetches.load(Ordering::Relaxed), 1);

        let reopened = crate::memory::tests::test_store().await;
        let third = run(
            input.clone(),
            &tenant_scope,
            &config,
            &model,
            &http,
            &reopened,
        )
        .await
        .unwrap();
        assert_eq!(third["memory"]["synthesis_reused"], true);
        assert_eq!(third["usage"]["cost"], 0.0);
        // An unrelated research run can refresh this URL. Conditional-fetch
        // reuse alone must not reuse this watch's now-outdated synthesis.
        let mut snapshot = crate::memory::tests::source("changed");
        snapshot.requested_url = "https://example.com/policy".into();
        snapshot.final_url = snapshot.requested_url.clone();
        snapshot.sha256 = "newer-sha".into();
        snapshot.markdown = "The policy was updated by another research run.".into();
        snapshot.etag = Some("\"stable\"".into());
        reopened
            .persist_run(
                &tenant_scope,
                RunWrite {
                    question: "Unrelated research".into(),
                    mode: "research".into(),
                    watch_id: None,
                    result: json!({}),
                    sources: vec![snapshot],
                    claims: vec![],
                },
                |_, _| {},
            )
            .await
            .unwrap();
        let refreshed = run(
            input.clone(),
            &tenant_scope,
            &config,
            &model,
            &http,
            &reopened,
        )
        .await
        .unwrap();
        assert_eq!(refreshed["memory"]["synthesis_reused"], false);
        assert_eq!(calls.syntheses.load(Ordering::Relaxed), 2);
        let mut changed = input;
        changed.question = "Who does this policy affect?".into();
        let fourth = run(changed, &tenant_scope, &config, &model, &http, &reopened)
            .await
            .unwrap();
        assert_eq!(fourth["memory"]["synthesis_reused"], false);
        assert_eq!(calls.syntheses.load(Ordering::Relaxed), 3);

        model_server.abort();
        scrape_server.abort();
    }
}
