//! Local A2UI experiment. The server owns the definition and SQL business rules;
//! this host owns transport, draft protection, appearance and renderer lifecycle.
use crate::mailbox::Sender;
use crate::{
    model::{data_dir, private_write},
    theme::CanvasTheme,
};
use a2ui_base::{catalog::Catalog, protocol::server_to_client::A2uiMessage};
use a2ui_gpui::GpuiApp;
use anyhow::{Context as _, Result, bail};
use gpui::{prelude::*, *};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    process::{Command, Stdio},
    sync::mpsc::Receiver,
    time::{Duration, Instant},
};

pub const URL: &str = "http://127.0.0.1:18455";
pub(crate) fn service_url() -> String {
    std::env::var("CALLIOPE_BUSINESS_URL")
        .ok()
        .filter(|s| {
            s.starts_with("http://127.0.0.1:") && s[17..].bytes().all(|b| b.is_ascii_digit())
        })
        .unwrap_or_else(|| URL.into())
}
const CATALOG: &str = "https://calliope.local/a2ui/workspace/v1";
enum Update {
    DraftSaveFailed(String),
    Reactive(String, u64, Result<Value>),
    Loaded(Result<Value>, String, bool, u64),
    Chosen(Result<Option<Vec<std::path::PathBuf>>>, String),
    Uploaded(Result<Value>, String),
    Revision(Result<u64>, u64),
    Sent(String, Result<Value>),
}

pub struct BusinessDesk {
    pending_case: Option<String>,
    reactive: crate::business_reactive::Reactive,
    reactive_clock: Instant,
    filters: crate::business_filters::Filters,
    filter_revision: u64,
    filter_flight: Option<(u64, Value)>,
    outbox: Vec<Value>,
    outbox_error: Option<String>,
    sending: bool,
    last_send: Instant,
    delete_report: Option<String>,
    probe: Option<Receiver<crate::business_probe::Request>>,
    probe_pending: Vec<crate::business_probe::Request>,
    preview_events: Vec<Value>,
    entry: Value,
    drafts: std::collections::BTreeMap<String, Value>,
    saved_drafts: String,
    draft_writer: crate::deferred_save::DeferredSave<std::collections::BTreeMap<String, Value>>,
    observed_revision: u64,
    persisted_revision: u64,
    generation: u64,
    renderer: Entity<GpuiApp>,
    snapshot: Value,
    busy: bool,
    polling: bool,
    last_poll: Instant,
    dirty: bool,
    notice: String,
    error: bool,
    tx: Sender<Update>,
    rx: Receiver<Update>,
    theme: CanvasTheme,
    theme_key: String,
    frame_probe: FrameProbe,
}

#[derive(Default)]
struct FrameProbe {
    remaining: usize,
    last: Option<Instant>,
    intervals_ms: Vec<f64>,
}
impl FrameProbe {
    fn sample(&mut self, window: &Window) {
        if self.remaining == 0 {
            return;
        }
        let now = Instant::now();
        if let Some(last) = self.last.replace(now) {
            self.intervals_ms
                .push(now.duration_since(last).as_secs_f64() * 1000.);
            self.remaining -= 1;
        }
        if self.remaining > 0 {
            window.request_animation_frame();
        }
    }
    fn status(&self) -> Value {
        let mut samples = self.intervals_ms.clone();
        samples.sort_by(f64::total_cmp);
        let percentile = |p: f64| {
            samples
                .get(((samples.len().saturating_sub(1)) as f64 * p).ceil() as usize)
                .copied()
        };
        json!({"remaining":self.remaining,"samples":samples.len(),"median_ms":percentile(0.5),"p95_ms":percentile(0.95),"max_ms":samples.last(),"metric":"forced-redraw frame intervals, including presentation pacing"})
    }
}

fn client() -> Result<reqwest::blocking::Client> {
    Ok(reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(8))
        .redirect(reqwest::redirect::Policy::none())
        .build()?)
}
#[derive(Debug)]
struct Rejection {
    status: u16,
    message: String,
    fields: Value,
}
impl std::fmt::Display for Rejection {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}
impl std::error::Error for Rejection {}
fn response(response: reqwest::blocking::Response) -> Result<Value> {
    let status = response.status();
    let value: Value = response.json()?;
    if !status.is_success() {
        return Err(Rejection {
            status: status.as_u16(),
            message: value["error"]
                .as_str()
                .unwrap_or("Local service failed")
                .into(),
            fields: value["fields"].clone(),
        }
        .into());
    }
    Ok(value)
}
pub(crate) fn ensure_server() -> Result<()> {
    let http = client()?;
    if let Ok(r) = http.get(format!("{}/api/health", service_url())).send() {
        let v = response(r)?;
        if v["application"] != "calliope-transaction-desk" {
            bail!("Port 18455 belongs to another service.");
        }
        return Ok(());
    }
    if std::env::var_os("CALLIOPE_BUSINESS_URL").is_some() {
        bail!("Isolated business service is unavailable");
    }
    let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("business/server.py");
    std::fs::create_dir_all(data_dir())?;
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(data_dir().join("business-server.log"))?;
    let mut child = Command::new("python3")
        .arg(script)
        .arg("--db")
        .arg(data_dir().join("business-demo.sqlite"))
        .stdin(Stdio::null())
        .stdout(log.try_clone()?)
        .stderr(log)
        .spawn()
        .context("Start local transaction service")?;
    std::thread::spawn(move || {
        let _ = child.wait();
    });
    for _ in 0..30 {
        std::thread::sleep(Duration::from_millis(100));
        if let Ok(r) = http.get(format!("{}/api/health", service_url())).send() {
            if response(r)?["application"] == "calliope-transaction-desk" {
                return Ok(());
            }
        }
    }
    bail!(
        "Local transaction service did not start. See business-server.log in the app data directory."
    )
}

