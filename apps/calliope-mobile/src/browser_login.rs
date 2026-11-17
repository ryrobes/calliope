//! Browser authentication uses the server's login page, including Google.
//! Pending secrets share the platform credential vault, never the draft journal.
use crate::{
    MobileShell, ShellReply,
    api::{Api, Connection},
};
use anyhow::{Result, ensure};
use gpui::*;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};

#[derive(Clone, Deserialize, Serialize)]
pub struct Flow {
    config: Connection,
    request_id: String,
    device_secret: String,
    pub code: String,
    expires_at: u64,
    pub verification_path: String,
    pub destination: Option<crate::native_link::Destination>,
}
impl Flow {
    pub fn load() -> Result<Option<Self>> {
        let value: serde_json::Value =
            serde_json::from_str(&crate::host::load_connection().map_err(anyhow::Error::msg)?)?;
        value
            .get("native_auth")
            .filter(|v| !v.is_null())
            .map(|v| serde_json::from_value(v.clone()).map_err(Into::into))
            .transpose()
    }
    pub fn save(&self) -> Result<()> {
        let mut value = crate::host::load_connection()
            .ok()
            .and_then(|v| serde_json::from_str::<serde_json::Value>(&v).ok())
            .filter(|v| v.is_object())
            .unwrap_or(json!({}));
        value["native_auth"] = serde_json::to_value(self)?;
        crate::host::save_connection(&value.to_string()).map_err(anyhow::Error::msg)
    }
    pub fn clear() {
        if let Ok(raw) = crate::host::load_connection() {
            if let Ok(mut value) = serde_json::from_str::<serde_json::Value>(&raw) {
                if let Some(object) = value.as_object_mut() {
                    object.remove("native_auth");
                    let _ = crate::host::save_connection(&value.to_string());
                }
            }
        }
    }
    pub fn url(&self) -> Result<String> {
        let base = reqwest::Url::parse(&self.config.base_url)?;
        let url = base.join(&self.verification_path)?;
        ensure!(
            url.origin() == base.origin() && url.path() == "/auth/native/approve",
            "Invalid sign-in address returned by server"
        );
        Ok(url.to_string())
    }
    pub fn cancel(&self) {
        if let Ok(api) = Api::new(self.config.clone()) {
            let _=api.post("/auth/native/poll", &json!({"request_id":self.request_id,"device_secret":self.device_secret,"cancel":true}));
        }
    }
    fn wait(&self, cancel: &AtomicBool) -> Result<Connection> {
        let api = Api::new(self.config.clone())?;
        while !cancel.load(Ordering::Relaxed) {
            ensure!(
                crate::app_connection::timestamp() < self.expires_at,
                "Browser sign-in expired. Try again."
            );
            let result = api.post(
                "/auth/native/poll",
                &json!({"request_id":self.request_id,"device_secret":self.device_secret}),
            );
            let result = match result {
                Ok(value) => value,
                Err(error) if crate::app_connection::retryable(&error) => {
                    std::thread::sleep(std::time::Duration::from_secs(2));
                    continue;
                }
                Err(error) => return Err(error),
            };
            match result["status"].as_str() {
                Some("approved") => {
                    let mut config = self.config.clone();
                    config.email = result["email"].as_str().unwrap_or_default().to_owned();
                    config.session_cookie = result["session_cookie"]
                        .as_str()
                        .unwrap_or_default()
                        .to_owned();
                    ensure!(
                        !config.email.is_empty() && !config.session_cookie.is_empty(),
                        "Incomplete sign-in response"
                    );
                    return Ok(config);
                }
                Some("pending") => std::thread::sleep(std::time::Duration::from_secs(2)),
                _ => anyhow::bail!("Browser sign-in was cancelled. Your drafts are still here."),
            }
        }
        anyhow::bail!("Sign-in cancelled")
    }
}

