//! Ordinary persisted Calliope conversations, scoped by app and tagged by turn.
use crate::api::Api;
use anyhow::{Result, ensure};
use serde_json::{Value, json};
use std::{collections::HashMap, io::BufReader, time::Duration};

#[derive(Clone, Default)]
pub struct Turn {
    pub client_id: String,
    pub id: String,
    pub question: String,
    pub answer: String,
    pub context: Value,
    pub applied: bool,
    pub durable: bool,
    /// The server's receipt for the reply: citations (spotlight, values, refs).
    pub receipt: Value,
    /// Files attached to the question.
    pub attachments: Value,
    /// Progress notes the agent left while working.
    pub working: Value,
    pub objects: Value,
}
#[derive(Default)]
pub struct Conversation {
    /// General chat retains every visible turn; app assistants retain their context scope.
    pub all_turns: bool,
    pub metadata: Value,
    pub surfaces: Vec<Value>,
    pub session: String,
    pub turns: Vec<Turn>,
    pub loading: bool,
    pub initialized: bool,
    pub running: bool,
    pub error: String,
    pub progress: String,
    /// Latest todo-plan snapshot of the running reply (`calliope.plan`).
    pub plan: Value,
    /// Latest delegated-specialist activity of the running reply.
    pub specialist: String,
    pub retry: Option<Turn>,
    pub draft: String,
}
pub enum Update {
    Stopped(String, Result<Value>),
    VoiceSettings(Result<crate::voice::Settings>),
    History(String, Result<Value>),
    Session(String, String),
    Event(String, String, String, Value),
    Done(String, Result<()>),
}
#[cfg(any(target_os = "android", target_os = "ios"))]
pub type ComposerState = gpui_component::input::TextareaState;
#[cfg(not(any(target_os = "android", target_os = "ios")))]
pub type ComposerState = gpui_component::input::InputState;
pub struct Assistant {
    pub improvement: bool,
    pub open: bool,
    pub history: bool,
    pub context_cache: std::cell::RefCell<Option<(String, Value)>>,
    pub voice: crate::voice::Voice,
    pub voice_settings: Option<crate::voice::Settings>,
    pub voice_loaded: bool,
    pub voice_slug: String,
    pub voice_location: String,
    pub transcript_pending: Option<(String, String)>,
    pub completed: Vec<(String, String, String)>,
    pub chats: HashMap<String, Conversation>,
    pub input: Option<gpui::Entity<ComposerState>>,
    pub input_slug: String,
    pub subscription: Option<gpui::Subscription>,
    pub tx: std::sync::mpsc::Sender<Update>,
    rx: std::sync::mpsc::Receiver<Update>,
}
impl Default for Assistant {
    fn default() -> Self {
        let (tx, rx) = std::sync::mpsc::channel();
        Self {
            improvement: false,
            open: false,
            history: false,
            context_cache: Default::default(),
            voice: Default::default(),
            voice_settings: None,
            voice_loaded: false,
            voice_slug: String::new(),
            voice_location: String::new(),
            transcript_pending: None,
            completed: Vec::new(),
            chats: HashMap::new(),
            input: None,
            input_slug: String::new(),
            subscription: None,
            tx,
            rx,
        }
    }
}
impl Assistant {
    pub fn load_voice(&mut self, api: Api) {
        if !self.voice_loaded {
            self.voice_loaded = true;
            let tx = self.tx.clone();
            let api = api.clone();
            std::thread::spawn(move || {
                let _ = tx.send(Update::VoiceSettings(crate::voice::Settings::load(&api)));
            });
        }
    }
    pub fn load_thread(&mut self, api: Api, key: &str, id: &str) {
        let chat = self.chats.entry(key.into()).or_default();
        if chat.loading || chat.running { return; }
        chat.all_turns = true;
        chat.loading = true;
        chat.session = id.to_owned();
        let key = key.to_owned();
        let id = id.to_owned();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let result = (|| {
                uuid::Uuid::parse_str(&id)?;
                let value = api.get(&format!("/api/calliope/sessions/{id}"))?;
                ensure!(value["session"]["access_role"] == "owner", "This conversation is not owned by your account");
                Ok(value)
            })();
            let _ = tx.send(Update::History(key, result));
        });
    }
    pub fn load(&mut self, api: Api, slug: &str) {
        self.load_voice(api.clone());
        let chat = self.chats.entry(slug.into()).or_default();
        if chat.initialized || chat.loading || chat.running {
            return;
        }
        chat.loading = true;
        let slug = slug.to_owned();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let result = (|| {
                let link = api.get(&format!("/api/apps/{slug}/conversation"))?;
                if let Some(id) = link["session_id"].as_str() {
                    uuid::Uuid::parse_str(id)?;
                    api.get(&format!("/api/calliope/sessions/{id}"))
                } else {
                    Ok(Value::Null)
                }
            })();
            let _ = tx.send(Update::History(slug, result));
        });
    }
    pub fn send(&mut self, api: Api, slug: &str, question: String, context: Value, retry: bool) {
        let chat = self.chats.entry(slug.into()).or_default();
        if chat.loading || chat.running || !chat.initialized {
            return;
        }
        let turn = if retry {
            let Some(t) = chat.retry.clone() else { return };
            t
        } else {
            if question.trim().is_empty() || question.len() > 16_000 {
                return;
            }
            let t = Turn {
                client_id: uuid::Uuid::new_v4().to_string(),
                question,
                context,
                ..Default::default()
            };
            chat.turns.push(t.clone());
            t
        };
        chat.running = true;
        chat.error.clear();
        chat.progress = "Thinking…".into();
        chat.plan = Value::Null;
        chat.specialist.clear();
        chat.retry = Some(turn.clone());
        let session = chat.session.clone();
        let general = chat.all_turns;
        let slug = slug.to_owned();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let result = (|| {
                let session = if session.is_empty() {
                    let body = if general { json!({}) } else { json!({"title":format!("{} · Callie",turn.context["title"].as_str().unwrap_or("Company app"))}) };
                    let value=api.post("/api/calliope/sessions", &body)?;
                    let id = value["session"]["id"]
                        .as_str()
                        .ok_or_else(|| anyhow::anyhow!("Missing conversation ID"))?
                        .to_owned();
                    let _ = tx.send(Update::Session(slug.clone(), id.clone()));
                    id
                } else {
                    session
                };
                uuid::Uuid::parse_str(&session)?;
                let path = format!("/api/calliope/sessions/{session}/turn");
                let original = json!({"message":turn.question,"client_turn_id":turn.client_id,"origin_context":turn.context});
                let body = if retry {
                    json!({"client_turn_id":turn.client_id,"reattach":true})
                } else {
                    original.clone()
                };
                let mut response = api
                    .request(reqwest::Method::POST, &path)?
                    .timeout(Duration::from_secs(900))
                    .json(&body)
                    .send()?;
                if retry && response.status().as_u16() == 404 && !turn.durable {
                    response = api
                        .request(reqwest::Method::POST, &path)?
                        .timeout(Duration::from_secs(900))
                        .json(&original)
                        .send()?;
                }
                if !response.status().is_success() {
                    Api::json_response(response)?;
                    anyhow::bail!("Message was rejected");
                }
                ensure!(
                    response
                        .headers()
                        .get(reqwest::header::CONTENT_TYPE)
                        .and_then(|s| s.to_str().ok())
                        .is_some_and(|s| s.contains("text/event-stream")),
                    "Expected a Calliope event stream"
                );
                let mut terminal = false;
                crate::app_stream::read_sse(BufReader::new(response), &mut |event, value| {
                    terminal |=
                        matches!(event.as_str(), "calliope.turn.completed" | "calliope.error");
                    let _ = tx.send(Update::Event(
                        slug.clone(),
                        turn.client_id.clone(),
                        event,
                        value,
                    ));
                })?;
                ensure!(
                    terminal,
                    "Connection interrupted. Retry reconnects to this same message"
                );
                Ok(())
            })();
            let _ = tx.send(Update::Done(slug, result));
        });
    }
    pub fn stop(&mut self, api: Api, slug: &str) {
        let Some(c) = self.chats.get_mut(slug) else {
            return;
        };
        let Some(t) = c.retry.as_ref().filter(|_| c.running) else {
            return;
        };
        if c.session.is_empty() {
            return;
        }
        c.progress = "Stopping…".into();
        let session = c.session.clone();
        let id = t.client_id.clone();
        let slug = slug.to_owned();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let result = api.post(
                &format!("/api/calliope/sessions/{session}/turn/stop"),
                &json!({"client_turn_id":id}),
            );
            let _ = tx.send(Update::Stopped(slug, result));
        });
    }
    pub fn poll(&mut self) -> bool {
        let mut changed = false;
        for update in self.rx.try_iter() {
            changed = true;
            match update {
                Update::Stopped(slug, result) => {
                    if let Err(e) = result {
                        self.chats.entry(slug).or_default().error = e.to_string();
                    }
                }
                Update::VoiceSettings(result) => {
                    self.voice_settings = result.ok();
                }
                Update::History(slug, result) => {
                    let c = self.chats.entry(slug.clone()).or_default();
                    c.loading = false;
                    match result {
                        Ok(v) => {
                            c.initialized = true;
                            c.error.clear();
                            c.retry = None;
                            c.session = v["session"]["id"].as_str().unwrap_or("").into();
                            c.metadata = v["session"].clone();
                            c.surfaces = v["surfaces"].as_array().cloned().unwrap_or_default();
                            c.turns = v["turns"]
                                .as_array()
                                .into_iter()
                                .flatten()
                                .filter(|t| if c.all_turns { t["turn_kind"] != "sketch_bootstrap" } else { t["origin_context"]["kind"] == "app_workspace" })
                                .map(|t| Turn {
                                    client_id: t["client_turn_id"].as_str().unwrap_or("").into(),
                                    id: t["id"].as_str().unwrap_or("").into(),
                                    question: t["user_message"].as_str().unwrap_or("").into(),
                                    answer: t["assistant_message"].as_str().unwrap_or("").into(),
                                    context: t["origin_context"].clone(),
                                    applied: false,
                                    durable: true,
                                    receipt: t["response_receipt"].clone(),
                                    attachments: t["attachments"].clone(),
                                    working: t["working_updates"].clone(),
                                    objects: t["object_refs"].clone(),
                                })
                                .collect();
                            if let Some(pending) = v["turns"].as_array().and_then(|a| {
                                a.iter().rev().find(|t| {
                                    matches!(
                                        t["status"].as_str(),
                                        Some("running" | "queued" | "pending")
                                    )
                                })
                            }) {
                                c.retry = c
                                    .turns
                                    .iter()
                                    .find(|t| t.id == pending["id"].as_str().unwrap_or(""))
                                    .cloned();
                                c.error = "A reply is still in progress. Reconnect to continue it."
                                    .into();
                            }
                        }
                        Err(e) => c.error = e.to_string(),
                    }
                }
                Update::Session(slug, id) => self.chats.entry(slug).or_default().session = id,
                Update::Event(slug, client_id, event, value) => {
                    let c = self.chats.entry(slug.clone()).or_default();
                    let Some(t) = c.turns.iter_mut().find(|t| t.client_id == client_id) else {
                        continue;
                    };
                    match event.as_str() {
                        "calliope.turn.started" => {
                            t.id = value["turn_id"].as_str().unwrap_or("").into();
                            t.durable = true;
                            if let Some(retry) = &mut c.retry {
                                retry.durable = true;
                            }
                        }
                        "calliope.draft" => {
                            if let Some(s) = value["text"].as_str() {
                                t.answer = s.into();
                            }
                        }
                        "assistant.delta" => {
                            if let Some(s) =
                                value["text"].as_str().or_else(|| value["delta"].as_str())
                            {
                                t.answer.push_str(s);
                            }
                        }
                        "calliope.surfaces" => {
                            // Items arrive while the reply is still running; newest first,
                            // a revised item replaces its earlier copy.
                            for surface in value["surfaces"].as_array().into_iter().flatten() {
                                if surface["id"].as_str().is_none_or(str::is_empty) {
                                    continue;
                                }
                                c.surfaces.retain(|s| s["id"] != surface["id"]);
                                c.surfaces.insert(0, surface.clone());
                            }
                        }
                        "calliope.plan" => c.plan = value,
                        "calliope.specialist" => {
                            let who = ["display_name", "name", "key"]
                                .iter()
                                .find_map(|k| value["specialist"][k].as_str())
                                .unwrap_or("A specialist");
                            c.specialist = match value["type"].as_str() {
                                Some("started") => format!("{who} is working on it"),
                                Some("completed" | "complete") => format!("{who} finished"),
                                _ => match value["text"].as_str().map(str::trim) {
                                    Some(text) if !text.is_empty() => {
                                        format!("{who}: {}", text.chars().take(160).collect::<String>())
                                    }
                                    _ => c.specialist.clone(),
                                },
                            };
                        }
                        "calliope.progress" => {
                            c.progress = value["message"]
                                .as_str()
                                .or_else(|| value["text"].as_str())
                                .unwrap_or("Working…")
                                .into()
                        }
                        "calliope.turn.completed" => {
                            if let Some(s) = value["assistant_message"].as_str() {
                                t.answer = s.into();
                            }
                            t.id = value["turn_id"].as_str().unwrap_or("").into();
                            if !value["response_receipt"].is_null() {
                                t.receipt = value["response_receipt"].clone();
                            }
                            c.retry = None;
                            c.plan = Value::Null;
                            c.specialist.clear();
                            self.completed.push((slug, c.session.clone(), t.id.clone()));
                        }
                        "calliope.error" => {
                            c.retry = None;
                            c.error = value["message"]
                                .as_str()
                                .or_else(|| value.pointer("/error/message").and_then(Value::as_str))
                                .unwrap_or("Callie could not finish this reply")
                                .into();
                        }
                        _ => {}
                    }
                }
                Update::Done(slug, result) => {
                    let c = self.chats.entry(slug.clone()).or_default();
                    c.running = false;
                    c.progress.clear();
                    c.plan = Value::Null;
                    c.specialist.clear();
                    if let Err(e) = result {
                        c.error = e.to_string();
                    }
                }
            }
        }
        changed
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn general_history_keeps_web_turns_and_stage_without_leaking_into_app_chat() {
        let mut a = Assistant::default();
        a.chats.entry("general".into()).or_default().all_turns = true;
        let payload = json!({"session":{"id":"s","title":"Existing web chat","access_role":"owner"},"surfaces":[{"id":"surface","kind":"query"}],"turns":[
            {"id":"bootstrap","turn_kind":"sketch_bootstrap"},
            {"id":"web","user_message":"A web question","assistant_message":"A web answer"},
            {"id":"app","user_message":"An app question","origin_context":{"kind":"app_workspace"}}
        ]});
        for key in ["general", "app"] { a.tx.send(Update::History(key.into(),Ok(payload.clone()))).unwrap(); }
        a.poll();
        assert_eq!(a.chats["general"].turns.len(),2);
        assert_eq!(a.chats["general"].turns[0].answer,"A web answer");
        assert_eq!(a.chats["general"].surfaces[0]["id"],"surface");
        assert_eq!(a.chats["general"].metadata["title"],"Existing web chat");
        assert_eq!(a.chats["app"].turns.len(),1);
        assert_eq!(a.chats["app"].turns[0].id,"app");
    }
    #[test]
    fn late_stream_updates_only_the_original_app_and_preserves_context() {
        let mut a = Assistant::default();
        for slug in ["a", "b"] {
            a.chats.entry(slug.into()).or_default().turns.push(Turn {
                client_id: slug.into(),
                context: json!({"record":slug}),
                ..Default::default()
            });
        }
        a.tx.send(Update::Event("a".into(),"a".into(),"calliope.turn.completed".into(),json!({"assistant_message":"A answer","turn_id":"durable-a","origin_context":{"record":"wrong"}}))).unwrap();
        assert!(a.poll());
        assert_eq!(a.chats["a"].turns[0].answer, "A answer");
        assert_eq!(a.chats["a"].turns[0].context["record"], "a");
        assert!(a.chats["b"].turns[0].answer.is_empty());
    }
    #[test]
    fn history_keeps_reply_receipts_and_live_items_arrive_mid_turn() {
        let mut a = Assistant::default();
        a.chats.entry("general".into()).or_default().all_turns = true;
        a.tx.send(Update::History("general".into(), Ok(json!({"session":{"id":"s"},"surfaces":[{"id":"old","kind":"query"}],"turns":[
            {"id":"t","client_turn_id":"c","user_message":"q","assistant_message":"a",
             "response_receipt":{"citations":{"spotlight":[{"surface_id":"old"}]}},
             "attachments":[{"name":"plan.pdf"}],"working_updates":[{"text":"Looking"}],"object_refs":[]}
        ]})))).unwrap();
        a.poll();
        let turn = &a.chats["general"].turns[0];
        assert_eq!(turn.receipt["citations"]["spotlight"][0]["surface_id"], "old");
        assert_eq!(turn.attachments[0]["name"], "plan.pdf");
        assert_eq!(turn.working[0]["text"], "Looking");
        let event = |name: &str, value: Value| Update::Event("general".into(), "c".into(), name.into(), value);
        a.tx.send(event("calliope.surfaces", json!({"surfaces":[{"id":"new","kind":"chart"},{"id":"old","kind":"query","title":"revised"},{"kind":"no id"}]}))).unwrap();
        a.tx.send(event("calliope.plan", json!({"todos":[{"id":"1","content":"Query","status":"in_progress"}],"summary":{"total":1}}))).unwrap();
        a.tx.send(event("calliope.specialist", json!({"type":"started","specialist":{"display_name":"Iris"}}))).unwrap();
        a.poll();
        let chat = &a.chats["general"];
        let ids: Vec<_> = chat.surfaces.iter().map(|s| s["id"].as_str().unwrap()).collect();
        assert_eq!(ids, ["old", "new"]);
        assert_eq!(chat.surfaces[0]["title"], "revised");
        assert_eq!(chat.plan["summary"]["total"], 1);
        assert_eq!(chat.specialist, "Iris is working on it");
        a.tx.send(event("calliope.turn.completed", json!({"turn_id":"t","assistant_message":"done","response_receipt":{"citations":{"spotlight":[{"surface_id":"new"}]}}}))).unwrap();
        a.poll();
        let chat = &a.chats["general"];
        assert_eq!(chat.turns[0].receipt["citations"]["spotlight"][0]["surface_id"], "new");
        assert!(chat.plan.is_null() && chat.specialist.is_empty());
    }
    #[test]
    fn restart_reconnects_to_an_existing_durable_turn() {
        let mut a = Assistant::default();
        a.tx.send(Update::History("app".into(),Ok(json!({"session":{"id":"session"},"turns":[{"id":"turn","client_turn_id":"client","status":"running","user_message":"hello","origin_context":{"kind":"app_workspace","location":"A"}}]})))).unwrap();
        a.poll();
        let c = &a.chats["app"];
        assert_eq!(c.session, "session");
        assert!(c.retry.as_ref().unwrap().durable);
        assert_eq!(c.retry.as_ref().unwrap().context["location"], "A");
        a.tx.send(Update::History("app".into(),Ok(json!({"session":{"id":"session"},"turns":[{"id":"turn","client_turn_id":"client","status":"completed","user_message":"hello","assistant_message":"done","origin_context":{"kind":"app_workspace"}}]})))).unwrap();
        a.poll();
        assert!(a.chats["app"].retry.is_none());
        assert!(a.chats["app"].error.is_empty());
    }
}
