//! General Callie conversations use the same server sessions as web and desktop.
use crate::{
    api::{Api, Connection},
    app_assistant::{Assistant, ComposerState, Conversation},
    theme::CanvasTheme,
    turn_outputs::{open_requests, turn_outputs},
    ui::{self, Glyph, Tone},
};
use a2ui_gpui::GpuiApp;
use gpui::{prelude::*, *};
use gpui_component::{
    input::{Input, InputEvent, InputState, Textarea},
    text::TextView,
};
use serde_json::{Value, json};
use std::time::Duration;

fn text(v: &Value, key: &str) -> String {
    v[key].as_str().unwrap_or_default().to_owned()
}
fn excerpt(value: &str, limit: usize) -> String {
    if value.trim().eq_ignore_ascii_case("null") {
        return String::new();
    }
    let mut chars = value.chars();
    let mut result = chars.by_ref().take(limit).collect::<String>();
    if chars.next().is_some() {
        result.push('…');
    }
    result
}
fn title(v: &Value) -> String {
    v["title"]
        .as_str()
        .filter(|s| !s.is_empty())
        .unwrap_or("Conversation")
        .into()
}
fn writable(v: &Value) -> bool {
    v["access_role"] == "owner"
        && v["archived"] != true
        && matches!(v["channel"].as_str(), None | Some("" | "web"))
}
fn stage_items(surfaces: &[Value]) -> Vec<Value> {
    let mut seen = std::collections::HashSet::new();
    surfaces
        .iter()
        .filter(|s| {
            s["presentation"]["hidden"] != true
                && s["source"]["suppressed"] != true
                && !(s["kind"] == "sketch"
                    && s["payload"]["auto_created"] == true
                    && s["payload"]["element_count"] == 0)
                && seen.insert(
                    s["lineage_key"]
                        .as_str()
                        .filter(|s| !s.is_empty())
                        .unwrap_or(s["id"].as_str().unwrap_or_default())
                        .to_owned(),
                )
        })
        .cloned()
        .collect()
}

/// Stage cards drawn per page.
const STAGE_PAGE: usize = 12;

