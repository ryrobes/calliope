use crate::ServiceError;
use postgres::{Client, Config, Row};
use serde::Serialize;
use serde_json::{json, Value};
use std::{collections::HashMap, sync::Arc, time::Duration};

#[derive(Clone)]
pub struct MemoryStore {
    config: Arc<Config>,
}

#[derive(Clone, Debug)]
pub struct CachedSnapshot {
    pub row_id: i64,
    pub requested_url: String,
    pub final_url: String,
    pub title: String,
    pub format: Option<String>,
    pub published_date: Option<String>,
    pub sha256: String,
    pub etag: Option<String>,
    pub last_modified: Option<String>,
    pub markdown: String,
    pub fetched_at: String,
    pub last_checked_at: String,
}

#[derive(Clone, Debug)]
pub struct SnapshotWrite {
    pub source_id: String,
    pub requested_url: String,
    pub final_url: String,
    pub title: String,
    pub format: Option<String>,
    pub published_date: Option<String>,
    pub sha256: String,
    pub etag: Option<String>,
    pub last_modified: Option<String>,
    pub markdown: String,
    pub selected_evidence: String,
    pub discovered_by: String,
    pub cache_status: String,
    pub selected_chars: usize,
    pub evidence_excerpt: String,
}

#[derive(Clone, Debug)]
pub struct ClaimWrite {
    pub claim: String,
    pub confidence: f64,
    pub source_ids: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct RunWrite {
    pub question: String,
    pub mode: String,
    pub watch_id: Option<String>,
    pub result: Value,
    pub sources: Vec<SnapshotWrite>,
    pub claims: Vec<ClaimWrite>,
}

#[derive(Clone, Debug)]
pub struct WatchState {
    pub run_id: String,
    pub result: Value,
}

#[derive(Clone, Debug, Serialize)]
pub struct SourceLineage {
    pub source_id: String,
    pub snapshot_id: String,
    pub sha256: String,
    pub cache_status: String,
}

#[derive(Clone, Debug)]
pub struct PersistedRun {
    pub run_id: String,
    pub previous_run_id: Option<String>,
    pub source_lineage: Vec<SourceLineage>,
    pub snapshots_written: usize,
    pub cache_reuses: usize,
    pub changed_sources: usize,
}

impl MemoryStore {
    pub async fn open(dsn: &str) -> Result<Self, ServiceError> {
        let mut config: Config = dsn
            .parse()
            .map_err(|_| ServiceError::new("memory_error", "Invalid CLOVER_RESEARCH_DSN"))?;
        config.connect_timeout(Duration::from_secs(5));
        config.options(
            "-c statement_timeout=15000 -c lock_timeout=5000 -c rvbbit.force_heap_scan=on",
        );
        let store = Self {
            config: Arc::new(config),
        };
        store.ready().await?;
        Ok(store)
    }

    pub async fn ready(&self) -> Result<(), ServiceError> {
        let config = self.config.clone();
        run_pg(move || {
            let mut client = connect(&config)?;
            client.query("SELECT id FROM clover_research.research_runs LIMIT 0", &[])?;
            Ok(())
        })
        .await
    }