impl MobileShell {
    pub(crate) fn browser_connect(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if self.connecting {
            return;
        }
        let base_url = self.fields[0]
            .read(cx)
            .value()
            .trim()
            .trim_end_matches('/')
            .to_owned();
        let config = Connection {
            base_url: base_url.clone(),
            browser_url: base_url,
            email: String::new(),
            session_cookie: String::new(),
        };
        if let Err(e) = Api::new(config.clone()) {
            self.message = e.to_string();
            cx.notify();
            return;
        }
        self.fields[2].update(cx, |f, cx| f.set_value("", window, cx));
        self.connecting = true;
        self.message = "Opening browser sign-in…".into();
        self.login_generation += 1;
        let generation = self.login_generation;
        let tx = self.tx.clone();
        let destination = self.destination.clone();
        std::thread::spawn(move || {
            let result = (|| {
                let value=Api::new(config.clone())?.post("/auth/native/start",&json!({"device":if cfg!(target_os="ios"){"Calliope iOS"}else{"Calliope Android"}}))?;
                let mut body = value;
                body["config"] = serde_json::to_value(&config)?;
                body["destination"] = serde_json::to_value(destination)?;
                let flow: Flow = serde_json::from_value(body)?;
                flow.url()?;
                Ok(flow)
            })();
            let _ = tx.send((
                generation,
                match result {
                    Ok(flow) => ShellReply::BrowserReady(flow, true),
                    Err(e) => ShellReply::Loaded(config, Err(e)),
                },
            ));
        });
        cx.notify();
    }
    pub(crate) fn browser_ready(
        &mut self,
        flow: Flow,
        open: bool,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let result = (|| {
            flow.save()?;
            if open {
                crate::host::open_url(&flow.url()?).map_err(anyhow::Error::msg)?;
            }
            Ok::<_, anyhow::Error>(())
        })();
        if let Err(e) = result {
            self.message = e.to_string();
            self.connecting = false;
            Flow::clear();
            cx.notify();
            return;
        }
        self.fields[0].update(cx, |f, cx| {
            f.set_value(flow.config.base_url.clone(), window, cx)
        });
        self.destination = flow.destination.clone();
        self.message = format!(
            "Match code {} in your browser, then connect this device.",
            flow.code
        );
        self.browser_flow = Some(flow.clone());
        self.connecting = true;
        let cancel = Arc::new(AtomicBool::new(false));
        self.browser_cancel = Some(cancel.clone());
        let tx = self.tx.clone();
        let generation = self.login_generation;
        std::thread::spawn(move || {
            let mut config = flow.config.clone();
            let result = (|| {
                config = flow.wait(&cancel)?;
                MobileShell::appearance(&config)
            })();
            let _ = tx.send((generation, ShellReply::Loaded(config, result)));
        });
        cx.notify();
    }
    pub(crate) fn cancel_browser(&mut self, cx: &mut Context<Self>) {
        self.login_generation += 1;
        if let Some(cancel) = self.browser_cancel.take() {
            cancel.store(true, Ordering::Relaxed);
        }
        if let Some(flow) = self.browser_flow.take() {
            std::thread::spawn(move || flow.cancel());
        }
        Flow::clear();
        self.connecting = false;
        self.message = "Sign-in cancelled. Your drafts are still here.".into();
        cx.notify();
    }
    pub(crate) fn incoming_link(
        &mut self,
        value: &str,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        match crate::native_link::parse(value) {
            Ok(None) => {} // A wake-up only; the private exchange supplies identity.
            Err(e) => {
                self.message = e.to_string();
                cx.notify();
            }
            Ok(Some(target)) => {
                if self.connecting {
                    self.message =
                        "Finish or cancel the current sign-in before opening another instance."
                            .into();
                    cx.notify();
                    return;
                }
                let same = reqwest::Url::parse(&self.config.base_url)
                    .ok()
                    .is_some_and(|u| u.origin().ascii_serialization() == target.server);
                self.destination = Some(target.clone());
                if same && self.apps.is_some() {
                    self.open_destination(cx);
                } else {
                    self.account = true;
                    self.account_editing = true;
                    self.fields[0].update(cx, |f, cx| f.set_value(target.server, window, cx));
                    self.fields[2].update(cx, |f, cx| f.set_value("", window, cx));
                    self.message = if target.session.is_some() {
                        "Sign in to this instance to open the conversation.".into()
                    } else {
                        "Sign in to this instance to open the shared app. Existing drafts stay with their account.".into()
                    };
                }
                cx.notify();
            }
        }
    }
    pub(crate) fn open_destination(&mut self, cx: &mut Context<Self>) {
        if let Some(session) = self.destination.as_ref().and_then(|t| t.session.clone()) {
            if let Some(chat) = &self.conversations {
                self.destination = None;
                chat.update(cx, |c, cx| c.open_link(session, cx));
                self.account = false;
                self.chat_tab = true;
                cx.notify();
            }
            return;
        }
        if let (Some(target), Some(apps)) = (self.destination.take(), &self.apps) {
            if let Some(slug) = target.app {
                let result = apps.update(cx, |a, cx| match (target.record, target.id) {
                    (Some(record), Some(id)) => {
                        a.open_record(json!({"slug":slug,"record":record,"id":id}), false, cx)
                    }
                    _ => a.request_open(slug, cx),
                });
                if let Err(e) = result {
                    self.message = e.to_string();
                }
            }
            self.account = false;
            self.chat_tab = false;
        }
    }
}
