//! Native recovery journal, not a second business database. One current workspace
//! per app with per-record edits, isolated by server/account. SQL and committed records stay on Calliope.
use crate::api::{Api, Connection};
use anyhow::{Result, ensure};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::mpsc,
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Draft {
    pub snapshot: Value,
    pub model: Value,
    pub pending: Option<Value>,
    pub files: BTreeMap<String, Value>,
    pub capture: Option<Value>,
    pub saved_at: u64,
    pub workspace: crate::app_workspace::Workspace,
}
impl Draft {
    pub fn follows_latest(&self) -> bool {
        if self.pending.is_some() || self.capture.is_some() || has_local(&self.model) {return false;}
        // Camera recovery can update the model before the next workspace save.
        let mut workspace=self.workspace.clone();
        workspace.remember(&self.snapshot,&self.model);
        workspace.dirty().is_empty()
    }
}
#[derive(Clone)]
pub struct Store {
    root: PathBuf,
    blobs: PathBuf,
    jobs: mpsc::Sender<Box<dyn FnOnce() + Send>>,
}
pub fn account_root(config: &Connection) -> PathBuf {
    use std::hash::{Hash, Hasher};
    let mut hash = std::collections::hash_map::DefaultHasher::new();
    (
        config.base_url.trim_end_matches('/'),
        config.email.trim().to_lowercase(),
    )
        .hash(&mut hash);
    crate::model::data_dir()
        .join("app-drafts")
        .join(format!("{:016x}", hash.finish()))
}
#[derive(Debug)]
pub struct HttpError {
    pub status: u16,
    pub code: String,
    pub message: String,
}
impl std::fmt::Display for HttpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} (HTTP {})", self.message, self.status)
    }
}
impl std::error::Error for HttpError {}
fn rejected(error: &anyhow::Error) -> bool {
    // Only server codes that explicitly guarantee no commit/rollback. An unknown
    // 400/409 is NOT enough: an external action may already have taken effect.
    error.downcast_ref::<HttpError>().is_some_and(|e| {
        matches!(
            e.code.as_str(),
            "INVALID_INPUT"
                | "INVALID_REQUEST_KEY"
                | "ACTION_NOT_FOUND"
                | "CONFLICT"
                | "ACTION_TIMEOUT"
                | "ACTION_FAILED"
                | "RESULT_TOO_LARGE"
                | "TOO_LARGE"
        )
    })
}
pub fn transport_error(error: &anyhow::Error) -> bool {
    error.chain().any(|e| {
        e.downcast_ref::<reqwest::Error>()
            .is_some_and(|e| e.is_connect() || e.is_timeout() || e.is_body() || e.is_request())
    })
}
pub fn atomic(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().unwrap();
    fs::create_dir_all(parent)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
    }
    let temp = parent.join(format!(".{}.tmp", uuid::Uuid::new_v4()));
    let result = (|| {
        let mut options = fs::OpenOptions::new();
        options.create_new(true).write(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temp)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        fs::rename(&temp, path)?;
        #[cfg(unix)]
        fs::File::open(parent)?.sync_all()?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result
}
fn read<T: serde::de::DeserializeOwned + Default>(path: &Path) -> Result<T> {
    match fs::read(path) {
        Ok(bytes) => Ok(serde_json::from_slice(&bytes)?),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(T::default()),
        Err(e) => Err(e.into()),
    }
}
fn filename(slug: &str) -> Result<String> {
    ensure!(
        !slug.is_empty()
            && slug.len() <= 160
            && slug
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_'),
        "Invalid app slug"
    );
    Ok(format!("{slug}.json"))
}
impl Store {
    pub fn new(config: &Connection) -> Self {
        let root = account_root(config);
        let blobs = crate::model::data_dir()
            .join("attachments")
            .join("drafts")
            .join(root.file_name().unwrap());
        Self::at(root, blobs)
    }
    fn at(root: PathBuf, blobs: PathBuf) -> Self {
        let (jobs, rx) = mpsc::channel::<Box<dyn FnOnce() + Send>>();
        std::thread::spawn(move || {
            for job in rx {
                job();
            }
        });
        Self { root, blobs, jobs }
    }
    fn sync<T: Send + 'static>(&self, f: impl FnOnce() -> Result<T> + Send + 'static) -> Result<T> {
        let (tx, rx) = mpsc::sync_channel(1);
        self.jobs
            .send(Box::new(move || {
                let _ = tx.send(f());
            }))
            .map_err(|_| anyhow::anyhow!("Draft writer stopped"))?;
        rx.recv()?
    }
    pub fn load(&self, slug: &str) -> Result<Draft> {
        let mut draft: Draft = read(&self.root.join("apps").join(filename(slug)?))?;
        // iOS may relocate its sandbox on app upgrades. Only rebase generated
        // blob names into this account's current directory, never arbitrary paths.
        for record in draft.files.values_mut().chain(draft.capture.iter_mut()) {
            if let Some(key) = record["id"].as_str().and_then(|s|s.strip_prefix("local:")) {
                if uuid::Uuid::parse_str(key).is_ok() {
                    let old = PathBuf::from(record["path"].as_str().unwrap_or(""));
                    let name = old.file_name().and_then(|s|s.to_str()).unwrap_or("");
                    if name == key || name == format!("{key}.jpg") {
                        record["path"] = json!(self.blobs.join(name));
                    }
                }
            }
        }
        Ok(draft)
    }
    fn write(&self, slug: &str, draft: &mut Draft) -> Result<()> {
        draft.workspace.remember(&draft.snapshot, &draft.model);
        draft.saved_at = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
        atomic(
            &self.root.join("apps").join(filename(slug)?),
            &serde_json::to_vec(draft)?,
        )
    }
    pub fn index(&self) -> Result<Value> {
        read(&self.root.join("index.json"))
    }
    pub fn annotate_index(&self, index: &mut Value) {
        if let Some(apps) = index["apps"].as_array_mut() {
            for app in apps {
                if let Some(slug) = app["slug"].as_str() {
                    if let Ok(draft) = self.load(slug) {
                        if !draft.snapshot.is_null() {
                            let count=draft.workspace.dirty().len();
                            app["cached"]=json!(true);
                            app["last_used"]=json!(draft.saved_at);
                            app["draft_count"]=json!(count);
                            app["draft_status"] = if draft.pending.is_some() {
                                json!("Pending submission")
                            } else if count>0 {
                                json!(format!("{count} unfinished {}",if count==1{"draft"}else{"drafts"}))
                            } else {
                                Value::Null
                            };
                        }
                    }
                }
            }
        }
    }
    pub fn save_index(&self, value: Value) -> Result<()> {
        let path = self.root.join("index.json");
        self.sync(move || atomic(&path, &serde_json::to_vec(&value)?))
    }
    pub fn save(
        &self, slug: String, snapshot: Value, model: Value,
        done: impl FnOnce(Result<()>) + Send + 'static,
    ) { self.save_workspace(slug, snapshot, model, None, done); }
    pub fn save_workspace(
        &self, slug: String, snapshot: Value, model: Value,
        workspace: Option<crate::app_workspace::Workspace>,
        done: impl FnOnce(Result<()>) + Send + 'static,
    ) {
        let store = self.clone();
        let _ = self.jobs.send(Box::new(move || {
            done((|| {
                let mut draft = store.load(&slug)?;
                // A submitted request is immutable until its result is resolved.
                if draft.pending.is_none() {
                    draft.snapshot = snapshot;
                    draft.model = model;
                    if let Some(workspace) = workspace { draft.workspace = workspace; }
                    store.write(&slug, &mut draft)?;
                }
                Ok(())
            })());
        }));
    }
    pub fn prepare(
        &self,
        slug: String,
        snapshot: Value,
        model: Value,
        body: Value,
    ) -> Result<Value> {
        let store = self.clone();
        self.sync(move || {
            let mut draft = store.load(&slug)?;
            if let Some(pending) = draft.pending {
                return Ok(pending);
            }
            draft.snapshot = snapshot;
            draft.model = model;
            draft.pending = Some(body.clone());
            store.write(&slug, &mut draft)?;
            Ok(body)
        })
    }
    pub fn complete(&self, slug: String, key: Value, mut snapshot: Value) -> Result<()> {
        let store = self.clone();
        self.sync(move || {
            let mut draft = store.load(&slug)?;
            ensure!(
                draft.pending.as_ref().map(|v| &v["request_key"]) == Some(&key),
                "Pending submission changed"
            );
            draft.pending = None;
            draft.workspace.accepted(&draft.snapshot, &snapshot, &draft.model);
            draft.workspace.restore_local(&mut snapshot);
            draft.model = snapshot["messages"][2]["updateDataModel"]["value"].clone();
            draft.snapshot = snapshot;
            let old_files = draft.files.clone();
            draft.workspace.remember(&draft.snapshot, &draft.model);
            draft.files.retain(|id, _| contains(&draft.model, id) || draft.workspace.records.values().any(|r| contains(&r.value,id)));
            store.write(&slug, &mut draft)?;
            for (id, file) in old_files {
                if !draft.files.contains_key(&id) {
                    if let Some(path) = file["path"].as_str() {
                        let _ = fs::remove_file(path);
                    }
                }
            }
            Ok(())
        })
    }
    pub fn stage(
        &self,
        slug: String,
        source: PathBuf,
        name: String,
        mime: String,
        binding: String,
        names: String,
    ) -> Result<Value> {
        let store = self.clone();
        self.sync(move || {
            let key = uuid::Uuid::new_v4().to_string();
            let path = store.blobs.join(&key);
            let mut bytes = Vec::new(); fs::File::open(source)?.take(5*1024*1024+1).read_to_end(&mut bytes)?;
            ensure!(!bytes.is_empty() && bytes.len() <= 5*1024*1024, "Choose a file up to 5 MB");
            ensure!(matches!(mime.as_str(), "image/png" | "image/jpeg" | "application/pdf" | "text/plain"), "Choose a PNG, JPEG, PDF or text file");
            let suffix=name.rsplit('.').next().unwrap_or("").to_lowercase();
            ensure!(!name.contains(['/', '\\']) && !name.chars().any(char::is_control) && name.chars().count()<=180, "Invalid attachment name");
            let valid=match mime.as_str() {
                "image/png" => suffix=="png" && bytes.starts_with(b"\x89PNG\r\n\x1a\n"),
                "image/jpeg" => matches!(suffix.as_str(),"jpg"|"jpeg") && bytes.starts_with(b"\xff\xd8\xff"),
                "application/pdf" => suffix=="pdf" && bytes.starts_with(b"%PDF-"),
                "text/plain" => suffix=="txt" && std::str::from_utf8(&bytes).is_ok() && !bytes.contains(&0),
                _ => false,
            };
            ensure!(valid,"Choose a PNG, JPEG, PDF or UTF-8 .txt file with matching content");
            let record = json!({"id":format!("local:{key}"), "request_key":key,"path":path,"name":name,"mime":mime});
            atomic(&path, &bytes)?;
            let mut draft = store.load(&slug)?;
            attach(&mut draft, &record, &binding, &names)?;
            store.write(&slug, &mut draft)?;
            Ok(record)
        })
    }
    #[cfg(any(target_os = "android", target_os = "ios", test))]
    pub fn prepare_camera(&self, slug: String, binding: String, names: String) -> Result<PathBuf> {
        let store = self.clone();
        self.sync(move || {
            let key = uuid::Uuid::new_v4().to_string();
            let path = store.blobs.join(format!("{key}.jpg"));
            atomic(&path, &[])?;
            let mut draft = store.load(&slug)?;
            if let Some(previous)=&draft.capture {
                let previous=PathBuf::from(previous["path"].as_str().unwrap_or(""));
                ensure!(!previous.with_extension("ready").exists(),"Reopen this app to recover the confirmed photo first");
                // Explicitly taking another photo abandons an unfinished capture
                // whose Activity result was lost (e.g. force-stop in the camera).
                let _=fs::remove_file(&previous);
                let _=fs::remove_file(format!("{}.tmp",previous.display()));
            }
            draft.capture = Some(json!({"id":format!("local:{key}"),"request_key":key,"path":path,"name":format!("Photo-{}.jpg", &key[..8]),"mime":"image/jpeg","binding":binding,"names":names}));
            store.write(&slug, &mut draft)?;
            Ok(path)
        })
    }
    // Native hosts create the ready marker only after explicit photo confirmation.
    // If the OS kills our process after capture, opening the app recovers it here.
    pub fn recover_camera(&self, slug: String) -> Result<Draft> {
        let store = self.clone();
        self.sync(move || {
            let mut draft = store.load(&slug)?;
            if let Some(record) = draft.capture.clone() {
                let path = PathBuf::from(record["path"].as_str().unwrap_or(""));
                if path.with_extension("ready").exists() {
                    attach(
                        &mut draft,
                        &record,
                        record["binding"].as_str().unwrap_or(""),
                        record["names"].as_str().unwrap_or(""),
                    )?;
                    draft.capture = None;
                    store.write(&slug, &mut draft)?;
                    let _ = fs::remove_file(path.with_extension("ready"));
                } else if !path.exists() || fs::metadata(&path)?.len() == 0 {
                    // Empty/cancelled capture. A late successful result remains on disk
                    // while the live picker is open, so recovery runs only on open/result.
                    draft.capture = None;
                    store.write(&slug, &mut draft)?;
                    let _ = fs::remove_file(path);
                }
            }
            Ok(draft)
        })
    }
    pub fn send(&self, api: &Api, slug: &str, mut body: Value) -> Result<(Value, Value)> {
        // Resolve staged IDs before the first business request; each upload has a
        // persistent independent key, including after lost responses/process death.
        let draft = self.load(slug)?;
        for (id, file) in &draft.files {
            if !contains(&body, id) {
                continue;
            }
            let record = if let Some(record) = file.get("uploaded") {
                record.clone()
            } else {
                use base64::Engine;
                let bytes = fs::read(file["path"].as_str().unwrap_or(""))?;
                let record = api.post(&format!("/api/apps/{slug}/files"), &json!({"name":file["name"],"mime":file["mime"],"request_key":file["request_key"],"data":base64::engine::general_purpose::STANDARD.encode(bytes)}))?;
                let store = self.clone();
                let slug = slug.to_owned();
                let id = id.clone();
                let record2 = record.clone();
                self.sync(move || {
                    let mut draft = store.load(&slug)?;
                    draft
                        .files
                        .get_mut(&id)
                        .ok_or_else(|| anyhow::anyhow!("Missing local file"))?["uploaded"] =
                        record2;
                    store.write(&slug, &mut draft)
                })?;
                record
            };
            replace(&mut body, id, &record["id"]);
        }
        let snapshot = match api.post(&format!("/api/apps/{slug}/ui"), &body) {
            Ok(snapshot) => snapshot,
            Err(error) => {
                if rejected(&error) {
                    let store = self.clone();
                    let slug = slug.to_owned();
                    self.sync(move || {
                        let mut draft = store.load(&slug)?;
                        draft.pending = None;
                        store.write(&slug, &mut draft)
                    })?;
                }
                return Err(error);
            }
        };
        // The journal is acknowledged before the UI reports success.
        self.complete(
            slug.to_owned(),
            body["request_key"].clone(),
            snapshot.clone(),
        )?;
        Ok((snapshot, body))
    }
}
pub fn is_command(snapshot: &Value, body: &Value) -> bool {
    body.get("event").is_some()
        && snapshot
            .get("commands")
            .and_then(Value::as_array)
            .map(|commands| commands.contains(&body["event"]))
            .unwrap_or(true) // Older servers: conservatively journal all events.
}
/// Local attachment handles never enter SQL. Preserve them in the returned model
/// only if the owning form still refers to the same record.
pub fn refresh_body(_snapshot: &Value, body: &Value) -> Value {
    fn strip(value: &mut Value) {
        match value {
            Value::Array(items) => {
                items.retain(|v| !local_id(v.as_str().unwrap_or("")));
                items.iter_mut().for_each(strip);
            }
            Value::Object(items) => items.values_mut().for_each(strip),
            _ => {}
        }
    }
    let mut body = body.clone();
    for key in ["state", "drafts"] {
        if let Some(value) = body.get_mut(key) { strip(value); }
    }
    body
}
fn local_id(id: &str) -> bool {
    id.strip_prefix("local:")
        .is_some_and(|v| uuid::Uuid::parse_str(v).is_ok())
}
pub fn restore_staged(snapshot: &mut Value, local: &Value) {
    fn collect(value: &Value, path: &str, patches: &mut Vec<(String, Value)>) {
        match value {
            Value::Array(items)
                if path.starts_with("/state/")
                    && items.iter().any(|v| local_id(v.as_str().unwrap_or(""))) =>
            {
                patches.push((path.into(), value.clone()))
            }
            Value::Object(items) => {
                for (key, value) in items {
                    let next = format!("{path}/{}", key.replace('~', "~0").replace('/', "~1"));
                    if local_id(key) {
                        patches.push((next, value.clone()));
                    } else {
                        collect(value, &next, patches);
                    }
                }
            }
            _ => {}
        }
    }
    let mut patches = Vec::new();
    collect(local, "", &mut patches);
    let forms = snapshot["forms"].as_array().cloned().unwrap_or_default();
    let model = &mut snapshot["messages"][2]["updateDataModel"]["value"];
    for (path, value) in patches {
        let same_record = forms.iter().all(|form| {
            let prefix = form["path"].as_str().unwrap_or("");
            if !path.starts_with(&format!("{prefix}/")) {
                return true;
            }
            let key = form["key"].as_str().unwrap_or("id");
            local.pointer(prefix).map(|v| &v[key]) == model.pointer(prefix).map(|v| &v[key])
        });
        if !same_record {
            continue;
        }
        if let Some(target) = model.pointer_mut(&path) {
            *target = value;
        } else if let Some((parent, key)) = path.rsplit_once('/') {
            if let Some(target) = model.pointer_mut(parent).and_then(Value::as_object_mut) {
                target.insert(key.replace("~1", "/").replace("~0", "~"), value);
            }
        }
    }
}