pub struct Conversations {
    api: Api,
    theme: CanvasTheme,
    assistant: Assistant,
    sessions: Vec<Value>,
    selected: Option<String>,
    stage: bool,
    stage_filter: String,
    /// Cards drawn in the Stage before "Show more".
    stage_shown: usize,
    /// A conversation a notification asked to open, once the list has it.
    pending_open: Option<String>,
    search: Entity<InputState>,
    input: Entity<ComposerState>,
    _subscriptions: Vec<Subscription>,
    loading: bool,
    error: String,
    list_tx: std::sync::mpsc::Sender<anyhow::Result<Value>>,
    list_rx: std::sync::mpsc::Receiver<anyhow::Result<Value>>,
    scroll: ScrollHandle,
    pub active: bool,
    previews: std::collections::HashMap<String, StagePreview>,
    preview_tx: std::sync::mpsc::Sender<(String, anyhow::Result<StagePreview>)>,
    preview_rx: std::sync::mpsc::Receiver<(String, anyhow::Result<StagePreview>)>,
    /// Stage items the viewer folded; everything else renders in full.
    collapsed: std::collections::HashSet<String>,
    /// Replies whose "worked through" steps are unfolded.
    work_open: std::collections::HashSet<String>,
    /// The Stage item a reply output opened, listed first and highlighted.
    stage_focus: Option<String>,
    /// Server-projected Stage items rendered natively, by surface id.
    /// Stage items drawn natively (shared with desktop Canvas).
    stage_cards: crate::stage_cards::StageCards,
    /// Results of sent card actions: (item, done notice or error).
    act_tx: std::sync::mpsc::Sender<(String, bool, anyhow::Result<String>)>,
    act_rx: std::sync::mpsc::Receiver<(String, bool, anyhow::Result<String>)>,
    /// Team mode changes: (chat key, requested value, server result).
    team_tx: std::sync::mpsc::Sender<(String, bool, anyhow::Result<Value>)>,
    team_rx: std::sync::mpsc::Receiver<(String, bool, anyhow::Result<Value>)>,
    /// After an answer, look for Callie's continuation turn: (deadline, last poll).
    /// Threads awaiting the turn an answer started: (give up at, last reload, notice to clear).
    watch: std::collections::HashMap<String, (std::time::Instant, std::time::Instant, String)>,
    visible_turns: usize,
    notice: String,
}
impl Conversations {
    pub fn new(
        config: Connection,
        theme: CanvasTheme,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Self {
        let search =
            cx.new(|cx| InputState::new(window, cx).placeholder("Find your conversations…"));
        let input = cx.new(|cx| {
            ComposerState::new(window, cx)
                .placeholder("Ask Callie anything…")
                .rows(3)
        });
        let subscriptions = vec![
            cx.subscribe(&search, |_, _, _: &InputEvent, cx| cx.notify()),
            cx.subscribe(&input, |s, _, event: &InputEvent, cx| {
                if matches!(event, InputEvent::Change) {
                    if let Some(key) = &s.selected {
                        s.assistant.chats.entry(key.clone()).or_default().draft =
                            s.input.read(cx).value().to_string();
                    }
                }
                if matches!(event, InputEvent::Focus) {
                    crate::host::input_kind("longText");
                }
                cx.notify();
            }),
        ];
        let (list_tx, list_rx) = std::sync::mpsc::channel();
        let (preview_tx, preview_rx) = std::sync::mpsc::channel();
        let (act_tx, act_rx) = std::sync::mpsc::channel();
        let (team_tx, team_rx) = std::sync::mpsc::channel();
        let api = Api::new(config).expect("validated account");
        let stage_cards = crate::stage_cards::StageCards::new(api.clone(), &theme);
        let mut this = Self {
            api,
            theme,
            assistant: Assistant::default(),
            sessions: vec![],
            selected: None,
            stage: false,
            stage_filter: "All".into(),
            stage_shown: STAGE_PAGE,
            pending_open: None,
            search,
            input,
            _subscriptions: subscriptions,
            loading: false,
            error: String::new(),
            list_tx,
            list_rx,
            scroll: ScrollHandle::new(),
            active: false,
            previews: Default::default(),
            preview_tx,
            preview_rx,
            collapsed: Default::default(),
            work_open: Default::default(),
            stage_focus: None,
            stage_cards,
            act_tx,
            act_rx,
            team_tx,
            team_rx,
            watch: Default::default(),
            visible_turns: 20,
            notice: String::new(),
        };
        this.assistant.load_voice(this.api.clone());
        this.refresh_list();
        cx.spawn(async move |view, cx| {
            loop {
                smol::Timer::after(Duration::from_millis(100)).await;
                if view.update(cx, |s, cx| s.tick(cx)).is_err() {
                    break;
                }
            }
        })
        .detach();
        this
    }
    pub fn report_error(&mut self, message: String, cx: &mut Context<Self>) {
        self.error = message;
        cx.notify();
    }
    /// Open a conversation from a notification or link.
    pub fn open_link(&mut self, session: String, cx: &mut Context<Self>) {
        self.pending_open = Some(session);
        self.stage = false;
        self.refresh_list();
        cx.notify();
    }
    fn refresh_list(&mut self) {
        if self.loading {
            return;
        }
        self.loading = true;
        self.error.clear();
        let api = self.api.clone();
        let tx = self.list_tx.clone();
        std::thread::spawn(move || {
            let _ = tx.send(api.get("/api/calliope/sessions"));
        });
    }
    fn tick(&mut self, cx: &mut Context<Self>) {
        while let Ok(result) = self.list_rx.try_recv() {
            self.loading = false;
            match result {
                Ok(v) => {
                    self.sessions = v["sessions"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter(|s| s["access_role"] == "owner" && s["archived"] != true)
                        .cloned()
                        .collect();
                    self.error.clear();
                }
                Err(e) => self.error = format!("{e:#}"),
            }
            cx.notify();
        }
        self.poll_cards(cx);
        let at_bottom = self.scroll.offset().y.abs() + px(80.) >= self.scroll.max_offset().y;
        if self.assistant.poll() {
            if at_bottom {
                self.scroll.scroll_to_bottom();
            }
            cx.notify();
        }
        let foreground = self.active && crate::host::is_foreground();
        if (!foreground || self.selected.as_deref() != Some(self.assistant.voice_slug.as_str()))
            && self.assistant.voice.phase != crate::voice::Phase::Idle
        {
            self.assistant.voice.cancel();
        }
        for event in self.assistant.voice.poll() {
            let key = self.assistant.voice_slug.clone();
            match event {
                crate::voice::Event::Transcript(text) => {
                    if self.selected.as_ref() == Some(&key) && foreground {
                        let chat = self.assistant.chats.entry(key.clone()).or_default();
                        if !chat.draft.is_empty() {
                            chat.draft.push(' ');
                        }
                        chat.draft.push_str(&text);
                        self.assistant.transcript_pending = Some((key, chat.draft.clone()));
                        self.notice = "Dictation added. Review it before sending.".into();
                    }
                }
                crate::voice::Event::Error(error) => {
                    self.assistant.chats.entry(key).or_default().error = error
                }
                _ => {}
            }
            cx.notify();
        }
        for (key, session, turn) in std::mem::take(&mut self.assistant.completed) {
            if self.assistant.chats[&key].running {
                self.assistant.completed.push((key, session, turn));
                continue;
            }
            if foreground && !self.stage && self.selected.as_ref() == Some(&key) && !turn.is_empty()
            {
                if let Some(settings) = self.assistant.voice_settings.clone().filter(|s| {
                    s.tts
                        && crate::theme::Preferences::load()
                            .read_aloud
                            .unwrap_or(s.auto_read)
                }) {
                    self.assistant.voice_slug = key.clone();
                    self.assistant.voice.speak(
                        self.api.clone(),
                        session.clone(),
                        turn,
                        settings,
                        true,
                    );
                }
            }
            self.assistant.load_thread(self.api.clone(), &key, &session);
            self.refresh_list();
        }
        if self.active && crate::host::take_back() {
            if self.selected.take().is_some() {
                self.stage = false;
                self.refresh_list();
                cx.notify();
            } else {
                crate::host::background();
            }
        }
    }
    fn select_key(&mut self, key: String, window: &mut Window, cx: &mut Context<Self>) {
        let draft = self
            .assistant
            .chats
            .entry(key.clone())
            .or_default()
            .draft
            .clone();
        self.assistant.voice.cancel();
        self.visible_turns = 20;
        self.stage_filter = "All".into();
        self.notice.clear();
        self.selected = Some(key);
        self.stage = false;
        self.stage_focus = None;
        self.error.clear();
        self.input
            .update(cx, |s, cx| s.set_value(draft, window, cx));
        self.scroll = ScrollHandle::new();
        self.scroll.scroll_to_bottom();
        window.blur(cx);
        cx.notify();
    }
    fn new_chat(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if let Some(key) = self
            .assistant
            .chats
            .iter()
            .find(|(_, c)| c.session.is_empty() && c.turns.is_empty() && !c.draft.is_empty())
            .map(|(key, _)| key.clone())
        {
            self.select_key(key, window, cx);
            return;
        }
        let key = format!("new:{}", uuid::Uuid::new_v4());
        self.assistant.chats.insert(
            key.clone(),
            Conversation {
                all_turns: true,
                initialized: true,
                metadata: json!({"title":"New conversation","access_role":"owner","channel":"web"}),
                ..Default::default()
            },
        );
        self.select_key(key, window, cx);
    }
    fn open_thread(
        &mut self,
        id: &str,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> anyhow::Result<()> {
        anyhow::ensure!(
            self.sessions.iter().any(|s| s["id"] == id),
            "Choose one of your conversations"
        );
        uuid::Uuid::parse_str(id)?;
        // A new chat keeps its original key while its initial response is streaming.
        let key = self
            .assistant
            .chats
            .iter()
            .find(|(_, c)| c.session == id)
            .map(|(k, _)| k.clone())
            .unwrap_or_else(|| format!("thread:{id}"));
        if self.selected.as_deref() != Some(key.as_str()) {
            // Another thread's cards are not kept alive in the background.
            self.stage_cards.clear();
            self.stage_shown = STAGE_PAGE;
        }
        self.assistant.load_thread(self.api.clone(), &key, id);
        self.select_key(key, window, cx);
        Ok(())
    }
    fn send(&mut self, retry: bool, window: &mut Window, cx: &mut Context<Self>) {
        let Some(key) = self.selected.clone() else {
            return;
        };
        let c = &self.assistant.chats[&key];
        if !writable(&c.metadata) || (!retry && c.retry.is_some()) {
            return;
        }
        let question = self.input.read(cx).value().to_string();
        if !retry && (question.trim().is_empty() || question.len() > 16_000) {
            return;
        }
        self.assistant.voice.cancel();
        self.notice.clear();
        self.assistant
            .send(self.api.clone(), &key, question, Value::Null, retry);
        if !retry && self.assistant.chats[&key].running {
            self.assistant.chats.get_mut(&key).unwrap().draft.clear();
            self.input.update(cx, |s, cx| s.set_value("", window, cx));
        }
        window.blur(cx);
        self.scroll.scroll_to_bottom();
        cx.notify();
    }
    fn dictate(&mut self, cx: &mut Context<Self>) {
        let Some(key) = self.selected.clone() else {
            return;
        };
        let chat = &self.assistant.chats[&key];
        if !writable(&chat.metadata) || chat.loading || chat.running || chat.retry.is_some() {
            return;
        }
        use crate::voice::Phase;
        self.assistant.chats.get_mut(&key).unwrap().error.clear();
        match self.assistant.voice.phase {
            Phase::Recording => self.assistant.voice.stop_recording(),
            Phase::Idle => {
                if let Some(settings) = self.assistant.voice_settings.clone().filter(|s| s.stt) {
                    if let Err(error) = crate::host::audio_request_permission() {
                        self.assistant.chats.get_mut(&key).unwrap().error = error;
                    } else {
                        self.assistant.voice_slug = key;
                        self.assistant.voice.record(self.api.clone(), settings);
                    }
                }
            }
            _ => self.assistant.voice.cancel(),
        }
        cx.notify();
    }
    fn read_reply(&mut self, index: usize, cx: &mut Context<Self>) {
        if let Some(key) = &self.selected {
            let chat = &self.assistant.chats[key];
            if let (Some(turn), Some(settings)) = (
                chat.turns.get(index),
                self.assistant.voice_settings.clone().filter(|s| s.tts),
            ) {
                if !turn.id.is_empty() && !turn.answer.is_empty() {
                    self.assistant.voice_slug = key.clone();
                    self.assistant.voice.speak(
                        self.api.clone(),
                        chat.session.clone(),
                        turn.id.clone(),
                        settings,
                        false,
                    );
                }
            }
        }
        cx.notify();
    }
    fn copy_reply(&mut self, index: usize, cx: &mut Context<Self>) {
        if let Some(turn) = self
            .selected
            .as_ref()
            .and_then(|key| self.assistant.chats.get(key))
            .and_then(|c| c.turns.get(index))
        {
            cx.write_to_clipboard(ClipboardItem::new_string(
                crate::app_context::prose(&turn.answer).to_owned(),
            ));
            self.notice = "Reply copied.".into();
            cx.notify();
        }
    }
    /// Flip the conversation's Team mode (optimistic, as on the web).
    fn toggle_team(&mut self, cx: &mut Context<Self>) {
        let Some(key) = self.selected.clone() else {
            return;
        };
        let chat = self.assistant.chats.entry(key.clone()).or_default();
        if chat.session.is_empty() || !writable(&chat.metadata) {
            return;
        }
        let next = chat.metadata["team_mode"] != true;
        chat.metadata["team_mode"] = json!(next);
        let (api, tx, session) = (self.api.clone(), self.team_tx.clone(), chat.session.clone());
        std::thread::spawn(move || {
            let result = uuid::Uuid::parse_str(&session)
                .map_err(anyhow::Error::from)
                .and_then(|_| api.patch(&format!("/api/calliope/sessions/{session}"), &json!({"team_mode": next})));
            let _ = tx.send((key, next, result));
        });
        cx.notify();
    }
    fn refresh_thread(&mut self, cx: &mut Context<Self>) {
        // Views that failed to load get another try.
        self.stage_cards.retry_failed();
        if let Some(key) = self.selected.clone() {
            let session = self.assistant.chats[&key].session.clone();
            if !session.is_empty() {
                self.assistant.load_thread(self.api.clone(), &key, &session);
            }
        } else {
            self.refresh_list();
        }
        cx.notify();
    }
    fn open_browser(&mut self, surface: Option<&str>, cx: &mut Context<Self>) {
        let result = (|| -> anyhow::Result<()> {
            let key = self
                .selected
                .as_ref()
                .ok_or_else(|| anyhow::anyhow!("Choose a conversation"))?;
            let id = &self.assistant.chats[key].session;
            uuid::Uuid::parse_str(id)?;
            let mut url = reqwest::Url::parse(&self.api.config.browser_url)?.join("/calliope")?;
            url.query_pairs_mut().append_pair("session", id);
            if let Some(surface) = surface {
                uuid::Uuid::parse_str(surface)?;
                url.query_pairs_mut().append_pair("surface", surface);
            }
            crate::host::open_url(url.as_str()).map_err(anyhow::Error::msg)
        })();
        if let Err(e) = result {
            self.error = e.to_string();
            cx.notify();
        }
    }
    pub fn inspect(&self, cx: &App) -> Value {
        let chat = self
            .selected
            .as_ref()
            .and_then(|k| self.assistant.chats.get(k));
        json!({"previews":self.previews.iter().map(|(id,p)|(id.clone(),json!({"loading":p.loading,"image":p.path.is_some(),"text":p.text.is_some(),"error":p.error}))).collect::<serde_json::Map<String,Value>>(),"voice":format!("{:?}",self.assistant.voice.phase),"audio_host":crate::host::audio_status(),"stt":self.assistant.voice_settings.as_ref().is_some_and(|s|s.stt),"tts":self.assistant.voice_settings.as_ref().is_some_and(|s|s.tts),"collapsed":self.collapsed,"stage_filter":self.stage_filter,"visible_turns":self.visible_turns,"screen":"conversations","selected":self.selected,"stage":self.stage,"loading":self.loading,"error":self.error,"query":self.search.read(cx).value().as_ref(),"sessions":self.sessions,"conversation":chat.map(|c|json!({"session":c.session,"metadata":c.metadata,"loading":c.loading,"running":c.running,"initialized":c.initialized,"error":c.error,"retry":c.retry.is_some(),"draft":c.draft,"turns":c.turns.iter().map(|t|json!({"id":t.id,"question":t.question,"answer":t.answer,"attachments":t.attachments,"working":t.working,"objects":t.objects})).collect::<Vec<_>>(),"surfaces":stage_items(&c.surfaces),"outputs":c.turns.iter().map(|t|{let o=turn_outputs(&t.id,&t.receipt,&stage_items(&c.surfaces));json!({"turn":t.id,"hero":o.hero.as_ref().map(|h|h["id"].clone()),"pointed":o.pointed,"chips":o.chips.iter().map(|s|s["id"].clone()).collect::<Vec<_>>(),"work":o.work.iter().map(|s|s["id"].clone()).collect::<Vec<_>>()})}).collect::<Vec<_>>(),"waiting":open_requests(&stage_items(&c.surfaces)).iter().map(|s|s["id"].clone()).collect::<Vec<_>>(),"plan":c.plan,"specialist":c.specialist})),"stage_focus":self.stage_focus,"work_open":self.work_open})
    }
    pub fn probe(&mut self, request: &Value, window: &mut Window, cx: &mut Context<Self>) -> Value {
        let result = (|| -> anyhow::Result<Value> {
            match request["op"].as_str().unwrap_or("") {
                "inspect" => return Ok(self.inspect(cx)),
                "fill" => match request["id"].as_str().unwrap_or("") {
                    "chat-search" => self
                        .search
                        .update(cx, |s, cx| s.set_value(text(request, "value"), window, cx)),
                    "chat-message" => {
                        let value = text(request, "value");
                        if let Some(key) = &self.selected {
                            self.assistant.chats.entry(key.clone()).or_default().draft =
                                value.clone();
                        }
                        // Programmatic set_value intentionally does not emit Change.
                        self.input
                            .update(cx, |s, cx| s.set_value(value, window, cx));
                    }
                    _ => anyhow::bail!("Unknown chat field"),
                },
                "press" => match request["id"].as_str().unwrap_or("") {
                    "chat-new" => self.new_chat(window, cx),
                    "chat-back" => {
                        self.selected = None;
                        self.stage = false;
                        self.refresh_list();
                    }
                    "chat-refresh" => self.refresh_thread(cx),
                    "chat-team" => self.toggle_team(cx),
                    "chat-open" => self.open_thread(&text(request, "session"), window, cx)?,
                    "chat-stage" => {
                        self.assistant.voice.cancel();
                        self.stage = true;
                        window.blur(cx);
                    }
                    "chat-messages" => {
                        self.stage = false;
                        self.stage_focus = None;
                    }
                    "turn-open" => self.focus_stage(&text(request, "surface")),
                    "turn-work" => self.toggle_work(&text(request, "turn")),
                    #[cfg(all(debug_assertions, target_os = "ios"))]
                    "chat-transcribe-fixture" => {
                        let key = self
                            .selected
                            .clone()
                            .ok_or_else(|| anyhow::anyhow!("Choose a draft"))?;
                        let pcm =
                            std::fs::read(crate::model::data_dir().join("test-dictation.pcm"))?;
                        anyhow::ensure!(
                            !pcm.is_empty() && pcm.len() <= 2 * 1024 * 1024 && pcm.len() % 2 == 0,
                            "Invalid test PCM"
                        );
                        self.assistant.voice_slug = key;
                        self.assistant
                            .voice
                            .transcribe_fixture(self.api.clone(), pcm);
                    }
                    "chat-dictate" => self.dictate(cx),
                    "chat-read" => {
                        self.read_reply(request["index"].as_u64().unwrap_or(0) as usize, cx)
                    }
                    "chat-copy" => {
                        self.copy_reply(request["index"].as_u64().unwrap_or(0) as usize, cx)
                    }
                    "chat-stop-audio" => self.assistant.voice.cancel(),
                    "chat-older" => self.visible_turns += 20,
                    "stage-filter" => {
                        let filter = text(request, "value");
                        anyhow::ensure!(
                            ["All", "Data", "Images", "Apps", "Files"].contains(&filter.as_str()),
                            "Unknown Stage filter"
                        );
                        self.stage_filter = filter;
                        self.stage_focus = None;
                    }
                    "stage-open-app" => self.open_stage_app(&text(request, "surface"), cx),
                    "stage-preview" => self.toggle_stage(&text(request, "surface")),
                    "chat-send" => self.send(false, window, cx),
                    "chat-retry" => self.send(true, window, cx),
                    "chat-stop" => {
                        if let Some(key) = &self.selected {
                            self.assistant.stop(self.api.clone(), key);
                        }
                    }
                    "chat-browser" => self.open_browser(request["surface"].as_str(), cx),
                    _ => anyhow::bail!("Unknown chat action"),
                },
                _ => anyhow::bail!("Unknown chat operation"),
            }
            cx.notify();
            Ok(json!({"ok":true}))
        })();
        result.unwrap_or_else(|e| json!({"error":e.to_string()}))
    }
}
impl Render for Conversations {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        if let Some(id) = self.pending_open.clone() {
            if self.sessions.iter().any(|s| s["id"] == id.as_str()) {
                self.pending_open = None;
                if let Err(e) = self.open_thread(&id, window, cx) {
                    self.error = e.to_string();
                }
            }
        }
        if let Some((key, text)) = self.assistant.transcript_pending.take() {
            if self.selected.as_ref() == Some(&key) {
                self.input.update(cx, |s, cx| s.set_value(text, window, cx));
            }
        }
        self.poll_stage(cx);
        let mut root = div()
            .size_full()
            .min_h_0()
            .flex()
            .flex_col()
            .text_color(self.theme.text)
            .font_family(self.theme.font_body.clone());
        if self.selected.is_none() {
            let t = &self.theme;
            root = root
                .child(
                    div()
                        .px_4()
                        .pt_3()
                        .pb_2()
                        .flex()
                        .items_center()
                        .gap_2()
                        .child(crate::brand::avatar(40.))
                        .child(div().flex_1().child(crate::brand::wordmark(23.)))
                        .child(ui::icon_button(
                            "chat-refresh",
                            Glyph::RefreshCw,
                            t,
                            !self.loading,
                            cx.listener(|s, _, _, cx| s.refresh_thread(cx)),
                        )),
                )
                .child(
                    div().px_4().pb_3().child(
                        Input::new(&self.search)
                            .prefix(ui::glyph(Glyph::Search, 16., t.muted))
                            .cleanable(true),
                    ),
                );
            let query = self.search.read(cx).value().to_lowercase();
            let mut list = div()
                .id("conversation-list")
                .flex_1()
                .min_h_0()
                .overflow_y_scroll()
                .px_4()
                .pb_4()
                .flex()
                .flex_col()
                .gap_2();
            list = list.child(ui::caption(
                if self.loading {
                    "Loading conversations…".into()
                } else {
                    format!("Your conversations · {}", self.sessions.len())
                },
                &self.theme,
            ));
            if let Some(key) = self
                .assistant
                .chats
                .iter()
                .find(|(_, c)| c.session.is_empty() && c.turns.is_empty() && !c.draft.is_empty())
                .map(|(key, _)| key.clone())
            {
                list = list.child(div().flex().child(ui::chip(
                    "chat-unsent",
                    Some(Glyph::SquarePen),
                    "Continue unsent message",
                    None,
                    true,
                    &self.theme,
                    true,
                    cx.listener(move |s, _, w, cx| s.select_key(key.clone(), w, cx)),
                )));
            }
            let mut count = 0;
            for session in self.sessions.iter().filter(|s| {
                format!("{} {}", title(s), text(s, "synopsis"))
                    .to_lowercase()
                    .contains(&query)
            }) {
                count += 1;
                let id = text(session, "id");
                let selected = id.clone();
                let synopsis = excerpt(&text(session, "synopsis"), 120);
                let date = text(session, "updated_at")
                    .chars()
                    .take(10)
                    .collect::<String>();
                let detail = format!(
                    "{date} · {} exchanges · {} stage items{}",
                    session["turn_count"].as_u64().unwrap_or(0),
                    session["surface_count"].as_u64().unwrap_or(0),
                    if writable(session) {
                        ""
                    } else {
                        " · View only"
                    }
                );
                list = list.child(
                    div()
                        .id(SharedString::from(format!("thread-{id}")))
                        .flex_shrink_0()
                        .p_4()
                        .rounded_xl()
                        .bg(self.theme.card)
                        .border_1()
                        .border_color(self.theme.line)
                        .cursor_pointer()
                        .flex()
                        .flex_col()
                        .gap_2()
                        .child(
                            div()
                                .font_weight(FontWeight::SEMIBOLD)
                                .child(title(session)),
                        )
                        .when(!synopsis.is_empty(), |s| {
                            s.child(div().text_sm().text_color(self.theme.muted).child(synopsis))
                        })
                        .child(
                            div()
                                .text_size(px(11.))
                                .text_color(self.theme.muted)
                                .child(detail),
                        )
                        .on_click(cx.listener(move |s, _, w, cx| {
                            if let Err(e) = s.open_thread(&selected, w, cx) {
                                s.error = e.to_string();
                                cx.notify();
                            }
                        })),
                );
            }
            if count == 0 && !self.loading {
                list = list.child(div().py_6().child(if query.is_empty() {
                    "Start a conversation with Callie. Your threads from the web appear here too."
                } else {
                    "No conversations match your search."
                }));
            }
            let t = &self.theme;
            // Compose floats over the list, as in mail and messaging apps.
            let compose = div()
                .id("chat-new")
                .absolute()
                .right_4()
                .bottom_4()
                .h(px(52.))
                .px_5()
                .rounded(px(18.))
                .bg(t.accent)
                .text_color(t.on_accent)
                .shadow_lg()
                .flex()
                .items_center()
                .gap_2()
                .font_weight(FontWeight::SEMIBOLD)
                .child(ui::glyph(Glyph::SquarePen, 20., t.on_accent))
                .child("New chat")
                .cursor_pointer()
                .active(|s| s.opacity(0.85))
                .on_click(cx.listener(|s, _, w, cx| s.new_chat(w, cx)));
            return root
                .child(div().relative().flex_1().min_h_0().flex().flex_col().child(list.pb(px(88.))).child(compose))
                .when(!self.error.is_empty(), |s| {
                    s.child(
                        div()
                            .p_3()
                            .text_color(self.theme.warning)
                            .child(self.error.clone()),
                    )
                });
        }
        let key = self.selected.clone().unwrap();
        let c = &self.assistant.chats[&key];
        let session = c.session.clone();
        let loading = c.loading;
        let running = c.running;
        let retry = c.retry.is_some();
        let initialized = c.initialized;
        let can_write = writable(&c.metadata);
        let error = c.error.clone();
        let progress = c.progress.clone();
        let turns = c.turns.clone();
        let items = stage_items(&c.surfaces);
        let requests = open_requests(&items);
        let mut status_lines = Vec::new();
        let summary = &c.plan["summary"];
        if summary["total"].as_u64().unwrap_or(0) > 0 {
            let current = c.plan["todos"]
                .as_array()
                .into_iter()
                .flatten()
                .find(|t| t["status"] == "in_progress")
                .and_then(|t| t["content"].as_str())
                .map(|t| format!(" · {}", excerpt(t, 60)))
                .unwrap_or_default();
            status_lines.push(format!(
                "Plan {} of {}{current}",
                summary["completed"].as_u64().unwrap_or(0),
                summary["total"].as_u64().unwrap_or(0)
            ));
        }
        if !c.specialist.is_empty() {
            status_lines.push(c.specialist.clone());
        }
        let short = f32::from(window.viewport_size().height) < 320.;
        let editing = self.input.read(cx).focus_handle(cx).is_focused(window);
        let t = self.theme.clone();
        let team = c.metadata["team_mode"] == true;
        root = root
            .child(
                div()
                    .pl_1()
                    .pr_2()
                    .py_1()
                    .flex()
                    .items_center()
                    .gap_1()
                    .flex_shrink_0()
                    .child(ui::icon_button(
                        "chat-back",
                        Glyph::ChevronLeft,
                        &t,
                        true,
                        cx.listener(|s, _, w, cx| {
                            w.blur(cx);
                            s.selected = None;
                            s.refresh_list();
                            cx.notify();
                        }),
                    ))
                    .child(
                        div()
                            .min_w_0()
                            .flex_1()
                            .truncate()
                            .text_size(px(16.))
                            .font_weight(FontWeight::SEMIBOLD)
                            .child(title(&c.metadata)),
                    )
                    .when(!session.is_empty() && !editing, |row| {
                        row.child(ui::icon_button(
                            "chat-browser",
                            Glyph::ExternalLink,
                            &t,
                            true,
                            cx.listener(|s, _, _, cx| s.open_browser(None, cx)),
                        ))
                    })
                    .child(if editing {
                        ui::tinted_icon_button(
                            "chat-new",
                            Glyph::Check,
                            t.accent,
                            &t,
                            true,
                            cx.listener(|_, _, w, cx| {
                                w.blur(cx);
                                cx.notify();
                            }),
                        )
                    } else {
                        ui::icon_button("chat-new", Glyph::SquarePen, &t, true, cx.listener(|s, _, w, cx| s.new_chat(w, cx)))
                    }),
            )
            .when(!short || self.stage, |s| {
                s.child(
                    div()
                        .px_3()
                        .pb_2()
                        .flex()
                        .items_center()
                        .gap_1()
                        .flex_shrink_0()
                        .child(
                            ui::segmented(&t)
                                .flex_1()
                                .child(ui::segment(
                                    "chat-messages",
                                    "Chat",
                                    None,
                                    !self.stage,
                                    &t,
                                    cx.listener(|s, _, _, cx| {
                                        s.stage = false;
                                        s.stage_focus = None;
                                        cx.notify();
                                    }),
                                ))
                                .child(ui::segment(
                                    "chat-stage",
                                    "Stage",
                                    Some(items.len()),
                                    self.stage,
                                    &t,
                                    cx.listener(|s, _, w, cx| {
                                        w.blur(cx);
                                        s.assistant.voice.cancel();
                                        s.stage = true;
                                        cx.notify();
                                    }),
                                )),
                        )
                        .when(!c.session.is_empty(), |row| {
                            // The web composer's Team toggle: Callie plans and delegates
                            // workstreams to specialists instead of working solo.
                            row.child(ui::chip(
                                "chat-team",
                                Some(Glyph::Users),
                                "Team",
                                None,
                                team,
                                &t,
                                can_write && !running,
                                cx.listener(|s, _, _, cx| s.toggle_team(cx)),
                            ))
                        })
                        .child(ui::icon_button(
                            "chat-refresh",
                            Glyph::RefreshCw,
                            &t,
                            !(loading || running),
                            cx.listener(|s, _, _, cx| s.refresh_thread(cx)),
                        )),
                )
            });
        let mut body = div()
            .id(SharedString::from(format!("messages-{key}-{}", self.stage)))
            .flex_1()
            .min_h_0()
            .overflow_y_scroll()
            .px_4()
            .py_3()
            .flex()
            .flex_col()
            .gap_4();
        if self.stage {
            let mut filters = div().flex().flex_wrap().gap_x_2();
            for filter in ["All", "Data", "Images", "Apps", "Files"] {
                let count = items
                    .iter()
                    .filter(|i| stage_category(i) == filter || filter == "All")
                    .count();
                if count == 0 {
                    continue;
                }
                filters = filters.child(ui::chip(
                    SharedString::from(format!("stage-filter-{filter}")),
                    None,
                    filter,
                    Some(count),
                    self.stage_filter == filter,
                    &t,
                    true,
                    cx.listener(move |s, _, _, cx| {
                        s.stage_filter = filter.into();
                        s.stage_focus = None;
                        s.stage_shown = STAGE_PAGE;
                        cx.notify();
                    }),
                ));
            }
            body = body.child(filters);
            // Per turn, as the chat reads: the reply's deliverables in full, its
            // working steps folded until opened (the web's Zen shape), and at
            // most `stage_shown` cards drawn so long threads stay light.
            enum Entry<'a> { Asked(&'a str), Card(&'a Value), Fold(String, usize), More(usize) }
            let mut ordered = items.clone();
            if let Some(focus) = &self.stage_focus {
                ordered.sort_by_key(|item| item["id"].as_str() != Some(focus.as_str()));
            }
            let questions: std::collections::HashMap<&str, &str> =
                turns.iter().map(|t| (t.id.as_str(), t.question.as_str())).collect();
            let work: std::collections::HashSet<String> = turns
                .iter()
                .flat_map(|t| turn_outputs(&t.id, &t.receipt, &items).work)
                .filter_map(|s| s["id"].as_str().map(str::to_owned))
                .collect();
            let mut plan: Vec<Entry> = Vec::new();
            let (mut last_turn, mut folded, mut drawn, mut hidden) = (String::new(), 0usize, 0usize, 0usize);
            for item in ordered.iter().filter(|i| self.stage_filter == "All" || stage_category(i) == self.stage_filter) {
                let turn = text(item, "turn_id");
                if turn != last_turn {
                    if folded > 0 {
                        plan.push(Entry::Fold(last_turn.clone(), folded));
                    }
                    folded = 0;
                    if let Some(question) = questions.get(turn.as_str()).filter(|q| !q.trim().is_empty()) {
                        plan.push(Entry::Asked(question));
                    }
                    last_turn = turn.clone();
                }
                let id = text(item, "id");
                let focused = self.stage_focus.as_deref() == Some(id.as_str());
                if work.contains(&id) && !focused && !self.work_open.contains(&turn) && self.stage_filter == "All" {
                    folded += 1;
                } else if drawn >= self.stage_shown && !focused {
                    hidden += 1;
                } else {
                    drawn += 1;
                    plan.push(Entry::Card(item));
                }
            }
            if folded > 0 {
                plan.push(Entry::Fold(last_turn.clone(), folded));
            }
            if hidden > 0 {
                plan.push(Entry::More(hidden));
            }
            // One batched request for the native views about to be drawn.
            let session = self.assistant.chats.get(&key).map(|c| c.session.clone()).unwrap_or_default();
            let projectable: Vec<(String, String)> = plan
                .iter()
                .filter_map(|entry| match entry { Entry::Card(item) if projected(item) => Some(*item), _ => None })
                .map(|i| (text(i, "id"), card_version_of(i)))
                .collect();
            self.stage_cards.request(&session, &projectable);
            for entry in plan {
                body = match entry {
                    Entry::Asked(question) => body.child(
                        div()
                            .pt_2()
                            .flex()
                            .gap_2()
                            .text_size(px(12.))
                            .text_color(self.theme.muted)
                            .child(ui::glyph(Glyph::MessageCircle, 14., self.theme.muted).mt(px(1.)))
                            .child(div().flex_1().min_w_0().child(excerpt(question, 140))),
                    ),
                    Entry::Card(item) => body.child(self.stage_card(item, cx)),
                    Entry::Fold(turn, count) => body.child(
                        div().flex().child(ui::chip(
                            SharedString::from(format!("stage-fold-{turn}")),
                            Some(Glyph::Layers),
                            format!("Worked through {count} step{}", if count == 1 { "" } else { "s" }),
                            None,
                            false,
                            &t,
                            true,
                            cx.listener(move |s, _, _, cx| {
                                s.toggle_work(&turn);
                                cx.notify();
                            }),
                        )),
                    ),
                    Entry::More(count) => body.child(
                        div().flex().justify_center().child(ui::action(
                            "stage-more",
                            Some(Glyph::ChevronDown),
                            format!("Show {count} more"),
                            Tone::Outline,
                            &t,
                            true,
                            cx.listener(|s, _, _, cx| {
                                s.stage_shown += STAGE_PAGE;
                                cx.notify();
                            }),
                        )),
                    ),
                };
            }
            if items.is_empty() {
                body = body.child(
                    div()
                        .py_8()
                        .flex()
                        .flex_col()
                        .items_center()
                        .gap_3()
                        .text_color(self.theme.muted)
                        .child(ui::glyph(Glyph::Layers, 32., self.theme.muted))
                        .child(div().text_center().child("Charts, tables and files Callie makes in this conversation appear here.")),
                );
            }
        } else {
            body = body.track_scroll(&self.scroll);
            if turns.is_empty() {
                body=body.child(div().py_6().flex().flex_col().gap_3().child(crate::brand::avatar(56.)).child(div().text_size(px(24.)).child("What are we working on?"))
                .child(div().text_color(self.theme.muted).child(if loading{"Loading your conversation…"}else{"Ask a question, explore your data, or pick up a piece of work with Callie."})));
            }
            let start = turns.len().saturating_sub(self.visible_turns);
            if start > 0 {
                body = body.child(div().flex().justify_center().child(ui::action(
                    "chat-older",
                    Some(Glyph::ChevronUp),
                    format!("Earlier exchanges ({start})"),
                    Tone::Outline,
                    &t,
                    true,
                    cx.listener(|s, _, _, cx| {
                        s.visible_turns += 20;
                        cx.notify();
                    }),
                )));
            }
            for (index, turn) in turns.iter().enumerate().skip(start) {
                let browser_base = self.api.config.browser_url.clone();
                let link_owner = cx.entity().downgrade();
                let attached = turn
                    .attachments
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|a| ["name", "filename", "original_name"].iter().find_map(|k| a[k].as_str()))
                    .collect::<Vec<_>>()
                    .join(", ");
                let outputs = turn_outputs(&turn.id, &turn.receipt, &items);
                let hero = outputs.hero.as_ref().map(|h| self.hero_card(h, outputs.pointed, cx));
                let extras = self.turn_extras(&turn.id, &outputs.chips, &outputs.work, cx);
                body = body.child(
                    div()
                        .flex_shrink_0()
                        .min_w_0()
                        .flex()
                        .flex_col()
                        .gap_3()
                        .child(
                            div().flex().justify_end().pl_8().child(
                                div()
                                    .min_w_0()
                                    .px_4()
                                    .py_2p5()
                                    .rounded(px(20.))
                                    .rounded_br(px(6.))
                                    .bg(self.theme.accent_soft)
                                    .child(turn.question.clone()),
                            ),
                        )
                        .when(!attached.is_empty(), |row| {
                            row.child(
                                div()
                                    .flex()
                                    .justify_end()
                                    .items_center()
                                    .gap_1()
                                    .text_size(px(11.))
                                    .text_color(self.theme.muted)
                                    .child(ui::glyph(Glyph::Paperclip, 12., self.theme.muted))
                                    .child(attached),
                            )
                        })
                        .child(
                            div()
                                .flex()
                                .items_center()
                                .gap_2()
                                .child(crate::brand::avatar(24.))
                                .child(
                                    div()
                                        .text_sm()
                                        .font_weight(FontWeight::SEMIBOLD)
                                        .child("Callie"),
                                ),
                        )
                        .child(
                            TextView::markdown(
                                SharedString::from(format!("reply-{key}-{index}")),
                                crate::app_context::prose(&turn.answer).to_owned(),
                            )
                            .selectable(true)
                            .scrollable(false)
                            .on_link_click(move |href, _, _, cx| {
                                match crate::app_pins::reference_from_url(&browser_base,href.as_ref()) {
                                    Ok(Some(reference))=>{
                                        let _=link_owner.update(cx,|s,cx|{
                                            s.assistant.voice.cancel();
                                            cx.emit(OpenApp(reference["slug"].as_str().unwrap().to_owned(),Some(reference)));
                                        });
                                        return;
                                    }
                                    Err(error)=>{let _=link_owner.update(cx,|s,cx|s.report_error(error.to_string(),cx));return;}
                                    Ok(None)=>{}
                                }
                                if let Ok(base) = reqwest::Url::parse(&browser_base) {
                                    if let Ok(url) = base.join(href.as_ref()) {
                                        let _ = crate::host::open_url(url.as_str());
                                    }
                                }
                            }),
                        )
                        .when_some(hero, |row, hero| row.child(hero))
                        .when_some(extras, |row, extras| row.child(extras))
                        .when(!turn.answer.is_empty(), |row| {
                            row.child(
                                div()
                                    .flex()
                                    .ml(px(-10.))
                                    .mt(px(-8.))
                                    .child(ui::tinted_icon_button(
                                        SharedString::from(format!("copy-{index}")),
                                        Glyph::Copy,
                                        self.theme.muted,
                                        &t,
                                        true,
                                        cx.listener(move |s, _, _, cx| s.copy_reply(index, cx)),
                                    ))
                                    .when(
                                        !turn.id.is_empty()
                                            && self
                                                .assistant
                                                .voice_settings
                                                .as_ref()
                                                .is_some_and(|s| s.tts),
                                        |actions| {
                                            actions.child(ui::tinted_icon_button(
                                                SharedString::from(format!("read-{index}")),
                                                Glyph::Volume2,
                                                self.theme.muted,
                                                &t,
                                                true,
                                                cx.listener(move |s, _, _, cx| s.read_reply(index, cx)),
                                            ))
                                        },
                                    ),
                            )
                        }),
                );
            }
        }
        root = root.child(body);
        if !error.is_empty() {
            root = root.child(
                div()
                    .px_4()
                    .py_2()
                    .text_sm()
                    .text_color(self.theme.warning)
                    .child(error),
            );
        }
        if !self.error.is_empty() {
            root = root.child(
                div()
                    .px_4()
                    .text_color(self.theme.warning)
                    .child(self.error.clone()),
            );
        }
        if !initialized && !loading {
            root = root.child(div().px_4().py_2().flex().child(ui::action(
                "chat-reload",
                Some(Glyph::RotateCw),
                "Retry loading",
                Tone::Tonal,
                &t,
                true,
                cx.listener(|s, _, _, cx| s.refresh_thread(cx)),
            )));
        }
        if self.stage {
            return root;
        }
        use crate::voice::Phase;
        let phase = self.assistant.voice.phase;
        if phase != Phase::Idle {
            let label = match phase {
                Phase::Recording => format!(
                    "Listening… {:.0}%",
                    (self.assistant.voice.level * 200.).min(100.)
                ),
                Phase::Transcribing => "Transcribing…".into(),
                Phase::Preparing => "Preparing voice…".into(),
                Phase::Playing => "Reading aloud…".into(),
                _ => String::new(),
            };
            root = root.child(
                div()
                    .mx_3()
                    .mb_2()
                    .pl_4()
                    .pr_1()
                    .rounded_full()
                    .bg(t.accent_soft)
                    .flex()
                    .items_center()
                    .gap_2()
                    .child(ui::glyph(
                        if phase == Phase::Playing { Glyph::Volume2 } else { Glyph::Mic },
                        18.,
                        t.accent,
                    ))
                    .child(
                        div()
                            .flex_1()
                            .text_sm()
                            .text_color(t.accent)
                            .child(label),
                    )
                    .when(phase == Phase::Recording, |s| {
                        s.child(ui::action(
                            "chat-finish-dictation",
                            Some(Glyph::Check),
                            "Done",
                            Tone::Primary,
                            &t,
                            true,
                            cx.listener(|s, _, _, cx| s.dictate(cx)),
                        ))
                    })
                    .child(ui::tinted_icon_button(
                        "chat-stop-audio",
                        Glyph::X,
                        t.accent,
                        &t,
                        true,
                        cx.listener(|s, _, _, cx| {
                            s.assistant.voice.cancel();
                            cx.notify();
                        }),
                    )),
            );
        } else if !self.notice.is_empty() && !short {
            root = root.child(
                div()
                    .px_4()
                    .text_size(px(11.))
                    .text_color(self.theme.muted)
                    .child(self.notice.clone()),
            );
        }
        if let Some(card) = self.waiting_card(&requests, &session, cx) {
            root = root.child(card);
        }
        let invalid = self.input.read(cx).value().trim().is_empty()
            || self.input.read(cx).value().len() > 16_000;
        root = root
            .when(running, |s| {
                s.child(
                    div()
                        .pl_4()
                        .pr_3()
                        .flex()
                        .items_center()
                        .gap_2()
                        .child(
                            div()
                                .flex_1()
                                .min_w_0()
                                .flex()
                                .flex_col()
                                .text_sm()
                                .text_color(self.theme.muted)
                                .child(progress)
                                .children(status_lines.into_iter().map(|line| {
                                    div().text_size(px(11.)).truncate().child(line)
                                })),
                        )
                        .child(ui::chip(
                            "chat-stop",
                            Some(Glyph::Square),
                            "Stop",
                            None,
                            false,
                            &t,
                            true,
                            cx.listener(|s, _, _, cx| {
                                if let Some(key) = &s.selected {
                                    s.assistant.stop(s.api.clone(), key);
                                }
                                cx.notify();
                            }),
                        )),
                )
            })
            .when(retry && !running, |s| {
                s.child(div().px_3().pb_2().flex().child(ui::action(
                    "chat-retry",
                    Some(Glyph::RefreshCw),
                    "Reconnect to reply",
                    Tone::Primary,
                    &t,
                    true,
                    cx.listener(|s, _, w, cx| s.send(true, w, cx)),
                )))
            })
            .when(!can_write && initialized, |s| {
                s.child(
                    div()
                        .p_3()
                        .text_sm()
                        .text_color(self.theme.muted)
                        .child("This thread is view only. Start a new chat to talk with Callie."),
                )
            })
            .when(can_write, |s| {
                let busy = loading || running || retry || !initialized;
                let listening = matches!(phase, Phase::Recording | Phase::Transcribing);
                let dictation = self.assistant.voice_settings.as_ref().is_some_and(|s| s.stt) && phase == Phase::Idle;
                s.child(
                    div().px_3().pt_1().pb_2().flex_shrink_0().child(
                        div()
                            .pl_3()
                            .pr_1()
                            .py_1()
                            .rounded(px(24.))
                            .bg(t.card)
                            .border_1()
                            .border_color(t.line)
                            .flex()
                            .items_end()
                            .gap_1()
                            .child(
                                div().flex_1().min_w_0().py_0p5().child(
                                    Textarea::new(&self.input)
                                        .appearance(false)
                                        .h(px(if f32::from(window.viewport_size().height) < 450. { 44. } else { 64. }))
                                        .disabled(busy || listening),
                                ),
                            )
                            .when(dictation, |row| {
                                row.child(ui::tinted_icon_button(
                                    "chat-dictate",
                                    Glyph::Mic,
                                    t.muted,
                                    &t,
                                    !busy,
                                    cx.listener(|s, _, w, cx| {
                                        w.blur(cx);
                                        s.dictate(cx);
                                    }),
                                ))
                            })
                            .child(ui::round_action(
                                "chat-send",
                                Glyph::ArrowUp,
                                &t,
                                !(busy || invalid || listening),
                                cx.listener(|s, _, w, cx| s.send(false, w, cx)),
                            )),
                    ),
                )
            });
        root
    }
}

include!("conversation_stage.rs");