impl BusinessDesk {
    pub fn flush_saves(&mut self, cx: &mut Context<Self>) {
        self.persist(cx);
        self.draft_writer.flush();
    }
    pub fn new(theme: CanvasTheme, cx: &mut Context<Self>) -> Self {
        let (tx, rx, wake) = crate::mailbox::channel();
        let save_tx = tx.clone();
        let draft_writer = crate::deferred_save::DeferredSave::new(move |drafts| {
            if let Err(error) = crate::business_drafts::save(&drafts) {
                eprintln!("Could not save local drafts: {error}");
                let _ = save_tx.send(Update::DraftSaveFailed(error.to_string()));
            }
        });
        let renderer = cx.new(|_| {
            let mut app = GpuiApp::new(vec![Catalog::new(CATALOG)], HashMap::new());
            app.chart_renderer = Some(crate::server_charts::themed(std::sync::Arc::new(crate::charts::render), theme.vega_config()));
            app.wake_host = Some(tx.notifier());
            app
        });
        let mut this = Self {
            pending_case: None,
            reactive: Default::default(),
            reactive_clock: Instant::now(),
            filters: Default::default(),
            filter_revision: u64::MAX,
            filter_flight: None,
            outbox: crate::business_outbox::load().unwrap_or_default(),
            outbox_error: crate::business_outbox::load().err().map(|e| e.to_string()),
            sending: false,
            last_send: Instant::now() - Duration::from_secs(30),
            delete_report: None,
            probe: crate::business_probe::start(tx.notifier()),
            probe_pending: Vec::new(),
            preview_events: Vec::new(),
            entry: Value::Null,
            drafts: crate::business_drafts::load(),
            saved_drafts: String::new(),
            draft_writer,
            observed_revision: u64::MAX,
            persisted_revision: u64::MAX,
            generation: 0,
            renderer,
            snapshot: Value::Null,
            busy: false,
            polling: false,
            last_poll: Instant::now(),
            dirty: false,
            notice: "Opening the transaction desk…".into(),
            error: false,
            tx,
            rx,
            theme: theme.clone(),
            theme_key: String::new(),
            frame_probe: FrameProbe::default(),
        };
        cx.on_release(|this, cx| {
            let draft = this.renderer.read(cx).data_value("/draft");
            this.persist_draft(draft);
        })
        .detach();
        this.set_theme(theme, cx);
        this.request(json!({"name":"refresh","context":{}}), cx);
        cx.spawn(async move |view, cx| {
            loop {
                smol::future::or(
                    async {
                        let _ = wake.recv().await;
                    },
                    async {
                        smol::Timer::after(Duration::from_millis(120)).await;
                    },
                )
                .await;
                if view.update(cx, |this, cx| this.tick(cx)).is_err() {
                    break;
                }
            }
        })
        .detach();
        this
    }

    pub fn set_theme(&mut self, theme: CanvasTheme, cx: &mut Context<Self>) {
        let css = |c: Rgba| {
            format!(
                "rgba({},{},{},{})",
                (c.r * 255.) as u8,
                (c.g * 255.) as u8,
                (c.b * 255.) as u8,
                c.a
            )
        };
        let value = json!({"name":theme.name,"dark":theme.dark,"font":theme.font_body.to_string(),
            "bg":css(theme.bg),"panel":css(theme.panel),"card":css(theme.card),"text":css(theme.text),
            "muted":css(theme.muted),"accent":css(theme.accent),"on_accent":css(theme.on_accent),"line":css(theme.line),
            "warm":css(theme.warning),"cool":css(theme.info),"success":css(theme.success),
            "font_display":theme.font_display.to_string(),"font_mono":theme.font_mono.to_string()});
        let key = value.to_string();
        if key != self.theme_key {
            if let Err(e) = private_write(&data_dir().join("business-theme.json"), key.as_bytes()) {
                eprintln!("Business theme export: {e}");
            }
            self.theme_key = key;
            let charts = crate::server_charts::themed(std::sync::Arc::new(crate::charts::render), theme.vega_config());
            self.renderer.update(cx, |r, cx| {
                r.set_chart_renderer(Some(charts));
                cx.notify();
            });
        }
        self.theme = theme;
    }