fn attach(draft: &mut Draft, record: &Value, binding: &str, names: &str) -> Result<()> {
    ensure!(
        draft.pending.is_none(),
        "Finish the pending submission before changing attachments"
    );
    let ids = draft
        .model
        .pointer_mut(binding)
        .and_then(Value::as_array_mut)
        .ok_or_else(|| anyhow::anyhow!("Attachment binding is unavailable"))?;
    ensure!(ids.len() < 5, "Up to five attachments per report");
    let id = record["id"].as_str().unwrap();
    if !ids.contains(&record["id"]) {
        ids.push(record["id"].clone());
    }
    if let Some(target) = draft.model.pointer_mut(names) {
        if target.is_null() {
            *target = json!({});
        }
        target[id] = record["name"].clone();
    }
    draft.files.insert(id.to_owned(), record.clone());
    Ok(())
}
pub fn contains(value: &Value, id: &str) -> bool {
    match value {
        Value::String(v) => v == id,
        Value::Array(a) => a.iter().any(|v| contains(v, id)),
        Value::Object(o) => o.values().any(|v| contains(v, id)),
        _ => false,
    }
}
pub fn has_local(value: &Value) -> bool {
    match value {
        Value::String(v) => v.starts_with("local:"),
        Value::Array(a) => a.iter().any(has_local),
        Value::Object(o) => o.values().any(has_local),
        _ => false,
    }
}
fn replace(value: &mut Value, id: &str, replacement: &Value) {
    match value {
        Value::String(v) if v == id => *value = replacement.clone(),
        Value::Array(a) => a.iter_mut().for_each(|v| replace(v, id, replacement)),
        Value::Object(o) => o.values_mut().for_each(|v| replace(v, id, replacement)),
        _ => {}
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn store() -> (tempfile::TempDir, Store) {
        let dir = tempfile::tempdir().unwrap();
        let store = Store::at(dir.path().join("drafts"), dir.path().join("files"));
        (dir, store)
    }
    fn saved(store: &Store, model: Value) {
        let (tx, rx) = mpsc::channel();
        store.save("test".into(), json!({"version":1}), model, move |r| {
            tx.send(r).unwrap();
        });
        rx.recv().unwrap().unwrap();
    }
    #[test]
    fn app_updates_keep_recovered_photos_and_unjournaled_field_edits() {
        let snapshot=json!({"version":1,"forms":[{"path":"/state/form","key":"id"}],"messages":[{},{},{"updateDataModel":{"value":{"state":{"form":{"id":"A","notes":"","attachments":[]}}}}}]});
        let mut draft=Draft{model:crate::app_workspace::model(&snapshot).clone(),snapshot,..Default::default()};
        assert!(draft.follows_latest());
        draft.model["state"]["form"]["notes"]=json!("unsaved");assert!(!draft.follows_latest());
        draft.model["state"]["form"]["notes"]=json!("");
        draft.model["state"]["form"]["attachments"]=json!(["local:photo"]);assert!(!draft.follows_latest());
        draft.model["state"]["form"]["attachments"]=json!([]);
        draft.capture=Some(json!({"path":"pending-photo"}));assert!(!draft.follows_latest());
        draft.capture=None;draft.pending=Some(json!({"request_key":"in-flight"}));assert!(!draft.follows_latest());
    }
    #[test]
    fn survives_restart_and_freezes_pending_request() {
        let (_dir, s) = store();
        saved(&s, json!({"state":{"notes":"offline"}}));
        let body = json!({"event":"submit","state":{"notes":"offline"},"request_key":"one"});
        assert_eq!(
            s.prepare("test".into(), json!({}), json!({}), body.clone())
                .unwrap(),
            body
        );
        saved(&s, json!({"state":{"notes":"changed"}}));
        let reopened = Store::at(s.root.clone(), s.blobs.clone());
        assert_eq!(reopened.load("test").unwrap().pending, Some(body.clone()));
        assert_eq!(
            reopened
                .prepare(
                    "test".into(),
                    json!({}),
                    json!({}),
                    json!({"request_key":"two"})
                )
                .unwrap(),
            body
        );
        assert!(
            reopened
                .complete("test".into(), json!("wrong"), json!({}))
                .is_err()
        );
        reopened.complete("test".into(),json!("one"),json!({"version":1,"messages":[{},{},{"updateDataModel":{"value":{"state":{"notes":""}}}}]})).unwrap();
        assert!(reopened.load("test").unwrap().pending.is_none());
    }
    #[test]
    fn stages_durable_bytes_and_resolves_nested_references() {
        let (dir, s) = store();
        saved(&s, json!({"state":{"files":[]},"names":{}}));
        let source = dir.path().join("note.txt");
        fs::write(&source, b"test evidence").unwrap();
        let file = s
            .stage(
                "test".into(),
                source.clone(),
                "note.txt".into(),
                "text/plain".into(),
                "/state/files".into(),
                "/names".into(),
            )
            .unwrap();
        fs::remove_file(source).unwrap();
        let draft = s.load("test").unwrap();
        assert_eq!(
            fs::read(file["path"].as_str().unwrap()).unwrap(),
            b"test evidence"
        );
        assert_eq!(draft.model["state"]["files"][0], file["id"]);
        assert!(has_local(&draft.model["state"]));
        let mut body = json!({"state":draft.model["state"],"context":{"id":file["id"]}});
        replace(&mut body, file["id"].as_str().unwrap(), &json!("server-id"));
        assert!(!has_local(&body));
        assert_eq!(body["context"]["id"], "server-id");
        assert_eq!(s.load("other").unwrap().files.len(), 0);
        assert!(s.load("../escape").is_err());
    }
    #[test]
    fn submitting_a_keeps_b_draft_and_photo_through_restart() {
        let (dir,s)=store();
        fn snapshot(id:&str)->Value {json!({"version":1,"forms":[{"path":"/state/form","source":"/record","key":"id"}],"messages":[{},{},{"updateDataModel":{"value":{"state":{"form":{"id":id,"notes":"","files":[]}},"record":{"id":id,"notes":"","files":[]},"names":{}}}}]})}
        fn save_page(s:&Store,page:Value,data:Value) {
            let(tx,rx)=mpsc::channel();s.save("test".into(),page,data,move|r|tx.send(r).unwrap());rx.recv().unwrap().unwrap();
        }
        let a=snapshot("A");save_page(&s,a.clone(),crate::app_workspace::model(&a).clone());
        let file=dir.path().join("a.txt");fs::write(&file,b"A photo stand-in").unwrap();
        let fa=s.stage("test".into(),file,"a.txt".into(),"text/plain".into(),"/state/form/files".into(),"/names".into()).unwrap();
        let a_draft=s.load("test").unwrap();let a_visit=a_draft.workspace.current(&a,&a_draft.model);
        let b=snapshot("B");save_page(&s,b.clone(),crate::app_workspace::model(&b).clone());
        let file=dir.path().join("b.txt");fs::write(&file,b"B photo stand-in").unwrap();
        let fb=s.stage("test".into(),file,"b.txt".into(),"text/plain".into(),"/state/form/files".into(),"/names".into()).unwrap();
        let reboot=Store::at(s.root.clone(),s.blobs.clone());let saved=reboot.load("test").unwrap();
        let restored=saved.workspace.restore(&a_visit);
        assert_eq!(crate::app_workspace::model(&restored)["state"]["form"]["files"][0],fa["id"]);
        save_page(&reboot,restored.clone(),crate::app_workspace::model(&restored).clone());
        reboot.prepare("test".into(),restored.clone(),crate::app_workspace::model(&restored).clone(),json!({"request_key":"save-a"})).unwrap();
        let mut success=a;success["resets"]=json!(["/state/form"]);
        reboot.complete("test".into(),json!("save-a"),success).unwrap();
        assert!(Path::new(fb["path"].as_str().unwrap()).exists());
        assert!(!Path::new(fa["path"].as_str().unwrap()).exists());
        assert_eq!(reboot.load("test").unwrap().workspace.dirty().len(),1);
        let body=refresh_body(&restored,&json!({"drafts":saved.workspace.hints(&json!(1))}));
        assert!(!has_local(&body));
    }
    #[test]
    fn cached_app_is_not_an_unfinished_draft() {
        let (_dir,s)=store();
        let page=json!({"version":1,"forms":[{"path":"/state/form","source":"/record","key":"id"}],"messages":[{},{},{"updateDataModel":{"value":{"state":{"form":{"id":"A","notes":""}},"record":{"id":"A","notes":""}}}}]});
        let mut data=crate::app_workspace::model(&page).clone();
        let save=|data:Value|{let(tx,rx)=mpsc::channel();s.save("test".into(),page.clone(),data,move|r|tx.send(r).unwrap());rx.recv().unwrap().unwrap();};
        save(data.clone());
        let mut index=json!({"apps":[{"slug":"test"}]});
        s.annotate_index(&mut index);
        assert_eq!(index["apps"][0]["cached"],true);
        assert!(index["apps"][0]["draft_status"].is_null());
        data["state"]["form"]["notes"]=json!("Unsent field notes");save(data);
        s.annotate_index(&mut index);
        assert_eq!(index["apps"][0]["draft_count"],1);
        assert_eq!(index["apps"][0]["draft_status"],"1 unfinished draft");
    }
    #[test]
    fn corrupt_draft_is_not_silently_replaced() {
        let (_dir, s) = store();
        atomic(&s.root.join("apps/test.json"), b"broken").unwrap();
        assert!(s.load("test").is_err());
        assert!(
            s.prepare("test".into(), json!({}), json!({}), json!({}))
                .is_err()
        );
    }
    #[test]
    fn attachments_and_confirmed_captures_survive_sandbox_relocation() {
        let temp = tempfile::tempdir().unwrap();
        let original = temp.path().join("original");
        let moved = temp.path().join("moved");
        let store = Store::at(original.join("account"),original.join("blobs"));
        saved(&store,json!({"state":{"files":[]},"names":{}}));
        let file=temp.path().join("evidence.txt"); fs::write(&file,b"field evidence").unwrap();
        let record=store.stage("test".into(),file,"evidence.txt".into(),"text/plain".into(),"/state/files".into(),"/names".into()).unwrap();
        let camera=store.prepare_camera("test".into(),"/state/files".into(),"/names".into()).unwrap();
        fs::write(&camera,b"confirmed JPEG fixture").unwrap();
        fs::write(camera.with_extension("ready"),b"ready").unwrap();
        fs::rename(&original,&moved).unwrap();
        let reopened=Store::at(moved.join("account"),moved.join("blobs"));
        let draft=reopened.recover_camera("test".into()).unwrap();
        assert_eq!(draft.files.len(),2);
        assert!(draft.capture.is_none());
        let saved=&draft.files[record["id"].as_str().unwrap()];
        assert_eq!(fs::read(saved["path"].as_str().unwrap()).unwrap(),b"field evidence");
        assert!(draft.files.values().all(|v|Path::new(v["path"].as_str().unwrap()).starts_with(&moved)));
    }

    #[test]
    fn camera_confirmation_survives_process_recreation() {
        let (_dir, s) = store();
        saved(&s, json!({"state":{"files":[]},"names":{}}));
        let path = s
            .prepare_camera("test".into(), "/state/files".into(), "/names".into())
            .unwrap();
        fs::write(&path, b"confirmed JPEG stand-in").unwrap();
        fs::write(path.with_extension("ready"), b"1").unwrap();
        let reopened = Store::at(s.root.clone(), s.blobs.clone());
        let draft = reopened.recover_camera("test".into()).unwrap();
        assert!(draft.capture.is_none());
        assert_eq!(draft.model["state"]["files"].as_array().unwrap().len(), 1);
        assert_eq!(
            reopened.recover_camera("test".into()).unwrap().files.len(),
            1
        );
        assert!(!path.with_extension("ready").exists());
    }
    #[test]
    fn lost_response_replays_same_action_without_reuploading() {
        use std::{
            io::{BufRead, BufReader},
            net::TcpListener,
        };
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let mut requests = Vec::new();
            for index in 0..3 {
                let (mut stream, _) = listener.accept().unwrap();
                stream
                    .set_read_timeout(Some(std::time::Duration::from_secs(5)))
                    .unwrap();
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut header = String::new();
                reader.read_line(&mut header).unwrap();
                let first = header.clone();
                let mut length = 0;
                loop {
                    header.clear();
                    reader.read_line(&mut header).unwrap();
                    if header == "\r\n" {
                        break;
                    }
                    if let Some(v) = header.to_lowercase().strip_prefix("content-length:") {
                        length = v.trim().parse::<usize>().unwrap();
                    }
                }
                let mut bytes = vec![0; length];
                reader.read_exact(&mut bytes).unwrap();
                requests.push((first, serde_json::from_slice::<Value>(&bytes).unwrap()));
                if index == 1 {
                    continue;
                } // server committed, but the socket died before a response
                let response = if index == 0 {
                    json!({"id":"file-server-id","name":"note.txt","mime":"text/plain"})
                } else {
                    json!({"version":1,"receipt":{"id":"one-commit"},"messages":[{},{},{"updateDataModel":{"value":{"state":{"files":[]}}}}]})
                };
                let bytes = serde_json::to_vec(&response).unwrap();
                write!(stream,"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: {}\r\n\r\n",bytes.len()).unwrap();
                stream.write_all(&bytes).unwrap();
            }
            requests
        });
        let (dir, s) = store();
        saved(&s, json!({"state":{"files":[]},"names":{}}));
        let source = dir.path().join("note.txt");
        fs::write(&source, b"field evidence").unwrap();
        let file = s
            .stage(
                "test".into(),
                source,
                "note.txt".into(),
                "text/plain".into(),
                "/state/files".into(),
                "/names".into(),
            )
            .unwrap();
        let model = s.load("test").unwrap().model;
        let body = json!({"version":1,"state":model["state"],"event":"submit","request_key":uuid::Uuid::new_v4().to_string()});
        s.prepare("test".into(), json!({"version":1}), model, body.clone())
            .unwrap();
        let api = Api::new(Connection {
            base_url: format!("http://{address}"),
            ..Default::default()
        })
        .unwrap();
        assert!(s.send(&api, "test", body.clone()).is_err());
        let reopened = Store::at(s.root.clone(), s.blobs.clone());
        assert_eq!(reopened.load("test").unwrap().pending, Some(body.clone()));
        reopened.send(&api, "test", body).unwrap();
        let requests = server.join().unwrap();
        assert!(requests[0].0.contains("/files"));
        assert_eq!(requests[1].1, requests[2].1);
        assert_eq!(requests[1].1["state"]["files"][0], "file-server-id");
        assert!(!has_local(&requests[1].1));
        assert!(reopened.load("test").unwrap().pending.is_none());
        assert!(!Path::new(file["path"].as_str().unwrap()).exists());
    }
    #[test]
    fn external_uncertainty_must_not_unlock_the_request() {
        for code in [
            "EXTERNAL_OUTCOME_UNKNOWN",
            "ACTION_UNCERTAIN",
            "INVALID_APP_INPUT",
        ] {
            assert!(!rejected(
                &HttpError {
                    status: 409,
                    code: code.into(),
                    message: "unknown".into()
                }
                .into()
            ));
        }
        assert!(rejected(
            &HttpError {
                status: 400,
                code: "ACTION_FAILED".into(),
                message: "rolled back".into()
            }
            .into()
        ));
        let mut a = Connection::default();
        a.email = "a@example.test".into();
        let mut b = a.clone();
        b.email = "b@example.test".into();
        assert_ne!(account_root(&a), account_root(&b));
        b = a.clone();
        b.base_url = "https://other.example.test".into();
        assert_ne!(account_root(&a), account_root(&b));
    }

    #[test]
    fn live_refresh_keeps_staged_files_on_the_same_record_only() {
        let mut snapshot = json!({"forms":[{"path":"/state/form","key":"id"}],"messages":[{},{"updateComponents":{"components":[{"component":"CAttachments","value":{"path":"/state/form/files"},"names":{"path":"/names"}}]}},{"updateDataModel":{"value":{"state":{"form":{"id":"job-a","files":[]}},"names":{}}}}]});
        let local = json!({"state":{"form":{"id":"job-a","files":["local:00000000-0000-4000-8000-000000000001","server-two"]}},"names":{"local:00000000-0000-4000-8000-000000000001":"Photo.jpg"}});
        let request = refresh_body(&snapshot, &json!({"state":local["state"]}));
        assert_eq!(request["state"]["form"]["files"], json!(["server-two"]));
        restore_staged(&mut snapshot, &local);
        assert_eq!(
            snapshot["messages"][2]["updateDataModel"]["value"]["state"]["form"]["files"],
            local["state"]["form"]["files"]
        );
        snapshot["messages"][2]["updateDataModel"]["value"]["state"]["form"] =
            json!({"id":"job-b","files":[]});
        restore_staged(&mut snapshot, &local);
        assert_eq!(
            snapshot["messages"][2]["updateDataModel"]["value"]["state"]["form"]["files"],
            json!([])
        );
    }
    #[test]
    fn first_open_does_not_invent_an_empty_state_payload() {
        assert_eq!(refresh_body(&json!({}), &json!({})), json!({}));
        assert!(!is_command(
            &json!({"commands":["save"]}),
            &json!({"event":"navigate"})
        ));
        assert!(is_command(
            &json!({"commands":["save"]}),
            &json!({"event":"save"})
        ));
    }
    #[test]
    fn unfinished_camera_capture_does_not_block_a_new_photo() {
        let (_dir, s) = store();
        saved(&s, json!({"state":{"files":[]},"names":{}}));
        let old = s
            .prepare_camera("test".into(), "/state/files".into(), "/names".into())
            .unwrap();
        fs::write(&old, b"unconfirmed camera output").unwrap();
        let next = s
            .prepare_camera("test".into(), "/state/files".into(), "/names".into())
            .unwrap();
        assert_ne!(old, next);
        assert!(!old.exists());
        assert!(next.exists());
        assert!(s.load("test").unwrap().files.is_empty());
    }
}
