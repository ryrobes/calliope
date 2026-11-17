//! Native Meaning projection. Background reads; stable local geometry; server pins.
use crate::{
    api::{Api, Connection},
    model::{display, short},
    theme::{CanvasTheme, with_alpha},
};
use gpui::{prelude::*, *};
use gpui_component::input::{Input, InputEvent, InputState};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    collections::{HashMap, HashSet},
    time::{Duration, Instant},
};

pub fn text(v: &Value, key: &str) -> String {
    v[key].as_str().unwrap_or("").to_owned()
}
fn items(v: &Value, key: &str) -> Vec<Value> {
    v[key].as_array().cloned().unwrap_or_default()
}
pub fn number(v: &Value) -> String {
    if let Some(n) = v.as_f64() {
        if n.abs() >= 1_000_000. {
            format!("{:.2}M", n / 1_000_000.)
        } else if n.abs() >= 10_000. {
            format!("{:.1}k", n / 1000.)
        } else if n.fract() == 0. {
            format!("{n:.0}")
        } else {
            format!("{n:.2}")
        }
    } else {
        short(&display(v), 45)
    }
}

#[derive(Clone)]
pub enum Action {
    Open(Value),
    Ask(Value),
}
impl EventEmitter<Action> for Meaning {}

enum Update {
    Graph(u64, anyhow::Result<Value>),
    Inspection(u64, anyhow::Result<Value>),
    Pins(anyhow::Result<Vec<Value>>),
    Changed(anyhow::Result<Value>),
    SaveFailed(String),
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Layout {
    pub positions: HashMap<String, [f32; 2]>,
    pub pan: [f32; 2],
    pub zoom: f32,
    pub shown: Option<HashSet<String>>,
    pub catalog_limit: usize,
}
impl Default for Layout {
    fn default() -> Self {
        Self {
            positions: HashMap::new(),
            pan: [30., 30.],
            zoom: 1.,
            shown: None,
            catalog_limit: 12,
        }
    }
}
#[derive(Clone)]
enum Drag {
    Pan(Point<Pixels>, [f32; 2]),
    Node(String, Point<Pixels>, [f32; 2]),
}

pub struct Meaning {
    pub theme: CanvasTheme,
    pub active: bool,
    pub pins: Vec<Value>,
    pub pins_loaded: bool,
    pub notice: Option<String>,
    pub graph: Value,
    area: String,
    selected: String,
    selected_edge: Option<String>,
    inspection_key: String,
    inspection: Value,
    inspection_request: u64,
    inspection_busy: bool,
    inspection_error: Option<String>,
    detail_expanded: bool,
    visible: HashSet<String>,
    layouts: HashMap<String, Layout>,
    writer: crate::deferred_save::DeferredSave<HashMap<String, Layout>>,
    viewport: Bounds<Pixels>,
    drag: Option<Drag>,
    fit_pending: bool,
    busy: bool,
    pins_busy: bool,
    pins_again: bool,
    changing: bool,
    stale: bool,
    generation: u64,
    limit: usize,
    read_at: Instant,
    pins_at: Instant,
    api: Api,
    tx: crate::mailbox::Sender<Update>,
    rx: std::sync::mpsc::Receiver<Update>,
    probe: Option<std::sync::mpsc::Receiver<crate::business_probe::Request>>,
    search: Entity<InputState>,
    _search_subscription: Subscription,
    catalog_open: bool,
    clear_catalog_search: bool,
    catalog_kind: String,
    catalog_search: Entity<InputState>,
    _catalog_subscription: Subscription,
}

impl Meaning {
    pub fn new(
        config: Connection,
        theme: CanvasTheme,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Self {
        let path = crate::app_drafts::account_root(&config).join("meaning-layout-v2.json");
        let layouts: HashMap<String, Layout> = std::fs::read(&path)
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or_default();
        let (tx, rx, wake) = crate::mailbox::channel();
        let probe = std::env::var_os("CALLIOPE_UI_PROBE").and_then(|p| {
            crate::business_probe::start_at(
                std::path::PathBuf::from(format!("{}.meaning", p.to_string_lossy())),
                tx.notifier(),
            )
        });
        let save_tx = tx.clone();
        let writer = crate::deferred_save::DeferredSave::new(move |v: HashMap<String, Layout>| {
            let result = serde_json::to_vec(&v)
                .map_err(|e| e.to_string())
                .and_then(|bytes| {
                    crate::app_drafts::atomic(&path, &bytes).map_err(|e| e.to_string())
                });
            if let Err(e) = result {
                let _ = save_tx.send(Update::SaveFailed(e));
            }
        });
        let search = cx.new(|cx| InputState::new(window, cx).placeholder("Find a business area…"));
        let search_subscription = cx.subscribe(&search, |s, _, _: &InputEvent, cx| {
            if s.area.is_empty() {
                s.filter_areas(cx);
                s.fit_pending = true;
                cx.notify();
            }
        });
        let catalog_search =
            cx.new(|cx| InputState::new(window, cx).placeholder("Find a loaded object…"));
        let catalog_subscription =
            cx.subscribe(&catalog_search, |_, _, _: &InputEvent, cx| cx.notify());
        let fit_pending = !layouts.contains_key("");
        let mut this = Self {
            theme,
            active: false,
            pins: vec![],
            pins_loaded: false,
            notice: None,
            graph: Value::Null,
            area: String::new(),
            selected: String::new(),
            selected_edge: None,
            inspection_key: String::new(),
            inspection: Value::Null,
            inspection_request: 0,
            inspection_busy: false,
            inspection_error: None,
            detail_expanded: false,
            visible: HashSet::new(),
            layouts,
            writer,
            viewport: Bounds::default(),
            drag: None,
            fit_pending,
            busy: false,
            pins_busy: false,
            pins_again: false,
            changing: false,
            stale: false,
            generation: 0,
            limit: 12,
            read_at: Instant::now() - Duration::from_secs(70),
            pins_at: Instant::now() - Duration::from_secs(70),
            api: Api::new(config).expect("validated connection"),
            tx,
            rx,
            probe,
            search,
            _search_subscription: search_subscription,
            catalog_open: false,
            clear_catalog_search: true,
            catalog_kind: String::new(),
            catalog_search,
            _catalog_subscription: catalog_subscription,
        };
        cx.spawn(async move |view, cx| {
            loop {
                smol::future::or(
                    async {
                        let _ = wake.recv().await;
                    },
                    async {
                        smol::Timer::after(Duration::from_secs(1)).await;
                    },
                )
                .await;
                if view.update(cx, |v, cx| v.tick(cx)).is_err() {
                    break;
                }
            }
        })
        .detach();
        this.refresh_pins();
        this
    }
    fn layout(&self) -> Layout {
        self.layouts.get(&self.area).cloned().unwrap_or_default()
    }
    fn filter_areas(&mut self, cx: &Context<Self>) {
        let q = self.search.read(cx).value().to_lowercase();
        self.visible = items(&self.graph, "nodes")
            .iter()
            .filter(|n| {
                q.is_empty()
                    || format!("{} {}", text(n, "title"), text(n, "description"))
                        .to_lowercase()
                        .contains(&q)
            })
            .map(|n| text(n, "id"))
            .collect();
    }
    fn save(&self) {
        self.writer.submit(self.layouts.clone());
    }
    pub fn open_reference(&mut self, r: &Value, cx: &mut Context<Self>) {
        if r["kind"] != "meaning" {
            return;
        }
        self.area = text(r, "area");
        self.selected = text(r, "node");
        self.selected_edge = None;
        self.visible.clear();
        self.graph = Value::Null;
        self.fit_pending = true;
        self.limit = self.layout().catalog_limit.clamp(12, 40);
        self.catalog_open = false;
        self.clear_catalog_search = true;
        self.catalog_kind.clear();
        self.load(cx);
    }
    fn open_area(&mut self, area: String, cx: &mut Context<Self>) {
        if self.area != area {
            self.graph = Value::Null;
            self.visible.clear();
            self.selected = if area.is_empty() {
                String::new()
            } else {
                format!("area:{area}")
            };
            self.fit_pending = !self.layouts.contains_key(&area);
        }
        self.area = area;
        self.selected_edge = None;
        self.limit = self.layout().catalog_limit.clamp(12, 40);
        self.catalog_open = false;
        self.clear_catalog_search = true;
        self.catalog_kind.clear();
        self.load(cx);
    }
    pub fn refresh(&mut self, cx: &mut Context<Self>) {
        self.load(cx);
        self.refresh_pins();
    }
    fn load(&mut self, cx: &mut Context<Self>) {
        self.generation += 1;
        let generation = self.generation;
        self.busy = true;
        self.notice = None;
        let (api, tx, area, focus, limit) = (
            self.api.clone(),
            self.tx.clone(),
            self.area.clone(),
            self.selected.clone(),
            self.limit,
        );
        std::thread::spawn(move || {
            let result = (|| {
                let mut u = api.url("/api/calliope/meaning/graph")?;
                u.query_pairs_mut()
                    .append_pair("area", &area)
                    .append_pair("focus", &focus)
                    .append_pair("limit", &limit.to_string());
                api.get(u.as_str())
            })();
            let _ = tx.send(Update::Graph(generation, result));
        });
        cx.notify();
    }
    pub fn refresh_pins(&mut self) {
        if self.pins_busy {
            self.pins_again = true;
            return;
        }
        self.pins_busy = true;
        let (api, tx) = (self.api.clone(), self.tx.clone());
        std::thread::spawn(move || {
            let result = (|| -> anyhow::Result<Vec<Value>> {
                let list = api.get("/api/calliope/meaning/pins")?;
                let mut output = vec![];
                for chunk in items(&list, "pins").chunks(4) {
                    std::thread::scope(|scope| {
                        let handles: Vec<_> = chunk
                            .iter()
                            .map(|p| {
                                let api = &api;
                                scope.spawn(move || {
                                    let mut pin = p.clone();
                                    match api.post("/api/calliope/meaning/object", &p["reference"])
                                    {
                                        Ok(v) => {
                                            pin["node"] = v["node"].clone();
                                            pin["as_of"] = v["as_of"].clone();
                                        }
                                        Err(e) => {
                                            pin["error"] = json!(e.to_string());
                                            pin["retryable"] =
                                                json!(crate::app_connection::retryable(&e));
                                        }
                                    }
                                    pin
                                })
                            })
                            .collect();
                        for h in handles {
                            if let Ok(v) = h.join() {
                                output.push(v);
                            }
                        }
                    });
                }
                Ok(output)
            })();
            let _ = tx.send(Update::Pins(result));
        });
    }
    pub fn change_pin(&mut self, body: Value, cx: &mut Context<Self>) {
        if self.changing {
            return;
        }
        self.changing = true;
        self.notice = None;
        let (api, tx) = (self.api.clone(), self.tx.clone());
        std::thread::spawn(move || {
            let _ = tx.send(Update::Changed(
                api.post("/api/calliope/meaning/pins", &body),
            ));
        });
        cx.notify();
    }
    fn prepare_layout(&mut self) {
        let nodes = items(&self.graph, "nodes");
        let ids: HashSet<_> = nodes.iter().map(|n| text(n, "id")).collect();
        self.visible.retain(|id| ids.contains(id));
        if !ids.contains(&self.selected) {
            self.selected.clear();
        }
        if self.visible.is_empty() {
            self.visible = if self.area.is_empty() {
                initial_visible(&nodes, true)
            } else {
                self.layout()
                    .shown
                    .unwrap_or_else(|| initial_visible(&nodes, false))
                    .into_iter()
                    .filter(|id| ids.contains(id))
                    .collect()
            };
        }
        if !self.selected.is_empty() {
            self.visible.insert(self.selected.clone());
        }
        if self.selected.is_empty() {
            self.selected = nodes.first().map(|n| text(n, "id")).unwrap_or_default();
        }
        let shown = self.display_nodes();
        let layout = self.layouts.entry(self.area.clone()).or_default();
        if !layout.zoom.is_finite()
            || layout.zoom < 0.25
            || layout.zoom > 1.6
            || layout.pan.iter().any(|p| !p.is_finite())
        {
            layout.zoom = 1.;
            layout.pan = [30., 30.];
        }

        for (id, mut p) in positions(&shown, self.area.is_empty()) {
            if !layout.positions.contains_key(&id) {
                while layout
                    .positions
                    .values()
                    .any(|old| (old[0] - p[0]).abs() < 301. && (old[1] - p[1]).abs() < 171.)
                {
                    p[1] += 215.;
                }
            }
            let old = layout.positions.entry(id).or_insert(p);
            if old.iter().any(|v| !v.is_finite() || v.abs() > 1_000_000.) {
                *old = p;
            }
        }
    }
    fn remember_view(&mut self) {
        if !self.area.is_empty() {
            let l = self.layouts.entry(self.area.clone()).or_default();
            l.shown = Some(self.visible.clone());
            l.catalog_limit = self.limit;
            self.save();
        }
    }
    fn fit(&mut self) {
        let mut l = self.layout();
        let display_visible = self.display_visible();
        let ps: Vec<_> = l
            .positions
            .iter()
            .filter(|(id, _)| display_visible.contains(*id))
            .map(|(_, p)| *p)
            .collect();
        if ps.is_empty() {
            return;
        }
        let x = ps.iter().map(|p| p[0]).fold(f32::INFINITY, f32::min);
        let y = ps.iter().map(|p| p[1]).fold(f32::INFINITY, f32::min);
        let r = ps
            .iter()
            .map(|p| p[0] + 300.)
            .fold(f32::NEG_INFINITY, f32::max);
        let b = ps
            .iter()
            .map(|p| p[1] + 170.)
            .fold(f32::NEG_INFINITY, f32::max);
        l.zoom = ((f32::from(self.viewport.size.width) - 70.).max(300.) / (r - x))
            .min((f32::from(self.viewport.size.height) - 60.).max(250.) / (b - y))
            .clamp(0.25, 1.05);
        l.pan = [
            (f32::from(self.viewport.size.width) - (r - x) * l.zoom) / 2. - x * l.zoom,
            (f32::from(self.viewport.size.height) - (b - y) * l.zoom) / 2. - y * l.zoom,
        ];
        self.layouts.insert(self.area.clone(), l);
        self.fit_pending = false;
        self.save();
    }
    fn zoom(&mut self, next: f32, anchor: Point<Pixels>) {
        let mut l = self.layout();
        let z = next.clamp(0.25, 1.6);
        let p = anchor - self.viewport.origin;
        l.pan = [
            f32::from(p.x) - (f32::from(p.x) - l.pan[0]) * z / l.zoom,
            f32::from(p.y) - (f32::from(p.y) - l.pan[1]) * z / l.zoom,
        ];
        l.zoom = z;
        self.layouts.insert(self.area.clone(), l);
        self.save();
    }
    fn hidden_neighbors(&self) -> Vec<String> {
        let mut seen = self.visible.clone();
        items(&self.graph, "edges")
            .iter()
            .filter_map(|e| {
                let other = if e["from"] == self.selected {
                    text(e, "to")
                } else if e["to"] == self.selected {
                    text(e, "from")
                } else {
                    return None;
                };
                if seen.insert(other.clone()) {
                    Some(other)
                } else {
                    None
                }
            })
            .collect()
    }
    fn expand(&mut self, cx: &mut Context<Self>) {
        // Reveal the complete recorded neighborhood of this object.
        for id in self.hidden_neighbors().into_iter() {
            self.visible.insert(id);
        }
        self.prepare_layout();
        self.remember_view();
        self.fit_pending = true;
        cx.notify();
    }
    fn show_node(&mut self, id: String, cx: &mut Context<Self>) {
        if id.starts_with("group:") {
            self.expand_group(&id, cx);
            return;
        }
        if !items(&self.graph, "nodes").iter().any(|n| n["id"] == id) {
            return;
        }
        self.selected = id.clone();
        self.selected_edge = None;
        self.visible.insert(id.clone());
        self.prepare_layout();
        let mut l = self.layout();
        if let Some(p) = l.positions.get(&id) {
            l.pan = [
                f32::from(self.viewport.size.width) / 2. - (p[0] + 150.) * l.zoom,
                f32::from(self.viewport.size.height) / 2. - (p[1] + 85.) * l.zoom,
            ];
        }
        self.layouts.insert(self.area.clone(), l);
        self.fit_pending = false;
        self.remember_view();
        cx.notify();
    }
    fn hide_node(&mut self, id: &str, cx: &mut Context<Self>) {
        if id == format!("area:{}", self.area) {
            return;
        }
        self.visible.remove(id);
        if self.selected == id {
            self.selected = format!("area:{}", self.area);
            self.selected_edge = None;
        }
        self.remember_view();
        cx.notify();
    }
    fn reset_view(&mut self, cx: &mut Context<Self>) {
        self.visible = initial_visible(&items(&self.graph, "nodes"), self.area.is_empty());
        let shown = self.display_nodes();
        self.layouts.entry(self.area.clone()).or_default().positions =
            positions(&shown, self.area.is_empty());
        self.selected = format!("area:{}", self.area);
        self.selected_edge = None;
        self.prepare_layout();
        self.remember_view();
        self.fit_pending = true;
        cx.notify();
    }
    fn load_catalog(&mut self, cx: &mut Context<Self>) {
        if !self.busy && self.graph["more"] == true && self.limit < 40 {
            self.limit = (self.limit + 12).min(40);
            self.load(cx);
        }
    }
    fn selected_node(&self) -> Option<Value> {
        items(&self.graph, "nodes")
            .into_iter()
            .find(|n| text(n, "id") == self.selected)
    }
    pub fn discussion(&self, node: &Value) -> Value {
        let loaded = self.inspection["node"]["reference"] == node["reference"];
        let detail = if loaded {
            self.inspection["detail"].clone()
        } else {
            Value::Null
        };
        let selected = if loaded {
            &self.inspection["node"]
        } else {
            node
        };
        json!({"kind":"meaning_neighborhood","detail":detail,"selected":selected,"relationships":items(&self.graph,"edges").into_iter().filter(|e|e["from"]==node["id"]||e["to"]==node["id"]).collect::<Vec<_>>(),"observed_at":self.graph["as_of"],"provisional":true})
    }
    fn tick(&mut self, cx: &mut Context<Self>) {
        while let Ok(update) = self.rx.try_recv() {
            match update {
                Update::Graph(generation, result) if generation == self.generation => {
                    self.busy = false;
                    self.read_at = Instant::now();
                    match result {
                        Ok(value) => {
                            if self.graph.is_null() && self.layout().shown.is_none() {
                                self.fit_pending = true;
                            }
                            self.graph = value;
                            self.stale = false;
                            self.notice = None;
                            self.prepare_layout();
                            self.remember_view();
                            if self.area.is_empty() {
                                self.filter_areas(cx);
                            }
                        }
                        Err(e) => {
                            self.stale = true;
                            self.notice = Some(e.to_string());
                            if !crate::app_connection::retryable(&e) {
                                self.graph = Value::Null;
                                self.visible.clear();
                            }
                        }
                    }
                    cx.notify();
                }
                Update::Graph(_, _) => {}
                Update::Inspection(request, result) if request == self.inspection_request => {
                    self.inspection_busy = false;
                    match result {
                        Ok(value) => self.inspection = value,
                        Err(e) => self.inspection_error = Some(e.to_string()),
                    }
                    cx.notify();
                }
                Update::Inspection(_, _) => {}
                Update::SaveFailed(e) => {
                    self.notice = Some(format!("Map placement could not be saved: {e}"));
                    cx.notify();
                }
                Update::Pins(result) => {
                    self.pins_busy = false;
                    self.pins_at = Instant::now();
                    match result {
                        Ok(mut pins) => {
                            self.pins_loaded = true;
                            for p in &mut pins {
                                if p["retryable"] == true {
                                    if let Some(old) = self.pins.iter().find(|o| o["id"] == p["id"])
                                    {
                                        p["node"] = old["node"].clone();
                                    }
                                }
                            }
                            self.pins = pins;
                        }
                        Err(e) => {
                            if !crate::app_connection::retryable(&e) {
                                self.pins.clear();
                            } else {
                                for p in &mut self.pins {
                                    p["error"] = json!("Offline · last received summary");
                                }
                            }
                            self.notice = Some(e.to_string());
                        }
                    }
                    if self.pins_again {
                        self.pins_again = false;
                        self.refresh_pins();
                    }
                    cx.notify();
                }
                Update::Changed(result) => {
                    self.changing = false;
                    match result {
                        Ok(v) => {
                            if let Some(id) = v["removed"].as_str() {
                                self.pins.retain(|p| p["id"] != id);
                            }
                            self.refresh_pins();
                        }
                        Err(e) => self.notice = Some(format!("Pin change was not confirmed: {e}")),
                    }
                    cx.notify();
                }
            }
        }
        let requests: Vec<_> = self
            .probe
            .as_ref()
            .map(|r| r.try_iter().collect())
            .unwrap_or_default();
        for r in requests {
            let out = match r.body["op"].as_str().unwrap_or("") {
                "inspect" => {
                    json!({"graph":self.graph,"display_nodes":self.display_nodes(),"display_edges":self.display_edges(),"area":self.area,"selected":self.selected,"busy":self.busy,"notice":self.notice,"visible":self.visible,"layout":self.layout(),"pins":self.pins,"pins_busy":self.pins_busy,"changing":self.changing,"catalog_open":self.catalog_open,"catalog_kind":self.catalog_kind,"catalog_matches":self.catalog_nodes(cx),"hidden_neighbors":self.hidden_neighbors(),"edge_labels":self.edge_labels(),"inspection":self.inspection,"inspection_busy":self.inspection_busy,"inspection_error":self.inspection_error,"detail_expanded":self.detail_expanded,"viewport":{"x":f32::from(self.viewport.origin.x),"y":f32::from(self.viewport.origin.y),"width":f32::from(self.viewport.size.width),"height":f32::from(self.viewport.size.height)}})
                }
                "area" => {
                    self.open_area(text(&r.body, "area"), cx);
                    json!({"ok":true})
                }
                "select" => {
                    let id = text(&r.body, "id");
                    if items(&self.graph, "nodes").iter().any(|n| n["id"] == id) {
                        self.selected = id;
                        self.selected_edge = None;
                        self.visible.insert(self.selected.clone());
                        self.prepare_layout();
                        self.remember_view();
                        cx.notify();
                        json!({"ok":true})
                    } else {
                        json!({"error":"Unknown node"})
                    }
                }
                "detail" => {
                    self.detail_expanded = r.body["expanded"] == true;
                    cx.notify();
                    json!({"ok":true})
                }
                "evidence" => {
                    let id = text(&r.body, "id");
                    self.selected_edge = items(&self.graph, "edges")
                        .iter()
                        .find(|e| {
                            e["id"] == id
                                && (e["from"] == self.selected || e["to"] == self.selected)
                        })
                        .map(|_| id);
                    cx.notify();
                    json!({"ok":true})
                }
                "catalog" => {
                    self.catalog_open = r.body["open"] != false;
                    self.catalog_kind = text(&r.body, "kind");
                    cx.notify();
                    json!({"ok":true})
                }
                "load_catalog" => {
                    self.load_catalog(cx);
                    json!({"ok":true})
                }
                "show" => {
                    self.show_node(text(&r.body, "id"), cx);
                    json!({"ok":true})
                }
                "hide" => {
                    self.hide_node(&text(&r.body, "id"), cx);
                    json!({"ok":true})
                }
                "reset_view" => {
                    self.reset_view(cx);
                    json!({"ok":true})
                }
                "expand" => {
                    self.expand(cx);
                    json!({"ok":true})
                }
                "fit" => {
                    self.fit();
                    cx.notify();
                    json!({"ok":true})
                }
                "pin" => {
                    if let Some(n) = self.selected_node() {
                        self.change_pin(json!({"operation":"pin","reference":n["reference"]}), cx);
                    }
                    json!({"ok":true})
                }
                "remove_pin" => {
                    self.change_pin(json!({"operation":"remove","id":r.body["id"]}), cx);
                    json!({"ok":true})
                }
                "context" => self
                    .selected_node()
                    .map(|n| self.discussion(&n))
                    .unwrap_or(Value::Null),
                "ask" => {
                    if let Some(n) = self.selected_node() {
                        cx.emit(Action::Ask(self.discussion(&n)));
                    }
                    json!({"ok":true})
                }
                "open" => {
                    if let Some(n) = self.selected_node() {
                        cx.emit(Action::Open(n));
                    }
                    json!({"ok":true})
                }
                _ => json!({"error":"Unknown Meaning probe operation"}),
            };
            let _ = r.reply.send(out);
        }
        if self.active && !self.busy && self.read_at.elapsed() > Duration::from_secs(60) {
            self.load(cx);
        }
        if !self.pins_busy && self.pins_at.elapsed() > Duration::from_secs(60) {
            self.refresh_pins();
        }
    }
}

fn initial_visible(nodes: &[Value], overview: bool) -> HashSet<String> {
    let mut counts: HashMap<String, usize> = HashMap::new();
    for n in nodes {
        *counts.entry(text(n, "kind")).or_default() += 1;
    }
    nodes
        .iter()
        .filter(|n| {
            overview
                || n["kind"] == "area"
                || (!matches!(text(n, "kind").as_str(), "idea" | "breakdown")
                    && counts[&text(n, "kind")] <= 6)
        })
        .map(|n| text(n, "id"))
        .collect()
}
fn positions(nodes: &[Value], overview: bool) -> HashMap<String, [f32; 2]> {
    let columns = (nodes.len() as f32).sqrt().ceil().max(1.) as usize;
    let mut counts: HashMap<usize, usize> = HashMap::new();
    nodes
        .iter()
        .enumerate()
        .map(|(i, n)| {
            let p = if overview {
                [(i % columns) as f32 * 365., (i / columns) as f32 * 215.]
            } else {
                let column = match text(
                    n,
                    if n["kind"] == "group" {
                        "group_kind"
                    } else {
                        "kind"
                    },
                )
                .as_str()
                {
                    "area" => 0,
                    "relation" => 1,
                    "cube" => 2,
                    "metric" | "idea" => 3,
                    _ => 4,
                };
                let row = counts.entry(column).or_default();
                let p = [
                    column as f32 * 390.,
                    if column == 0 {
                        180.
                    } else {
                        *row as f32 * 215.
                    },
                ];
                *row += 1;
                p
            };
            (text(n, "id"), p)
        })
        .collect()
}

include!("meaning_groups.rs");
include!("meaning_geometry.rs");
include!("meaning_catalog.rs");
include!("meaning_view.rs");
include!("meaning_details.rs");

#[cfg(test)]
mod tests {
    use super::*;
    #[::core::prelude::v1::test]
    fn neighborhoods_start_bounded_and_positions_do_not_overlap() {
        let mut nodes = vec![json!({"id":"area:x","kind":"area"})];
        for kind in ["relation", "metric", "artifact"] {
            for i in 0..20 {
                nodes.push(json!({"id":format!("{kind}:{i}"),"kind":kind}));
            }
        }
        assert_eq!(initial_visible(&nodes, false).len(), 1);
        let ps = positions(&nodes, false);
        let unique: HashSet<_> = ps.values().map(|p| (p[0] as i32, p[1] as i32)).collect();
        assert_eq!(unique.len(), nodes.len());
    }
    #[::core::prelude::v1::test]
    fn starting_view_uses_distinct_formulas_without_merging_objects() {
        let nodes = vec![
            json!({"id":"area:x","kind":"area"}),
            json!({"id":"metric:cost","kind":"metric","display_group":"sum-cost"}),
            json!({"id":"metric:cost-alias","kind":"metric","display_group":"sum-cost"}),
            json!({"id":"metric:units","kind":"metric","display_group":"sum-units"}),
            json!({"id":"metric:price","kind":"metric","display_group":"avg-price"}),
            json!({"id":"idea:price","kind":"idea"}),
        ];
        let visible = initial_visible(&nodes, false);
        assert!(
            visible.contains("metric:cost")
                && visible.contains("metric:units")
                && visible.contains("metric:price")
        );
        assert!(visible.contains("metric:cost-alias") && !visible.contains("idea:price"));
        assert_eq!(nodes.len(), 6);
    }
    #[::core::prelude::v1::test]
    fn legacy_geometry_keeps_positions_and_new_curation_round_trips() {
        let mut layout: Layout = serde_json::from_value(
            json!({"positions":{"metric:cost":[17.,32.]},"zoom":0.8,"pan":[20.,40.]}),
        )
        .unwrap();
        assert_eq!(layout.positions["metric:cost"], [17., 32.]);
        assert!(layout.shown.is_none());
        assert_eq!(layout.catalog_limit, 12);
        layout.shown = Some(HashSet::from(["metric:cost".to_owned()]));
        layout.catalog_limit = 24;
        let restored: Layout =
            serde_json::from_value(serde_json::to_value(&layout).unwrap()).unwrap();
        assert_eq!(restored.shown, layout.shown);
        assert_eq!(restored.catalog_limit, 24);
    }
    #[::core::prelude::v1::test]
    fn small_values_do_not_look_like_missing_or_unscaled_values() {
        assert_eq!(number(&json!(0)), "0");
        assert_eq!(number(&json!(0.84)), "0.84");
        assert_eq!(number(&json!(12000)), "12.0k");
    }
}
