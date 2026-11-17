use std::{env, net::SocketAddr, time::Duration};

#[derive(Clone, Debug)]
pub struct Config {
    pub bind: SocketAddr,
    pub max_inputs: usize,
    pub max_concurrency: usize,
    pub max_question_chars: usize,
    pub max_sources: usize,
    pub max_source_chars: usize,
    pub max_evidence_chars: usize,
    pub max_snapshot_chars: usize,
    pub max_search_uses: usize,
    pub max_followup_sources: usize,
    pub followup_search_uses: usize,
    pub max_followup_queries: usize,
    pub search_results_per_use: usize,
    pub max_search_results: usize,
    pub search_context_chars: usize,
    pub discovery_max_tokens: usize,
    pub synthesis_max_tokens: usize,
    pub request_timeout: Duration,
    pub openrouter_base: String,
    pub openrouter_api_key: String,
    pub model: String,
    pub scrape_url: String,
    pub postgres_dsn: String,
}

impl Config {
    pub fn from_env() -> Result<Self, String> {
        let bind = value("CLOVER_RESEARCH_BIND", "127.0.0.1:8092")?
            .parse()
            .map_err(|error| format!("bad CLOVER_RESEARCH_BIND: {error}"))?;
        let max_inputs = bounded_usize("CLOVER_RESEARCH_MAX_INPUTS", 2, 1, 8)?;
        let max_concurrency = bounded_usize("CLOVER_RESEARCH_MAX_CONCURRENCY", 2, 1, 8)?;
        let max_question_chars =
            bounded_usize("CLOVER_RESEARCH_MAX_QUESTION_CHARS", 4_000, 100, 20_000)?;
        let max_sources = bounded_usize("CLOVER_RESEARCH_MAX_SOURCES", 6, 1, 8)?;
        let max_source_chars =
            bounded_usize("CLOVER_RESEARCH_MAX_SOURCE_CHARS", 10_000, 1_000, 30_000)?;
        let max_evidence_chars = bounded_usize(
            "CLOVER_RESEARCH_MAX_EVIDENCE_CHARS",
            48_000,
            max_source_chars,
            120_000,
        )?;
        let max_snapshot_chars = bounded_usize(
            "CLOVER_RESEARCH_MAX_SNAPSHOT_CHARS",
            250_000,
            max_source_chars,
            1_000_000,
        )?;
        let max_search_uses = bounded_usize("CLOVER_RESEARCH_MAX_SEARCH_USES", 2, 1, 4)?;
        let max_followup_sources = bounded_usize("CLOVER_RESEARCH_MAX_FOLLOWUP_SOURCES", 2, 1, 4)?;
        let followup_search_uses = bounded_usize("CLOVER_RESEARCH_FOLLOWUP_SEARCH_USES", 1, 1, 2)?;
        let max_followup_queries = bounded_usize("CLOVER_RESEARCH_MAX_FOLLOWUP_QUERIES", 2, 1, 4)?;
        let search_results_per_use =
            bounded_usize("CLOVER_RESEARCH_SEARCH_RESULTS_PER_USE", 5, 1, 10)?;
        let max_search_results = bounded_usize(
            "CLOVER_RESEARCH_MAX_SEARCH_RESULTS",
            8,
            search_results_per_use,
            20,
        )?;
        let search_context_chars =
            bounded_usize("CLOVER_RESEARCH_SEARCH_CONTEXT_CHARS", 3_000, 500, 10_000)?;
        let discovery_max_tokens =
            bounded_usize("CLOVER_RESEARCH_DISCOVERY_MAX_TOKENS", 700, 200, 2_000)?;
        let synthesis_max_tokens =
            bounded_usize("CLOVER_RESEARCH_SYNTHESIS_MAX_TOKENS", 2_000, 500, 6_000)?;
        let request_timeout = Duration::from_millis(bounded_u64(
            "CLOVER_RESEARCH_TIMEOUT_MS",
            150_000,
            10_000,
            300_000,
        )?);
        let openrouter_api_key = crate::credentials::resolve("OPENROUTER_API_KEY")
            .ok_or_else(|| "OPENROUTER_API_KEY is required".to_string())?;
        if openrouter_api_key.trim().is_empty() {
            return Err("OPENROUTER_API_KEY cannot be blank".into());
        }

        Ok(Self {
            bind,
            max_inputs,
            max_concurrency,
            max_question_chars,
            max_sources,
            max_source_chars,
            max_evidence_chars,
            max_snapshot_chars,
            max_search_uses,
            max_followup_sources,
            followup_search_uses,
            max_followup_queries,
            search_results_per_use,
            max_search_results,
            search_context_chars,
            discovery_max_tokens,
            synthesis_max_tokens,
            request_timeout,
            openrouter_base: value(
                "CLOVER_RESEARCH_OPENROUTER_BASE",
                "https://openrouter.ai/api/v1",
            )?,
            openrouter_api_key,
            model: value("CLOVER_RESEARCH_MODEL", "openai/gpt-6-luna")?,
            scrape_url: value(
                "CLOVER_RESEARCH_SCRAPE_URL",
                "http://127.0.0.1:8091/v1/scrape",
            )?,
            postgres_dsn: env::var("CLOVER_RESEARCH_DSN")
                .ok()
                .filter(|v| !v.trim().is_empty())
                .ok_or("CLOVER_RESEARCH_DSN is required; prepare the Postgres schema first")?,
        })
    }
}

fn value(name: &str, default: &str) -> Result<String, String> {
    match env::var(name) {
        Ok(value) if value.trim().is_empty() => Err(format!("{name} cannot be blank")),
        Ok(value) => Ok(value),
        Err(env::VarError::NotPresent) => Ok(default.to_string()),
        Err(error) => Err(format!("cannot read {name}: {error}")),
    }
}

fn bounded_usize(name: &str, default: usize, min: usize, max: usize) -> Result<usize, String> {
    let parsed = value(name, &default.to_string())?
        .parse::<usize>()
        .map_err(|error| format!("bad {name}: {error}"))?;
    if !(min..=max).contains(&parsed) {
        return Err(format!("{name} must be between {min} and {max}"));
    }
    Ok(parsed)
}

fn bounded_u64(name: &str, default: u64, min: u64, max: u64) -> Result<u64, String> {
    let parsed = value(name, &default.to_string())?
        .parse::<u64>()
        .map_err(|error| format!("bad {name}: {error}"))?;
    if !(min..=max).contains(&parsed) {
        return Err(format!("{name} must be between {min} and {max}"));
    }
    Ok(parsed)
}
