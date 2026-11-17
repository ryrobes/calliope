//! Stage items as A2UI documents (docs/STAGE_A2UI_PLAN.md), drawn by the shared
//! renderer: one card host for the phone and desktop Canvas. The server
//! projects each item (`POST /sessions/{id}/views`); the host keeps its frame
//! (title, pins, focus) and a card owns its body.
use crate::{api::Api, theme::CanvasTheme};
use a2ui_base::{catalog::Catalog, protocol::server_to_client::A2uiMessage};
use a2ui_gpui::GpuiApp;
use gpui::{prelude::*, *};
use serde_json::{Value, json};
use std::collections::HashMap;

const CATALOG: &str = include_str!("../business/catalog.json");

pub struct Card {
    pub renderer: Entity<GpuiApp>,
    /// Action recipes from the projection: event name -> {method, path, body, fields, done}.
    pub actions: Value,
    pub height: f32,
    pub busy: bool,
    pub error: String,
}

/// A recipe ready to send: POST `body` to `path`, then show `done`. `follow`
/// (default) means the request starts a turn the host should attach to; an
/// approval that only changes its own item refreshes it instead.
pub struct Submission {
    pub id: String,
    pub path: String,
    pub body: Value,
    pub done: String,
    pub follow: bool,
    /// `OPEN` recipes: `path` is a vetted URL for the system browser.
    pub open: bool,
}

/// Where an `OPEN` recipe may go: a path on this instance or an HTTPS URL,
/// never credentials in the URL (the rule company-app links follow).
pub fn open_target(base: &str, href: &str) -> anyhow::Result<String> {
    anyhow::ensure!(
        !href.is_empty() && href.len() <= 4096 && !href.chars().any(|c| c.is_whitespace() || c.is_control() || c == '\\'),
        "Link unavailable"
    );
    let relative = href.starts_with('/') && !href.starts_with("//");
    anyhow::ensure!(relative || href.starts_with("https://"), "Links require an instance path or HTTPS URL");
    let target = if relative { reqwest::Url::parse(base)?.join(href)? } else { reqwest::Url::parse(href)? };
    anyhow::ensure!(
        (relative && matches!(target.scheme(), "http" | "https") || target.scheme() == "https")
            && target.host_str().is_some()
            && target.username().is_empty()
            && target.password().is_none(),
        "Links require an instance path or HTTPS URL without credentials"
    );
    Ok(target.to_string())
}

/// The catalog and components this build renders, sent with every projection request.
pub fn client() -> Value {
    let catalog: Value = serde_json::from_str(CATALOG).unwrap_or_default();
    let mut components: Vec<String> =
        catalog["components"].as_object().map(|c| c.keys().cloned().collect()).unwrap_or_default();
    components.extend(
        ["Button", "TextField", "ChoicePicker", "CheckBox", "Column", "Row", "Text", "Card", "List", "Divider"]
            .map(String::from),
    );
    json!({"catalog_id":catalog["id"],"catalog_version":catalog["version"],"components":components})
}

/// Renderer values in the shape the server validates: a single choice is a
/// string, and an empty choice or blank text is not an answer.
pub fn answer_value(value: Value) -> Option<Value> {
    match value {
        Value::Array(mut items) if items.len() == 1 => answer_value(items.remove(0)),
        Value::Array(items) if items.is_empty() => None,
        Value::String(text) if text.trim().is_empty() => None,
        Value::Null => None,
        other => Some(other),
    }
}

/// What changes in place on an item, so its card refreshes when it moves.
/// Most items never change: a revision is a new item with a new id.
pub fn card_version(kind: &str, payload: &Value) -> String {
    match kind {
        "input" => format!("{}:{}", payload["request_state"]["status"], payload["request_state"]["revision"]),
        "decision" => format!("{}:{}", payload["status"], payload["revision"]),
        "action" => format!(
            "{}:{}:{}:{}:{}:{}",
            payload["status"],
            payload["thread_status"],
            payload["round_count"],
            payload["transcript"].as_array().map_or(0, Vec::len),
            payload["run"]["status"],
            payload["run"]["verification"]["local_sql_review"]["state"],
        ),
        "document" => payload["content_sha256"].to_string(),
        _ => String::new(),
    }
}