    fn persist(&mut self, cx: &mut Context<Self>) {
        self.persist_draft(self.renderer.read(cx).data_value("/draft"));
    }
    fn persist_draft(&mut self, draft: Value) {
        if self.snapshot.is_null() {
            return;
        }
        self.entry["local"] = draft;
        self.dirty = self.entry["local"] != self.entry["base"];
        let key = crate::business_drafts::key(&self.snapshot);
        if self.dirty || self.conflicts() > 0 {
            self.drafts.insert(key, self.entry.clone());
        } else {
            self.drafts.remove(&key);
        }
        let encoded = serde_json::to_string(&self.drafts).unwrap_or_default();
        if encoded != self.saved_drafts {
            self.draft_writer.submit(self.drafts.clone());
            self.saved_drafts = encoded;
        }
    }
    fn conflicts(&self) -> usize {
        self.entry["conflicts"].as_object().map_or(0, |v| v.len())
    }
    fn install(&mut self, value: Value, mode: &str, cx: &mut Context<Self>) -> Result<()> {
        self.persist(cx);
        let key = crate::business_drafts::key(&value);
        let same = !self.snapshot.is_null() && key == crate::business_drafts::key(&self.snapshot);
        if mode == "discard" && same && value["view"] == "field" {
            if let Some(ids) = self.entry["local"]["attachments"].as_array() {
                for id in ids {
                    if let Some(id) = id.as_str() {
                        let _ = crate::business_outbox::remove_file(id);
                    }
                }
            }
        }
        if matches!(mode, "ack" | "discard") {
            self.drafts.remove(&key);
        }
        self.entry = if let Some(cached) = self.drafts.get(&key) {
            let cached = crate::business_drafts::acknowledge(
                cached,
                &value["draft"],
                &value["acknowledgedDraftPaths"],
            );
            crate::business_drafts::reconcile(
                &cached["base"],
                &cached["local"],
                &value["draft"],
                &cached["conflicts"],
            )
        } else {
            json!({"base":value["draft"],"local":value["draft"],"conflicts":{}})
        };
        self.reactive.install(&value);
        let mut messages = value["messages"].clone();
        for message in messages.as_array_mut().context("Messages missing")? {
            if value["reactive"].as_array().is_some_and(|a| !a.is_empty()) {
                if let Some(update) = message.get_mut("updateComponents") {
                    update["components"] = self.reactive.components(&value["baseComponents"]);
                }
                if let Some(data) = message.pointer_mut("/updateDataModel/value") {
                    data["resources"] = self.reactive.states();
                }
            }
            if let Some(data) = message.pointer_mut("/updateDataModel/value") {
                data["draft"] = self.entry["local"].clone();
                data["errors"] = self.conflict_errors(&value);
                if same && mode == "merge" {
                    for name in [
                        "filter",
                        "hvac_filter",
                        "controls",
                        "receiving_filter",
                        "dispatch_filter",
                        "planning",
                        "field_controls",
                        "preferences",
                    ] {
                        let existing = self.renderer.read(cx).data_value(&format!("/{name}"));
                        if !existing.is_null() {
                            data[name] = existing;
                        }
                    }
                }
            }
        }
        let messages: Vec<A2uiMessage> = serde_json::from_value(messages)?;
        self.renderer
            .update(cx, |r, cx| {
                let result = if same {
                    r.reconcile_surface(messages)
                } else {
                    r.load_surface(messages)
                };
                cx.notify();
                result
            })
            .map_err(anyhow::Error::msg)?;
        self.notice = value["notice"]
            .as_str()
            .filter(|s| !s.is_empty())
            .unwrap_or("Live updates · drafts saved on this device")
            .into();
        if value["view"] == "field" {
            let _ = private_write(
                &data_dir().join("field-cache.json"),
                &serde_json::to_vec(&value)?,
            );
            if let Some(ids) = self.entry["local"]["attachments"].as_array() {
                for id in ids {
                    if let Some(id) = id.as_str() {
                        if let Ok(file) = crate::business_outbox::file(id) {
                            self.renderer.update(cx, |r, _| {
                                r.set_data_value(
                                    &format!("/attachment_names/{id}"),
                                    file["name"].clone(),
                                )
                            });
                        }
                    }
                }
            }
        }
        let nodes = value["messages"]
            .as_array()
            .and_then(|messages| {
                messages
                    .iter()
                    .find_map(|m| m.pointer("/updateComponents/components"))
            })
            .cloned()
            .unwrap_or(Value::Null);
        self.filters
            .install(&nodes, &self.renderer.read(cx).data_value("/"));
        self.filter_revision = u64::MAX;
        self.snapshot = value;
        self.error = false;
        self.persist(cx);
        Ok(())
    }
    fn conflict_errors(&self, value: &Value) -> Value {
        let mut errors = serde_json::Map::new();
        if let Some(messages) = value["messages"].as_array() {
            for message in messages {
                if let Some(nodes) = message
                    .pointer("/updateComponents/components")
                    .and_then(Value::as_array)
                {
                    for node in nodes {
                        if let Some(path) = node["value"]["path"]
                            .as_str()
                            .and_then(|p| p.strip_prefix("/draft"))
                        {
                            if self.entry["conflicts"]["/version"]["recordChanged"] == true {
                                errors.insert(node["id"].as_str().unwrap_or("").into(),"Another receipt changed this order. Review remaining quantities and resolve above.".into());
                            } else if let Some(conflict) = self.entry["conflicts"].get(path) {
                                errors.insert(node["id"].as_str().unwrap_or("").into(),format!("Changed elsewhere. Saved: {}. Your edit: {}. Resolve above.",conflict["remote"],conflict["local"]).into());
                            }
                        }
                    }
                }
            }
        }
        Value::Object(errors)
    }
    fn resolve_conflicts(&mut self, server: bool, cx: &mut Context<Self>) {
        self.persist(cx);
        self.entry = crate::business_drafts::resolve(&self.entry, server);
        self.renderer.update(cx, |r, cx| {
            r.set_data_value("/draft", self.entry["local"].clone());
            r.set_data_value("/errors", json!({}));
            cx.notify();
        });
        self.persist(cx);
        self.notice = "Conflicts resolved. Review and save when ready.".into();
        self.error = false;
        cx.notify();
    }
    fn request(&mut self, action: Value, cx: &mut Context<Self>) {
        self.filters.cancel();
        self.filter_flight = None;
        self.dispatch(action, false, cx);
    }
    pub fn open_case(&mut self, id: String, cx: &mut Context<Self>) {
        if self.busy || self.snapshot.is_null() {
            self.pending_case = Some(id);
            return;
        }
        self.request(
            json!({"name":"set_view","context":{"view":"case","id":id}}),
            cx,
        );
    }
    pub fn is_preview(&self) -> bool {
        self.snapshot["view"] == "studio"
    }

