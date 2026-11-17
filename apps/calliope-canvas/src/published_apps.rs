//! Server-owned apps with a native recovery journal; business data remains on Calliope.
use crate::{
    api::{Api, Connection},
    business_filters::Filters,
    theme::CanvasTheme,
};
use a2ui_base::{catalog::Catalog, protocol::server_to_client::A2uiMessage};
use a2ui_gpui::GpuiApp;
use anyhow::Result;
use gpui::{prelude::*, *};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    time::{Duration, Instant},
};

enum Update {
    Saved(u64, Result<()>),
    List(Result<Value>),
    Attached(Result<Option<Value>>, String),
    Opened(Result<std::path::PathBuf>),
    Loaded(Result<Value>, Value),
}
pub struct PublishedApps {
    pub pins: Entity<crate::app_pins::Pins>,
    _pin_subscription: Subscription,
    pending_record: Option<(Value, bool)>,
    requested_slug: Option<String>,
    return_stack: Vec<(String, crate::app_workspace::Visit)>,
    record_entry: bool,
    assistant: crate::app_assistant::Assistant,
    renderer: Entity<GpuiApp>,
    probe: Option<std::sync::mpsc::Receiver<crate::business_probe::Request>>,
    probe_pending: Vec<crate::business_probe::Request>,
    api: Api,
    store: crate::app_drafts::Store,
    offline: bool,
    workspace: crate::app_workspace::Workspace,
    recovery: crate::app_connection::Recovery,
    drafts_open: bool,
    discard_confirm: Option<String>,
    menu_open: bool,
    status_open: bool,
    search_input: Option<Entity<gpui_component::input::InputState>>,
    search_subscription: Option<Subscription>,
    restore_layout: Option<(Value, u8)>,
    read_requests: u64,
    saved_model: Value,
    saved_revision: u64,
    draft_generation: u64,
    saved_generation: u64,
    draft_error: Option<String>,
    pub theme: CanvasTheme,
    pub active: bool,
    pub on_connection: Option<std::rc::Rc<dyn Fn(&mut App)>>,
    slug: String,
    apps: Vec<Value>,
    picker_width: f32,
    snapshot: Value,
    busy: bool,
    command_pending: bool,
    queued_action: Option<Value>,
    notice: String,
    failed: bool,
    retry: Option<Value>,
    filters: Filters,
    clock: Instant,
    last_refresh: Instant,
    watch_seen: Value,
    watch_due: Option<Instant>,
    reactive_pending: bool,
    tx: crate::mailbox::Sender<Update>,
    rx: std::sync::mpsc::Receiver<Update>,
}
impl PublishedApps {
    /// Follow the host theme; charts redraw only when their theme changes.
    #[cfg_attr(any(target_os = "android", target_os = "ios"), allow(dead_code))]
    pub fn set_theme(&mut self, theme: CanvasTheme, cx: &mut Context<Self>) {
        let (before, after) = (self.theme.vega_config(), theme.vega_config());
        self.theme = theme;
        if before != after {
            let charts = crate::server_charts::themed(crate::server_charts::renderer(self.api.clone()), after);
            self.renderer.update(cx, |r, cx| {
                r.set_chart_renderer(Some(charts));
                cx.notify();
            });
        }
    }
    pub fn new(config: Connection, theme: CanvasTheme, cx: &mut Context<Self>) -> Self {
        Self::with_pins(config, theme, None, cx)
    }
    pub fn with_pins(config: Connection, theme: CanvasTheme, pins: Option<Entity<crate::app_pins::Pins>>, cx: &mut Context<Self>) -> Self {
        let pins = pins.unwrap_or_else(|| cx.new(|cx| crate::app_pins::Pins::new(config.clone(), cx)));
        let pin_subscription = cx.observe(&pins, |_, _, cx| cx.notify());
        let store = crate::app_drafts::Store::new(&config);
        let mut cached_index = store.index().unwrap_or(Value::Null);
        store.annotate_index(&mut cached_index);
        let cached_apps = cached_index["apps"].as_array().cloned().unwrap_or_default();
        let api = Api::new(config).expect("validated Calliope connection");
        let (tx, rx, wake) = crate::mailbox::channel();
        let renderer = cx.new(|_| {
            let mut r = GpuiApp::new(
                vec![Catalog::new(include_str!("../business/catalog.json"))],
                HashMap::new(),
            );
            r.chart_renderer = Some(crate::server_charts::themed(crate::server_charts::renderer(api.clone()), theme.vega_config()));
            r.wake_host = Some(tx.notifier());
            #[cfg(any(target_os = "android", target_os = "ios"))]
            { r.on_input_kind = Some(std::sync::Arc::new(crate::host::input_kind)); }
            r
        });
        let args: Vec<_> = std::env::args().collect();
        let slug = args
            .iter()
            .position(|a| a == "--app")
            .and_then(|i| args.get(i + 1))
            .cloned()
            .unwrap_or_default();
        let mut this = Self {
            pins,
            _pin_subscription: pin_subscription,
            pending_record: None,
            requested_slug: None,
            return_stack: Vec::new(),
            record_entry: false,
            assistant: Default::default(),
            probe: crate::business_probe::start(tx.notifier()),
            probe_pending: Vec::new(),
            renderer,
            api,
            store,
            offline: false,
            workspace: Default::default(),
            recovery: Default::default(),
            drafts_open: false,
            discard_confirm: None,
            menu_open: false,
            status_open: false,
            search_input: None,
            search_subscription: None,
            restore_layout: None,
            read_requests: 0,
            saved_model: Value::Null,
            saved_revision: u64::MAX,
            draft_generation: 0,
            saved_generation: 0,
            draft_error: None,
            theme,
            active: true,
            on_connection: None,
            slug,
            apps: cached_apps,
            picker_width: 400.,
            snapshot: Value::Null,
            busy: false,
            command_pending: false,
            queued_action: None,
            notice: "Loading apps…".into(),
            failed: false,
            retry: None,
            filters: Filters::default(),
            clock: Instant::now(),
            last_refresh: Instant::now(),
            watch_seen: Value::Null,
            watch_due: None,
            reactive_pending: false,
            tx,
            rx,
        };
        if this.slug.is_empty() {
            this.list();
        } else {
            this.restore(cx);
            this.load(None, cx);
        }
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
                if view.update(cx, |v, cx| v.tick(cx)).is_err() {
                    break;
                }
            }
        })
        .detach();
        this
    }
    fn capture_layout(&mut self, cx: &mut Context<Self>) {
        self.workspace.layout = self.renderer.read(cx).view_state();
        self.saved_model = Value::Null;
        self.persist(cx);
    }
    fn show_visit(&mut self, visit: crate::app_workspace::Visit, cx: &mut Context<Self>) {
        let mut snapshot = self.workspace.restore(&visit);
        if let Ok(draft) = self.store.load(&self.slug) {
            restore_names(&mut snapshot, &draft.files);
        }
        if let Ok(messages) =
            serde_json::from_value::<Vec<A2uiMessage>>(snapshot["messages"].clone())
        {
            match self.renderer.update(cx, |r, cx| {
                let result = r.load_surface(messages);
                cx.notify();
                result
            }) {
                Ok(()) => {
                    self.snapshot = snapshot;
                    self.workspace.layout = visit.layout.clone();
                    self.workspace.fetched_at = visit.fetched_at;
                    self.restore_layout = Some((visit.layout, 2));
                    self.saved_model = Value::Null;
                    self.filters.install(
                        &self.snapshot["messages"][1]["updateComponents"]["components"],
                        &self.renderer.read(cx).data_value("/"),
                    );
                    self.watch_seen = self.watched(&self.renderer.read(cx).data_value("/"));
                    self.watch_due = None;
                    self.drafts_open = false;
                    self.persist(cx);
                    self.notice = "Restored cached view and this record's latest draft.".into();
                    if !self.offline {
                        self.load(None, cx);
                    }
                }
                Err(e) => {
                    self.notice = e;
                    self.failed = true;
                }
            }
        }
        cx.notify();
    }
    fn back(&mut self, cx: &mut Context<Self>) {
        if self.assistant.open {self.assistant.open=false;self.assistant.voice.cancel();cx.notify();return;}
        if self.discard_confirm.take().is_some() {cx.notify();return;}
        if self.menu_open || self.status_open {self.menu_open=false;self.status_open=false;cx.notify();return;}
        if self.drafts_open {
            self.drafts_open = false;
            cx.notify();
            return;
        }
        if self.renderer.update(cx, |r, _| r.close_overlay()) {
            cx.notify();
            return;
        }
        if self.busy {
            return;
        }
        if self.retry.is_none() && !self.workspace.history.is_empty() {
            self.capture_layout(cx);
            let visit = self.workspace.history.pop().unwrap();
            self.show_visit(visit, cx);
        } else if self.retry.is_none() && !self.return_stack.is_empty() {
            self.capture_layout(cx);
            let (slug, visit) = self.return_stack.pop().unwrap();
            self.slug = slug;
            self.workspace = Default::default();
            self.snapshot = Value::Null;
            self.saved_model = Value::Null;
            self.restore(cx);
            self.show_visit(visit, cx);
        } else if !self.slug.is_empty() {
            self.home(cx);
        } else {
            #[cfg(any(target_os = "android", target_os = "ios"))]
            crate::host::background();
        }
    }
    fn resume_draft(&mut self, key: &str, cx: &mut Context<Self>) {
        if self.busy || self.retry.is_some() {
            return;
        }
        let visit = self
            .workspace
            .records
            .get(key)
            .and_then(|r| self.workspace.visits.get(&r.location))
            .cloned();
        if let Some(visit) = visit {
            self.capture_layout(cx);
            let data = self.renderer.read(cx).data_value("/");
            if crate::app_workspace::location(&self.snapshot, &data)
                != crate::app_workspace::location(&visit.snapshot, &visit.model)
            {
                self.workspace
                    .push(self.workspace.current(&self.snapshot, &data));
            }
            self.show_visit(visit, cx);
        }
    }
    fn request_discard(&mut self,key:&str,cx:&mut Context<Self>) {
        if !self.busy && self.retry.is_none() && self.workspace.records.get(key).is_some_and(|r|r.dirty()) {
            self.drafts_open=true;self.discard_confirm=Some(key.into());cx.notify();
        }
    }
    fn discard_draft(&mut self, key: &str, cx: &mut Context<Self>) {
        if self.discard_confirm.as_deref()!=Some(key){return;}
        if self.busy || self.retry.is_some() {
            return;
        }
        self.discard_confirm=None;
        self.capture_layout(cx);
        if let Some(record) = self.workspace.records.get_mut(key) {
            record.value = record.base.clone();
            let path = record.path.clone();
            let value = record.value.clone();
            let current = self.renderer.read(cx).data_value(&path);
            let matches = record.version == self.snapshot["version"] && crate::app_workspace::forms(&self.snapshot)
                .find(|f| f["path"] == path)
                .is_some_and(|f| current[f["key"].as_str().unwrap_or("id")] == record.id);
            if matches {
                self.renderer.update(cx, |r, cx| {
                    r.set_data_value(&path, value);
                    cx.notify();
                });
            }
            self.saved_model = Value::Null;
            self.persist(cx);
            self.notice = "Local edits discarded for this record. Other drafts are kept.".into();
            if !self.offline {
                self.load(None, cx);
            }
        }
        cx.notify();
    }
    fn home(&mut self, cx: &mut Context<Self>) {
        if self.busy {
            return;
        }
        self.pending_record = None;
        self.return_stack.clear();
        self.record_entry = false;
        self.capture_layout(cx);
        if let Some(app)=self.apps.iter_mut().find(|a|a["slug"]==self.slug) {
            let count=self.workspace.dirty().len();
            app["cached"]=json!(true);app["draft_count"]=json!(count);
            app["last_used"]=json!(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs());
            app["draft_status"]=if self.retry.is_some(){json!("Pending submission")}else if count>0{json!(format!("{count} unfinished {}",if count==1{"draft"}else{"drafts"}))}else{Value::Null};
        }
        self.menu_open=false;self.status_open=false;self.discard_confirm=None;
        self.renderer.update(cx, |r, _| {
            let _ = r.load_surface(vec![]);
        });
        self.draft_error = None;
        self.slug.clear();
        self.workspace = Default::default();
        self.drafts_open = false;
        self.restore_layout = None;
        self.snapshot = Value::Null;
        self.saved_model = Value::Null;
        self.retry = None;
        self.queued_action = None;
        self.filters = Filters::default();
        self.watch_due = None;
        self.reactive_pending = false;
        self.list();
        cx.notify();
    }
    pub fn enqueue_probe(
        &mut self,
        request: crate::business_probe::Request,
        cx: &mut Context<Self>,
    ) {
        self.probe_pending.push(request);
        cx.notify();
    }
    pub fn request_open(&mut self,slug:String,cx:&mut Context<Self>)->Result<()> {
        anyhow::ensure!(self.retry.is_none()&&!self.command_pending,"Resolve the pending app submission before navigating");
        anyhow::ensure!(!slug.is_empty()&&slug.len()<=160&&slug.bytes().all(|c|c.is_ascii_alphanumeric()||c==b'-'||c==b'_'),"Invalid app address");
        self.requested_slug=Some(slug);cx.notify();Ok(())
    }
    fn open(&mut self, slug: String, cx: &mut Context<Self>) {
        self.menu_open=false;self.status_open=false;self.discard_confirm=None;
        if self.busy || !self.apps.iter().any(|a| a["slug"] == slug) {
            return;
        }
        self.capture_layout(cx);
        self.return_stack.clear();
        self.record_entry = false;
        self.workspace = Default::default();
        self.drafts_open = false;
        self.snapshot = Value::Null;
        self.saved_model = Value::Null;
        self.retry = None;
        self.draft_error = None;
        self.renderer.update(cx, |r, _| {
            let _ = r.load_surface(vec![]);
        });
        self.slug = slug;
        self.restore(cx);
        self.load(None, cx);
        cx.notify();
    }
    fn restore(&mut self, cx: &mut Context<Self>) {
        match self.store.recover_camera(self.slug.clone()) {
            Ok(draft) if !draft.snapshot.is_null() => {
                // Reopening preserves the cached version; updating is an explicit action.
                self.workspace = draft.workspace;
                self.restore_layout = Some((self.workspace.layout.clone(), 2));
                let mut snapshot = draft.snapshot;
                snapshot["messages"][2]["updateDataModel"]["value"] = draft.model.clone();
                if let Ok(messages) =
                    serde_json::from_value::<Vec<A2uiMessage>>(snapshot["messages"].clone())
                {
                    let result = self.renderer.update(cx, |r, cx| {
                        let result = r.load_surface(messages);
                        cx.notify();
                        result
                    });
                    if result.is_ok() {
                        self.snapshot = snapshot;
                        self.saved_model = draft.model;
                        self.saved_revision = self.renderer.read(cx).data_revision();
                        self.retry = draft.pending;
                        self.watch_seen = self.watched(&self.saved_model);
                        self.filters.install(
                            &self.snapshot["messages"][1]["updateComponents"]["components"],
                            &self.saved_model,
                        );
                        self.notice = "Restored this device's draft and cached app data.".into();
                    }
                }
            }
            Ok(_) => {}
            Err(e) => {
                self.draft_error = Some(format!("Could not read the saved draft: {e}"));
            }
        }
    }
    fn persist(&mut self, cx: &mut Context<Self>) {
        if self.slug.is_empty() || self.snapshot.is_null() || self.retry.is_some() {
            return;
        }
        let revision = self.renderer.read(cx).data_revision();
        if revision == self.saved_revision && !self.saved_model.is_null() {
            return;
        }
        let model = self.renderer.read(cx).data_value("/");
        self.saved_revision = revision;
        if model == self.saved_model {
            return;
        }
        self.saved_model = model.clone();
        self.workspace.remember(&self.snapshot, &model);
        self.draft_generation += 1;
        let generation = self.draft_generation;
        let tx = self.tx.clone();
        self.store.save_workspace(
            self.slug.clone(),
            self.snapshot.clone(),
            model,
            Some(self.workspace.clone()),
            move |result| {
                let _ = tx.send(Update::Saved(generation, result));
            },
        );
    }
    fn browser(&mut self, cx: &mut Context<Self>) {
        let url = format!(
            "{}/apps/{}",
            self.api.config.browser_url.trim_end_matches('/'),
            self.slug
        );
        #[cfg(any(target_os = "android", target_os = "ios"))]
        if let Err(error) = crate::host::open_url(&url) {
            self.notice = error;
            cx.notify();
        }
        #[cfg(not(any(target_os = "android", target_os = "ios")))]
        cx.open_url(&url);
    }
    fn list(&mut self) {
        let api = self.api.clone();
        let tx = self.tx.clone();
        let store = self.store.clone();
        self.busy = true;
        self.read_requests += 1;
        std::thread::spawn(move || {
            let result = api.get("/api/calliope/ui-apps").and_then(|mut v| {
                store.save_index(v.clone())?;
                store.annotate_index(&mut v);
                Ok(v)
            });
            let _ = tx.send(Update::List(result));
        });
    }
    fn load(&mut self, action: Option<Value>, cx: &mut Context<Self>) {
        if self.busy || self.retry.is_some() || self.draft_error.is_some() {
            return;
        }
        if action.is_some() {
            self.capture_layout(cx);
        } else {
            self.persist(cx);
        }
        let mut body = json!({});
        if !self.snapshot.is_null() {
            body["version"] = self.snapshot["version"].clone();
            body["state"] = self.renderer.read(cx).data_value("/state");
        }
        if let Some(a) = action {
            body["event"] = a["name"].clone();
            body["context"] = a["context"].clone();
            body["request_key"] = uuid::Uuid::new_v4().to_string().into();
        }
        if self.offline
            && body.get("event").is_some()
            && !crate::app_drafts::is_command(&self.snapshot, &body)
        {
            let action = action_for_body(&body);
            let data = self.renderer.read(cx).data_value("/");
            if let Some(visit) = self.workspace.cached_action(&self.snapshot, &data, &action) {
                self.workspace
                    .push(self.workspace.current(&self.snapshot, &data));
                self.show_visit(visit, cx);
            } else {
                self.notice="This view is not cached. Back and Drafts can reopen previously visited records.".into();
                cx.notify();
            }
            return;
        }
        if body.get("event").is_some() {
            self.reactive_pending = true;
        }
        self.send(body, cx);
    }
    fn send(&mut self, mut body: Value, cx: &mut Context<Self>) {
        if self.retry.is_none() && crate::app_drafts::is_command(&self.snapshot, &body)
            && !self.renderer.update(cx, |r,cx| {let valid=r.validate_fields();cx.notify();valid}) {
            self.failed=true;
            self.notice="Check the highlighted fields before submitting.".into();
            cx.notify();
            return;
        }
        let store = self.store.clone();
        let snapshot = self.snapshot.clone();
        let model = self.renderer.read(cx).data_value("/");
        if self.retry.is_none() && snapshot["record_drafts"] == true
            && (body.get("state").is_some() || body.get("record").is_some()) {
            // Draft hints participate in server hydration before dependent SQL/decisions.
            // Record links intentionally omit state, but must retain the destination's edits.
            // Local files from other records must never be uploaded by this submission.
            body["drafts"] = crate::app_drafts::refresh_body(
                &snapshot,
                &json!({"drafts":self.workspace.hints(&snapshot["version"])}),
            )["drafts"]
                .clone();
        }
        let read_body = crate::app_drafts::refresh_body(&snapshot, &body);
        if !crate::app_drafts::is_command(&snapshot, &body) {
            self.read_requests += 1;
        }
        self.busy = true;
        self.command_pending = body.get("event").is_some() || body.get("record").is_some()
            || (!snapshot.is_null() && body["version"] != snapshot["version"]);
        self.failed = false;
        self.notice = "Updating…".into();
        let api = self.api.clone();
        let tx = self.tx.clone();
        let slug = self.slug.clone();
        std::thread::spawn(move || {
            let result = (|| {
                if slug.is_empty()
                    || !slug
                        .bytes()
                        .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
                {
                    anyhow::bail!("Invalid app slug");
                }
                if crate::app_drafts::is_command(&snapshot, &body) {
                    let pending = store.prepare(slug.clone(), snapshot, model, body.clone())?;
                    store.send(&api, &slug, pending).map(|(value, _)| value)
                } else {
                    api.post(&format!("/api/apps/{slug}/ui"), &read_body)
                }
            })();
            let _ = tx.send(Update::Loaded(result, body));
        });
    }
    fn action(&mut self, action: Value, cx: &mut Context<Self>) {
        match action["name"].as_str() {
            Some("open_link") => self.open_link(&action["context"]["componentId"],cx),
            Some("choose_attachment" | "capture_photo") => {
                if self.retry.is_some() {
                    return;
                }
                self.persist(cx);
                #[cfg(any(target_os = "android", target_os = "ios"))]
                let camera = action["name"] == "capture_photo";
                let path = action["context"]["path"].as_str().unwrap_or("").to_owned();
                let allowed = self.snapshot["messages"][1]["updateComponents"]["components"]
                    .as_array()
                    .is_some_and(|nodes| {
                        nodes.iter().any(|n| {
                            n["component"] == "CAttachments"
                                && n["readOnly"] != true
                                && n["value"]["path"] == path
                        })
                    });
                if !allowed || !path.starts_with("/state/") {
                    return;
                }
                let names = self.snapshot["messages"][1]["updateComponents"]["components"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .find(|n| n["component"] == "CAttachments" && n["value"]["path"] == path)
                    .and_then(|n| n["names"]["path"].as_str())
                    .unwrap_or("")
                    .to_owned();
                self.busy = true;
                self.command_pending = true;
                self.notice = "Choose a photo or document…".into();
                let store = self.store.clone();
                let slug = self.slug.clone();
                let tx = self.tx.clone();
                #[cfg(any(target_os = "android", target_os = "ios"))]
                std::thread::spawn(move || {
                    let result = if camera {
                        (|| {
                            let target =
                                store.prepare_camera(slug.clone(), path.clone(), names.clone())?;
                            let picked = crate::host::capture_photo(&target);
                            let recovered = store.recover_camera(slug.clone())?;
                            Ok(picked?.and_then(|_| {
                                recovered
                                    .files
                                    .values()
                                    .find(|v| v["path"].as_str() == target.to_str())
                                    .cloned()
                            }))
                        })()
                    } else {
                        crate::host::choose_attachment().and_then(|file| match file {
                            None => Ok(None),
                            Some((file, name, mime)) => {
                                let result = store
                                    .stage(
                                        slug.clone(),
                                        file.clone(),
                                        name,
                                        mime,
                                        path.clone(),
                                        names.clone(),
                                    )
                                    .map(Some);
                                let _ = std::fs::remove_file(file);
                                result
                            }
                        })
                    };
                    let _ = tx.send(Update::Attached(result, path));
                });
                #[cfg(not(any(target_os = "android", target_os = "ios")))]
                {
                    let chooser = cx.prompt_for_paths(PathPromptOptions {
                        files: true,
                        directories: false,
                        multiple: false,
                        prompt: Some("Attach photo or document".into()),
                    });
                    cx.spawn(async move |_, _| {
                        let chosen = match chooser.await {
                            Ok(v) => v,
                            Err(e) => Err(anyhow::anyhow!(e)),
                        };
                        std::thread::spawn(move || {
                            let result = chosen.and_then(|files| {
                                match files.and_then(|v| v.into_iter().next()) {
                                    None => Ok(None),
                                    Some(file) => {
                                        let name = file
                                            .file_name()
                                            .and_then(|v| v.to_str())
                                            .unwrap_or("attachment");
                                        let mime = match file
                                            .extension()
                                            .and_then(|v| v.to_str())
                                            .unwrap_or("")
                                            .to_lowercase()
                                            .as_str()
                                        {
                                            "png" => "image/png",
                                            "jpg" | "jpeg" => "image/jpeg",
                                            "pdf" => "application/pdf",
                                            "txt" => "text/plain",
                                            _ => "application/octet-stream",
                                        };
                                        store
                                            .stage(
                                                slug.clone(),
                                                file.clone(),
                                                name.into(),
                                                mime.into(),
                                                path.clone(),
                                                names.clone(),
                                            )
                                            .map(Some)
                                    }
                                }
                            });
                            let _ = tx.send(Update::Attached(result, path));
                        });
                    })
                    .detach();
                }
            }
            Some("open_attachment") => {
                let id = action["context"]["id"].as_str().unwrap_or("").to_owned();
                if id.starts_with("local:") {
                    if let Ok(draft) = self.store.load(&self.slug) {
                        if let Some(file) = draft.files.get(&id) {
                            let path = file["path"].as_str().unwrap_or("");
                            #[cfg(any(target_os = "android", target_os = "ios"))]
                            if let Err(error) = crate::host::open_attachment(
                                path,
                                file["mime"].as_str().unwrap_or("application/octet-stream"),
                            ) {
                                self.failed=true; self.notice=error.to_string(); cx.notify();
                            }
                            #[cfg(not(any(target_os = "android", target_os = "ios")))]
                            if let Ok(url) = reqwest::Url::from_file_path(path) {
                                cx.open_url(url.as_str());
                            }
                        }
                    }
                    return;
                }
                if uuid::Uuid::parse_str(&id).is_err() {
                    return;
                }
                self.busy = true;
                self.command_pending = true;
                self.notice = "Opening attachment…".into();
                let api = self.api.clone();
                let tx = self.tx.clone();
                std::thread::spawn(move || {
                    let result = (|| {
                        let meta = api.get(&format!("/api/calliope/app-files/{id}/meta"))?;
                        let name = meta["name"].as_str().unwrap_or("attachment");
                        anyhow::ensure!(
                            !name.contains(['/', '\\']) && name != ".." && name != ".",
                            "Invalid attachment name"
                        );
                        let bytes =
                            api.asset(&format!("/api/calliope/app-files/{id}"), 5 * 1024 * 1024)?;
                        let folder = crate::model::data_dir().join("attachments").join(id);
                        std::fs::create_dir_all(&folder)?;
                        #[cfg(unix)]
                        {
                            use std::os::unix::fs::PermissionsExt;
                            std::fs::set_permissions(
                                &folder,
                                std::fs::Permissions::from_mode(0o700),
                            )?;
                        }
                        let path = folder.join(name);
                        std::fs::write(&path, bytes)?;
                        #[cfg(any(target_os = "android", target_os = "ios"))]
                        crate::host::open_attachment(
                            &path.to_string_lossy(),
                            meta["mime"].as_str().unwrap_or("application/octet-stream"),
                        )?;
                        Ok(path)
                    })();
                    let _ = tx.send(Update::Opened(result));
                });
            }
            _ => self.load(Some(action), cx),
        }
    }
    fn tick(&mut self, cx: &mut Context<Self>) {
        self.assistant_poll(cx);
        if self.failed && self.notice == "Check the highlighted fields before submitting."
            && !self.renderer.read(cx).has_field_errors() {
            self.failed = false;
            self.notice = "Fields corrected. Ready to submit.".into();
            cx.notify();
        }
        #[cfg(any(target_os = "android", target_os = "ios"))]
        if self.active && crate::host::take_back() {
            self.back(cx);
        }
        if let Some(rx) = &self.probe {
            self.probe_pending.extend(rx.try_iter());
        }
        if !self.probe_pending.is_empty() {
            cx.notify();
        }
        self.persist(cx);
        let now = self.clock.elapsed().as_millis() as u64;
        let foreground = self.active;
        #[cfg(any(target_os = "android", target_os = "ios"))]
        let foreground = foreground && crate::host::is_foreground();
        if self.recovery.foreground(foreground, now) {
            cx.notify();
        }
        let data = self.renderer.read(cx).data_value("/");
        self.filters.observe(&data, now);
        let watched = self.watched(&data);
        if !self.offline
            && !self.slug.is_empty()
            && !self.snapshot.is_null()
            && watched != self.watch_seen
        {
            self.watch_seen = watched;
            self.watch_due = Some(Instant::now() + Duration::from_millis(450));
            self.reactive_pending = true;
            cx.notify();
        }
        while let Ok(update) = self.rx.try_recv() {
            if let Update::Saved(generation, result) = update {
                match result {
                    Ok(()) => {
                        self.saved_generation = self.saved_generation.max(generation);
                        self.draft_error = None;
                    }
                    Err(e) => {
                        self.draft_error = Some(format!("Draft was NOT saved on this device: {e}"));
                        self.saved_model = Value::Null;
                    }
                }
                cx.notify();
                continue;
            }
            self.busy = false;
            self.command_pending = false;
            self.last_refresh = Instant::now();
            match update {
                Update::Saved(_, _) => unreachable!(),
                Update::Attached(result, path) => match result {
                    Ok(Some(record)) => {
                        self.renderer.update(cx, |r, cx| {
                            let mut ids =
                                r.data_value(&path).as_array().cloned().unwrap_or_default();
                            if ids.len() < 5 && !ids.contains(&record["id"]) {
                                ids.push(record["id"].clone());
                                r.set_data_value(&path, json!(ids));
                            }
                            for node in
                                self.snapshot["messages"][1]["updateComponents"]["components"]
                                    .as_array()
                                    .into_iter()
                                    .flatten()
                            {
                                if node["component"] == "CAttachments"
                                    && node["value"]["path"] == path
                                {
                                    if let Some(names_path) = node["names"]["path"].as_str() {
                                        let mut names = r.data_value(names_path);
                                        names[record["id"].as_str().unwrap_or("")] =
                                            record["name"].clone();
                                        r.set_data_value(names_path, names);
                                    }
                                }
                            }
                            cx.notify();
                        });
                        self.failed = false;
                        self.notice =
                            "Attachment saved on this device. Submit to send it to Calliope."
                                .into();
                    }
                    Ok(None) => {
                        self.failed = false;
                        self.notice = "File selection cancelled. Your draft is kept.".into();
                    }
                    Err(e) => {
                        self.failed = true;
                        self.notice = e.to_string();
                    }
                },
                Update::Opened(result) => match result {
                    Ok(path) => {
                        #[cfg(not(any(target_os = "android", target_os = "ios")))]
                        if let Ok(url) = reqwest::Url::from_file_path(&path) {
                            cx.open_url(url.as_str());
                        }
                        #[cfg(any(target_os = "android", target_os = "ios"))]
                        let _ = path;
                        self.failed = false;
                        self.notice = "Attachment handed to your device viewer.".into();
                    }
                    Err(e) => {
                        self.failed = true;
                        self.notice = e.to_string();
                    }
                },
                Update::List(result) => match result {
                    Ok(v) => {
                        self.offline = false;
                        self.recovery.success(now);
                        self.failed = false;
                        self.apps = v["apps"].as_array().cloned().unwrap_or_default();
                        self.notice = if self.slug.is_empty() {"Choose a company app"} else {"Connected · pending submission stays on this device until you choose Send pending"}.into();
                    }
                    Err(e) => {
                        self.offline = crate::app_connection::retryable(&e);
                        self.recovery.failed(now, self.offline);
                        self.notice = if self.offline && !self.apps.is_empty() {
                            "Offline · open a previously visited app to continue its draft.".into()
                        } else {
                            e.to_string()
                        };
                        self.failed = !self.offline;
                    }
                },
                Update::Loaded(result, body) => match result {
                    Ok(mut value) => {
                        let catalog: Value = serde_json::from_str(include_str!("../business/catalog.json")).unwrap();
                        if let Some(reason) = compatibility(&value["release"], &catalog) {
                            self.recovery.success(now); // Avoid retrying an unsupported version every frame.
                            self.pending_record = None;
                            self.reactive_pending = false;
                            self.notice = reason; self.failed = true; self.status_open = true;
                            if !self.snapshot.is_null() {self.snapshot["latest_version"] = value["latest_version"].clone();self.snapshot["update"] = value["update"].clone();}
                            cx.notify(); continue;
                        }
                        let switching = !self.snapshot.is_null() && self.snapshot["version"] != value["version"];
                        self.recovery.success(now);
                        self.offline = false;
                        self.retry = None;
                        if body.get("event").is_none() && body.get("record").is_none() && body.get("state").is_some()
                            && !self.snapshot.is_null()
                            && body["state"] != data["state"]
                        {
                            cx.notify();
                            continue;
                        }
                        if switching {
                            self.drafts_open = false;
                            self.status_open = false;
                            self.discard_confirm = None;
                            self.workspace = Default::default();
                            self.restore_layout = Some((Value::Null, 2));
                            self.queued_action = None;
                        }
                        self.workspace.layout = if switching {Value::Null} else {self.renderer.read(cx).view_state()};
                        let old_visit = self.workspace.current(&self.snapshot, &data);
                        self.workspace.accepted(&self.snapshot, &value, &data);
                        self.workspace.restore_local(&mut value);
                        if let Ok(draft) = self.store.load(&self.slug) {
                            restore_names(&mut value, &draft.files);
                        }
                        let moved = !self.snapshot.is_null()
                            && crate::app_workspace::location(&self.snapshot, &data)
                                != crate::app_workspace::location(
                                    &value,
                                    crate::app_workspace::model(&value),
                                );
                        if moved && !switching && !self.record_entry {
                            self.workspace.push(old_visit);
                            self.workspace.layout = Value::Null;
                            self.restore_layout = Some((Value::Null, 2));
                        }
                        self.record_entry = false;
                        self.workspace.fetched_at = crate::app_connection::timestamp();
                        let result =
                            serde_json::from_value::<Vec<A2uiMessage>>(value["messages"].clone())
                                .map_err(|e| e.to_string())
                                .and_then(|messages| {
                                    self.renderer.update(cx, |r, cx| {
                                        let result = if self.snapshot.is_null() || switching {
                                            r.load_surface(messages)
                                        } else {
                                            r.reconcile_surface(messages)
                                        };
                                        cx.notify();
                                        result
                                    })
                                });
                        if let Err(e) = result {
                            self.notice = e;
                            self.failed = true;
                            self.pending_record = None;
                        } else {
                            self.reactive_pending = false;
                            self.snapshot = value;
                            self.saved_model = Value::Null;
                            self.persist(cx);
                            self.watch_seen = self.watched(&self.renderer.read(cx).data_value("/"));
                            self.watch_due = None;
                            self.filters.install(
                                &self.snapshot["messages"][1]["updateComponents"]["components"],
                                &self.renderer.read(cx).data_value("/"),
                            );
                            self.failed = self.snapshot["queries"]
                                .as_object()
                                .is_some_and(|q| q.values().any(|q| q.get("error").is_some()));
                            self.notice = if self.failed {
                                "Some SQL bindings could not refresh. Check the app definition."
                            } else if !self.snapshot["receipt"].is_null() {
                                "Saved in Calliope."
                            } else {
                                "Connected · drafts stay on this device until submitted"
                            }
                            .into();
                            if body.get("record").is_some() {
                                if self.pending_record.take().is_some_and(|(_, ask)| ask) {self.assistant_open(cx);}
                            }
                            if !self.snapshot["receipt"].is_null() {self.pins.update(cx, |p, _| p.refresh());}
                        }
                    }
                    Err(e) => {
                        self.reactive_pending = false;
                        self.offline = crate::app_connection::retryable(&e);
                        self.recovery.failed(now, self.offline);
                        self.failed = !self.offline;
                        if body.get("record").is_some() {
                            self.pending_record = None;
                            self.notice = format!("Could not open that record: {e}. Your existing draft is kept.");
                            cx.notify();
                            continue;
                        }
                        if self.offline && self.snapshot.is_null(){self.restore(cx);}
                        self.notice = if self.offline {
                            if self.snapshot.is_null() {
                                "This app is not available offline. Open it once while connected."
                                    .into()
                            } else {
                                "Offline · cached data and decisions. Your draft stays on this device.".into()
                            }
                        } else {
                            e.to_string()
                        };
                        if body.get("event").is_some() {
                            self.retry = self
                                .store
                                .load(&self.slug)
                                .ok()
                                .and_then(|draft| draft.pending);
                            if self.retry.is_some() {
                                self.notice.push_str(" Pending submission is kept. Reconnect, then choose Send pending.");
                            } else {
                                self.notice
                                    .push_str(" Your draft is kept and can be corrected.");
                            }
                        }
                    }
                },
            }
            cx.notify();
        }
        if !self.busy && let Some(slug)=self.requested_slug.take(){
            if self.apps.iter().any(|a|a["slug"]==slug){self.open(slug,cx);}else{self.notice="That app is not in the available app catalog. Refresh the catalog and try again.".into();self.failed=true;}
        }
        if !self.busy && self.pending_record.is_some() {self.start_record(cx);}
        let actions = self.renderer.update(cx, |r, _| r.take_actions());
        for action in actions {
            if !self.busy {
                self.filters.cancel();
                self.action(action, cx);
                cx.notify();
            } else if !self.command_pending && self.queued_action.is_none() {
                self.queued_action = Some(action);
            }
        }
        if !self.busy {
            if let Some(action) = self.queued_action.take() {
                self.filters.cancel();
                self.action(action, cx);
                cx.notify();
            }
        }
        if !self.busy
            && self.recovery.foreground
            && !self.recovery.blocked
            && self.draft_error.is_none()
        {
            let input_due = !self.offline
                && !self.slug.is_empty()
                && self.retry.is_none()
                && (self.filters.poll(now).is_some()
                    || self.watch_due.is_some_and(|at| Instant::now() >= at));
            if input_due || self.recovery.due(now) {
                self.watch_due = None;
                if self.slug.is_empty() || self.retry.is_some() {
                    self.list();
                } else {
                    self.load(None, cx);
                }
                cx.notify();
            }
        }
    }

    fn watched(&self, data: &Value) -> Value {
        Value::Array(
            self.snapshot["watch"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(|p| data.pointer(p).cloned().unwrap_or(Value::Null))
                .collect(),
        )
    }
}
impl Render for PublishedApps {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let compact = window.viewport_size().width < px(620.);
        let requests = std::mem::take(&mut self.probe_pending);
        for request in requests {
            let result = if request.body["op"] == "inspect" {
                json!({"offline":self.offline,"foreground":self.recovery.foreground,"read_requests":self.read_requests,"retry_attempts":self.recovery.attempts,"history_depth":self.workspace.history.len(),"return_depth":self.return_stack.len(),"drafts":self.workspace.dirty(),"discard_confirmation":self.discard_confirm,"search":self.search_query(cx),"matching_apps":self.matching_apps(cx).len(),"fetched_at":self.workspace.fetched_at,"pending":self.retry,"draft_saved":self.draft_generation == self.saved_generation && self.draft_error.is_none() && self.saved_revision == self.renderer.read(cx).data_revision(),"draft_error":self.draft_error,"busy":self.busy,"notice":self.notice,"failed":self.failed,"slug":self.slug,"version":self.snapshot["version"],"latest_version":self.latest_version(),"release":self.snapshot["release"],"apps":self.apps,"tile_columns":tile_columns(self.picker_width),"surface":self.renderer.read(cx).probe_snapshot(),"render":self.renderer.read(cx).probe_render_status(cx)})
            } else if request.body["op"] == "back" {
                self.back(cx);
                json!({"ok":true})
            } else if request.body["op"] == "resume_draft" {
                self.resume_draft(request.body["key"].as_str().unwrap_or(""), cx);
                json!({"ok":true})
            } else if request.body["op"] == "discard_draft" {
                self.request_discard(request.body["key"].as_str().unwrap_or(""), cx);
                json!({"ok":true})
            } else if request.body["op"] == "confirm_discard" {
                if let Some(key)=self.discard_confirm.clone(){self.discard_draft(&key,cx);}
                json!({"ok":self.discard_confirm.is_none()})
            } else if request.body["op"] == "search" {
                self.ensure_search(window,cx);
                if let Some(input)=&self.search_input{input.update(cx,|s,cx|s.set_value(request.body["value"].as_str().unwrap_or("").to_owned(),window,cx));}
                json!({"ok":true})
            } else if request.body["op"] == "home" {
                self.home(cx);
                json!({"ok":self.slug.is_empty()})
            } else if request.body["op"] == "pins" {
                json!({"pins":self.pins.read(cx).items.iter().map(|p|p.value.clone()).collect::<Vec<_>>(),"busy":self.pins.read(cx).busy,"changing":self.pins.read(cx).changing,"notice":self.pins.read(cx).notice,"records":self.snapshot["records"]})
            } else if request.body["op"] == "pin_record" {
                self.pin_current(cx);json!({"ok":true})
            } else if request.body["op"] == "remove_pin" {
                self.pins.update(cx,|p,cx|p.change(json!({"operation":"remove","id":request.body["id"]}),cx));json!({"ok":true})
            } else if request.body["op"] == "refresh_pins" {
                self.pins.update(cx,|p,_|p.refresh());json!({"ok":true})
            } else if request.body["op"] == "open_record" {
                match self.open_record(request.body["reference"].clone(), request.body["ask"]==true, cx) {Ok(())=>json!({"ok":true}),Err(e)=>json!({"error":e.to_string()})}
            } else if request.body["op"] == "open_app" {
                let slug = request.body["slug"].as_str().unwrap_or("").to_owned();
                self.open(slug.clone(), cx);
                json!({"ok":self.slug == slug})
            } else if request.body["op"] == "send_pending" {
                if !self.busy {
                    if let Some(body) = self.retry.clone() {
                        self.send(body, cx);
                    }
                }
                json!({"queued":self.busy})
            } else if request.body["op"] == "assistant_inspect" {
                self.assistant_probe(cx)
            } else if request.body["op"] == "assistant_open" {
                self.assistant_open(cx);json!({"ok":true})
            } else if request.body["op"] == "assistant_close" {
                self.assistant.open=false;self.assistant.voice.cancel();json!({"ok":true})
            } else if request.body["op"] == "assistant_send" {
                if let Some(value)=request.body["improvement"].as_bool() {self.assistant.improvement=value;}
                if let Some(input)=&self.assistant.input{input.update(cx,|s,cx|s.set_value(request.body["message"].as_str().unwrap_or("").to_owned(),window,cx));}
                self.assistant_send(request.body["retry"]==true,window,cx);self.assistant_probe(cx)
            } else if request.body["op"] == "assistant_dictate" {
                self.assistant_dictate(cx);self.assistant_probe(cx)
            } else if request.body["op"] == "assistant_stop_audio" {
                self.assistant.voice.cancel();self.assistant_probe(cx)
            } else if request.body["op"] == "assistant_read" {
                self.assistant_read(request.body["index"].as_u64().unwrap_or(0) as usize,cx);self.assistant_probe(cx)
            } else if request.body["op"] == "assistant_apply" {
                match self.assistant_apply(request.body["index"].as_u64().unwrap_or(0) as usize,window,cx){Ok(())=>json!({"ok":true}),Err(e)=>json!({"error":e.to_string()})}
            } else if request.body["op"] == "update_app" {
                self.update_latest(cx);json!({"queued":self.busy,"notice":self.notice})
            } else if request.body["op"] == "refresh" {
                self.load(None, cx);
                json!({"queued":self.busy})
            } else if self.command_pending || self.retry.is_some() || self.queued_action.is_some() {
                json!({"error":"UI is busy"})
            } else {
                self.renderer
                    .update(cx, |r, cx| r.probe_command(&request.body, window, cx))
            };
            let _ = request.reply.send(result);
        }
        self.renderer.update(cx, |r, cx| {
            r.workspace_display_font = self.theme.font_display.clone();
            if r.refresh_pending != self.reactive_pending {
                r.refresh_pending = self.reactive_pending;
                cx.notify();
            }
            r.interaction_disabled =
                self.command_pending || self.retry.is_some() || self.queued_action.is_some();
        });
        if let Some((layout, frames)) = self.restore_layout.take() {
            self.renderer.update(cx, |r, cx| {
                r.restore_view_state(&layout);
                cx.notify();
            });
            if frames > 0 {
                self.restore_layout = Some((layout, frames - 1));
                cx.notify();
            }
        }
        let weak = cx.entity().downgrade();
        let columns = tile_columns(self.picker_width);
        let matching=self.matching_apps(cx);
        let header=self.shell_header(compact,window,cx);
        let pins_view = if self.slug.is_empty() {Some(self.pins_view(cx))} else {None};
        let recent_work = if self.slug.is_empty() {Some(self.recent_work(cx))} else {None};
        let assistant=self.assistant_view(window,cx);
        div().relative().size_full().text_size(px(14.)).flex().flex_col().gap_3()
            .capture_key_down(cx.listener(|s,event:&KeyDownEvent,_,cx|{
                if event.keystroke.key=="escape" && (s.menu_open || s.status_open || s.drafts_open) {s.back(cx);cx.stop_propagation();}
            }))
            .child(header)
            .when_some(pins_view,|s,v|s.child(v))
            .when_some(recent_work,|s,v|s.child(v))
            .when(self.drafts_open && !self.slug.is_empty(), |s| {
                let drafts=self.workspace.dirty();
                s.child(div().id("record-drafts-list").overflow_y_scroll().max_h(px(250.)).px_6().py_2()
                    .flex().flex_col().gap_2()
                    .child("Unfinished records · saved on this device")
                    .when_some(self.discard_confirm.clone(),|s,key| {
                        let label=self.workspace.records.get(&key).map(|r|if r.label.is_empty(){r.id.to_string()}else{r.label.clone()}).unwrap_or_default();
                        s.child(div().p_3().rounded_lg().bg(self.theme.card).flex().flex_col().gap_2()
                            .child(format!("Discard unsent changes to {label}?"))
                            .child(div().flex().gap_3()
                                .child(gpui_component::button::Button::new("confirm-discard").label("Discard changes").on_click(cx.listener(move|s,_,_,cx|s.discard_draft(&key,cx))))
                                .child(gpui_component::button::Button::new("cancel-discard").label("Keep editing").on_click(cx.listener(|s,_,_,cx|{s.discard_confirm=None;cx.notify();})))))
                    })
                    .when(drafts.is_empty(),|s|s.child("No unfinished record drafts."))
                    .children(drafts.into_iter().map(|draft| {
                        let key=draft["key"].as_str().unwrap_or("").to_owned();let discard=key.clone();
                        let record=draft["record"].as_str().map(str::to_owned).unwrap_or_else(||draft["record"].to_string());
                        let label=draft["label"].as_str().unwrap_or(&record).to_owned();
                        div().flex().items_center().gap_3().border_b_1().border_color(self.theme.line)
                            .child(div().id(SharedString::from(format!("draft-{key}"))).flex_1().min_h(px(44.)).flex().items_center().cursor_pointer()
                                .child(label)
                                .on_click(cx.listener(move|this,_,_,cx|this.resume_draft(&key,cx))))
                            .child(div().id(SharedString::from(format!("discard-{discard}"))).min_h(px(44.)).px_2().flex().items_center().cursor_pointer().text_color(self.theme.muted)
                                .child("Discard")
                                .on_click(cx.listener(move|this,_,_,cx|this.request_discard(&discard,cx))))
                    })))
            })
            .when(self.slug.is_empty(), |s| {
                s.child(
                    div()
                        .id("company-apps-scroll")
                        .relative()
                        .flex_1()
                        .min_h_0()
                        .overflow_y_scroll()
                        .px_6()
                        .pb_6()
                        .when(matching.is_empty(),|s|s.child(div().py_6().flex().flex_col().gap_2()
                            .child(if self.busy && self.apps.is_empty(){"Loading your apps…"}else if self.apps.is_empty(){"No company apps available yet"}else{"No apps match your search"})
                            .child(div().text_color(self.theme.muted).child(if self.apps.is_empty(){"Published apps shared with your account will appear here."}else{"Try another name or clear the search."}))))
                        .child(div().grid().grid_cols(columns).gap_3().w_full().children(
                            matching.iter().map(|a| {
                                let slug = a["slug"].as_str().unwrap_or("").to_owned();
                                let name = a["name"].as_str().unwrap_or(&slug);
                                let initials: String = name
                                    .split_whitespace()
                                    .filter(|s| !s.eq_ignore_ascii_case("demo"))
                                    .filter_map(|s| s.chars().next())
                                    .take(2)
                                    .collect();
                                let color = self.theme.charts[slug.bytes().fold(0usize, |n, b| {
                                    n.wrapping_mul(31).wrapping_add(b as usize)
                                }) % self.theme.charts.len()];
                                div()
                                    .id(SharedString::from(slug.clone()))
                                    .min_w_0()
                                    .min_h(px(214.))
                                    .p_4()
                                    .rounded_xl()
                                    .flex()
                                    .flex_col()
                                    .gap_3()
                                    .bg(self.theme.card)
                                    .border_1()
                                    .border_color(self.theme.line)
                                    .hover(|s| s.bg(self.theme.hover).border_color(color))
                                    .cursor_pointer()
                                    .child(
                                        div()
                                            .flex()
                                            .items_center()
                                            .justify_between()
                                            .child(
                                                div()
                                                    .size(px(42.))
                                                    .rounded_lg()
                                                    .bg(color)
                                                    .text_color(self.theme.bg)
                                                    .text_size(px(18.))
                                                    .font_family(self.theme.font_display.clone())
                                                    .flex()
                                                    .items_center()
                                                    .justify_center()
                                                    .child(initials),
                                            )
                                            .child(div().text_color(self.theme.muted).child("↗")),
                                    )
                                    .child(
                                        div()
                                            .font_family(self.theme.font_display.clone())
                                            .text_size(px(17.))
                                            .line_clamp(2)
                                            .child(name.to_owned()),
                                    )
                                    .child(
                                        div()
                                            .flex_shrink_0()
                                            .text_size(px(12.))
                                            .text_color(self.theme.muted)
                                            .line_clamp(3)
                                            .child(
                                                a["description"].as_str().unwrap_or("").to_owned(),
                                            ),
                                    )
                                    .child(div().flex_1().min_h(px(4.)))
                                    .child(
                                        div().text_size(px(10.)).text_color(color).child(
                                            a["app_kind"]
                                                .as_str()
                                                .unwrap_or("Company app")
                                                .replace('_', " "),
                                        ),
                                    )
                                    .when(self.offline && a["cached"]==true && a["draft_status"].is_null(),|s|s.child(div().text_size(px(11.)).text_color(self.theme.muted).child("Available offline")))
                                    .when_some(a["draft_status"].as_str(), |s, status| {
                                        s.child(
                                            div()
                                                .text_size(px(11.))
                                                .text_color(self.theme.muted)
                                                .child(status.to_owned()),
                                        )
                                    })
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.open(slug.clone(), cx)
                                    }))
                            }),
                        ))
                        .child(
                            canvas(
                                move |bounds, _, cx| {
                                    let _ = weak.update(cx, |this, cx| {
                                        let width = f32::from(bounds.size.width);
                                        if (this.picker_width - width).abs() > 1. {
                                            this.picker_width = width;
                                            cx.notify();
                                        }
                                    });
                                },
                                |_, _, _, _| {},
                            )
                            .absolute()
                            .top_0()
                            .left_0()
                            .w_full()
                            .h(px(1.)),
                        ),
                )
            })
            .when(!self.slug.is_empty(), |s| {
                s.child(
                    div()
                        .relative()
                        .w_full()
                        .flex_1()
                        .min_h_0()
                        .when(!self.snapshot.is_null(), |s| s.child(self.renderer.clone()))
                        .when(self.snapshot.is_null(), |s| s.child(div().p_6().text_color(self.theme.muted)
                            .child(if self.failed {"Open this app in the browser, or return to Apps."} else {"Opening app…"}))),
                )
            })
            .when_some(assistant,|s,view|s.child(view))
    }
}

fn tile_columns(width: f32) -> u16 {
    if width < 340. {
        1
    } else {
        ((width - 48.) / 210.).floor().clamp(2., 6.) as u16
    }
}

fn action_for_body(body: &Value) -> Value {
    json!({"name":body["event"],"context":body["context"]})
}
fn restore_names(snapshot: &mut Value, files: &std::collections::BTreeMap<String, Value>) {
    let nodes = snapshot["messages"][1]["updateComponents"]["components"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let model = &mut snapshot["messages"][2]["updateDataModel"]["value"];
    for node in nodes.iter().filter(|n| n["component"] == "CAttachments") {
        let ids = node["value"]["path"]
            .as_str()
            .and_then(|p| model.pointer(p))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        if let Some(names) = node["names"]["path"]
            .as_str()
            .and_then(|p| model.pointer_mut(p))
        {
            if names.is_null() {
                *names = json!({});
            }
            for id in ids.iter().filter_map(Value::as_str) {
                if let Some(file) = files.get(id) {
                    names[id] = file["name"].clone();
                }
            }
        }
    }
}

include!("published_shell.rs");

include!("published_assistant.rs");
include!("published_records.rs");

include!("published_versions.rs");
include!("published_links.rs");