pub struct StageCards {
    api: Api,
    charts: Value,
    font: SharedString,
    cards: HashMap<String, Card>,
    /// Items without a card: "loading", a fallback reason, or "error: …".
    state: HashMap<String, String>,
    /// The item version each card or state was fetched for.
    versions: HashMap<String, String>,
    /// Requests in flight; a changed card keeps showing until its update lands.
    pending: std::collections::HashSet<String>,
    tx: std::sync::mpsc::Sender<(String, anyhow::Result<Value>)>,
    rx: std::sync::mpsc::Receiver<(String, anyhow::Result<Value>)>,
}

impl StageCards {
    pub fn new(api: Api, theme: &CanvasTheme) -> Self {
        let (tx, rx) = std::sync::mpsc::channel();
        Self {
            api,
            charts: theme.vega_config(),
            font: theme.font_display.clone(),
            cards: HashMap::new(),
            state: HashMap::new(),
            versions: HashMap::new(),
            pending: Default::default(),
            tx,
            rx,
        }
    }

    pub fn get(&self, id: &str) -> Option<&Card> {
        self.cards.get(id)
    }
    pub fn get_mut(&mut self, id: &str) -> Option<&mut Card> {
        self.cards.get_mut(id)
    }
    pub fn remove(&mut self, id: &str) {
        self.cards.remove(id);
    }
    pub fn state(&self, id: &str) -> Option<&str> {
        self.state.get(id).map(String::as_str)
    }
    pub fn set_state(&mut self, id: &str, state: &str) {
        self.state.insert(id.into(), state.into());
    }
    /// Forget an item's card so its next view is fetched fresh.
    pub fn reload(&mut self, id: &str) {
        self.cards.remove(id);
        self.state.remove(id);
        self.versions.remove(id);
    }
    /// Drop every card (for example when the host switches threads).
    pub fn clear(&mut self) {
        self.cards.clear();
        self.state.clear();
        self.versions.clear();
    }
    /// Views that failed to load get another try.
    pub fn retry_failed(&mut self) {
        self.state.retain(|_, state| !state.starts_with("error"));
    }

    /// Asks for the views of items that have none yet or whose version moved
    /// (`card_version`), twenty per request: `(id, version)`.
    pub fn request(&mut self, session: &str, items: &[(String, String)]) {
        let placements: Vec<(String, String, String)> =
            items.iter().map(|(id, version)| (id.clone(), id.clone(), version.clone())).collect();
        self.request_placed(session, &placements);
    }

    /// Like `request`, keyed by placement: `(key, surface id, version)`. A host
    /// that shows one item in two places at once (a canvas tile and an
    /// inspector) gives each its own card, so each renderer measures its own width.
    pub fn request_placed(&mut self, session: &str, placements: &[(String, String, String)]) {
        if uuid::Uuid::parse_str(session).is_err() {
            return;
        }
        let wanted: Vec<(String, String, String)> = placements
            .iter()
            .filter(|(key, _, version)| {
                !self.pending.contains(key)
                    && ((!self.cards.contains_key(key) && !self.state.contains_key(key))
                        || self.versions.get(key).is_some_and(|known| known != version))
            })
            .cloned()
            .collect();
        for (key, _, version) in &wanted {
            self.versions.insert(key.clone(), version.clone());
        }
        let wanted: Vec<(String, String)> = wanted.into_iter().map(|(key, id, _)| (key, id)).collect();
        for chunk in wanted.chunks(20) {
            for (key, _) in chunk {
                self.pending.insert(key.clone());
                if !self.cards.contains_key(key) {
                    self.state.insert(key.clone(), "loading".into());
                }
            }
            let mut ids: Vec<String> = chunk.iter().map(|(_, id)| id.clone()).collect();
            ids.sort();
            ids.dedup();
            let (api, tx, session, chunk) = (self.api.clone(), self.tx.clone(), session.to_owned(), chunk.to_vec());
            std::thread::spawn(move || {
                match api.post(
                    &format!("/api/calliope/sessions/{session}/views"),
                    &json!({"surface_ids":ids,"client":client()}),
                ) {
                    Ok(mut response) => {
                        let views: HashMap<String, Value> = response["views"]
                            .as_array_mut()
                            .map(|views| {
                                views
                                    .drain(..)
                                    .filter_map(|view| Some((view["surface_id"].as_str()?.to_owned(), view)))
                                    .collect()
                            })
                            .unwrap_or_default();
                        for (key, id) in chunk {
                            let view = views.get(&id).cloned().ok_or_else(|| anyhow::anyhow!("No view was returned"));
                            let _ = tx.send((key, view));
                        }
                    }
                    Err(e) => {
                        let message = format!("{e:#}");
                        for (key, _) in chunk {
                            let _ = tx.send((key, Err(anyhow::anyhow!(message.clone()))));
                        }
                    }
                }
            });
        }
    }