    pub async fn latest_snapshots(
        &self,
        tenant_scope: &str,
        urls: &[String],
    ) -> Result<HashMap<String, CachedSnapshot>, ServiceError> {
        if urls.is_empty() {
            return Ok(HashMap::new());
        }
        let config = self.config.clone();
        let scope = tenant_scope.to_owned();
        let urls = urls.to_vec();
        run_pg(move || {
            let rows = connect(&config)?.query(
                "SELECT DISTINCT ON (requested_url) id,requested_url,final_url,title,format,published_date,
                 content_sha256,etag,last_modified,markdown,
                 to_char(fetched_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"'),
                 to_char(last_checked_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"')
                 FROM clover_research.web_snapshots WHERE tenant_scope=$1 AND requested_url=ANY($2)
                 ORDER BY requested_url,last_checked_at DESC,id DESC", &[&scope,&urls])?;
            Ok(rows.iter().map(snapshot_from_row).map(|s| (s.requested_url.clone(),s)).collect())
        }).await
    }

    pub async fn watch_state(
        &self,
        tenant_scope: &str,
        watch_id: &str,
    ) -> Result<Option<WatchState>, ServiceError> {
        let config = self.config.clone();
        let scope = tenant_scope.to_owned();
        let watch_id = watch_id.to_owned();
        run_pg(move || {
            Ok(connect(&config)?.query_opt(
                "SELECT r.id,r.result_json FROM clover_research.research_watches w
                 JOIN clover_research.research_runs r ON r.id=w.last_run_id AND r.tenant_scope=w.tenant_scope
                 WHERE w.tenant_scope=$1 AND w.watch_id=$2", &[&scope,&watch_id])?
                .map(|row| WatchState { run_id: run_id(row.get(0)), result: row.get(1) }))
        }).await
    }

    // The final public receipt, its exact evidence, claims and watch pointer commit
    // together. A reader can never see an unfinished receipt from a second UPDATE.
    pub async fn persist_run<F>(
        &self,
        tenant_scope: &str,
        mut run: RunWrite,
        finalize: F,
    ) -> Result<Value, ServiceError>
    where
        F: FnOnce(&mut Value, &PersistedRun) + Send + 'static,
    {
        let config = self.config.clone();
        let scope = tenant_scope.to_owned();
        run_pg(move || {
            let mut client = connect(&config)?;
            let mut tx = client.transaction()?;
            if let Some(watch) = &run.watch_id {
                tx.query_one("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", &[&format!("clover-research:{scope}:{watch}")])?;
            }
            let previous_run_id = if let Some(watch) = &run.watch_id {
                tx.query_opt("SELECT last_run_id FROM clover_research.research_watches WHERE tenant_scope=$1 AND watch_id=$2", &[&scope,watch])?
                    .map(|r| run_id(r.get(0)))
            } else { None };
            let row_id: i64 = tx.query_one(
                "INSERT INTO clover_research.research_runs(tenant_scope,mode,question,watch_id,result_json)
                 VALUES($1,$2,$3,$4,$5) RETURNING id",
                &[&scope,&run.mode,&run.question,&run.watch_id,&run.result])?.get(0);
            let mut persisted = PersistedRun { run_id: run_id(row_id), previous_run_id,
                source_lineage: Vec::new(), snapshots_written: 0, cache_reuses: 0, changed_sources: 0 };
            let mut snapshot_ids = HashMap::new();
            // Consistent lock order also prevents overlapping multi-source writes
            // in different discovery orders from deadlocking on the unique key.
            run.sources.sort_by(|a,b| a.requested_url.cmp(&b.requested_url));
            for source in run.sources {
                let snapshot_row_id: i64 = tx.query_one(
                    "INSERT INTO clover_research.web_snapshots AS s
                     (tenant_scope,requested_url,final_url,title,format,published_date,content_sha256,etag,last_modified,markdown)
                     VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                     ON CONFLICT(tenant_scope,requested_url,content_sha256) DO UPDATE SET
                     final_url=excluded.final_url,title=excluded.title,format=COALESCE(excluded.format,s.format),
                     published_date=COALESCE(excluded.published_date,s.published_date),etag=COALESCE(excluded.etag,s.etag),
                     last_modified=COALESCE(excluded.last_modified,s.last_modified),last_checked_at=clock_timestamp() RETURNING id",
                    &[&scope,&source.requested_url,&source.final_url,&source.title,&source.format,&source.published_date,
                      &source.sha256,&source.etag,&source.last_modified,&source.markdown])?.get(0);
                tx.execute("INSERT INTO clover_research.research_run_sources
                     (run_id,source_id,snapshot_id,discovered_by,cache_status,selected_chars,evidence_excerpt,evidence_text)
                     VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
                     &[&row_id,&source.source_id,&snapshot_row_id,&source.discovered_by,&source.cache_status,
                       &(source.selected_chars as i32),&source.evidence_excerpt,&source.selected_evidence])?;
                match source.cache_status.as_str() {
                    "new" | "changed" => persisted.snapshots_written += 1,
                    "revalidated" | "unchanged" | "stale_fallback" => persisted.cache_reuses += 1,
                    _ => {}
                }
                if source.cache_status == "changed" { persisted.changed_sources += 1; }
                snapshot_ids.insert(source.source_id.clone(), snapshot_id(snapshot_row_id));
                persisted.source_lineage.push(SourceLineage { source_id: source.source_id,
                    snapshot_id: snapshot_id(snapshot_row_id), sha256: source.sha256, cache_status: source.cache_status });
            }
            for (ordinal, claim) in run.claims.into_iter().enumerate() {
                let linked: Vec<_> = claim.source_ids.iter().filter_map(|id| snapshot_ids.get(id)).collect();
                tx.execute("INSERT INTO clover_research.research_claims(run_id,ordinal,claim,confidence,source_ids_json,snapshot_ids_json)
                    VALUES($1,$2,$3,$4,$5,$6)", &[&row_id,&(ordinal as i32),&claim.claim,&claim.confidence,&json!(claim.source_ids),&json!(linked)])?;
            }
            finalize(&mut run.result, &persisted);
            tx.execute("UPDATE clover_research.research_runs SET result_json=$1 WHERE id=$2 AND tenant_scope=$3", &[&run.result,&row_id,&scope])?;
            if let Some(watch) = run.watch_id {
                tx.execute("INSERT INTO clover_research.research_watches(tenant_scope,watch_id,question,last_run_id)
                    VALUES($1,$2,$3,$4) ON CONFLICT(tenant_scope,watch_id) DO UPDATE SET
                    question=excluded.question,last_run_id=excluded.last_run_id,last_checked_at=clock_timestamp()",
                    &[&scope,&watch,&run.question,&row_id])?;
            }
            tx.commit()?;
            Ok(run.result)
        }).await
    }
}

type DbError = Box<dyn std::error::Error + Send + Sync>;
fn connect(config: &Config) -> Result<Client, DbError> {
    let tls = native_tls::TlsConnector::new()?;
    Ok(config.connect(postgres_native_tls::MakeTlsConnector::new(tls))?)
}

fn snapshot_from_row(row: &Row) -> CachedSnapshot {
    CachedSnapshot {
        row_id: row.get(0),
        requested_url: row.get(1),
        final_url: row.get(2),
        title: row.get(3),
        format: row.get(4),
        published_date: row.get(5),
        sha256: row.get(6),
        etag: row.get(7),
        last_modified: row.get(8),
        markdown: row.get(9),
        fetched_at: row.get(10),
        last_checked_at: row.get(11),
    }
}

async fn run_pg<T, F>(work: F) -> Result<T, ServiceError>
where
    T: Send + 'static,
    F: FnOnce() -> Result<T, DbError> + Send + 'static,
{
    tokio::task::spawn_blocking(work)
        .await
        .map_err(|_| ServiceError::new("memory_error", "Research persistence worker failed"))?
        .map_err(|error| {
            // Database errors may contain evidence or credentials; emit only SQLSTATE.
            let code = error
                .downcast_ref::<postgres::Error>()
                .and_then(|e| e.code())
                .map(|c| c.code())
                .unwrap_or("connection");
            tracing::warn!(code, "Research Postgres operation failed");
            ServiceError::new(
                "memory_error",
                format!(
                    "Research Postgres unavailable ({code}); check schema and service credentials"
                ),
            )
        })
}
fn run_id(id: i64) -> String {
    format!("R{id}")
}
fn snapshot_id(id: i64) -> String {
    format!("P{id}")
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    pub(crate) fn scope() -> String {
        use sha2::{Digest, Sha256};
        hex::encode(Sha256::digest(format!(
            "{:?}-{:?}",
            std::time::SystemTime::now(),
            std::thread::current().id()
        )))
    }
    pub(crate) async fn test_store() -> MemoryStore {
        MemoryStore::open(&std::env::var("CLOVER_RESEARCH_TEST_DSN").expect("test DSN required"))
            .await
            .unwrap()
    }
    pub(crate) fn source(status: &str) -> SnapshotWrite {
        SnapshotWrite {
            source_id: "S1".into(),
            requested_url: "https://example.com/".into(),
            final_url: "https://example.com/".into(),
            title: "Example".into(),
            format: Some("html".into()),
            published_date: None,
            sha256: "abc123".into(),
            etag: Some("etag".into()),
            last_modified: None,
            markdown: "Full Evidence".into(),
            selected_evidence: "Evidence".into(),
            discovered_by: "seed".into(),
            cache_status: status.into(),
            selected_chars: 8,
            evidence_excerpt: "Evidence".into(),
        }
    }
    fn write(status: &str) -> RunWrite {
        RunWrite {
            question: "Question?".into(),
            mode: "watch".into(),
            watch_id: Some("watch-1".into()),
            result: json!({"answer_markdown":"Answer [S1]"}),
            sources: vec![source(status)],
            claims: vec![ClaimWrite {
                claim: "Answer".into(),
                confidence: 0.9,
                source_ids: vec!["S1".into()],
            }],
        }
    }
    fn finalize(value: &mut Value, persisted: &PersistedRun) {
        value["run_id"] = json!(persisted.run_id);
        value["previous_run_id"] = json!(persisted.previous_run_id);
        value["lineage"] = json!(persisted.source_lineage);
    }
    #[tokio::test]
    #[ignore = "requires CLOVER_RESEARCH_TEST_DSN and schema.sql"]
    async fn postgres_isolates_tenants_and_commits_complete_receipts() {
        let store = test_store().await;
        let tenant = scope();
        let first = store
            .persist_run(&tenant, write("new"), finalize)
            .await
            .unwrap();
        let other = scope();
        assert!(store
            .latest_snapshots(&other, &["https://example.com/".into()])
            .await
            .unwrap()
            .is_empty());
        assert!(store
            .watch_state(&other, "watch-1")
            .await
            .unwrap()
            .is_none());
        let reopened = test_store().await;
        let previous = reopened
            .watch_state(&tenant, "watch-1")
            .await
            .unwrap()
            .unwrap();
        assert_eq!(previous.result, first);
        let second = reopened
            .persist_run(&tenant, write("unchanged"), finalize)
            .await
            .unwrap();
        assert_eq!(second["previous_run_id"], first["run_id"]);
        assert_eq!(
            second["lineage"][0]["snapshot_id"],
            first["lineage"][0]["snapshot_id"]
        );
        let mut broken = write("changed");
        broken.sources.push(source("new")); // Duplicate run/source key: whole transaction must fail.
        assert!(store.persist_run(&tenant, broken, finalize).await.is_err());
        assert_eq!(
            store
                .watch_state(&tenant, "watch-1")
                .await
                .unwrap()
                .unwrap()
                .result,
            second
        );
        let found = store
            .latest_snapshots(&tenant, &["https://example.com/".into()])
            .await
            .unwrap();
        assert_eq!(found["https://example.com/"].markdown, "Full Evidence");
    }
    #[tokio::test]
    #[ignore = "requires CLOVER_RESEARCH_TEST_DSN and schema.sql"]
    async fn concurrent_watch_commits_form_one_history_chain() {
        let store = test_store().await;
        let tenant = scope();
        let (a, b) = tokio::join!(
            store.persist_run(&tenant, write("new"), finalize),
            store.persist_run(&tenant, write("new"), finalize)
        );
        let a = a.unwrap();
        let b = b.unwrap();
        assert!(a["previous_run_id"] == b["run_id"] || b["previous_run_id"] == a["run_id"]);
        assert_eq!(
            a["lineage"][0]["snapshot_id"],
            b["lineage"][0]["snapshot_id"]
        );
    }
}