    pub fn is_case(&self) -> bool {
        self.snapshot["view"] == "case"
    }
    pub fn case_context(&self, cx: &App) -> Option<String> {
        if !self.is_case() {
            return None;
        }
        let data = self.renderer.read(cx).data_value("/");
        let mut context = data["context"].clone();
        context["revision"] = self.snapshot["revision"].clone();
        context["unsubmittedDraft"] = data["draft"].clone();
        context["semanticSupport"] = if data["resources"]["support"].is_null() {
            json!({"source":"user-preference","label":data["case"]["focus"]})
        } else {
            data["resources"]["support"].clone()
        };
        serde_json::to_string_pretty(&context).ok()
    }
    fn dispatch(&mut self, mut action: Value, background: bool, cx: &mut Context<Self>) {
        if self.busy {
            return;
        }
        if self.reactive.action(
            action["name"].as_str().unwrap_or(""),
            action["context"]["id"].as_str().unwrap_or(""),
        ) {
            self.paint_reactive(cx);
            return;
        }
        // Generated previews are inert. Exercise bindings and record intent, but
        // never turn a generated action name into a business command or OS action.
        if self.snapshot["view"] == "studio" {
            if self.preview_events.len() < 100 {
                self.preview_events.push(action);
            }
            self.notice = "Preview action recorded; no business command executed.".into();
            cx.notify();
            return;
        }
        self.persist(cx);
        let name = action["name"].as_str().unwrap_or("").to_string();
        if name == "case_context" {
            if let Some(context) = self.case_context(cx) {
                cx.write_to_clipboard(ClipboardItem::new_string(context));
                self.notice = "Case context copied for Callie. No command submitted.".into();
            }
            return;
        }
        if matches!(
            name.as_str(),
            "case_save"
                | "case_receipt_preview"
                | "case_receive_confirm"
                | "case_schedule_preview"
                | "case_schedule_confirm"
        ) && self.conflicts() > 0
        {
            self.notice = "Resolve conflicting case fields before reviewing or saving.".into();
            self.error = true;
            return;
        }
        if name == "remove_local_evidence" {
            if let Err(e) =
                crate::business_outbox::remove_file(action["context"]["id"].as_str().unwrap_or(""))
            {
                self.notice = e.to_string();
                self.error = true;
            }
            return;
        }
        if name == "open_attachment" {
            if let Some(id) = action["context"]["attachmentId"]
                .as_str()
                .or_else(|| action["context"]["id"].as_str())
                .filter(|id| {
                    id.starts_with("ATT-")
                        && id.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-')
                })
            {
                cx.open_url(&format!("{}/api/attachments/{id}", service_url()));
            }
            return;
        }
        if name == "choose_attachment" {
            self.choose_attachment(
                action["context"]["path"]
                    .as_str()
                    .unwrap_or("/draft/attachments")
                    .to_string(),
                cx,
            );
            return;
        }
        if crate::business_drafts::consumes(&name) {
            if self.conflicts() > 0 {
                self.notice = "Resolve the changed fields before saving.".into();
                self.error = true;
                return;
            }
            if !self.renderer.update(cx, |r, cx| {
                let ok = r.validate_fields();
                cx.notify();
                ok
            }) {
                self.notice = "Check the highlighted fields before continuing.".into();
                self.error = true;
                return;
            }
        } else if self.dirty
            && !background
            && name != "discard"
            && !crate::business_drafts::navigation(&name)
        {
            self.notice = "Save your draft before changing workflow state.".into();
            self.error = true;
            return;
        }
        if name == "field_queue" {
            if let Some(error) = &self.outbox_error {
                self.notice = format!("Device queue could not load: {error}. Draft retained.");
                self.error = true;
                return;
            }
            let draft = self.renderer.read(cx).data_value("/draft");
            match crate::business_outbox::enqueue(&mut self.outbox, draft) {
                Ok(()) => {
                    self.renderer.update(cx, |r, cx| {
                        r.set_data_value("/draft", self.snapshot["draft"].clone());
                        r.set_data_value("/errors", json!({}));
                        cx.notify();
                    });
                    self.persist(cx);
                    self.last_send = Instant::now() - Duration::from_secs(30);
                    self.notice =
                        "Report and evidence saved on this device. Sending when connected.".into();
                    self.error = false;
                }
                Err(e) => {
                    self.notice = e.to_string();
                    self.error = true;
                }
            }
            cx.notify();
            return;
        }
        let mode = if name == "discard" {
            "discard"
        } else if crate::business_drafts::consumes(&name) {
            "ack"
        } else {
            "merge"
        }
        .to_string();
        action["selected"] = self.snapshot["selected"].as_str().unwrap_or("cedar").into();
        action["owner"] = self.snapshot["owner"].as_str().unwrap_or("Everyone").into();
        action["view"] = self.snapshot["view"].as_str().unwrap_or("dossier").into();
        action["state"] = self.snapshot["state"].clone();
        action["panes"] = self.snapshot["panes"]
            .as_array()
            .cloned()
            .unwrap_or_default()
            .into();
        action["source"] = "Desktop".into();
        action["requestId"] = uuid::Uuid::new_v4().to_string().into();
        self.generation += 1;
        let generation = self.generation;
        if background {
            self.polling = true;
        } else {
            self.polling = false;
            self.busy = true;
            self.notice = "Working…".into();
            self.error = false;
        }
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let result = (|| {
                ensure_server()?;
                response(
                    client()?
                        .post(format!("{}/api/action", service_url()))
                        .json(&action)
                        .send()?,
                )
            })();
            let _ = tx.send(Update::Loaded(result, mode, background, generation));
        });
    }
    fn choose_attachment(&mut self, path: String, cx: &mut Context<Self>) {
        let chooser = cx.prompt_for_paths(PathPromptOptions {
            files: true,
            directories: false,
            multiple: false,
            prompt: Some("Attach delivery evidence".into()),
        });
        self.busy = true;
        self.notice = "Choose a delivery photo or document…".into();
        let tx = self.tx.clone();
        cx.spawn(async move |_, _| {
            let result = match chooser.await {
                Ok(v) => v,
                Err(e) => Err(anyhow::anyhow!(e)),
            };
            let _ = tx.send(Update::Chosen(result, path));
        })
        .detach();
    }
    fn paint_reactive(&mut self, cx: &mut Context<Self>) {
        if self.snapshot["reactive"]
            .as_array()
            .is_none_or(|a| a.is_empty())
        {
            return;
        }
        let components = self.reactive.components(&self.snapshot["baseComponents"]);
        let data = self.renderer.read(cx).data_value("/");
        let mut messages = self.snapshot["messages"].clone();
        for message in messages.as_array_mut().into_iter().flatten() {
            if let Some(update) = message.get_mut("updateComponents") {
                update["components"] = components.clone();
            }
            if let Some(update) = message.get_mut("updateDataModel") {
                update["value"] = data.clone();
                update["value"]["resources"] = self.reactive.states();
            }
        }
        let result = serde_json::from_value::<Vec<A2uiMessage>>(messages)
            .map_err(|e| e.to_string())
            .and_then(|messages| {
                self.renderer.update(cx, |r, cx| {
                    let result = r.reconcile_surface(messages);
                    cx.notify();
                    result
                })
            });
        if let Err(e) = result {
            self.error = true;
            self.notice = format!("Reactive update rejected: {e}");
        }
        cx.notify();
    }
    fn tick_reactive(&mut self, cx: &mut Context<Self>) {
        if self.busy {
            return;
        }
        let now = self.reactive_clock.elapsed().as_millis() as u64;
        let revision = self.renderer.read(cx).data_revision();
        let changed = if revision != self.observed_revision {
            self.observed_revision = revision;
            self.reactive
                .observe(&self.renderer.read(cx).data_value("/"), now)
        } else {
            false
        };
        let requests = self.reactive.poll(now);
        if self.reactive.pending(now) || changed || !requests.is_empty() {
            self.paint_reactive(cx);
        }
        for request in requests {
            let tx = self.tx.clone();
            std::thread::spawn(move || {
                let result = (|| {
                    response(
                        client()?
                            .post(format!("{}/api/reactive", service_url()))
                            .timeout(Duration::from_secs(12))
                            .json(&request.body)
                            .send()?,
                    )
                })();
                let _ = tx.send(Update::Reactive(request.id, request.ticket, result));
            });
        }
    }
    fn tick_filters(&mut self, cx: &mut Context<Self>) {
        let now = self.reactive_clock.elapsed().as_millis() as u64;
        let revision = self.renderer.read(cx).data_revision();
        if revision != self.filter_revision {
            self.filter_revision = revision;
            self.filters
                .observe(&self.renderer.read(cx).data_value("/"), now);
        }
        if self.busy || self.polling {
            return;
        }
        if let Some(action) = self.filters.poll(now) {
            let context = action["context"].clone();
            let before = self.generation;
            self.dispatch(action, true, cx);
            if self.generation != before {
                self.filter_flight = Some((self.generation, context));
                self.notice = "Updating results…".into();
                self.error = false;
            }
            cx.notify();
        }
    }
    fn tick(&mut self, cx: &mut Context<Self>) {
        self.tick_filters(cx);
        if !self.busy && !self.polling && !self.snapshot.is_null() {
            if let Some(id) = self.pending_case.take() {
                self.open_case(id, cx);
            }
        }
        if let Some(rx) = &self.probe {
            self.probe_pending.extend(rx.try_iter());
        }
        if !self.probe_pending.is_empty() {
            cx.notify();
        }
        self.tick_reactive(cx);
        let was_dirty = self.dirty;
        let revision = self.renderer.read(cx).data_revision();
        if revision != self.persisted_revision {
            self.persisted_revision = revision;
            self.persist(cx);
        }
        if self.dirty != was_dirty {
            cx.notify();
        }
        let actions = self.renderer.update(cx, |r, _| r.take_actions());
        for action in actions {
            self.request(action, cx);
            cx.notify();
        }
        while let Ok(update) = self.rx.try_recv() {
            match update {
                Update::DraftSaveFailed(error) => {
                    self.saved_drafts.clear();
                    self.notice = format!("Could not save local drafts: {error}");
                    self.error = true;
                }
                Update::Reactive(id, ticket, result) => {
                    // Observe again at completion, before accepting an older payload.
                    self.reactive.observe(
                        &self.renderer.read(cx).data_value("/"),
                        self.reactive_clock.elapsed().as_millis() as u64,
                    );
                    if self
                        .reactive
                        .receive(&id, ticket, result.unwrap_or(Value::Null))
                    {
                        self.paint_reactive(cx);
                    }
                }

                Update::Loaded(result, mode, background, generation) => {
                    if generation != self.generation {
                        continue;
                    }
                    if background {
                        self.polling = false;
                    } else {
                        self.busy = false;
                    }
                    if self
                        .filter_flight
                        .as_ref()
                        .is_some_and(|(ticket, _)| *ticket == generation)
                    {
                        let (_, submitted) = self.filter_flight.take().unwrap();
                        if !self
                            .filters
                            .matches(&submitted, &self.renderer.read(cx).data_value("/"))
                        {
                            self.notice =
                                "Filters changed; showing previous results until the next update."
                                    .into();
                            cx.notify();
                            continue;
                        }
                    } else if background && self.filters.pending() {
                        // A health refresh must not consume newly typed filter intent.
                        continue;
                    }
                    match result {
                        Ok(value) => {
                            if let Err(e) = self.install(value, &mode, cx) {
                                self.notice = e.to_string();
                                self.error = true;
                            }
                        }
                        Err(e) => {
                            if let Some(rejection) = e.downcast_ref::<Rejection>() {
                                if !rejection.fields.is_null() {
                                    self.renderer.update(cx, |r, cx| {
                                        r.set_data_value("/errors", rejection.fields.clone());
                                        cx.notify();
                                    });
                                }
                                if rejection.status == 409 {
                                    self.dispatch(json!({"name":"refresh","context":{}}), true, cx);
                                }
                            }
                            if self.snapshot.is_null() {
                                if let Ok(bytes) =
                                    std::fs::read(data_dir().join("field-cache.json"))
                                {
                                    if let Ok(cached) = serde_json::from_slice(&bytes) {
                                        let _ = self.install(cached, "merge", cx);
                                    }
                                }
                            }
                            self.notice = if self.snapshot["view"] == "field" {
                                format!("Sync unavailable: {e}. Offline field notebook is open.")
                            } else {
                                e.to_string()
                            };
                            self.error = true;
                        }
                    }
                }
                Update::Sent(id, result) => {
                    self.sending = false;
                    if let Some(index) = self.outbox.iter().position(|i| i["id"] == id) {
                        let mut next = self.outbox.clone();
                        let item = &mut next[index];
                        match result {
                            Ok(value) => {
                                item["status"] = "delivered".into();
                                item["receipt"] = value["id"].clone();
                                item["error"] = "".into();
                                item["body"]["files"] = json!([]);
                            }
                            Err(e) => {
                                item["status"] = if e
                                    .downcast_ref::<Rejection>()
                                    .is_some_and(|r| (400..500).contains(&r.status))
                                {
                                    "review"
                                } else {
                                    "pending"
                                }
                                .into();
                                item["error"] = e.to_string().into();
                            }
                        }
                        match crate::business_outbox::save(&next) {
                            Ok(()) => {
                                if next[index]["status"] == "delivered" {
                                    crate::business_outbox::remove_files(&next[index]);
                                }
                                self.outbox = next;
                            }
                            Err(e) => {
                                self.notice = format!(
                                    "Could not record delivery acknowledgement: {e}. The stable submission ID will be retried."
                                );
                                self.error = true;
                            }
                        }
                    }
                }
                Update::Revision(result, generation) => {
                    if generation != self.generation {
                        continue;
                    }
                    self.polling = false;
                    match result {
                        Ok(revision)
                            if revision != self.snapshot["revision"].as_u64().unwrap_or(0)
                                && !self.busy
                                && !self.filters.pending() =>
                        {
                            self.dispatch(json!({"name":"refresh","context":{}}), true, cx)
                        }
                        Ok(_) if self.notice.starts_with("Sync unavailable:") => {
                            self.notice = "Live connection restored. Your draft is kept.".into();
                            self.error = false;
                        }
                        Err(e) => {
                            self.notice = format!("Sync unavailable: {e}. Your draft is kept.");
                            self.error = true;
                        }
                        _ => {}
                    }
                }
                Update::Chosen(result, path) => match result {
                    Ok(Some(paths)) if !paths.is_empty() => {
                        let file = paths[0].clone();
                        if self.snapshot["view"] == "field" {
                            let result = crate::business_outbox::capture(&file);
                            let _ = self.tx.send(Update::Uploaded(result, path));
                            continue;
                        }
                        let tx = self.tx.clone();
                        self.notice = "Uploading delivery evidence…".into();
                        std::thread::spawn(move || {
                            use base64::Engine;
                            let result = (|| {
                                let bytes = std::fs::read(&file)?;
                                if bytes.len() > 5 * 1024 * 1024 {
                                    bail!("Delivery files must be at most 5 MB.");
                                }
                                let mime = match file
                                    .extension()
                                    .and_then(|s| s.to_str())
                                    .unwrap_or("")
                                    .to_lowercase()
                                    .as_str()
                                {
                                    "png" => "image/png",
                                    "jpg" | "jpeg" => "image/jpeg",
                                    "pdf" => "application/pdf",
                                    "txt" => "text/plain",
                                    _ => bail!("Choose PNG, JPEG, PDF or text."),
                                };
                                response(client()?.post(format!("{}/api/attachments",service_url())).json(&json!({"name":file.file_name().unwrap_or_default().to_string_lossy(),"mime":mime,"data":base64::engine::general_purpose::STANDARD.encode(bytes)})).send()?)
                            })();
                            let _ = tx.send(Update::Uploaded(result, path));
                        });
                    }
                    Ok(_) => {
                        self.busy = false;
                        self.notice = "Attachment selection cancelled.".into();
                    }
                    Err(e) => {
                        self.busy = false;
                        self.notice = e.to_string();
                        self.error = true;
                    }
                },
                Update::Uploaded(result, path) => {
                    self.busy = false;
                    match result {
                        Ok(v) => {
                            self.renderer.update(cx, |r, cx| {
                                let mut ids =
                                    r.data_value(&path).as_array().cloned().unwrap_or_default();
                                if ids.len()
                                    < if self.snapshot["view"] == "field" {
                                        3
                                    } else {
                                        5
                                    }
                                {
                                    ids.push(v["id"].clone());
                                    r.set_data_value(&path, json!(ids));
                                    r.set_data_value(
                                        &format!(
                                            "/attachment_names/{}",
                                            v["id"].as_str().unwrap_or("")
                                        ),
                                        v["name"].clone(),
                                    );
                                }
                                cx.notify();
                            });
                            self.notice=if self.snapshot["view"]=="field"{"Evidence saved on this device. Queue the report to submit it."}else{"Evidence attached to this draft. Post the receipt to link it permanently."}.into();
                            self.persist(cx);
                        }
                        Err(e) => {
                            self.notice = e.to_string();
                            self.error = true;
                        }
                    }
                }
            }
            cx.notify();
        }
        if !self.sending
            && self.outbox_error.is_none()
            && self.last_send.elapsed() > Duration::from_secs(15)
        {
            if let Some(item) = self
                .outbox
                .iter()
                .find(|i| i["status"] == "pending")
                .cloned()
            {
                self.sending = true;
                self.last_send = Instant::now();
                let tx = self.tx.clone();
                std::thread::spawn(move || {
                    let id = item["id"].as_str().unwrap_or("").to_string();
                    let result = (|| {
                        response(
                            client()?
                                .post(format!("{}/api/field-reports", service_url()))
                                .json(&item["body"])
                                .send()?,
                        )
                    })();
                    let _ = tx.send(Update::Sent(id, result));
                });
            }
        }
        if !self.busy
            && !self.polling
            && !self.filters.pending()
            && !self.snapshot.is_null()
            && self.snapshot["view"] != "studio"
            && self.last_poll.elapsed() > Duration::from_secs(4)
        {
            self.last_poll = Instant::now();
            self.polling = true;
            let generation = self.generation;
            let tx = self.tx.clone();
            std::thread::spawn(move || {
                let result = (|| {
                    Ok(response(
                        client()?
                            .get(format!("{}/api/health", service_url()))
                            .send()?,
                    )?["revision"]
                        .as_u64()
                        .unwrap_or(0))
                })();
                let _ = tx.send(Update::Revision(result, generation));
            });
        }
    }
}