    fn chart_renderer(&self) -> a2ui_gpui::vega::ChartRenderer {
        crate::server_charts::themed(crate::server_charts::renderer(self.api.clone()), self.charts.clone())
    }

    /// Builds cards for arrived views and fits each to what its renderer drew.
    /// Returns whether anything changed.
    pub fn poll<V: 'static>(&mut self, cx: &mut Context<V>) -> bool {
        let mut changed = false;
        while let Ok((id, view)) = self.rx.try_recv() {
            changed = true;
            self.pending.remove(&id);
            // A failed refresh keeps the card the person was looking at.
            if view.is_err() && self.cards.contains_key(&id) {
                continue;
            }
            let previous = self.cards.get(&id).map(|card| card.height);
            let messages =
                view.as_ref().ok().and_then(|v| serde_json::from_value::<Vec<A2uiMessage>>(v["messages"].clone()).ok());
            match (view, messages) {
                (Ok(view), Some(messages)) if view["fallback"].is_null() => {
                    // Cards use the host card's full width; forms keep the page inset.
                    let padding = (view["kind"] != "input").then_some(0.);
                    let (font, charts) = (self.font.clone(), self.chart_renderer());
                    let renderer = cx.new(|_| {
                        let mut r = GpuiApp::new(vec![Catalog::new(CATALOG)], HashMap::new());
                        r.workspace_display_font = font;
                        r.frame_padding = padding;
                        r.chart_renderer = Some(charts);
                        #[cfg(any(target_os = "android", target_os = "ios"))]
                        {
                            r.on_input_kind = Some(std::sync::Arc::new(crate::host::input_kind));
                        }
                        r
                    });
                    match renderer.update(cx, |r, _| r.load_surface(messages)) {
                        Ok(()) => {
                            self.state.remove(&id);
                            self.cards.insert(id, Card {
                                renderer,
                                actions: view["actions"].clone(),
                                // A refreshed card starts at its old height: no jump while it measures.
                                height: previous.unwrap_or(view["height_hint"].as_f64().unwrap_or(320.).clamp(220., 560.) as f32),
                                busy: false,
                                error: String::new(),
                            });
                        }
                        Err(e) => {
                            self.state.insert(id, format!("render: {e}"));
                        }
                    }
                }
                (Ok(view), _) => {
                    // An item that no longer projects (an answered question) drops its card.
                    self.cards.remove(&id);
                    self.state.insert(id, view["fallback"].as_str().unwrap_or("unavailable").into());
                }
                (Err(e), _) => {
                    self.state.insert(id, format!("error: {e}"));
                }
            }
        }
        for card in self.cards.values_mut() {
            // The hint only covers the first frame.
            let measured = card.renderer.read(cx).content_height;
            let height = measured.clamp(40., 900.);
            if measured > 0. && (height - card.height).abs() > 0.5 {
                card.height = height;
                changed = true;
            }
        }
        changed
    }

    /// Button presses as requests to send: a recipe's fixed body plus values
    /// read from the card's data model. Only POSTs to Calliope's own API run.
    pub fn submissions<V: 'static>(&mut self, cx: &mut Context<V>) -> Vec<Submission> {
        let mut runs = Vec::new();
        for (id, card) in &mut self.cards {
            for action in card.renderer.update(cx, |r, _| r.take_actions()) {
                let recipe = &card.actions[action["name"].as_str().unwrap_or("")];
                if recipe["method"] == "OPEN" {
                    match open_target(&self.api.config.browser_url, recipe["url"].as_str().unwrap_or("")) {
                        Ok(url) => runs.push(Submission {
                            id: id.clone(), path: url, body: Value::Null, done: String::new(), follow: false, open: true,
                        }),
                        Err(e) => card.error = format!("{e:#}"),
                    }
                    continue;
                }
                let path = recipe["path"].as_str().unwrap_or("");
                if card.busy || recipe["method"] != "POST" || !path.starts_with("/api/calliope/") {
                    continue;
                }
                let mut body = recipe["body"].clone();
                if !body.is_object() {
                    body = json!({});
                }
                let renderer = card.renderer.read(cx);
                for (key, source) in recipe["fields"].as_object().into_iter().flatten() {
                    body[key] = match source {
                        Value::String(path) => answer_value(renderer.data_value(path)).unwrap_or(Value::Null),
                        Value::Object(map) => Value::Object(
                            map.iter()
                                .filter_map(|(field, path)| {
                                    answer_value(renderer.data_value(path.as_str().unwrap_or(""))).map(|v| (field.clone(), v))
                                })
                                .collect(),
                        ),
                        _ => Value::Null,
                    };
                }
                card.busy = true;
                card.error.clear();
                runs.push(Submission {
                    id: id.clone(),
                    path: path.to_owned(),
                    body,
                    done: recipe["done"].as_str().unwrap_or("Sent.").to_owned(),
                    follow: recipe["follow"] != false,
                    open: false,
                });
            }
        }
        runs
    }

    /// Follow the host theme; charts redraw only when their theme changes.
    pub fn set_theme<V: 'static>(&mut self, theme: &CanvasTheme, cx: &mut Context<V>) {
        let charts = theme.vega_config();
        if charts == self.charts {
            return;
        }
        self.charts = charts;
        self.font = theme.font_display.clone();
        for card in self.cards.values() {
            let renderer = self.chart_renderer();
            card.renderer.update(cx, |r, cx| {
                r.set_chart_renderer(Some(renderer));
                cx.notify();
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{answer_value, client};
    use serde_json::json;

    #[test]
    fn answers_take_the_shape_the_server_validates() {
        assert_eq!(answer_value(json!(["revenue"])), Some(json!("revenue")));
        assert_eq!(answer_value(json!([])), None);
        assert_eq!(answer_value(json!("  ")), None);
        assert_eq!(answer_value(json!(false)), Some(json!(false)));
        assert_eq!(answer_value(json!(["a", "b"])), Some(json!(["a", "b"])));
    }

    #[test]
    fn versions_move_only_when_an_item_changes_in_place() {
        use super::card_version;
        let open = json!({"request_state": {"status": "open", "revision": 1}});
        let revised = json!({"request_state": {"status": "open", "revision": 2}});
        assert_ne!(card_version("input", &open), card_version("input", &revised));
        let planned = json!({"run": {"status": "planned"}});
        let ran = json!({"run": {"status": "complete"}});
        assert_ne!(card_version("action", &planned), card_version("action", &ran));
        let thread = |n: usize| json!({"status": "running", "transcript": vec![json!({}); n]});
        assert_ne!(card_version("action", &thread(1)), card_version("action", &thread(2)));
        assert_eq!(card_version("query", &json!({"rows": [[1]]})), card_version("query", &json!({"rows": [[2]]})));
    }

    #[test]
    fn open_recipes_go_only_to_the_instance_or_https() {
        let base = "https://calliope.local";
        assert_eq!(super::open_target(base, "/d/report").unwrap(), "https://calliope.local/d/report");
        assert_eq!(super::open_target(base, "https://accounts.example.com/o?x=1").unwrap(), "https://accounts.example.com/o?x=1");
        for bad in ["javascript:alert(1)", "//evil.test", "http://elsewhere.test", "https://a:b@site.test", "/x y", ""] {
            assert!(super::open_target(base, bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn the_client_declares_its_catalog_and_built_ins() {
        let client = client();
        let components: Vec<_> = client["components"].as_array().unwrap().iter().filter_map(|c| c.as_str()).collect();
        assert!(components.contains(&"CResultTable") && components.contains(&"CVegaChart") && components.contains(&"ChoicePicker"));
        assert_eq!(client["catalog_id"], "https://calliope.local/a2ui/workspace/v1");
    }
}
