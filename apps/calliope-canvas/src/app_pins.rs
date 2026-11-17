//! Personal references in Postgres, with a disposable last-read cache per account.
use crate::api::{Api, Connection};
use a2ui_base::{catalog::Catalog, protocol::server_to_client::A2uiMessage};
use a2ui_gpui::GpuiApp;
use anyhow::{Result, ensure};
use gpui::*;
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    time::{Duration, Instant},
};

pub fn validate_reference(value: &Value) -> Result<()> {
    ensure!(
        value.as_object().is_some_and(|v| v.len() == 3),
        "Invalid record reference"
    );
    for key in ["slug", "record"] {
        ensure!(
            value[key].as_str().is_some_and(|s| !s.is_empty()
                && s.len() <= 160
                && s.bytes()
                    .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')),
            "Invalid record address"
        );
    }
    ensure!(
        value["id"]
            .as_str()
            .is_some_and(|s| !s.is_empty() && s.len() <= 512)
            || value["id"].is_i64(),
        "Invalid record id"
    );
    Ok(())
}

pub fn browser_url(config: &Connection, reference: &Value) -> Result<String> {
    validate_reference(reference)?;
    let mut url = reqwest::Url::parse(&config.browser_url)?;
    url.set_path(&format!("/apps/{}", reference["slug"].as_str().unwrap()));
    url.set_query(None);
    let fragment = reqwest::Url::parse_with_params(
        "https://local.invalid",
        &[("record", reference.to_string())],
    )?;
    url.set_fragment(fragment.query());
    Ok(url.to_string())
}

/// Only this instance's ordinary record links become native navigation.
pub fn reference_from_url(base: &str, href: &str) -> Result<Option<Value>> {
    let origin = reqwest::Url::parse(base)?;
    let url = origin.join(href)?;
    if url.origin() != origin.origin() || !url.username().is_empty() || url.password().is_some() {
        return Ok(None);
    }
    let Some(slug) = url.path().strip_prefix("/apps/") else { return Ok(None); };
    if slug.contains('/') || slug.is_empty() { return Ok(None); }
    let mut fragment = origin.clone();
    fragment.set_query(url.fragment());
    let Some((_, value)) = fragment.query_pairs().find(|(k, _)| k == "record") else { return Ok(None); };
    let reference: Value = serde_json::from_str(&value)?;
    validate_reference(&reference)?;
    ensure!(reference["slug"] == slug, "Record belongs to another app");
    Ok(Some(reference))
}

pub struct PinView {
    pub value: Value,
    pub renderer: Option<Entity<GpuiApp>>,
}
impl PinView {
    pub fn id(&self) -> String {
        self.value["id"].as_str().unwrap_or("").to_owned()
    }
    pub fn title(&self) -> String {
        self.value["summary"]["records"][0]["title"]
            .as_str()
            .unwrap_or("Pinned record")
            .to_owned()
    }
    pub fn status(&self) -> String {
        if let Some(error) = self.value["error"].as_str() {
            return error.to_owned();
        }
        let age = crate::app_connection::age(self.value["fetched_at"].as_u64().unwrap_or(0));
        if self.value["cached"] == true {
            format!("Cached · {age}")
        } else {
            format!("Live · {age}")
        }
    }
}
enum Update {
    List(Result<Value>),
    Summary(String, Result<Value>),
    Done,
    Changed(Result<Value>),
}
pub struct Pins {
    pub items: Vec<PinView>,
    pub notice: Option<String>,
    pub busy: bool,
    pub active: bool,
    pub changing: bool,
    api: Api,
    cache: std::path::PathBuf,
    refresh_again: bool,
    last: Instant,
    tx: crate::mailbox::Sender<Update>,
    rx: std::sync::mpsc::Receiver<Update>,
}
impl Pins {
    pub fn new(config: Connection, cx: &mut Context<Self>) -> Self {
        let cache = crate::app_drafts::account_root(&config).join("record-pins.json");
        let (tx, rx, wake) = crate::mailbox::channel();
        let cached: Vec<Value> = std::fs::read(&cache)
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or_default();
        let mut this = Self {
            items: Vec::new(),
            notice: None,
            busy: false,
            active: true,
            changing: false,
            api: Api::new(config).expect("validated connection"),
            cache,
            refresh_again: false,
            last: Instant::now() - Duration::from_secs(20),
            tx,
            rx,
        };
        for mut value in cached.into_iter().take(32) {
            if !value.is_object() || validate_reference(&value["reference"]).is_err()
                || !value["id"].as_str().is_some_and(|id| uuid::Uuid::parse_str(id).is_ok()) {
                continue;
            }
            value["cached"] = json!(true);
            let renderer = Self::renderer(&value["summary"], cx);
            this.items.push(PinView { value, renderer });
        }
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
        this.refresh();
        this
    }
    fn renderer(summary: &Value, cx: &mut Context<Self>) -> Option<Entity<GpuiApp>> {
        let messages: Vec<A2uiMessage> =
            serde_json::from_value(summary["messages"].clone()).ok()?;
        let mut renderer = GpuiApp::new(
            vec![Catalog::new(include_str!("../business/catalog.json"))],
            HashMap::new(),
        );
        renderer.load_surface(messages).ok()?;
        renderer.interaction_disabled = true;
        renderer.summary_preview = true;
        Some(cx.new(|_| renderer))
    }
    pub fn contains(&self, reference: &Value) -> bool {
        self.items
            .iter()
            .any(|p| p.value["reference"] == *reference)
    }
    pub fn change(&mut self, body: Value, cx: &mut Context<Self>) {
        if self.changing {
            return;
        }
        self.changing = true;
        self.notice = None;
        let (api, tx) = (self.api.clone(), self.tx.clone());
        std::thread::spawn(move || {
            let _ = tx.send(Update::Changed(api.post("/api/calliope/app-pins", &body)));
        });
        cx.notify();
    }
    pub fn refresh(&mut self) {
        if self.busy {
            self.refresh_again = true;
            return;
        }
        self.busy = true;
        self.refresh_again = false;
        let (api, tx) = (self.api.clone(), self.tx.clone());
        std::thread::spawn(move || {
            let list = api.get("/api/calliope/app-pins");
            let pins = list
                .as_ref()
                .ok()
                .and_then(|v| v["pins"].as_array())
                .cloned()
                .unwrap_or_default();
            let _ = tx.send(Update::List(list));
            for chunk in pins.chunks(4) {
                std::thread::scope(|scope| {
                    for p in chunk {
                        let api = &api;
                        let tx = &tx;
                        scope.spawn(move || {
                            let result = validate_reference(&p["reference"]).and_then(|_| {
                                api.post(
                                    &format!(
                                        "/api/apps/{}/record-summary",
                                        p["reference"]["slug"].as_str().unwrap()
                                    ),
                                    &p["reference"],
                                )
                            });
                            let _ = tx.send(Update::Summary(
                                p["id"].as_str().unwrap_or("").into(),
                                result,
                            ));
                        });
                    }
                });
            }
            let _ = tx.send(Update::Done);
        });
    }
    fn tick(&mut self, cx: &mut Context<Self>) {
        let mut changed = false;
        while let Ok(update) = self.rx.try_recv() {
            changed = true;
            match update {
                Update::List(Ok(value)) => {
                    self.notice = None;
                    let mut old = std::mem::take(&mut self.items);
                    for value in value["pins"].as_array().into_iter().flatten().take(32) {
                        if let Some(index) = old.iter().position(|p| p.value["id"] == value["id"]) {
                            self.items.push(old.remove(index));
                        } else {
                            self.items.push(PinView {
                                value: value.clone(),
                                renderer: None,
                            });
                        }
                    }
                }
                Update::List(Err(error)) => {
                    let denied = error
                        .downcast_ref::<crate::app_drafts::HttpError>()
                        .is_some_and(|e| matches!(e.status, 401 | 403));
                    if denied {
                        self.items.clear();
                    } else {
                        for pin in &mut self.items {
                            pin.value["cached"] = json!(true);
                        }
                    }
                    self.notice = Some(error.to_string());
                }
                Update::Summary(id, result) => {
                    if let Some(index) = self.items.iter().position(|p| p.id() == id) {
                        match result {
                            Ok(summary) => {
                                // Keep the renderer and its layout when the data is unchanged.
                                if self.items[index].value["summary"]["messages"]
                                    != summary["messages"]
                                    || self.items[index].renderer.is_none()
                                {
                                    self.items[index].renderer = Self::renderer(&summary, cx);
                                }
                                let pin = &mut self.items[index];
                                pin.value["summary"] = summary;
                                pin.value["error"] = Value::Null;
                                pin.value["cached"] = json!(false);
                                pin.value["fetched_at"] = json!(crate::app_connection::timestamp());
                            }
                            Err(error) => {
                                let pin = &mut self.items[index];
                                pin.value["cached"] = json!(true);
                                if !crate::app_connection::retryable(&error) {
                                    pin.value["summary"] = Value::Null;
                                    pin.renderer = None;
                                    pin.value["error"] = json!(error.to_string());
                                }
                            }
                        }
                    }
                }
                Update::Done => {
                    self.busy = false;
                    self.last = Instant::now();
                }
                Update::Changed(result) => {
                    self.changing = false;
                    match result {
                        Ok(value) => {
                            if let Some(id) = value["removed"].as_str() {
                                self.items.retain(|p| p.id() != id);
                            } else if !self.items.iter().any(|p| p.value["id"] == value["id"]) {
                                self.items.push(PinView {
                                    value,
                                    renderer: None,
                                });
                            }
                            self.notice = None;
                            self.refresh_again = true;
                        }
                        Err(error) => {
                            self.notice = Some(format!("Pin change was not confirmed: {error}"))
                        }
                    }
                }
            }
        }
        if changed {
            let bytes =
                serde_json::to_vec(&self.items.iter().map(|p| &p.value).collect::<Vec<_>>())
                    .unwrap_or_default();
            let cache = self.cache.clone();
            // The small cache is disposable. Never block the UI on disk writes.
            if !self.busy {
                std::thread::spawn(move || {
                    let _ = crate::app_drafts::atomic(&cache, &bytes);
                });
            }
            cx.notify();
        }
        let foreground = self.active;
        #[cfg(any(target_os = "android", target_os = "ios"))]
        let foreground = foreground && crate::host::is_foreground();
        if !self.busy
            && (self.refresh_again
                || (foreground && self.last.elapsed() >= Duration::from_secs(15)))
        {
            self.refresh();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[::core::prelude::v1::test]
    fn references_preserve_types_and_links_stay_on_the_instance() {
        let r = json!({"slug":"field","record":"visit","id":"HV /#? é"});
        let c = Connection {
            browser_url: "https://company.example/".into(),
            ..Default::default()
        };
        let u = reqwest::Url::parse(&browser_url(&c, &r).unwrap()).unwrap();
        assert_eq!(u.host_str(), Some("company.example"));
        assert_eq!(u.path(), "/apps/field");
        let q = reqwest::Url::parse(&format!("https://x/?{}", u.fragment().unwrap())).unwrap();
        assert_eq!(
            serde_json::from_str::<Value>(&q.query_pairs().next().unwrap().1).unwrap(),
            r
        );
        assert!(
            validate_reference(&json!({"slug":"../escape","record":"visit","id":"1"})).is_err()
        );
        assert!(validate_reference(&json!({"slug":"field","record":"visit","id":true})).is_err());
    }
}

#[cfg(test)]
mod navigation_tests {
    use super::*;
    #[::core::prelude::v1::test]
    fn only_matching_instance_record_links_navigate_natively() {
        let reference=json!({"slug":"visits","record":"visit","id":"HV / 2 & 日本"});
        let mut url=reqwest::Url::parse("https://instance.test/apps/visits").unwrap();
        let query=reqwest::Url::parse_with_params("https://unused",&[("record",reference.to_string())]).unwrap();
        url.set_fragment(query.query());
        assert_eq!(reference_from_url("https://instance.test",url.as_str()).unwrap(),Some(reference));
        assert!(reference_from_url("https://another.test",url.as_str()).unwrap().is_none());
        assert!(reference_from_url("https://instance.test","/apps/visits").unwrap().is_none());
        assert!(reference_from_url("https://instance.test","/apps/visits#record={}").is_err());
        url.set_path("/apps/other");
        assert!(reference_from_url("https://instance.test",url.as_str()).is_err());
    }
}
