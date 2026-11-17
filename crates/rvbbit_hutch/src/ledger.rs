//! Optional Postgres ledger. The request path only tries a bounded memory queue.
//! No SQLite spool, network IO, database lock, or provider payload on that path.
use postgres::{Client, Config};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    sync::{
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        mpsc, Arc, Mutex,
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

fn default_dsn_env() -> String {
    "CLOVER_LEDGER_DSN".into()
}
fn default_capacity() -> usize {
    4096
}
#[derive(Debug, Clone, Deserialize)]
pub struct LedgerConfig {
    #[serde(default = "default_dsn_env")]
    pub dsn_env: String,
    pub instance_id: String,
    #[serde(default = "default_capacity")]
    pub queue_capacity: usize,
}
#[derive(Default)]
struct Shared {
    depth: AtomicUsize,
    written: AtomicU64,
    dropped: AtomicU64,
    failures: AtomicU64,
    last_write: AtomicU64,
    connected: AtomicBool,
    stop: AtomicBool,
    snapshot: Mutex<Value>,
    error: Mutex<Option<&'static str>>,
}
pub struct PgLedger {
    config: LedgerConfig,
    writer_id: String,
    tx: mpsc::SyncSender<Value>,
    shared: Arc<Shared>,
    worker: Mutex<Option<thread::JoinHandle<()>>>,
}
fn millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
impl PgLedger {
    pub fn start(config: &LedgerConfig) -> Result<Self, String> {
        if config.instance_id.is_empty()
            || config.instance_id.len() > 128
            || !(1..=65536).contains(&config.queue_capacity)
        {
            return Err(
                "ledger requires an instance_id (1..128 bytes) and queue_capacity (1..65536)"
                    .into(),
            );
        }
        // Check configuration, not availability, at startup. Never print the DSN.
        let dsn = std::env::var(&config.dsn_env)
            .map_err(|_| "ledger DSN environment variable is missing")?;
        let mut pg: Config = dsn.parse().map_err(|_| "invalid ledger Postgres DSN")?;
        pg.connect_timeout(Duration::from_secs(2));
        pg.tcp_user_timeout(Duration::from_secs(5));
        pg.options(
            "-c statement_timeout=5000 -c lock_timeout=2000 -c application_name=clover-ledger -c rvbbit.force_heap_scan=on",
        );
        let tls =
            native_tls::TlsConnector::new().map_err(|_| "ledger TLS initialization failed")?;
        let (tx, rx) = mpsc::sync_channel(config.queue_capacity);
        let shared = Arc::new(Shared::default());
        let writer_id = uuid::Uuid::new_v4().to_string();
        let instance_id = config.instance_id.clone();
        let worker_id = writer_id.clone();
        let state = shared.clone();
        let capacity = config.queue_capacity;
        let worker = thread::Builder::new()
            .name("clover-ledger".into())
            .spawn(move || {
                run(pg, tls, rx, state, instance_id, worker_id, capacity);
            })
            .map_err(|_| "cannot start ledger worker")?;
        Ok(Self {
            config: config.clone(),
            writer_id,
            tx,
            shared,
            worker: Mutex::new(Some(worker)),
        })
    }
    pub fn enqueue(&self, kind: &str, data: impl Serialize) {
        let event = json!({"kind":kind,"event_id":uuid::Uuid::new_v4().to_string(),
            "instance_id":self.config.instance_id,"unix_ms":millis(),"data":data});
        // Bounded records AND record size, including the in-flight retry batch.
        if event.to_string().len() > 16384
            || self
                .shared
                .depth
                .fetch_update(Ordering::AcqRel, Ordering::Relaxed, |n| {
                    (n < self.config.queue_capacity).then_some(n + 1)
                })
                .is_err()
        {
            self.shared.dropped.fetch_add(1, Ordering::Relaxed);
            return;
        }
        if self.tx.try_send(event).is_err() {
            self.shared.depth.fetch_sub(1, Ordering::Release);
            self.shared.dropped.fetch_add(1, Ordering::Relaxed);
        }
    }
    pub fn snapshot(&self) -> Value {
        self.shared.snapshot.lock().unwrap().clone()
    }
    pub fn status(&self) -> Value {
        status(
            &self.shared,
            &self.config.instance_id,
            &self.writer_id,
            self.config.queue_capacity,
        )
    }
    pub async fn shutdown(&self) {
        let end = Instant::now() + Duration::from_secs(6);
        while self.shared.depth.load(Ordering::Acquire) > 0 && Instant::now() < end {
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        self.shared.stop.store(true, Ordering::Release);
        let mut handle = self.worker.lock().unwrap();
        if handle.as_ref().is_some_and(|h| h.is_finished()) {
            let _ = handle.take().unwrap().join();
        }
        let remaining = self.shared.depth.load(Ordering::Acquire);
        if remaining > 0 {
            tracing::warn!(
                remaining,
                "ledger shutdown left unwritten telemetry in memory"
            );
        }
    }
}
impl Drop for PgLedger {
    fn drop(&mut self) {
        self.shared.stop.store(true, Ordering::Release);
    }
}
fn status(s: &Shared, instance: &str, writer: &str, capacity: usize) -> Value {
    json!({"mode":"postgres","instance_id":instance,"writer_id":writer,"queue_capacity":capacity,
        "queue_depth":s.depth.load(Ordering::Acquire),"written_events":s.written.load(Ordering::Relaxed),
        "dropped_events":s.dropped.load(Ordering::Relaxed),"failed_batches":s.failures.load(Ordering::Relaxed),
        "connected":s.connected.load(Ordering::Acquire),"last_write_unix_ms":s.last_write.load(Ordering::Relaxed),
        "last_error":*s.error.lock().unwrap(),"snapshot_unix_ms":s.snapshot.lock().unwrap().get("snapshot_unix_ms").cloned(),
        "durability":"bounded_memory_until_postgres_commit"})
}
fn run(
    config: Config,
    tls: native_tls::TlsConnector,
    rx: mpsc::Receiver<Value>,
    s: Arc<Shared>,
    instance: String,
    writer: String,
    capacity: usize,
) {
    let mut client: Option<Client> = None;
    let mut batch = Vec::new();
    let mut next_connect = Instant::now();
    let mut next_snapshot = Instant::now();
    let mut backoff = 1;
    while !s.stop.load(Ordering::Acquire) {
        if batch.is_empty() {
            match rx.recv_timeout(Duration::from_millis(100)) {
                Ok(event) => batch.push(event),
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
                Err(mpsc::RecvTimeoutError::Timeout) => {}
            }
        }
        while batch.len() < 128 {
            match rx.try_recv() {
                Ok(event) => batch.push(event),
                Err(_) => break,
            }
        }
        if client.is_none() {
            if Instant::now() < next_connect {
                thread::sleep(Duration::from_millis(100));
                continue;
            }
            match config
                .connect(postgres_native_tls::MakeTlsConnector::new(tls.clone()))
                .and_then(|mut c| {
                    // Schema installation is an administrator operation. Runtime
                    // credentials never need DDL or access to RVBBIT catalogs.
                    c.simple_query("SELECT clover_telemetry.ingest('[]'::jsonb)")?;
                    Ok(c)
                }) {
                Ok(c) => {
                    client = Some(c);
                    s.connected.store(true, Ordering::Release);
                    *s.error.lock().unwrap() = None;
                    backoff = 1;
                    next_snapshot = Instant::now();
                }
                Err(_) => {
                    fail(&s, "connect_or_schema_failed");
                    next_connect = Instant::now() + Duration::from_secs(backoff);
                    backoff = (backoff * 2).min(15);
                    continue;
                }
            }
        }
        let c = client.as_mut().unwrap();
        if !batch.is_empty() {
            // Single function statement is atomic; a lost commit ACK is safe to retry.
            if c.execute("SELECT clover_telemetry.ingest($1)", &[&json!(batch)])
                .is_err()
            {
                fail(&s, "write_failed");
                client = None;
                next_connect = Instant::now() + Duration::from_secs(1);
                continue;
            }
            s.depth.fetch_sub(batch.len(), Ordering::Release);
            s.written.fetch_add(batch.len() as u64, Ordering::Relaxed);
            s.last_write.store(millis(), Ordering::Relaxed);
            batch.clear();
        }
        if Instant::now() >= next_snapshot {
            match c.query_one(SNAPSHOT, &[&instance]) {
                Ok(row) => {
                    let mut snapshot: Value = row.get(0);
                    snapshot["snapshot_unix_ms"] = json!(millis());
                    *s.snapshot.lock().unwrap() = snapshot;
                    let value = status(&s, &instance, &writer, capacity);
                    if c.execute("INSERT INTO clover_telemetry.writers(writer_id,instance_id,status) VALUES($1,$2,$3) ON CONFLICT(writer_id) DO UPDATE SET seen_at=now(),status=excluded.status",&[&writer,&instance,&value]).is_err() {
                        fail(&s,"heartbeat_failed");client=None;
                    }
                }
                Err(_) => {
                    fail(&s, "snapshot_failed");
                    client = None;
                }
            }
            next_snapshot = Instant::now() + Duration::from_secs(5);
        }
    }
}
fn fail(s: &Shared, code: &'static str) {
    s.connected.store(false, Ordering::Release);
    s.failures.fetch_add(1, Ordering::Relaxed);
    let mut error = s.error.lock().unwrap();
    if *error != Some(code) {
        tracing::warn!(
            code,
            "ledger unavailable; inference continues using bounded telemetry queue"
        );
    }
    *error = Some(code);
}
const SNAPSHOT: &str = r#"
SELECT jsonb_build_object(
 'pending',COALESCE((SELECT jsonb_agg(to_jsonb(p)) FROM (
   SELECT id AS row_id,tenant,canonical_model,provider,upstream_model,provider_request_id FROM clover_telemetry.upstream_attempts
   WHERE instance_id=$1 AND cost_status='pending' AND provider_request_id IS NOT NULL
     AND next_reconcile_unix<=extract(epoch FROM now())::bigint AND reconcile_attempts<10 ORDER BY id LIMIT 100
 ) p),'[]'::jsonb),
 'coverage',COALESCE((SELECT jsonb_agg(to_jsonb(c)) FROM (
   SELECT tenant,canonical_model,provider,
     count(*) FILTER(WHERE provider_cost_nanousd IS NOT NULL OR provider_request_id IS NOT NULL OR status BETWEEN 200 AND 299) AS eligible_attempts,
     count(provider_cost_nanousd) AS final_attempts,count(*) FILTER(WHERE cost_status='pending') AS pending_attempts
   FROM clover_telemetry.upstream_attempts WHERE instance_id=$1 GROUP BY tenant,canonical_model,provider
 ) c),'[]'::jsonb),
 'costs',COALESCE((SELECT jsonb_agg(to_jsonb(c)) FROM (
   SELECT tenant,canonical_model,provider,cost_source,count(*) AS observations,sum(provider_cost_nanousd)::bigint AS cost_nanousd
   FROM clover_telemetry.upstream_attempts WHERE instance_id=$1 AND provider_cost_nanousd IS NOT NULL
   GROUP BY tenant,canonical_model,provider,cost_source
 ) c),'[]'::jsonb),
 'prices',COALESCE((SELECT jsonb_agg(to_jsonb(p)) FROM (
   SELECT DISTINCT ON(provider,upstream_model) provider,upstream_model,prompt_usd_per_token,completion_usd_per_token,source
   FROM clover_telemetry.provider_price_snapshots WHERE instance_id=$1 ORDER BY provider,upstream_model,captured_at DESC
 ) p),'[]'::jsonb)
)
"#;