impl Render for BusinessDesk {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        self.frame_probe.sample(window);
        let requests = std::mem::take(&mut self.probe_pending);
        for request in requests {
            let op = request.body["op"].as_str().unwrap_or("");
            let result = if op == "frames" {
                if let Some(count) = request.body["count"].as_u64() {
                    self.frame_probe = FrameProbe {
                        remaining: count.clamp(1, 360) as usize,
                        ..Default::default()
                    };
                    window.request_animation_frame();
                }
                self.frame_probe.status()
            } else if op == "inspect" {
                json!({"view":self.snapshot["view"],"selected":self.snapshot["selected"],"revision":self.snapshot["revision"],"busy":self.busy||self.polling||self.reactive.busy()||self.filters.pending(),"reactive":self.reactive.states(),"notice":self.notice,"error":self.error,"dirty":self.dirty,"events":self.preview_events,"conflicts":self.entry["conflicts"],"queue":self.outbox.iter().map(|i|json!({"id":i["id"],"status":i["status"],"error":i["error"],"receipt":i["receipt"]})).collect::<Vec<_>>(),"surface":self.renderer.read(cx).probe_snapshot(),"render":self.renderer.read(cx).probe_render_status(cx)})
            } else if self.busy || self.polling {
                json!({"error":"UI is busy"})
            } else if op == "decision" {
                if self.reactive.force(
                    request.body["id"].as_str().unwrap_or(""),
                    request.body["value"].as_str().unwrap_or(""),
                ) {
                    self.paint_reactive(cx);
                    json!({"forced":true})
                } else {
                    json!({"error":"Unknown resource or variant"})
                }
            } else if op == "load" {
                // Only the private opt-in probe exposes this; Studio validates
                // the catalog, resources and bindings before sending a preview.
                let value = request.body["value"].clone();
                if value["view"] != "studio" {
                    json!({"error":"Load requires a validated Studio preview"})
                } else {
                    if let Some(appearance) = value.get("previewTheme") {
                        let theme = CanvasTheme::from_appearance(appearance);
                        theme.apply(cx);
                        self.set_theme(theme, cx);
                    }
                    self.preview_events.clear();
                    self.reactive.clear();
                    self.drafts.remove(&crate::business_drafts::key(&value));
                    match self.install(value, "ack", cx) {
                        Ok(()) => json!({"loaded":true}),
                        Err(e) => json!({"error":e.to_string()}),
                    }
                }
            } else if op == "attach" {
                let definition = self.renderer.read(cx).probe_snapshot();
                let node = definition["nodes"].as_array().and_then(|nodes| {
                    nodes.iter().find(|n| {
                        n["id"] == request.body["id"]
                            && n["component"] == "CAttachments"
                            && n["props"]["offline"] == true
                    })
                });
                if let Some(node) = node {
                    let path = node["bindings"]["value"]["path"]
                        .as_str()
                        .unwrap_or("")
                        .to_string();
                    if self
                        .renderer
                        .read(cx)
                        .data_value(&path)
                        .as_array()
                        .is_some_and(|a| a.len() >= 3)
                    {
                        json!({"error":"At most three evidence files"})
                    } else {
                        let result = crate::business_outbox::capture(std::path::Path::new(
                            request.body["file"].as_str().unwrap_or(""),
                        ));
                        self.busy = true;
                        let _ = self.tx.send(Update::Uploaded(result, path));
                        json!({"queued":true})
                    }
                } else {
                    json!({"error":"Attach requires an offline evidence component"})
                }
            } else {
                self.renderer
                    .update(cx, |r, cx| r.probe_command(&request.body, window, cx))
            };
            let _ = request.reply.send(result);
        }
        self.renderer.update(cx, |r, cx| {
            r.workspace_display_font = self.theme.font_display.clone();
            if r.interaction_disabled != self.busy {
                r.interaction_disabled = self.busy;
                cx.notify();
            }
        });
        if self.is_preview() {
            return div().flex().flex_col().size_full().min_h_0()
                .bg(self.theme.bg).text_color(self.theme.text)
                .font_family(self.theme.font_body.clone()).text_size(px(14.)).gap_2()
                .child(div().px_5().pt_4().flex_shrink_0()
                    .child(div().text_size(px(11.)).text_color(self.theme.muted).child("CALLIOPE / APP PREVIEW"))
                    .child(div().text_size(px(24.)).font_family(self.theme.font_display.clone())
                        .child(self.snapshot["title"].as_str().unwrap_or("App preview").to_string())))
                .child(div().px_5().text_size(px(12.)).text_color(if self.error {self.theme.danger} else {self.theme.muted}).child(self.notice.clone()))
                .child(div().relative().flex_1().min_h_0().child(self.renderer.clone()));
        }
        div().flex().flex_col().size_full().text_size(px(14.)).gap_2()
            .child(div().px_6().pt_3().flex().items_center().gap_4()
                .child(div().flex_1()
                    .child(div().text_size(px(12.)).text_color(self.theme.muted).child(if self.snapshot["view"] == "studio" { "CALLIOPE UI STUDIO  /  GENERATED PREVIEW" } else if matches!(self.snapshot["view"].as_str(),Some("counter"|"receiving")) { "NORTHSTAR SUPPLY  /  FICTIONAL PARTS WORKSPACE" } else if matches!(self.snapshot["view"].as_str(),Some("hvac"|"dispatch"|"field"|"adaptive"|"case")) { "NORTHSTAR SERVICE  /  FICTIONAL HVAC WORKSPACE" } else { "MAPLE & CO.  /  FICTIONAL REAL ESTATE WORKSPACE" })))
                .child(div().text_color(self.theme.accent).child(if self.snapshot["view"] == "studio" { "● Preview" } else if self.dirty { "● Unsaved draft" } else { "● Local SQL" }))
                .child(div().id("business-reload").px_3().py_2().cursor_pointer().child("Reload")
                    .on_click(cx.listener(|this,_,_,cx| { this.request(json!({"name":"refresh","context":{}}),cx); cx.notify(); }))))
            .child(div().px_6().text_size(px(12.)).text_color(if self.error {self.theme.danger} else {self.theme.muted}).child(self.notice.clone()))
            .when(!self.drafts.is_empty(),|s|s.child(div().px_6().flex().flex_wrap().gap_2().children(self.drafts.keys().cloned().map(|key|{
                div().id(SharedString::from(format!("draft-{key}"))).px_2().py_1().rounded_md().bg(self.theme.card).text_size(px(11.)).cursor_pointer().child(format!("Draft · {key}")).on_click(cx.listener(move|this,_,_,cx|{if let Some((view,id))=key.split_once(':'){this.request(json!({"name":"set_view","context":{"view":view,"id":id}}),cx);}cx.notify();}))
            }))))
            .when(self.conflicts()>0,|s|s.child(div().px_6().flex().gap_3().text_color(self.theme.warning).child(format!("{} field(s) changed elsewhere. Review the highlighted values.",self.conflicts()))
                .child(div().id("use-server").cursor_pointer().child("Use server values").on_click(cx.listener(|this,_,_,cx|this.resolve_conflicts(true,cx))))
                .child(div().id("keep-local").cursor_pointer().child("Keep my edits").on_click(cx.listener(|this,_,_,cx|this.resolve_conflicts(false,cx))))))
            .when(self.conflicts()>0,|s|s.child(div().id("conflict-details").px_6().max_h(px(140.)).overflow_y_scroll().text_size(px(12.)).text_color(self.theme.warning).children(self.entry["conflicts"].as_object().into_iter().flat_map(|o|o.iter()).map(|(path,c)|div().child(if c["recordChanged"]==true{"Another receipt changed this order. Check remaining quantities before keeping this delivery.".to_string()}else{format!("{path}: saved {} · your edit {}",c["remote"],c["local"])})))))
            .when(!self.outbox.is_empty(),|s|s.child(div().id("device-outbox").px_6().max_h(px(170.)).overflow_y_scroll().children(self.outbox.iter().rev().take(10).map(|item|{
                let id=item["id"].as_str().unwrap_or("").to_string();let retry=id.clone();let remove=id.clone();let pending=item["status"]!="delivered";
                div().flex().items_center().gap_3().py_1().text_size(px(12.)).child(div().flex_1().child(format!("{} · {} {}",item["body"]["draft"]["id"].as_str().unwrap_or("Visit"),item["status"].as_str().unwrap_or("pending"),item["receipt"].as_str().unwrap_or(""))).child(div().text_color(self.theme.muted).child(item["error"].as_str().unwrap_or("").to_string())))
                 .when(pending,|s|s.child(div().id(SharedString::from(format!("retry-{id}"))).cursor_pointer().child("Retry").on_click(cx.listener(move|this,_,_,cx|{if this.sending{return;}let mut next=this.outbox.clone();if let Some(i)=next.iter_mut().find(|i|i["id"]==retry){i["status"]="pending".into();}if crate::business_outbox::save(&next).is_ok(){this.outbox=next;this.last_send=Instant::now()-Duration::from_secs(30);}cx.notify();}))))
                 .child(div().id(SharedString::from(format!("remove-{id}"))).cursor_pointer().child(if pending{if self.delete_report.as_deref()==Some(&id){"Confirm delete"}else{"Delete queued report"}}else{"Dismiss"}).on_click(cx.listener(move|this,_,_,cx|{if this.sending{return;}if pending&&this.delete_report.as_deref()!=Some(&remove){this.delete_report=Some(remove.clone());cx.notify();return;}let old=this.outbox.iter().find(|i|i["id"]==remove).cloned();let next:Vec<_>=this.outbox.iter().filter(|i|i["id"]!=remove).cloned().collect();if crate::business_outbox::save(&next).is_ok(){if let Some(old)=old{crate::business_outbox::remove_files(&old);}this.outbox=next;this.delete_report=None;}cx.notify();})))
            }))))
            .child(div().relative().flex_1().min_h_0().child(self.renderer.clone())
                .when(self.busy, |s| s.child(div().absolute().inset_0().bg(crate::theme::with_alpha(self.theme.bg, 0.5))
                    .on_mouse_down(MouseButton::Left, |_,_,cx| cx.stop_propagation()))))
            .child(div().px_6().pb_3().text_size(px(11.)).text_color(self.theme.muted)
                .child(if self.snapshot["view"] == "studio" { "A2UI v0.9 · native GPUI preview · actions are recorded without execution" } else { "A2UI v0.9 · native GPUI renderer · same definition and SQL store as the web view" }))
    }
}
