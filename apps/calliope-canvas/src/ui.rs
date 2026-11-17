mod board_live;
mod board_view;
mod case_workspace;

use crate::mailbox::Sender;
use crate::model::Surface;
use crate::{
    api::{Api, Connection, browser_link},
    model::*,
    theme::{self, CanvasTheme, Preferences, with_alpha},
    voice::{self, Phase},
};
use gpui::{prelude::*, *};
use gpui_component::{
    input::{Input, InputEvent, InputState},
    scroll::{Scrollbar, ScrollbarShow},
    text::TextView,
};
use serde_json::Value;
use std::{
    collections::{HashMap, HashSet},
    path::PathBuf,
    sync::mpsc::Receiver,
    time::{Duration, Instant},
};

enum Update {
    SaveFailed(String),
    Cases(Result<Vec<Value>, String>),
    Scoped(u64, Box<Update>),
    Appearance(Box<theme::Assets>),
    VoiceSettings(voice::Settings),
    FoundationError(String),
    Sessions(Vec<Session>),
    Thread(Thread),
    Login(Connection),
    Contents(String, Value),
    Preview(String, Value),
    PreviewFailed(String, String),
    BoardRead(u64, Result<Value, String>),
    TopicCreated(Session),
    Image(String, PathBuf),
    ImageFailed(String, String),
    Created(Session),
    Event(String, String, Value),
    Done(String),
    TurnStartError(String),
    Error(String),
}

#[derive(Clone)]
struct Updates {
    tx: Sender<Update>,
    epoch: u64,
}
impl Updates {
    fn send(&self, update: Update) -> anyhow::Result<()> {
        self.tx
            .send(Update::Scoped(self.epoch, Box::new(update)))
            .map_err(|_| anyhow::anyhow!("Canvas closed"))
    }
}

#[derive(Clone)]
enum Drag {
    Meaning(String, Point<Pixels>, f32, f32),
    MeaningResize(String, Point<Pixels>, [f32; 2]),
    Record(String, Point<Pixels>, f32, f32),
    RecordResize(String, Point<Pixels>, [f32; 2]),
    Case(String, Point<Pixels>, f32, f32),
    Card(String, Point<Pixels>, f32, f32),
    Pan(Point<Pixels>, [f32; 2]),
    Resize(String, Point<Pixels>, [f32; 2], [bool; 2]),
    Topic(String, Point<Pixels>, f32, f32),
}

pub struct CanvasApp {
    meaning: Option<Entity<crate::meaning::Meaning>>,
    meaning_open: bool,
    pending_meaning_prompt: Option<(String, String)>,
    record_pins: Option<Entity<crate::app_pins::Pins>>,
    case_cards: Vec<Value>,
    case_poll: Instant,
    case_polling: bool,
    case_stale: bool,
    pending_case_prompt: Option<(String, String)>,
    business: Option<Entity<crate::business::BusinessDesk>>,
    business_open: bool,
    apps: Option<Entity<crate::published_apps::PublishedApps>>,
    apps_open: bool,
    theme: CanvasTheme,
    preferences: Preferences,
    appearance_open: bool,
    voice: voice::Voice,
    voice_settings: voice::Settings,
    voice_script: String,
    spoken_turns: HashSet<String>,
    reply_turn: Option<(String, String)>,
    epoch: u64,
    config: Connection,
    home: Home,
    home_writer: crate::deferred_save::DeferredSave<Home>,
    sessions: Vec<Session>,
    threads: HashMap<String, Thread>,
    pending: HashSet<String>,
    contents: HashMap<String, Value>,
    previews: HashMap<String, Value>,
    board_reads: crate::live::BoardReads,
    images: HashMap<String, PathBuf>,
    preview_pending: HashSet<String>,
    /// Stage items drawn natively, keyed by placement; rebuilt per connection.
    stage_cards: Option<(u64, crate::stage_cards::StageCards)>,
    preview_attempts: HashMap<String, Instant>,
    preview_errors: HashMap<String, String>,
    composer_drafts: HashMap<String, String>,
    selected: Option<String>,
    target: Option<String>,
    drawer: bool,
    browse_thread: Option<String>,
    inspector: bool,
    inspector_wide: bool,
    history: bool,
    unwrap: bool,
    login: bool,
    connection_editing: bool,
    server_address: Entity<InputState>,
    connected: bool,
    busy: bool,
    running: bool,
    running_session: Option<String>,
    draft: String,
    reply: String,
    reply_visible: bool,
    status: String,
    error: Option<String>,
    drag: Option<Drag>,
    viewport: Bounds<Pixels>,
    seed_ids: Vec<String>,
    tx: Sender<Update>,
    rx: Receiver<Update>,
    composer: Entity<InputState>,
    search: Entity<InputState>,
    email: Entity<InputState>,
    password: Entity<InputState>,
    _subscriptions: Vec<Subscription>,
}

impl CanvasApp {
    pub fn new(window: &mut Window, cx: &mut Context<Self>) -> Self {
        let config = Connection::load();
        let home = Home::load(&config.account());
        let initial_target = home.active_topic().map(|t| t.session_id.clone());
        let composer = cx.new(|cx| {
            InputState::new(window, cx)
                .placeholder("Ask Callie, or select something to talk about it…")
        });
        let search =
            cx.new(|cx| InputState::new(window, cx).placeholder("Find a conversation or object"));
        let server_address=cx.new(|cx|InputState::new(window,cx).placeholder("https://your-calliope.example"));
        server_address.update(cx,|s,cx|s.set_value(config.base_url.clone(),window,cx));
        let email = cx.new(|cx| InputState::new(window, cx).placeholder("Email"));
        email.update(cx, |s, cx| s.set_value(config.email.clone(), window, cx));
        let password = cx.new(|cx| {
            InputState::new(window, cx)
                .placeholder("Password")
                .masked(true)
        });
        let subs = vec![
            cx.subscribe_in(&composer, window, |this, _, e, window, cx| {
                if matches!(e, InputEvent::PressEnter { .. }) {
                    this.send(window, cx)
                }
            }),
            cx.subscribe(&search, |_, _, _: &InputEvent, cx| cx.notify()),
            cx.subscribe_in(&password, window, |this, _, e, window, cx| {
                if matches!(e, InputEvent::PressEnter { .. }) {
                    this.sign_in(window, cx)
                }
            }),
        ];
        let (tx, rx, wake) = crate::mailbox::channel();
        let save_tx = tx.clone();
        let home_writer = crate::deferred_save::DeferredSave::new(move |mut home: Home| {
            if let Err(error) = home.save() {
                eprintln!("Could not save the canvas: {error}");
                let _ = save_tx.send(Update::SaveFailed(error.to_string()));
            }
        });
        let mut app = Self {
            meaning: None,
            meaning_open: std::env::args().any(|a|a=="--meaning"),
            pending_meaning_prompt: None,
            record_pins: None,
            case_cards: vec![],
            case_poll: Instant::now() - Duration::from_secs(10),
            case_polling: false,
            case_stale: false,
            pending_case_prompt: None,
            business: None,
            apps: None,
            apps_open: std::env::args().any(|a| a == "--app" || a == "--apps"),
            business_open: std::env::args().any(|arg| arg == "--business"),
            theme: CanvasTheme::default(),
            preferences: Preferences::load(),
            appearance_open: false,
            voice: voice::Voice::default(),
            voice_settings: voice::Settings::default(),
            voice_script: String::new(),
            spoken_turns: HashSet::new(),
            reply_turn: None,
            epoch: 0,
            config,
            home,
            home_writer,
            sessions: vec![],
            threads: HashMap::new(),
            pending: HashSet::new(),
            contents: HashMap::new(),
            previews: HashMap::new(),
            board_reads: crate::live::BoardReads::default(),
            images: HashMap::new(),
            preview_pending: HashSet::new(),
            stage_cards: None,
            preview_attempts: HashMap::new(),
            preview_errors: HashMap::new(),
            composer_drafts: HashMap::new(),
            selected: None,
            target: initial_target,
            drawer: false,
            browse_thread: None,
            inspector: false,
            inspector_wide: false,
            history: false,
            unwrap: false,
            login: false,
            connection_editing: false,
            server_address,
            connected: false,
            busy: false,
            running: false,
            running_session: None,
            draft: String::new(),
            reply: String::new(),
            reply_visible: false,
            status: "Connecting to Lab…".into(),
            error: None,
            drag: None,
            viewport: Bounds::default(),
            seed_ids: vec![],
            tx,
            rx,
            composer,
            search,
            email,
            password,
            _subscriptions: subs,
        };
        if app.config.session_cookie.is_empty() {
            app.login = true;
            app.status = "Sign in to Lab".into();
        } else {
            app.connect();
        }
        let closing_view = cx.weak_entity();
        window.on_window_should_close(cx, move |_, cx| {
            let _ = closing_view.update(cx, |this, cx| this.flush_saves(cx));
            true
        });
        cx.on_app_quit(|this, cx| {
            this.flush_saves(cx);
            async {}
        })
        .detach();
        cx.spawn_in(window, async move |this, cx| {
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
                if this
                    .update_in(cx, |this, window, cx| this.drain(window, cx))
                    .is_err()
                {
                    break;
                }
            }
        })
        .detach();
        app
    }

    fn work(&self, f: impl FnOnce(Api, Updates) -> anyhow::Result<()> + Send + 'static) {
        let config = self.config.clone();
        let tx = Updates {
            tx: self.tx.clone(),
            epoch: self.epoch,
        };
        std::thread::spawn(move || {
            let result = Api::new(config).and_then(|api| f(api, tx.clone()));
            if let Err(e) = result {
                let _ = tx.send(Update::Error(e.to_string()));
            }
        });
    }
    fn connect(&mut self) {
        self.busy = true;
        self.error = None;
        self.status = "Connecting to Lab…".into();
        self.work(|api, tx| {
            tx.send(Update::Sessions(api.sessions()?))?;
            Ok(())
        });
        self.sync_foundations();
    }

    fn sync_foundations(&self) {
        self.work(|api, tx| {
            match theme::load(&api) {
                Ok(assets) => tx.send(Update::Appearance(Box::new(assets)))?,
                Err(e) => tx.send(Update::FoundationError(format!("Appearance: {e}")))?,
            }
            Ok(())
        });
        self.work(|api, tx| {
            match voice::Settings::load(&api) {
                Ok(settings) => tx.send(Update::VoiceSettings(settings))?,
                Err(e) => tx.send(Update::FoundationError(format!("Voice settings: {e}")))?,
            }
            Ok(())
        });
    }
    fn save(&mut self) {
        self.home_writer.submit(self.home.clone());
    }
    fn flush_saves(&mut self, cx: &mut Context<Self>) {
        self.save();
        if let Some(desk) = &self.business {
            desk.update(cx, |desk, cx| desk.flush_saves(cx));
        }
        self.home_writer.flush();
    }
    fn fetch_thread(&mut self, id: String) {
        if !self.pending.insert(id.clone()) {
            return;
        }
        self.work(move |api, tx| {
            tx.send(Update::Thread(api.thread(&id)?))?;
            Ok(())
        });
    }

    fn drain(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.poll_cases();
        let mut changed = false;
        while let Ok(update) = self.rx.try_recv() {
            let update = match update {
                Update::Scoped(epoch, update) if epoch == self.epoch => *update,
                Update::Scoped(..) => continue,
                update => update,
            };
            changed = true;
            match update {
                Update::SaveFailed(error) => {
                    self.error = Some(format!("Could not save the canvas: {error}"));
                }
                Update::Cases(result) => {
                    self.case_polling = false;
                    self.case_stale = result.is_err();
                    if let Ok(cards) = result {
                        for (i, card) in cards.iter().enumerate() {
                            if let Some(id) = card["id"].as_str() {
                                self.home
                                    .case_positions
                                    .entry(id.into())
                                    .or_insert([20. + i as f32 * 380., -300.]);
                            }
                        }
                        self.case_cards = cards;
                    }
                }
                Update::Scoped(..) => unreachable!("worker envelope is unwrapped above"),
                Update::Appearance(assets) => {
                    let theme::Assets {
                        mut theme,
                        fonts,
                        warnings,
                    } = *assets;
                    if let Err(e) = cx
                        .text_system()
                        .add_fonts(fonts.into_iter().map(std::borrow::Cow::Owned).collect())
                    {
                        theme.font_body = "Inter".into();
                        theme.font_mono = "monospace".into();
                        self.error = Some(format!("Shared fonts could not load: {e}"));
                    }
                    theme.apply(cx);
                    self.theme = theme;
                    if !warnings.is_empty() {
                        self.error = Some(warnings.join(" · "));
                    }
                }
                Update::VoiceSettings(settings) => self.voice_settings = settings,
                Update::FoundationError(error) => self.error = Some(error),
                Update::Sessions(sessions) => {
                    self.sessions = sessions;
                    self.connected = true;
                    self.busy = false;
                    self.status = format!("{} conversations in Lab", self.sessions.len());
                    let ids: HashSet<_> = self
                        .home
                        .pins
                        .iter()
                        .map(|p| p.session_id.clone())
                        .collect();
                    let first_topics = self.home.topics.is_empty();
                    for session in self.sessions.clone() {
                        if ids.contains(&session.id)
                            && !self.home.topics.iter().any(|t| t.session_id == session.id)
                        {
                            self.home.ensure_topic(&session, true);
                        }
                    }
                    let ids: HashSet<_> = ids
                        .into_iter()
                        .chain(self.home.topics.iter().map(|t| t.session_id.clone()))
                        .collect();
                    for id in ids {
                        self.fetch_thread(id);
                    }
                    if first_topics && self.home.active_space == "Home" {
                        self.fit();
                    }
                    self.save();
                    if !self.home.seeded {
                        self.seed_ids = self
                            .sessions
                            .iter()
                            .filter(|s| s.channel == "web" && s.surface_count > 5)
                            .take(6)
                            .map(|s| s.id.clone())
                            .collect();
                        for id in self.seed_ids.clone() {
                            self.fetch_thread(id);
                        }
                        if self.seed_ids.is_empty() {
                            self.home.seeded = true;
                            self.drawer = true;
                            self.save();
                        }
                    }
                }
                Update::Thread(thread) => {
                    let id = thread.session.id.clone();
                    self.pending.remove(&id);
                    if let Some(s) = self.sessions.iter_mut().find(|s| s.id == id) {
                        let count = s.surface_count;
                        *s = thread.session.clone();
                        s.surface_count = count;
                    }
                    self.home.sync_topic(&thread);
                    self.threads.insert(id.clone(), thread);
                    self.save();
                    if !self.home.seeded
                        && self.seed_ids.iter().all(|id| self.threads.contains_key(id))
                    {
                        let mut choices = Vec::new();
                        for id in &self.seed_ids {
                            let mut items = latest(&self.threads[id].surfaces);
                            items.sort_by_key(Surface::priority);
                            if let Some(s) = items.first() {
                                choices.push(s.clone());
                            }
                        }
                        choices.sort_by_key(Surface::priority);
                        for s in choices.into_iter().take(4) {
                            if let Some(session) =
                                self.sessions.iter().find(|t| t.id == s.session_id).cloned()
                            {
                                self.home.ensure_topic(&session, true);
                                if let Some(thread) = self.threads.get(&s.session_id) {
                                    self.home.sync_topic(thread);
                                }
                            }
                        }
                        self.home.seeded = true;
                        self.fit();
                        self.save();
                    }
                    // Board members use the same authenticated image path as kept images.
                    self.request_kept_images();
                }
                Update::Login(config) => {
                    self.epoch += 1;
                    self.running = false;
                    self.running_session = None;
                    self.reply.clear();
                    self.draft.clear();
                    self.voice.cancel();
                    self.reply_turn = None;
                    self.spoken_turns.clear();
                    self.config = config;
                    self.apps = None;
                    self.record_pins = None;
                    self.meaning = None;
                    self.pending_meaning_prompt = None;
                    self.home = Home::load(&self.config.account());
                    self.login = false;
                    self.threads.clear();
                    self.contents.clear();
                    self.previews.clear();
                    self.board_reads = crate::live::BoardReads::default();
                    self.preview_attempts.clear();
                    self.preview_errors.clear();
                    self.composer_drafts.clear();
                    self.preview_pending.clear();
                    self.pending.clear();
                    self.images.clear();
                    self.selected = None;
                    self.target = self.home.active_topic().map(|t| t.session_id.clone());
                    self.composer
                        .update(cx, |s, cx| s.set_value("", window, cx));
                    self.connect();
                }
                Update::Contents(id, value) => {
                    self.contents.insert(id, value);
                    self.busy = false;
                    self.status = "Choose a definition to keep on your canvas".into();
                }
                Update::Preview(id, value) => {
                    self.preview_pending.remove(&id);
                    self.preview_errors.remove(&id);
                    self.previews.insert(id, value);
                    self.busy = false;
                    self.status = "Native piece ready · current query result".into();
                }
                Update::PreviewFailed(id, error) => {
                    self.preview_pending.remove(&id);
                    self.preview_errors.insert(id, error);
                }
                Update::BoardRead(id, result) => {
                    self.board_reads.complete(id, result);
                }
                Update::TopicCreated(session) => {
                    self.busy = false;
                    let id = session.id.clone();
                    self.home.ensure_topic(&session, true);
                    self.sessions.insert(0, session);
                    self.enter_topic(id.clone(), window, cx);
                    if let Some((case, prompt)) = self.pending_case_prompt.take() {
                        self.home.case_topics.insert(case, id.clone());
                        self.append_case_prompt(prompt, window, cx);
                        self.save();
                    }
                    if let Some((key,prompt))=self.pending_meaning_prompt.take(){
                        self.home.meaning_topics.insert(key,id.clone());
                        self.append_meaning_prompt(prompt,window,cx);self.save();
                    }
                }
                Update::Image(id, path) => {
                    self.preview_pending.remove(&format!("image:{id}"));
                    self.images.insert(id, path);
                }
                Update::ImageFailed(id, error) => {
                    let key = format!("image:{id}");
                    self.preview_pending.remove(&key);
                    self.preview_errors.insert(key, error);
                }
                Update::Created(session) => {
                    self.target = Some(session.id.clone());
                    self.running_session = Some(session.id.clone());
                    self.home.ensure_topic(&session, true);
                    self.sessions.insert(0, session);
                }
                Update::Event(sid, event, value) => {
                    self.running_session = Some(sid.clone());
                    match event.as_str() {
                        "calliope.draft" => {
                            self.draft = string(&value, "text");
                            self.reply_visible = true;
                        }
                        "assistant.delta" => {
                            if let Some(s) = value
                                .get("text")
                                .or_else(|| value.get("delta"))
                                .and_then(Value::as_str)
                            {
                                self.draft.push_str(s);
                            }
                        }
                        "calliope.progress" => {
                            let s = string(&value, "text");
                            if !s.is_empty() {
                                self.status = short(&s, 115);
                            }
                        }
                        "calliope.surfaces" => {
                            if let Some(items) = value.get("surfaces").and_then(Value::as_array) {
                                if let Some(t) = self.threads.get_mut(&sid) {
                                    for v in items {
                                        if let Ok(s) = serde_json::from_value::<Surface>(v.clone())
                                        {
                                            t.surfaces.retain(|p| p.id != s.id);
                                            t.surfaces.insert(0, s);
                                        }
                                    }
                                }
                                if let Some(thread) = self.threads.get(&sid) {
                                    self.home.sync_topic(thread);
                                }
                                self.save();
                                self.status = "New work is available in this topic".into();
                            }
                        }
                        "calliope.turn.completed" => {
                            self.reply = string(&value, "assistant_message");
                            self.reply_visible = true;
                            self.draft.clear();
                            self.status =
                                "Reply received · open the conversation to browse new work".into();
                            let turn = string(&value, "turn_id");
                            if !turn.is_empty() {
                                self.reply_turn = Some((sid.clone(), turn.clone()));
                                if self.read_aloud()
                                    && self.target.as_ref() == Some(&sid)
                                    && self.home.active_topic().is_some()
                                    && self.voice_settings.tts
                                    && self.spoken_turns.insert(turn.clone())
                                {
                                    self.start_voice(sid.clone(), turn, true);
                                }
                            }
                        }
                        "calliope.error" => {
                            self.error = Some(
                                value
                                    .get("message")
                                    .map(display)
                                    .unwrap_or_else(|| short(&value.to_string(), 240)),
                            );
                        }
                        _ => {}
                    }
                }
                Update::Done(sid) => {
                    self.running = false;
                    self.fetch_thread(sid);
                }
                Update::TurnStartError(e) => {
                    self.running = false;
                    self.error = Some(e);
                    self.reply_visible = false;
                }
                Update::Error(e) => {
                    self.pending_case_prompt = None;
                    self.pending_meaning_prompt = None;
                    self.busy = false;
                    self.preview_pending.clear();
                    self.pending.clear();
                    if e.contains("expired") {
                        self.voice.cancel();
                        self.connected = false;
                        self.login = true;
                        self.threads.clear();
                        self.previews.clear();
                        self.board_reads = crate::live::BoardReads::default();
                        self.images.clear();
                    }
                    self.error = Some(e);
                }
            }
        }
        for event in self.voice.poll() {
            changed = true;
            match event {
                voice::Event::Transcript(text) => {
                    let draft = self.composer.read(cx).value().to_string();
                    let combined = if draft.trim().is_empty() {
                        text
                    } else {
                        format!("{} {}", draft.trim_end(), text)
                    };
                    self.composer.update(cx, |s, cx| {
                        s.set_value(combined, window, cx);
                        s.focus(window, cx);
                    });
                    self.status = "Dictation ready · review your draft, then Send".into();
                }
                voice::Event::Script(script) => self.voice_script = script,
                voice::Event::Played(seconds) => {
                    self.status = format!("Voice finished · {seconds:.1} seconds")
                }
                voice::Event::Error(error) => self.error = Some(error),
                _ => {}
            }
        }
        if self.refresh_visible(window) {
            changed = true;
        }
        if self.refresh_boards(window) {
            changed = true;
        }
        if self.sync_stage_cards(cx) {
            changed = true;
        }
        if changed {
            cx.notify();
        }
    }

    /// Charts pinned directly or as members of pinned boards, one card per
    /// placement (canvas tile, inspector, board cell).
    fn sync_stage_cards(&mut self, cx: &mut Context<Self>) -> bool {
        if !self.connected {
            return false;
        }
        if self.stage_cards.as_ref().is_none_or(|(epoch, _)| *epoch != self.epoch) {
            let Ok(api) = Api::new(self.config.clone()) else {
                return false;
            };
            self.stage_cards = Some((self.epoch, crate::stage_cards::StageCards::new(api, &self.theme)));
        }
        let mut wanted: HashMap<String, Vec<(String, String, String)>> = HashMap::new();
        for pin in &self.home.pins {
            let Some(thread) = self.threads.get(&pin.session_id) else {
                continue;
            };
            let Some(surface) = thread.surfaces.iter().find(|s| s.id == pin.surface_id) else {
                continue;
            };
            let placements = wanted.entry(pin.session_id.clone()).or_default();
            if surface.kind == "chart" {
                for expanded in [false, true] {
                    placements.push((
                        chart_placement(&surface.id, &pin.id, expanded),
                        surface.id.clone(),
                        crate::stage_cards::card_version(&surface.kind, &surface.payload),
                    ));
                }
            } else if surface.kind == "board" {
                let Ok(board) = crate::board::parse(surface) else {
                    continue;
                };
                for (index, cell) in board.cells.iter().enumerate() {
                    if let Some(member) = cell.member(surface, &thread.surfaces).filter(|m| m.kind == "chart") {
                        let member_pin = cell.pin(pin, index);
                        for expanded in [false, true] {
                            placements.push((
                                chart_placement(&member.id, &member_pin.id, expanded),
                                member.id.clone(),
                                crate::stage_cards::card_version(&member.kind, &member.payload),
                            ));
                        }
                    }
                }
            }
        }
        let theme = self.theme.clone();
        let Some((_, cards)) = self.stage_cards.as_mut() else {
            return false;
        };
        cards.set_theme(&theme, cx);
        for (session, placements) in wanted {
            cards.request_placed(&session, &placements);
        }
        cards.poll(cx)
    }

    /// A Stage chart drawn by the shared renderer, or a note while it loads or
    /// when it can only open in the browser.
    pub(super) fn stage_chart(&self, key: &str, z: f32) -> AnyElement {
        let cards = self.stage_cards.as_ref().map(|(_, cards)| cards);
        if let Some(card) = cards.and_then(|cards| cards.get(key)) {
            return div().w_full().h(px(card.height)).child(card.renderer.clone()).into_any_element();
        }
        let note = match cards.and_then(|cards| cards.state(key)) {
            None | Some("loading") => "Drawing the chart…",
            Some(_) => "This chart opens in the browser for its interactive view.",
        };
        div()
            .p(px(18. * z))
            .text_size(px(self.theme.typography.body * z))
            .text_color(self.theme.muted)
            .child(note)
            .into_any_element()
    }

    fn request_preview(&mut self, p: Pin, refresh: bool) {
        let key = p.object_key();
        if !self.preview_pending.insert(key.clone()) {
            return;
        }
        self.preview_attempts.insert(key.clone(), Instant::now());
        self.preview_errors.remove(&key);
        self.work(move |api, tx| {
            match api.preview(&p.surface_id, p.query_id.as_deref().unwrap_or(""), refresh) {
                Ok(value) => tx.send(Update::Preview(key, value))?,
                Err(e) => tx.send(Update::PreviewFailed(key, e.to_string()))?,
            }
            Ok(())
        });
    }
    fn refresh_visible(&mut self, window: &Window) -> bool {
        if !self.connected || self.login || !window.is_window_active() {
            return false;
        }
        let count = self
            .preview_pending
            .iter()
            .filter(|k| !k.starts_with("image:"))
            .count()
            + self.board_reads.pending_count();
        if count >= 2 {
            return false;
        }
        let z = self.home.zoom;
        let w = f32::from(self.viewport.size.width);
        let h = f32::from(self.viewport.size.height);
        let pins: Vec<_> = self
            .home
            .pins
            .iter()
            .filter(|p| {
                let key = p.object_key();
                let live = self.home.live_objects.contains(&key);
                let due = self
                    .preview_attempts
                    .get(&key)
                    .is_none_or(|at| at.elapsed() >= Duration::from_secs(60));
                let x = self.home.pan[0] + p.x * z;
                let y = self.home.pan[1] + p.y * z;
                p.space == self.home.active_space
                    && p.query_id.is_some()
                    && x < w
                    && x + p.width * z > 0.
                    && y < h
                    && y + p.height * z > 0.
                    && self.surface_for(p).is_some()
                    && due
                    && (live || !self.previews.contains_key(&key))
                    && !self.preview_pending.contains(&key)
            })
            .take(2 - count)
            .cloned()
            .collect();
        let changed = !pins.is_empty();
        for p in pins {
            let live = self.home.live_objects.contains(&p.object_key());
            self.request_preview(p, live);
        }
        changed
    }
    fn change_space(&mut self, space: String, window: &mut Window, cx: &mut Context<Self>) {
        self.composer_drafts.insert(
            self.home.active_space.clone(),
            self.composer.read(cx).value().to_string(),
        );
        self.voice.cancel();
        self.home.switch_space(space.clone());
        self.target = self.home.active_topic().map(|t| t.session_id.clone());
        if let Some(t) = self.home.topics.iter_mut().find(|t| t.space() == space) {
            t.unread = false;
        }
        let draft = self
            .composer_drafts
            .get(&space)
            .cloned()
            .unwrap_or_default();
        self.composer
            .update(cx, |s, cx| s.set_value(draft, window, cx));
        self.selected = None;
        self.inspector = false;
        self.history = false;
        self.drawer = false;
        self.drag = None;
        self.reply_visible = self.target.is_some() && self.target == self.running_session;
        self.save();
        cx.notify();
    }
    fn enter_topic(&mut self, id: String, window: &mut Window, cx: &mut Context<Self>) {
        if let Some(session) = self.sessions.iter().find(|s| s.id == id).cloned() {
            self.home.ensure_topic(&session, true);
        } else {
            return;
        }
        if let Some(thread) = self.threads.get(&id) {
            self.home.sync_topic(thread);
        }
        self.change_space(topic_space(&id), window, cx);
        self.browse_thread = Some(id.clone());
        self.fetch_thread(id);
    }
    fn new_topic(&mut self, cx: &mut Context<Self>) {
        if self.busy || !self.connected {
            return;
        }
        self.busy = true;
        self.work(|api, tx| {
            tx.send(Update::TopicCreated(api.create_thread()?))?;
            Ok(())
        });
        cx.notify();
    }

    fn read_aloud(&self) -> bool {
        self.preferences
            .read_aloud
            .unwrap_or(self.voice_settings.auto_read)
    }
    fn start_voice(&mut self, session: String, turn: String, automatic: bool) {
        if !self.voice_settings.tts {
            return;
        }
        match Api::new(self.config.clone()) {
            Ok(api) => {
                self.voice_script.clear();
                self.voice
                    .speak(api, session, turn, self.voice_settings.clone(), automatic);
            }
            Err(e) => self.error = Some(e.to_string()),
        }
    }
    fn listen(&mut self, cx: &mut Context<Self>) {
        let current = self
            .target
            .as_ref()
            .and_then(|id| self.threads.get(id))
            .and_then(|t| {
                t.turns
                    .iter()
                    .rev()
                    .find(|t| t["status"] == "complete" && t["turn_kind"] != "sketch_bootstrap")
            })
            .map(|t| (self.target.clone().unwrap(), string(t, "id")));
        if let Some((session, turn)) = current.or_else(|| {
            self.reply_turn
                .clone()
                .filter(|(sid, _)| self.target.as_ref() == Some(sid))
        }) {
            self.start_voice(session, turn, false);
        } else {
            self.status = "Select a conversation with a reply to listen to".into();
        }
        cx.notify();
    }
    fn dictate(&mut self, cx: &mut Context<Self>) {
        if self.voice.phase == Phase::Recording {
            self.voice.stop_recording();
        } else if self.voice.phase != Phase::Transcribing
            && self.voice_settings.stt
            && !self.running
        {
            match Api::new(self.config.clone()) {
                Ok(api) => {
                    self.error = None;
                    self.voice.record(api, self.voice_settings.clone());
                }
                Err(e) => self.error = Some(e.to_string()),
            }
        }
        cx.notify();
    }

    fn sign_in(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if self.busy {
            return;
        }
        self.busy = true;
        self.error = None;
        let mut config = self.config.clone();
        let server=self.server_address.read(cx).value().trim().trim_end_matches('/').to_owned();
        if server!=config.base_url {config.base_url=server.clone();config.browser_url=server;config.session_cookie.clear();}
        config.email = self.email.read(cx).value().trim().to_string();
        let password = self.password.read(cx).value().to_string();
        self.password
            .update(cx, |s, cx| s.set_value("", window, cx));
        let tx = self.tx.clone();
        std::thread::spawn(move || match Api::login(config, password) {
            Ok(c) => {
                let _ = tx.send(Update::Login(c));
            }
            Err(e) => {
                let _ = tx.send(Update::Error(e.to_string()));
            }
        });
        cx.notify();
    }

    fn surface_for(&self, p: &Pin) -> Option<Surface> {
        self.threads
            .get(&p.session_id)?
            .surfaces
            .iter()
            .find(|s| s.id == p.surface_id)
            .cloned()
    }
    fn selected_pin(&self) -> Option<Pin> {
        self.home
            .pins
            .iter()
            .find(|p| Some(&p.id) == self.selected.as_ref())
            .cloned()
    }
    fn select(&mut self, id: String) {
        if let Some(p) = self.home.pins.iter().find(|p| p.id == id)
            && self.home.active_topic().is_none()
        {
            self.target = Some(p.session_id.clone());
        }
        self.selected = Some(id);
        self.unwrap = false;
    }
    fn keep(&mut self, s: Surface) {
        self.selected = Some(self.home.restore_surface(&s));
        self.target = Some(s.session_id.clone());
        self.save();
        self.request_kept_images();
    }

    fn send(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let message = self.composer.read(cx).value().to_string();
        if message.trim().is_empty()
            || self.home.active_topic().is_none()
            || self.running
            || !self.connected
            || matches!(self.voice.phase, Phase::Recording | Phase::Transcribing)
        {
            return;
        }
        if let Some(id) = &self.target
            && self
                .sessions
                .iter()
                .find(|s| &s.id == id)
                .is_some_and(|s| !s.writable())
        {
            self.error=Some("This conversation is read-only here. Start a new conversation or open its original channel.".into());
            cx.notify();
            return;
        }
        let target = self.target.clone();
        let selected = self
            .selected_pin()
            .filter(|p| Some(&p.session_id) == target.as_ref())
            .map(|p| p.surface_id);
        self.composer
            .update(cx, |s, cx| s.set_value("", window, cx));
        self.running = true;
        self.voice.cancel();
        self.running_session = target.clone();
        self.reply_visible = true;
        self.draft.clear();
        self.reply.clear();
        self.error = None;
        self.status = "Callie is working…".into();
        self.work(move |api, tx| {
            let sid = if let Some(id) = target {
                id
            } else {
                match api.create_thread() {
                    Ok(s) => {
                        let id = s.id.clone();
                        tx.send(Update::Created(s))?;
                        id
                    }
                    Err(e) => {
                        tx.send(Update::TurnStartError(e.to_string()))?;
                        return Ok(());
                    }
                }
            };
            let client_id = uuid::Uuid::new_v4().to_string();
            let result = api.turn(
                &sid,
                &message,
                selected.as_deref(),
                &client_id,
                |event, value| {
                    let _ = tx.send(Update::Event(sid.clone(), event, value));
                },
            );
            if let Err(e) = result {
                let _ = tx.send(Update::Error(format!(
                    "{e}. A submitted turn may still be running; refresh before sending again."
                )));
            }
            tx.send(Update::Done(sid))?;
            Ok(())
        });
        cx.notify();
    }

    fn open_contents(&mut self, cx: &mut Context<Self>) {
        if let Some(p) = self.selected_pin() {
            self.inspector = true;
            self.history = false;
            self.unwrap = true;
            if !self.contents.contains_key(&p.surface_id) {
                self.busy = true;
                let id = p.surface_id;
                self.work(move |api, tx| {
                    tx.send(Update::Contents(id.clone(), api.contents(&id)?))?;
                    Ok(())
                });
            }
        }
        cx.notify();
    }

    fn keep_piece(&mut self, item: Value, cx: &mut Context<Self>) {
        let Some(parent) = self.selected_pin() else {
            return;
        };
        let query_id = string(&item, "id");
        if item["can_explore"] != true || query_id.is_empty() {
            return;
        }
        if let Some(existing) = self.home.pins.iter().find(|p| {
            p.surface_id == parent.surface_id
                && p.query_id.as_deref() == Some(&query_id)
                && p.space == self.home.active_space
        }) {
            self.selected = Some(existing.id.clone());
            self.unwrap = false;
            self.inspector = false;
            cx.notify();
            return;
        }
        let mut pin = parent.clone();
        pin.id = uuid::Uuid::new_v4().to_string();
        pin.query_id = Some(query_id.clone());
        pin.title = string(&item, "title");
        pin.width = 440.;
        pin.height = 300.;
        let id = self.home.place_in(&pin, &self.home.active_space.clone());
        self.home.live_objects.insert(pin.object_key());
        self.selected = Some(id);
        self.unwrap = false;
        self.inspector = false;
        self.save();
        self.request_preview(pin, true);
        cx.notify();
    }

    fn refresh(&mut self, cx: &mut Context<Self>) {
        self.preview_errors
            .retain(|key, _| !key.starts_with("image:"));
        if let Some(pin) = self.selected_pin() {
            if pin.query_id.is_some() {
                self.request_preview(pin, true);
            } else {
                self.fetch_thread(pin.session_id);
            }
        } else if let Some(topic) = self.home.active_topic() {
            self.fetch_thread(topic.session_id.clone());
        } else {
            self.connect();
        }
        cx.notify();
    }
    fn zoom_at(&mut self, next: f32, anchor: Point<Pixels>) {
        let old = self.home.zoom;
        let next = next.clamp(0.25, 1.6);
        let x = f32::from(anchor.x - self.viewport.origin.x);
        let y = f32::from(anchor.y - self.viewport.origin.y);
        self.home.pan[0] = x - (x - self.home.pan[0]) * next / old;
        self.home.pan[1] = y - (y - self.home.pan[1]) * next / old;
        self.home.zoom = next;
    }
    fn fit(&mut self) {
        let mut rects: Vec<_> = self
            .home
            .pins
            .iter()
            .filter(|p| p.space == self.home.active_space)
            .map(|p| (p.x, p.y, p.width, p.height))
            .collect();
        if self.home.active_space == "Home" {
            rects.extend(self.home.record_positions.values().map(|p|(p[0],p[1],p[2],p[3])));
            rects.extend(self.home.meaning_positions.values().map(|p|(p[0],p[1],p[2],p[3])));
            rects.extend(
                self.case_cards
                    .iter()
                    .filter(|c| c["pinned"] == true)
                    .filter_map(|c| {
                        self.home
                            .case_positions
                            .get(c["id"].as_str()?)
                            .map(|p| (p[0], p[1], 352., 240.))
                    }),
            );
            rects.extend(
                self.home
                    .topics
                    .iter()
                    .filter(|t| t.pinned)
                    .map(|t| (t.x, t.y, 352., 200.)),
            );
        }
        if rects.is_empty() {
            self.home.pan = [36., 24.];
            self.home.zoom = 1.;
            return;
        }
        let minx = rects.iter().map(|r| r.0).fold(f32::INFINITY, f32::min);
        let miny = rects.iter().map(|r| r.1).fold(f32::INFINITY, f32::min);
        let maxx = rects
            .iter()
            .map(|r| r.0 + r.2)
            .fold(f32::NEG_INFINITY, f32::max);
        let maxy = rects
            .iter()
            .map(|r| r.1 + r.3)
            .fold(f32::NEG_INFINITY, f32::max);
        let w = f32::from(self.viewport.size.width).max(600.) - 72.;
        let h = f32::from(self.viewport.size.height).max(400.) - 64.;
        self.home.zoom = (w / (maxx - minx)).min(h / (maxy - miny)).clamp(0.25, 1.05);
        self.home.pan = [36. - minx * self.home.zoom, 24. - miny * self.home.zoom];
        self.save();
    }

    fn button(&self, id: impl Into<ElementId>, label: impl Into<SharedString>) -> Stateful<Div> {
        div()
            .id(id)
            .px_3()
            .py_2()
            .rounded_lg()
            .text_size(px(self.theme.typography.body))
            .text_color(self.theme.muted)
            .cursor_pointer()
            .hover(|s| s.bg(self.theme.line).text_color(self.theme.text))
            .child(label.into())
    }

    fn header(&mut self, window: &Window, cx: &mut Context<Self>) -> AnyElement {
        let compact = window.viewport_size().width < px(1200.);
        let mut count = self
            .home
            .pins
            .iter()
            .filter(|p| p.space == self.home.active_space)
            .count();
        if self.home.active_space=="Home" {count+=self.record_pins.as_ref().map(|p|p.read(cx).items.len()).unwrap_or(0);}
        if self.home.active_space=="Home" {count+=self.meaning.as_ref().map(|p|p.read(cx).pins.len()).unwrap_or(0);}
        div()
            .h(px(76.))
            .flex_shrink_0()
            .px_6()
            .flex()
            .items_center()
            .gap_4()
            .when(compact, |s| s.gap_2().px_4())
            .border_b_1()
            .border_color(self.theme.line)
            .child(crate::brand::avatar(40.))
            .child(
                div()
                    .flex()
                    .flex_col()
                    .child(crate::brand::wordmark(23.))
                    .child(
                        div()
                            .flex()
                            .gap_2()
                            .items_center()
                            .child(div().size(px(5.)).rounded_full().bg(if self.connected {
                                self.theme.success
                            } else {
                                self.theme.warning
                            }))
                            .child(
                                div()
                                    .text_size(px(self.theme.typography.caption))
                                    .text_color(self.theme.muted)
                                    .child("PERSONAL CANVAS"),
                            ),
                    ),
            )
            .child(div().w(px(if compact {0.} else {16.})))
            .children(self.home.spaces.clone().into_iter().map(|space| {
                let active = space == self.home.active_space && !self.meaning_open;
                let label = space.clone();
                self.button(SharedString::from(format!("space-{space}")), label)
                    .when(active, |s| {
                        s.bg(self.theme.card).text_color(self.theme.text)
                    })
                    .on_click(cx.listener(move |this, _, window, cx| {
                        this.meaning_open=false;
                        this.change_space(space.clone(), window, cx);
                    }))
            }))
            .child(self.button("meaning-mode","Meaning").when(self.meaning_open,|s|s.bg(self.theme.card).text_color(self.theme.text))
                .on_click(cx.listener(|s,_,_,cx|{s.meaning_open=true;cx.notify();})))
            .child(div().flex_1())
            .child(
                div()
                    .text_size(px(self.theme.typography.caption))
                    .text_color(self.theme.muted)
                    .when(compact, |s| s.hidden())
                    .child(if self.meaning_open {"Business map".into()} else if self.home.active_space == "Home" {
                        format!(
                            "{} topics · {count} objects",
                            self.home.topics.iter().filter(|t| t.pinned).count()
                        )
                    } else {
                        format!("{count} objects")
                    }),
            )
            .child(
                self.button("company-apps", "Apps")
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.apps_open = true;
                        cx.notify();
                    })),
            )
            .when(std::env::args().any(|a| a == "--business"), |s| {
                s.child(
                    self.button("business-desk", "UI examples")
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.business_open = true;
                            cx.notify();
                        })),
                )
            })
            .child(
                self.button("browse", if compact {"Browse"} else {"Browse Calliope"})
                    .when(self.drawer, |s| {
                        s.bg(self.theme.card).text_color(self.theme.accent)
                    })
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.meaning_open = false;
                        this.drawer = !this.drawer;
                        cx.notify();
                    })),
            )
            .child(
                self.button("refresh", "Refresh")
                    .on_click(cx.listener(|this, _, _, cx| {
                        if this.meaning_open {
                            if let Some(meaning)=&this.meaning { meaning.update(cx,|s,cx|s.refresh(cx)); }
                        } else { this.refresh(cx); }
                    })),
            )
            .child(
                self.button("appearance", "Appearance")
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.appearance_open = !this.appearance_open;
                        cx.notify();
                    })),
            )
            .child(
                self.button("connection", "Connection")
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.login = !this.login;
                        this.connection_editing=false;
                        cx.notify();
                    })),
            )
            .into_any_element()
    }

    fn drawer(&mut self, cx: &mut Context<Self>) -> AnyElement {
        let query = self.search.read(cx).value().to_lowercase();
        let mut rows = div()
            .id("library-scroll")
            .flex_1()
            .min_h_0()
            .overflow_y_scroll()
            .px_3()
            .pb_4()
            .flex()
            .flex_col()
            .gap_1();
        if let Some(id) = self.browse_thread.clone() {
            if let Some(thread) = self.threads.get(&id) {
                let items: Vec<_> = latest(&thread.surfaces)
                    .into_iter()
                    .filter(|s| {
                        format!("{} {}", s.title, s.kind)
                            .to_lowercase()
                            .contains(&query)
                    })
                    .collect();
                for s in items {
                    let kept = self.home.pins.iter().any(|p| {
                        p.surface_id == s.id
                            && p.query_id.is_none()
                            && p.space == self.home.active_space
                    });
                    let title = s.title.clone();
                    let kind = s.kind.clone();
                    let open = s.clone();
                    rows = rows.child(
                        div()
                            .id(SharedString::from(format!("library-{}", s.id)))
                            .p_3()
                            .rounded_lg()
                            .hover(|s| s.bg(self.theme.card))
                            .flex()
                            .items_center()
                            .gap_2()
                            .child(
                                div()
                                    .flex_1()
                                    .min_w_0()
                                    .flex()
                                    .flex_col()
                                    .gap_2()
                                    .child(
                                        div()
                                            .text_size(px(self.theme.typography.caption))
                                            .text_color(self.theme.accent)
                                            .child(kind.to_uppercase()),
                                    )
                                    .child(
                                        div()
                                            .text_size(px(self.theme.typography.body))
                                            .child(short(&title, 85)),
                                    ),
                            )
                            .child(
                                self.button(
                                    SharedString::from(format!("keep-{}", s.id)),
                                    if kept { "Kept" } else { "+" },
                                )
                                .on_click(cx.listener(
                                    move |this, _, window, cx| {
                                        if this
                                            .home
                                            .active_topic()
                                            .is_some_and(|t| t.session_id != open.session_id)
                                        {
                                            this.enter_topic(open.session_id.clone(), window, cx);
                                        }
                                        this.keep(open.clone());
                                        cx.notify();
                                    },
                                )),
                            ),
                    );
                }
            } else {
                rows = rows.child(
                    div()
                        .p_3()
                        .text_color(self.theme.muted)
                        .child("Loading objects…"),
                );
            }
        } else {
            for s in self.sessions.iter().filter(|s| {
                format!("{} {}", s.title, s.synopsis.as_deref().unwrap_or(""))
                    .to_lowercase()
                    .contains(&query)
            }) {
                let id = s.id.clone();
                let name = s.title.clone();
                let count = s.surface_count;
                let channel = s.channel.clone();
                rows = rows.child(
                    div()
                        .id(SharedString::from(format!("thread-{id}")))
                        .p_3()
                        .rounded_lg()
                        .cursor_pointer()
                        .hover(|s| s.bg(self.theme.card))
                        .flex()
                        .flex_col()
                        .gap_2()
                        .child(div().text_size(px(self.theme.typography.body)).child(name))
                        .child(
                            div()
                                .text_size(px(self.theme.typography.caption))
                                .text_color(self.theme.muted)
                                .child(format!(
                                    "{count} objects{}",
                                    if channel == "web" {
                                        "".into()
                                    } else {
                                        format!(" · {channel}")
                                    }
                                )),
                        )
                        .on_click(cx.listener(move |this, _, window, cx| {
                            this.browse_thread = Some(id.clone());
                            this.search.update(cx, |s, cx| s.set_value("", window, cx));
                            if !this.threads.contains_key(&id) {
                                this.fetch_thread(id.clone());
                            }
                            cx.notify();
                        })),
                );
            }
        }
        let title = self
            .browse_thread
            .as_ref()
            .and_then(|id| self.sessions.iter().find(|s| &s.id == id))
            .map(|s| short(&s.title, 65))
            .unwrap_or_else(|| "Your conversations".into());
        div()
            .w(px(300.))
            .h_full()
            .flex_shrink_0()
            .bg(self.theme.panel)
            .border_r_1()
            .border_color(self.theme.line)
            .flex()
            .flex_col()
            .child(
                div()
                    .p_4()
                    .flex()
                    .flex_col()
                    .gap_3()
                    .when(self.browse_thread.is_some(), |s| {
                        s.child(self.button("back-library", "← All conversations").on_click(
                            cx.listener(|this, _, window, cx| {
                                this.browse_thread = None;
                                this.search.update(cx, |s, cx| s.set_value("", window, cx));
                                cx.notify();
                            }),
                        ))
                    })
                    .child(
                        div()
                            .text_size(px(self.theme.typography.heading))
                            .child(title),
                    )
                    .when_some(self.browse_thread.clone(), |s, id| {
                        s.child(
                            self.button("talk-in-conversation", "Open topic →")
                                .text_color(self.theme.accent)
                                .on_click(cx.listener(move |this, _, window, cx| {
                                    this.enter_topic(id.clone(), window, cx);
                                })),
                        )
                    })
                    .child(Input::new(&self.search).appearance(false)),
            )
            .child(rows)
            .into_any_element()
    }

    fn object_body(
        &self,
        p: &Pin,
        s: Option<&Surface>,
        z: f32,
        expanded: bool,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let Some(s) = s else {
            return div()
                .p_4()
                .text_color(self.theme.muted)
                .text_size(px(self.theme.typography.caption * z))
                .child(if self.pending.contains(&p.session_id) {
                    "Loading from Lab…"
                } else {
                    "Object unavailable. Refresh or check access."
                })
                .into_any_element();
        };
        if s.kind == "board" {
            return self.board_body(p, s, z, expanded, window, cx);
        }
        let preview = p
            .query_id
            .as_ref()
            .and_then(|_| self.previews.get(&p.object_key()));
        if p.query_id.is_some() && preview.is_none() {
            return div()
                .p_4()
                .flex()
                .flex_col()
                .gap_3()
                .text_size(px(self.theme.typography.body * z))
                .text_color(self.theme.muted)
                .child(if self.preview_pending.contains(&p.object_key()) {
                    "Reading this definition…"
                } else {
                    "Definition kept. Refresh to read its current data."
                })
                .into_any_element();
        }
        let payload = preview.map(|v| &v["result"]).unwrap_or(&s.payload);
        let rows = payload
            .get("rows")
            .or_else(|| payload.get("sample"))
            .and_then(Value::as_array);
        let metric = if let Some(v) = preview
            .and_then(|v| v.get("scalar"))
            .filter(|v| !v.is_null())
        {
            v.get("value")
        } else if s.kind == "metric" {
            s.payload.pointer("/result/value")
        } else {
            None
        };
        if let Some(value) = metric {
            let name = preview
                .and_then(|v| v.pointer("/scalar/label"))
                .and_then(Value::as_str)
                .unwrap_or(&p.title);
            return div()
                .p(px(20. * z))
                .flex()
                .flex_col()
                .gap(px(12. * z))
                .child(
                    div()
                        .text_size(px(self.theme.typography.display * z))
                        .font_family(self.theme.font_mono.clone())
                        .text_color(self.theme.accent)
                        .font_weight(FontWeight::MEDIUM)
                        .child(short(&display(value), 24)),
                )
                .child(
                    div()
                        .text_size(px(self.theme.typography.caption * z))
                        .text_color(self.theme.muted)
                        .child(short(name, 90)),
                )
                .into_any_element();
        }
        if let Some(rows) = rows {
            let grid = crate::grid::element(
                format!("grid-{}-{}-{expanded}", self.epoch, p.id),
                payload,
                z,
                &self.theme,
                window,
                cx,
            );
            return div()
                .w_full()
                .min_h_0()
                .min_w_0()
                .when(expanded, |s| {
                    let height = ((rows.len() + 1) as f32 * 32. + 44.)
                        .clamp(160., f32::from(window.viewport_size().height) * 0.60);
                    s.h(px(height)).flex_shrink_0()
                })
                .when(!expanded, |s| s.h_full())
                .child(grid)
                .into_any_element();
        }
        if s.kind == "image"
            && let Some(path) = self.images.get(&s.id)
        {
            return img(path.clone())
                .w_full()
                .h(px(if expanded { 400. } else { 164. } * z))
                .object_fit(ObjectFit::Contain)
                .into_any_element();
        }
        if s.kind == "artifact" {
            let sources = s
                .payload
                .pointer("/manifest/sources")
                .and_then(Value::as_array)
                .map(|v| v.len())
                .unwrap_or(0);
            let queries = s
                .payload
                .pointer("/deps/queries")
                .and_then(Value::as_u64)
                .unwrap_or(0);
            return div()
                .p(px(18. * z))
                .flex()
                .flex_col()
                .gap(px(if expanded { 15. } else { 10. } * z))
                .child(
                    div()
                        .text_size(px(self.theme.typography.body * z))
                        .line_height(relative(1.5))
                        .text_color(self.theme.text)
                        .child(short(&s.description(), if expanded { 1200 } else { 120 })),
                )
                .child(
                    div()
                        .flex()
                        .gap(px(14. * z))
                        .text_size(px(self.theme.typography.caption * z))
                        .text_color(self.theme.accent)
                        .child(format!("{sources} sources"))
                        .child(format!("{queries} queries")),
                )
                .when(expanded, |s| {
                    s.child(
                        div()
                            .text_size(px(self.theme.typography.caption * z))
                            .text_color(self.theme.muted)
                            .child("Open the app, or keep a piece of it here."),
                    )
                })
                .into_any_element();
        }
        if s.kind == "chart" {
            return self.stage_chart(&chart_placement(&s.id, &p.id, expanded), z);
        }
        let mut text = s.description();
        if text.is_empty() {
            text=match s.kind.as_str(){"chart"=>"A chart from this conversation. Open in the browser for its full interactive view.","map"=>"A geographic view from this conversation. Open in the browser to explore.","board"=>"A collection of related work. Open its conversation to explore the members.","email"=>"An email draft or exchange. Open in the browser to review its content and actions.","input"=>"Callie is waiting for input. Open in the browser to review the request.","document"=>"A saved document. Open in the browser to read or download it.",_=>"Saved work from this conversation."}.into();
        }
        if expanded && matches!(s.kind.as_str(), "answer" | "document") {
            return div()
                .p_4()
                .child(TextView::markdown(
                    SharedString::from(format!("text-{}", p.id)),
                    short(&text, 24000),
                    window,
                    cx,
                ))
                .into_any_element();
        }
        div()
            .p(px(18. * z))
            .text_size(px(self.theme.typography.body * z))
            .line_height(relative(1.5))
            .text_color(self.theme.muted)
            .child(short(&text, if expanded { 4000 } else { 280 }))
            .into_any_element()
    }

    fn query_status(&self, p: &Pin) -> String {
        let key = p.object_key();
        if self.preview_pending.contains(&key) {
            return "Refreshing…".into();
        }
        if self.preview_errors.contains_key(&key) {
            return if self.previews.contains_key(&key) {
                "Refresh failed · previous result"
            } else {
                "Query unavailable · open for details"
            }
            .into();
        }
        if let Some(v) = self.previews.get(&key) {
            let at = string(v, "observed_at");
            let time = at.get(11..19).unwrap_or(&at);
            let scope = if v.pointer("/context/as_of").is_some_and(|v| !v.is_null()) {
                " · fixed date"
            } else {
                ""
            };
            format!(
                "Read {time} UTC{scope}{}",
                if self.home.live_objects.contains(&key) {
                    " · auto 60s"
                } else {
                    " · manual"
                }
            )
        } else {
            "Waiting to read".into()
        }
    }

    fn card(&self, p: Pin, window: &mut Window, cx: &mut Context<Self>) -> AnyElement {
        let z = self.home.zoom;
        let selected = self.selected.as_ref() == Some(&p.id);
        let s = self.surface_for(&p);
        let kind = if p.board_cell.is_some() {
            "board piece".into()
        } else if p.query_id.is_some() {
            if self.home.live_objects.contains(&p.object_key()) {
                "live view".into()
            } else {
                "query view".into()
            }
        } else {
            s.as_ref()
                .map(|s| {
                    if s.kind == "query" {
                        "receipt".into()
                    } else {
                        s.kind.clone()
                    }
                })
                .unwrap_or_else(|| "object".into())
        };
        let origin = self
            .sessions
            .iter()
            .find(|s| s.id == p.session_id)
            .map(|s| s.title.clone())
            .unwrap_or_else(|| "Lab conversation".into());
        let id = p.id.clone();
        let click_id = id.clone();
        let open_id = id.clone();
        let origin_id = p.session_id.clone();
        let body = self.object_body(&p, s.as_ref(), z, false, window, cx);
        let open_board = p.board_cell.is_none() && s.as_ref().is_some_and(|s| s.kind == "board");
        let version = s
            .as_ref()
            .and_then(|s| s.artifact_version)
            .map(|v| format!(" · v{v}"))
            .unwrap_or_default();
        div()
            .id(SharedString::from(format!("card-{}", p.id)))
            .occlude()
            .absolute()
            .left(px(self.home.pan[0] + p.x * z))
            .top(px(self.home.pan[1] + p.y * z))
            .w(px(p.width * z))
            .h(px(p.height * z))
            .bg(with_alpha(self.theme.card, 0.94))
            .rounded(px(self.theme.radius * z))
            .border_1()
            .border_color(if selected {
                self.theme.accent
            } else {
                self.theme.line
            })
            .shadow(vec![BoxShadow {
                color: with_alpha(self.theme.bg, 0.65).into(),
                offset: point(px(0.), px(6. * z)),
                blur_radius: px(24. * z),
                spread_radius: px(0.),
            }])
            .overflow_hidden()
            .flex()
            .flex_col()
            .on_mouse_down(
                MouseButton::Left,
                cx.listener(move |this, _, _, cx| {
                    this.select(click_id.clone());
                    cx.stop_propagation();
                    cx.notify();
                }),
            )
            .child(
                div()
                    .id(SharedString::from(format!("drag-{id}")))
                    .flex_shrink_0()
                    .px(px(17. * z))
                    .pt(px(15. * z))
                    .pb(px(10. * z))
                    .cursor(CursorStyle::OpenHand)
                    .flex()
                    .flex_col()
                    .gap(px(6. * z))
                    .child(
                        div()
                            .flex()
                            .items_center()
                            .justify_between()
                            .child(
                                div()
                                    .text_size(px(self.theme.typography.caption * z))
                                    .text_color(self.theme.accent)
                                    .child(format!("{}{version}", kind.to_uppercase())),
                            )
                            .child(
                                div()
                                    .text_size(px(self.theme.typography.caption * z))
                                    .text_color(self.theme.muted)
                                    .child("⠿"),
                            ),
                    )
                    .child(
                        div()
                            .text_size(px(self.theme.typography.heading * z))
                            .font_weight(FontWeight::MEDIUM)
                            .child(short(&p.title, 68)),
                    )
                    .on_mouse_down(
                        MouseButton::Left,
                        cx.listener(move |this, e: &MouseDownEvent, _, cx| {
                            this.select(id.clone());
                            if let Some(p) = this.home.pins.iter().find(|p| p.id == id) {
                                this.drag = Some(Drag::Card(id.clone(), e.position, p.x, p.y));
                            }
                            cx.stop_propagation();
                            cx.notify();
                        }),
                    ),
            )
            .when(p.query_id.is_some(), |card| {
                card.child(
                    div()
                        .px(px(17. * z))
                        .pb(px(4. * z))
                        .text_size(px(10. * z))
                        .text_color(self.theme.muted)
                        .child(self.query_status(&p)),
                )
            })
            .child(div().flex_1().min_h_0().overflow_hidden().child(body))
            .child(
                div()
                    .h(px(35. * z))
                    .flex_shrink_0()
                    .pl(px(16. * z))
                    .pr(px(32. * z))
                    .border_t_1()
                    .border_color(self.theme.line)
                    .flex()
                    .items_center()
                    .justify_between()
                    .gap_2()
                    .child(
                        div()
                            .flex_1()
                            .min_w_0()
                            .text_size(px(self.theme.typography.caption * z))
                            .text_color(self.theme.muted)
                            .id(SharedString::from(format!("origin-{}", p.id)))
                            .cursor_pointer()
                            .on_mouse_down(MouseButton::Left, |_, _, cx| cx.stop_propagation())
                            .on_click(cx.listener(move |this, _, window, cx| {
                                this.enter_topic(origin_id.clone(), window, cx)
                            }))
                            .child(short(&origin, 30)),
                    )
                    .child(
                        div()
                            .id(SharedString::from(format!("open-{open_id}")))
                            .text_size(px(self.theme.typography.caption * z))
                            .text_color(self.theme.accent)
                            .cursor_pointer()
                            .px_2()
                            .py_1()
                            .child(if open_board {
                                "Open board ↗"
                            } else {
                                "Explore ↗"
                            })
                            .on_mouse_down(MouseButton::Left, |_, _, cx| cx.stop_propagation())
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.select(open_id.clone());
                                this.inspector = true;
                                this.inspector_wide |= open_board;
                                this.history = false;
                                cx.notify();
                            })),
                    ),
            )
            .child(self.resize_handle(&p, [true, false], cx))
            .child(self.resize_handle(&p, [false, true], cx))
            .child(self.resize_handle(&p, [true, true], cx))
            .into_any_element()
    }

    // Capture a gesture through its release, even over an occluding block/panel.
    fn track_drag(&mut self, e: &MouseMoveEvent, cx: &mut Context<Self>) {
        if !e.dragging() {
            if self.drag.take().is_some() {
                self.save();
                cx.notify();
            }
            return;
        }
        if let Some(drag) = self.drag.clone() {
            match drag {
                Drag::Meaning(id,start,x,y)=>{
                    if let Some(p)=self.home.meaning_positions.get_mut(&id){p[0]=x+f32::from(e.position.x-start.x)/self.home.zoom;p[1]=y+f32::from(e.position.y-start.y)/self.home.zoom;}
                }
                Drag::MeaningResize(id,start,original)=>{
                    if let Some(p)=self.home.meaning_positions.get_mut(&id){p[2]=(original[0]+f32::from(e.position.x-start.x)/self.home.zoom).max(300.);p[3]=(original[1]+f32::from(e.position.y-start.y)/self.home.zoom).max(250.);}
                }
                Drag::Record(id,start,x,y) => {
                    if let Some(p)=self.home.record_positions.get_mut(&id) {p[0]=x+f32::from(e.position.x-start.x)/self.home.zoom;p[1]=y+f32::from(e.position.y-start.y)/self.home.zoom;}
                }
                Drag::RecordResize(id,start,original) => {
                    if let Some(p)=self.home.record_positions.get_mut(&id) {p[2]=(original[0]+f32::from(e.position.x-start.x)/self.home.zoom).max(300.);p[3]=(original[1]+f32::from(e.position.y-start.y)/self.home.zoom).max(280.);}
                }
                Drag::Case(id, start, x, y) => {
                    self.home.case_positions.insert(
                        id,
                        [
                            x + f32::from(e.position.x - start.x) / self.home.zoom,
                            y + f32::from(e.position.y - start.y) / self.home.zoom,
                        ],
                    );
                }
                Drag::Card(id, start, x, y) => {
                    let z = self.home.zoom;
                    if let Some(p) = self.home.pins.iter_mut().find(|p| p.id == id) {
                        p.x = x + f32::from(e.position.x - start.x) / z;
                        p.y = y + f32::from(e.position.y - start.y) / z;
                    }
                }
                Drag::Topic(id, start, x, y) => {
                    let z = self.home.zoom;
                    if let Some(t) = self.home.topics.iter_mut().find(|t| t.session_id == id) {
                        t.x = x + f32::from(e.position.x - start.x) / z;
                        t.y = y + f32::from(e.position.y - start.y) / z;
                    }
                }
                Drag::Resize(id, start, original, axes) => {
                    let delta = [
                        if axes[0] {
                            f32::from(e.position.x - start.x)
                        } else {
                            0.
                        },
                        if axes[1] {
                            f32::from(e.position.y - start.y)
                        } else {
                            0.
                        },
                    ];
                    if let Some(p) = self.home.pins.iter_mut().find(|p| p.id == id) {
                        p.resize_from(original, delta, self.home.zoom);
                    }
                }
                Drag::Pan(start, pan) => {
                    self.home.pan = [
                        pan[0] + f32::from(e.position.x - start.x),
                        pan[1] + f32::from(e.position.y - start.y),
                    ];
                }
            }
            cx.notify();
        }
    }

    fn finish_drag(&mut self, e: &MouseUpEvent, window: &mut Window, cx: &mut Context<Self>) {
        if e.button != MouseButton::Left {
            return;
        }

        if let Some(drag) = self.drag.take() {
            if let Drag::Topic(id, start, _, _) = drag
                && f32::from(e.position.x - start.x).abs() < 5.
                && f32::from(e.position.y - start.y).abs() < 5.
            {
                self.enter_topic(id, window, cx);
            }
            self.save();
            cx.notify();
        }
    }

    fn resize_handle(&self, p: &Pin, axes: [bool; 2], cx: &mut Context<Self>) -> AnyElement {
        let id = p.id.clone();
        let corner = axes == [true, true];
        let grip = (22. * self.home.zoom).max(16.);
        div()
            .id(SharedString::from(format!("resize-{}-{axes:?}", p.id)))
            .absolute()
            .right_0()
            .bottom_0()
            .when(corner, |s| s.w(px(grip)).h(px(grip)))
            .when(!corner && axes[0], |s| s.w(px(6.)).top_0().bottom(px(grip)))
            .when(!corner && axes[1], |s| s.h(px(6.)).left_0().right(px(grip)))
            .cursor(if corner {
                CursorStyle::ResizeUpLeftDownRight
            } else if axes[0] {
                CursorStyle::ResizeLeftRight
            } else {
                CursorStyle::ResizeUpDown
            })
            .text_color(self.theme.muted)
            .text_size(px(16. * self.home.zoom.max(0.7)))
            .flex()
            .items_center()
            .justify_center()
            .when(corner, |s| s.child("◢"))
            .hover(|s| s.bg(self.theme.accent_soft).text_color(self.theme.accent))
            .on_mouse_down(
                MouseButton::Left,
                cx.listener(move |this, e: &MouseDownEvent, _, cx| {
                    this.select(id.clone());
                    if let Some(p) = this.home.pins.iter().find(|p| p.id == id) {
                        this.drag = Some(Drag::Resize(
                            id.clone(),
                            e.position,
                            [p.width, p.height],
                            axes,
                        ));
                    }
                    cx.stop_propagation();
                    cx.notify();
                }),
            )
            .into_any_element()
    }

    fn topic_bubble(&self, topic: Topic, cx: &mut Context<Self>) -> AnyElement {
        let z = self.home.zoom;
        let id = topic.session_id.clone();
        let open_id = id.clone();
        let space = topic.space();
        let items: Vec<_> = self.home.pins.iter().filter(|p| p.space == space).collect();
        let working = self.running && self.running_session.as_ref() == Some(&id);
        let state = if working {
            "Callie is working"
        } else if topic.unread {
            "New work inside"
        } else {
            "Open workspace"
        };
        let titles: Vec<_> = items.iter().take(2).map(|p| short(&p.title, 42)).collect();
        div()
            .id(SharedString::from(format!("topic-{id}")))
            .absolute()
            .left(px(self.home.pan[0] + topic.x * z))
            .top(px(self.home.pan[1] + topic.y * z))
            .w(px(352. * z))
            .h(px(200. * z))
            .rounded(px(30. * z))
            .bg(with_alpha(self.theme.card, 0.94))
            .border_1()
            .border_color(with_alpha(self.theme.accent, 0.5))
            .shadow(vec![BoxShadow {
                color: with_alpha(self.theme.bg, 0.65).into(),
                offset: point(px(0.), px(8. * z)),
                blur_radius: px(28. * z),
                spread_radius: px(0.),
            }])
            .cursor(CursorStyle::OpenHand)
            .px(px(23. * z))
            .py(px(18. * z))
            .flex()
            .flex_col()
            .gap(px(10. * z))
            .overflow_hidden()
            .on_mouse_down(
                MouseButton::Left,
                cx.listener(move |this, e: &MouseDownEvent, _, cx| {
                    this.selected = None;
                    this.drag = Some(Drag::Topic(id.clone(), e.position, topic.x, topic.y));
                    cx.stop_propagation();
                    cx.notify();
                }),
            )
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap(px(8. * z))
                    .child(div().size(px(6. * z)).rounded_full().bg(if working {
                        self.theme.warning
                    } else {
                        self.theme.accent
                    }))
                    .child(
                        div()
                            .text_size(px(self.theme.typography.caption * z))
                            .text_color(self.theme.accent)
                            .child(format!("TOPIC · {} objects", items.len())),
                    ),
            )
            .child(
                div()
                    .text_size(px(21. * z))
                    .font_family(self.theme.font_display.clone())
                    .font_weight(FontWeight::MEDIUM)
                    .child(short(&topic.title, 60)),
            )
            .child(
                div()
                    .flex_1()
                    .overflow_hidden()
                    .flex()
                    .flex_col()
                    .gap(px(4. * z))
                    .children(titles.into_iter().map(|title| {
                        div()
                            .text_size(px(self.theme.typography.caption * z))
                            .text_color(self.theme.muted)
                            .child(title)
                    })),
            )
            .child(
                div()
                    .flex()
                    .items_center()
                    .justify_between()
                    .child(
                        div()
                            .text_size(px(self.theme.typography.caption * z))
                            .text_color(if topic.unread {
                                self.theme.accent
                            } else {
                                self.theme.muted
                            })
                            .child(state),
                    )
                    .child(
                        div()
                            .id(SharedString::from(format!("enter-{open_id}")))
                            .cursor_pointer()
                            .px_2()
                            .py_1()
                            .text_size(px(self.theme.typography.body * z))
                            .text_color(self.theme.accent)
                            .child("Enter →")
                            .on_mouse_down(MouseButton::Left, |_, _, cx| cx.stop_propagation())
                            .on_click(cx.listener(move |this, _, window, cx| {
                                this.enter_topic(open_id.clone(), window, cx)
                            })),
                    ),
            )
            .into_any_element()
    }

    fn stage(&mut self, window: &mut Window, cx: &mut Context<Self>) -> AnyElement {
        let weak = cx.entity().downgrade();
        let pan = self.home.pan;
        let zoom = self.home.zoom;
        let grid_color = self.theme.grid;
        let grid = canvas(
            move |bounds, _, cx| {
                let _ = weak.update(cx, |this, _| this.viewport = bounds);
                bounds
            },
            move |_, bounds, window, _| {
                let step = 32. * zoom;
                let w = f32::from(bounds.size.width);
                let h = f32::from(bounds.size.height);
                let mut x = pan[0].rem_euclid(step);
                while x < w {
                    let mut y = pan[1].rem_euclid(step);
                    while y < h {
                        window.paint_quad(fill(
                            Bounds::new(
                                bounds.origin + point(px(x), px(y)),
                                size(px(1.5), px(1.5)),
                            ),
                            grid_color,
                        ));
                        y += step;
                    }
                    x += step;
                }
            },
        )
        .absolute()
        .size_full();
        let mut pins: Vec<_> = self
            .home
            .pins
            .iter()
            .filter(|p| p.space == self.home.active_space)
            .cloned()
            .collect();
        // The block being handled should stay accessible as it grows over neighbors.
        pins.sort_by_key(|p| self.selected.as_ref() == Some(&p.id));
        let topics: Vec<_> = if self.home.active_space == "Home" {
            self.home
                .topics
                .iter()
                .filter(|t| t.pinned)
                .cloned()
                .collect()
        } else {
            vec![]
        };
        let case_cards = self.case_workspace_cards(cx);
        let record_cards = self.record_cards(cx);
        let meaning_cards = self.meaning_cards(cx);
        let empty = pins.is_empty() && topics.is_empty() && case_cards.is_empty() && record_cards.is_empty() && meaning_cards.is_empty();
        let cards: Vec<_> = pins.into_iter().map(|p| self.card(p, window, cx)).collect();
        let bubbles: Vec<_> = topics
            .into_iter()
            .map(|t| self.topic_bubble(t, cx))
            .collect();
        let in_topic = self.home.active_topic().is_some();
        div().id("stage").relative().flex_1().min_w_0().h_full().overflow_hidden().bg(self.theme.bg)
            .on_mouse_down(MouseButton::Left,cx.listener(|this,e:&MouseDownEvent,_,cx|{this.drag=Some(Drag::Pan(e.position,this.home.pan));this.selected=None;cx.notify();}))
            .on_scroll_wheel(cx.listener(|this,e:&ScrollWheelEvent,_,cx|{
                let delta=e.delta.pixel_delta(px(24.));
                this.zoom_at(this.home.zoom*(f32::from(delta.y)*0.006).exp(),e.position);
                this.save();cx.stop_propagation();cx.notify();
            }))
            .when_some(self.theme.wallpaper.clone(), |s,path| s
                .child(img(path).absolute().size_full().object_fit(ObjectFit::Cover).opacity(self.preferences.wallpaper_strength))
                .child(div().absolute().size_full().bg(linear_gradient(180.,
                    linear_color_stop(with_alpha(self.theme.bg,0.04),0.),
                    linear_color_stop(with_alpha(self.theme.bg,0.50),1.)))))
            .child(grid).children(bubbles).children(cards).children(case_cards).children(record_cards).children(meaning_cards)
            .when(empty,|s|s.child(div().absolute().left(px(72.)).top(px(72.)).w(px(420.)).flex().flex_col().gap_4()
                .child(div().text_size(px(self.theme.typography.display)).text_color(self.theme.text).child(if in_topic { "Room to think." } else { "Make yourself at home." }))
                .child(div().text_size(px(self.theme.typography.heading)).line_height(relative(1.6)).text_color(self.theme.muted).child(if in_topic { "Talk with Callie below. Keep what helps, hide the clutter, and pin useful objects Home. Your full history stays in Lab." } else { "Open a topic to work with Callie. Keep the things you want to follow here on your desk." }))
                .child(self.button("empty-browse","Browse your Lab →").text_color(self.theme.accent).on_click(cx.listener(|this,_,_,cx|{this.drawer=true;cx.notify();})))))
            .child(div().absolute().bottom(px(14.)).left(px(20.)).text_size(px(self.theme.typography.caption)).text_color(self.theme.muted).child("Scroll to zoom · drag canvas to pan · headings to move · edges to resize"))
            .child(div().absolute().bottom(px(10.)).right(px(20.)).flex().items_center().gap_1().bg(self.theme.bg).rounded_lg()
                .child(self.button("zoom-out","−").on_click(cx.listener(|this,_,_,cx|{this.zoom_at(this.home.zoom/1.15,this.viewport.center());this.save();cx.notify();})))
                .child(div().text_size(px(self.theme.typography.caption)).text_color(self.theme.muted).child(format!("{}%",(self.home.zoom*100.).round())))
                .child(self.button("zoom-in","+").on_click(cx.listener(|this,_,_,cx|{this.zoom_at(this.home.zoom*1.15,this.viewport.center());this.save();cx.notify();})))
                .child(self.button("fit","Fit").on_click(cx.listener(|this,_,_,cx|{this.fit();cx.notify();}))))
            .into_any_element()
    }

    fn inspector(&mut self, window: &mut Window, cx: &mut Context<Self>) -> AnyElement {
        let scroll_state = window.use_keyed_state(
            SharedString::from(format!(
                "inspector-scroll-{}-{}-{}-{}",
                self.epoch,
                if self.history {
                    &self.target
                } else {
                    &self.selected
                }
                .as_deref()
                .unwrap_or("empty"),
                self.history,
                self.unwrap
            )),
            cx,
            |_, _| ScrollHandle::new(),
        );
        let scroll_handle = scroll_state.read(cx).clone();
        let mut content = div()
            .id("detail-scroll")
            .size_full()
            .min_h_0()
            .pr(px(14.))
            .overflow_y_scroll()
            .track_scroll(&scroll_handle)
            .flex()
            .flex_col();
        let title = if self.history {
            "Conversation"
        } else if self.unwrap {
            "Inside this app"
        } else {
            "A closer look"
        };
        if self.history {
            if let Some(t) = self.target.as_ref().and_then(|id| self.threads.get(id)) {
                for turn in t
                    .turns
                    .iter()
                    .filter(|t| t["turn_kind"] != "sketch_bootstrap")
                    .rev()
                    .take(12)
                    .collect::<Vec<_>>()
                    .into_iter()
                    .rev()
                {
                    let human = string(turn, "user_message");
                    let reply = string(turn, "assistant_message");
                    content = content.child(
                        div()
                            .p_4()
                            .border_b_1()
                            .border_color(self.theme.line)
                            .flex()
                            .flex_col()
                            .gap_3()
                            .child(
                                div()
                                    .text_size(px(self.theme.typography.caption))
                                    .text_color(self.theme.accent)
                                    .child("YOU"),
                            )
                            .child(
                                div()
                                    .text_size(px(self.theme.typography.body))
                                    .child(short(&human, 3000)),
                            )
                            .child(
                                div()
                                    .text_size(px(self.theme.typography.caption))
                                    .text_color(self.theme.accent)
                                    .child("CALLIE"),
                            )
                            .child(TextView::markdown(
                                SharedString::from(format!("turn-{}", string(turn, "id"))),
                                short(&reply, 12000),
                                window,
                                cx,
                            )),
                    );
                }
            } else {
                content = content.child(
                    div()
                        .p_4()
                        .text_color(self.theme.muted)
                        .child("Select an object or start a conversation."),
                );
            }
        } else if let Some(p) = self.selected_pin() {
            let s = self.surface_for(&p);
            content = content.child(
                div()
                    .px_4()
                    .pt_4()
                    .text_size(px(21.))
                    .child(p.title.clone()),
            );
            if self.unwrap {
                if let Some(items) = self
                    .contents
                    .get(&p.surface_id)
                    .and_then(|v| v["items"].as_array())
                {
                    for item in items {
                        let item = item.clone();
                        let enabled = item["can_explore"] == true;
                        let source = match string(&item, "source").as_str() {
                            "captured" => "Observed query",
                            "semantic" => "Declared calculation",
                            _ => "Saved definition",
                        };
                        content = content.child(
                            div()
                                .p_4()
                                .border_b_1()
                                .border_color(self.theme.line)
                                .flex()
                                .flex_col()
                                .gap_3()
                                .child(
                                    div()
                                        .text_size(px(self.theme.typography.caption))
                                        .text_color(self.theme.accent)
                                        .child(source),
                                )
                                .child(
                                    div()
                                        .text_size(px(self.theme.typography.body))
                                        .child(string(&item, "title")),
                                )
                                .child(
                                    div()
                                        .text_size(px(self.theme.typography.caption))
                                        .text_color(self.theme.muted)
                                        .child(short(&string(&item, "description"), 220)),
                                )
                                .child(
                                    self.button(
                                        SharedString::from(format!(
                                            "piece-{}",
                                            string(&item, "id")
                                        )),
                                        if enabled {
                                            "Keep live view +"
                                        } else {
                                            "Unavailable for preview"
                                        },
                                    )
                                    .when(enabled, |s| {
                                        s.text_color(self.theme.accent).on_click(cx.listener(
                                            move |this, _, _, cx| this.keep_piece(item.clone(), cx),
                                        ))
                                    }),
                                ),
                        );
                    }
                    if items.is_empty() {
                        content=content.child(div().p_4().text_color(self.theme.muted).child("This version has no extractable definitions yet. Open the full app in your browser."));
                    }
                } else {
                    content = content.child(
                        div()
                            .p_4()
                            .text_color(self.theme.muted)
                            .child("Loading versioned definitions…"),
                    );
                }
            } else {
                content = content.child(self.object_body(&p, s.as_ref(), 1., true, window, cx));
                if let Some(error) = self.preview_errors.get(&p.object_key()) {
                    content = content.child(
                        div()
                            .p_4()
                            .text_size(px(self.theme.typography.caption))
                            .text_color(self.theme.danger)
                            .child(short(error, 400)),
                    );
                }
                if let Some(s) = &s {
                    let time = self
                        .previews
                        .get(&p.object_key())
                        .and_then(|v| v["observed_at"].as_str())
                        .unwrap_or(&s.created_at);
                    content = content.child(
                        div()
                            .p_4()
                            .text_size(px(self.theme.typography.caption))
                            .line_height(relative(1.5))
                            .text_color(self.theme.muted)
                            .child(format!(
                                "{} {}",
                                if p.query_id.is_some() {
                                    "Query result read at"
                                } else {
                                    "Saved object from"
                                },
                                short(time, 25)
                            )),
                    );
                    if s.kind == "artifact" && p.query_id.is_none() {
                        content = content.child(
                            self.button("unwrap", "Pull out a native piece →")
                                .mx_4()
                                .text_color(self.theme.accent)
                                .on_click(cx.listener(|this, _, _, cx| this.open_contents(cx))),
                        );
                    }
                    if let Some(url) = browser_link(&self.config, s) {
                        content = content.child(
                            self.button("browser", "Open in browser ↗")
                                .mx_4()
                                .on_click(move |_, _, cx| cx.open_url(&url)),
                        );
                    }
                }
                content = content.child(
                    div()
                        .p_4()
                        .flex()
                        .flex_wrap()
                        .gap_2()
                        .child(
                            self.button("card-size", "Reset block size")
                                .on_click(cx.listener(|this, _, _, cx| {
                                    let board = this.selected_pin().is_some_and(|p| {
                                        p.board_cell.is_none()
                                            && this
                                                .surface_for(&p)
                                                .is_some_and(|s| s.kind == "board")
                                    });
                                    if let Some(id) = &this.selected
                                        && let Some(p) =
                                            this.home.pins.iter_mut().find(|p| &p.id == id)
                                    {
                                        p.width = if board { 960. } else { 352. };
                                        p.height = if board { 640. } else { 264. };
                                    }
                                    this.save();
                                    cx.notify();
                                })),
                        )
                        .when(p.space != "Home", |row| {
                            row.child(
                                self.button(
                                    "pin-home",
                                    if self.home.pins.iter().any(|q| {
                                        q.space == "Home" && q.object_key() == p.object_key()
                                    }) {
                                        "Pinned Home"
                                    } else {
                                        "Pin to Home"
                                    },
                                )
                                .on_click(cx.listener(
                                    |this, _, _, cx| {
                                        if let Some(pin) = this.selected_pin() {
                                            this.home.place_in(&pin, "Home");
                                            this.save();
                                            this.status =
                                                "Pinned Home · shared object, separate placement"
                                                    .into();
                                        }
                                        cx.notify();
                                    },
                                )),
                            )
                        })
                        .when(p.query_id.is_some(), |row| {
                            row.child(
                                self.button(
                                    "auto-refresh",
                                    if self.home.live_objects.contains(&p.object_key()) {
                                        "Auto refresh: on"
                                    } else {
                                        "Auto refresh: off"
                                    },
                                )
                                .on_click(cx.listener(
                                    |this, _, _, cx| {
                                        if let Some(pin) = this.selected_pin() {
                                            let key = pin.object_key();
                                            if !this.home.live_objects.remove(&key) {
                                                this.home.live_objects.insert(key);
                                                this.request_preview(pin, true);
                                            }
                                            this.save();
                                        }
                                        cx.notify();
                                    },
                                )),
                            )
                        })
                        .when(p.query_id.is_some(), |row| {
                            row.child(
                                self.button("refresh-object", "Refresh now")
                                    .on_click(cx.listener(|this, _, _, cx| this.refresh(cx))),
                            )
                        })
                        .child(self.button("remove", "Hide from this canvas").on_click(
                            cx.listener(|this, _, _, cx| {
                                if let Some(id) = this.selected.take() {
                                    let surface = this
                                        .home
                                        .pins
                                        .iter()
                                        .find(|p| p.id == id)
                                        .and_then(|p| this.surface_for(p));
                                    this.home.hide_placement(&id, surface.as_ref());
                                }
                                this.inspector = false;
                                this.save();
                                cx.notify();
                            }),
                        )),
                );
            }
        } else {
            content = content.child(
                div()
                    .p_4()
                    .text_color(self.theme.muted)
                    .child("Select something on your canvas."),
            );
        }
        div()
            .id("inspector")
            .occlude()
            .w(px(if self.inspector_wide && !self.history {
                f32::from(window.viewport_size().width) - 32.
            } else {
                460.
            }))
            .when(self.inspector_wide && !self.history, |s| {
                s.absolute().top_0().bottom_0().right(px(16.))
            })
            .h_full()
            .flex_shrink_0()
            .bg(self.theme.panel)
            .border_l_1()
            .border_color(self.theme.line)
            .flex()
            .flex_col()
            .child(
                div()
                    .px_4()
                    .py_3()
                    .flex()
                    .items_center()
                    .justify_between()
                    .border_b_1()
                    .border_color(self.theme.line)
                    .child(
                        div()
                            .text_size(px(self.theme.typography.caption))
                            .text_color(self.theme.muted)
                            .child(title),
                    )
                    .when(!self.history, |s| {
                        s.child(
                            self.button(
                                "wide-detail",
                                if self.inspector_wide {
                                    "Side view"
                                } else {
                                    "Wide view"
                                },
                            )
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.inspector_wide = !this.inspector_wide;
                                cx.notify();
                            })),
                        )
                    })
                    .child(self.button("close-detail", "Close").on_click(cx.listener(
                        |this, _, _, cx| {
                            this.inspector = false;
                            this.history = false;
                            cx.notify();
                        },
                    ))),
            )
            .child(div().relative().flex_1().min_h_0().child(content).child(
                div().absolute().inset_0().child(
                    Scrollbar::vertical(&scroll_handle).scrollbar_show(ScrollbarShow::Always),
                ),
            ))
            .into_any_element()
    }

    fn topic_bar(&self, cx: &mut Context<Self>) -> AnyElement {
        let topic = self.home.active_topic().unwrap();
        let id = topic.session_id.clone();
        let hide_id = id.clone();
        div()
            .h(px(44.))
            .px_6()
            .flex()
            .items_center()
            .gap_3()
            .border_b_1()
            .border_color(self.theme.line)
            .child(
                self.button("back-home", "← Home")
                    .text_color(self.theme.accent)
                    .on_click(cx.listener(|this, _, window, cx| {
                        this.change_space("Home".into(), window, cx)
                    })),
            )
            .child(
                div()
                    .text_size(px(self.theme.typography.caption))
                    .text_color(self.theme.muted)
                    .child(short(&topic.title, 65)),
            )
            .child(div().flex_1())
            .child(
                self.button("topic-items", "All topic items")
                    .on_click(cx.listener(move |this, _, window, cx| {
                        this.browse_thread = Some(id.clone());
                        this.drawer = true;
                        this.search.update(cx, |s, cx| s.set_value("", window, cx));
                        this.fetch_thread(id.clone());
                        cx.notify();
                    })),
            )
            .child(
                self.button("put-topic-away", "Put topic away")
                    .on_click(cx.listener(move |this, _, window, cx| {
                        if let Some(t) = this
                            .home
                            .topics
                            .iter_mut()
                            .find(|t| t.session_id == hide_id)
                        {
                            t.pinned = false;
                        }
                        this.change_space("Home".into(), window, cx);
                    })),
            )
            .into_any_element()
    }
    fn home_footer(&self, cx: &mut Context<Self>) -> AnyElement {
        div().px_6().py_3().bg(self.theme.panel).border_t_1().border_color(self.theme.line)
            .flex().items_center().gap_4()
            .child(div().flex_1().flex().flex_col().gap_1()
                .child(div().text_size(px(self.theme.typography.body)).child("Your desk, shared with Callie"))
                .child(div().text_size(px(self.theme.typography.caption)).text_color(self.theme.muted).child(if self.running { "Callie is working inside a topic. You can keep exploring." } else { "Open a topic to talk. Pin useful objects here to keep them in view." })))
            .child(self.button("home-new-topic",if self.busy {"Connecting…"}else{"New topic +"})
                .bg(self.theme.accent_soft).text_color(self.theme.accent)
                .on_click(cx.listener(|this,_,_,cx|this.new_topic(cx))))
            .child(self.button("home-browse","Browse topics").on_click(cx.listener(|this,_,window,cx| {
                this.drawer=true;this.browse_thread=None;this.search.update(cx,|s,cx|s.set_value("",window,cx));cx.notify();
            })))
            .into_any_element()
    }

    fn composer(&mut self, window: &mut Window, cx: &mut Context<Self>) -> AnyElement {
        let target = self
            .target
            .as_ref()
            .and_then(|id| self.sessions.iter().find(|s| &s.id == id));
        let target_title = target
            .map(|s| short(&s.title, 65))
            .unwrap_or_else(|| "New conversation".into());
        let dictating = matches!(self.voice.phase, Phase::Recording | Phase::Transcribing);
        let can_send =
            self.connected && !self.running && !dictating && target.is_none_or(Session::writable);
        let selected = self.selected_pin().map(|p| short(&p.title, 65));
        let mut footer = div()
            .w_full()
            .flex_shrink_0()
            .bg(self.theme.panel)
            .border_t_1()
            .border_color(self.theme.line)
            .px_6()
            .py_3()
            .flex()
            .flex_col()
            .gap_2();
        if self.reply_visible && self.target == self.running_session {
            let reply_origin = self
                .running_session
                .as_ref()
                .and_then(|id| self.sessions.iter().find(|s| &s.id == id))
                .map(|s| short(&s.title, 40))
                .unwrap_or_else(|| "New conversation".into());
            let reply = if !self.draft.is_empty() {
                self.draft.clone()
            } else if !self.reply.is_empty() {
                self.reply.clone()
            } else {
                "Thinking with what’s here…".into()
            };
            footer = footer.child(
                div()
                    .flex()
                    .gap_4()
                    .items_start()
                    .child(
                        div()
                            .w(px(120.))
                            .flex_shrink_0()
                            .pt_2()
                            .flex()
                            .flex_col()
                            .gap_2()
                            .text_size(px(self.theme.typography.caption))
                            .text_color(self.theme.accent)
                            .child("CALLIE")
                            .child(div().text_color(self.theme.muted).child(reply_origin)),
                    )
                    .child(
                        div()
                            .id("live-reply")
                            .flex_1()
                            .min_w_0()
                            .max_h(px(210.))
                            .overflow_y_scroll()
                            .text_size(px(self.theme.typography.body))
                            .child(TextView::markdown(
                                "latest-reply",
                                short(&reply, 16000),
                                window,
                                cx,
                            )),
                    )
                    .child(self.button("hide-reply", "Hide").on_click(cx.listener(
                        |this, _, _, cx| {
                            this.reply_visible = false;
                            cx.notify();
                        },
                    ))),
            );
        }
        if self.voice.phase != Phase::Idle {
            let label = match self.voice.phase {
                Phase::Recording => "Listening · stop when you’re ready",
                Phase::Transcribing => "Turning your words into a draft…",
                Phase::Preparing => "Callie is preparing her voice…",
                Phase::Playing => "Callie is speaking",
                Phase::Idle => "",
            };
            footer = footer.child(
                div()
                    .flex()
                    .items_center()
                    .gap_3()
                    .child(
                        div()
                            .w(px(52.))
                            .h(px(4.))
                            .rounded_full()
                            .bg(self.theme.line)
                            .child(
                                div()
                                    .w(px(52. * (self.voice.level * 6.).clamp(0.05, 1.)))
                                    .h_full()
                                    .rounded_full()
                                    .bg(self.theme.accent),
                            ),
                    )
                    .child(
                        div()
                            .text_size(px(self.theme.typography.caption))
                            .text_color(self.theme.accent)
                            .child(label),
                    )
                    .child(
                        div()
                            .flex_1()
                            .min_w_0()
                            .overflow_hidden()
                            .text_size(px(self.theme.typography.caption))
                            .text_color(self.theme.muted)
                            .child(if self.voice.phase == Phase::Playing {
                                short(&self.voice_script, 100)
                            } else {
                                String::new()
                            }),
                    )
                    .child(
                        self.button(
                            "cancel-audio",
                            if dictating { "Discard" } else { "Stop audio" },
                        )
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.voice.cancel();
                            cx.notify();
                        })),
                    ),
            );
        }
        footer
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap_2()
                    .child(
                        div()
                            .text_size(px(self.theme.typography.caption))
                            .text_color(self.theme.muted)
                            .child("TALKING IN"),
                    )
                    .child(
                        div()
                            .text_size(px(self.theme.typography.caption))
                            .text_color(self.theme.accent)
                            .child(target_title),
                    )
                    .when_some(selected, |s, title| {
                        s.child(
                            div()
                                .text_size(px(self.theme.typography.caption))
                                .text_color(self.theme.muted)
                                .child(format!(" / {title}")),
                        )
                    })
                    .child(div().flex_1())
                    .child(self.button("history", "Conversation").on_click(cx.listener(
                        |this, _, _, cx| {
                            this.history = !this.history;
                            this.inspector = this.history;
                            if let Some(id) = this.target.clone() {
                                this.fetch_thread(id);
                            }
                            cx.notify();
                        },
                    )))
                    .child(self.button("new-chat", "New topic").on_click(cx.listener(
                        |this, _, _, cx| {
                            this.new_topic(cx);
                        },
                    ))),
            )
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap_4()
                    .child(
                        self.button(
                            "dictate",
                            if self.voice.phase == Phase::Recording {
                                "Stop recording"
                            } else if self.voice.phase == Phase::Transcribing {
                                "Transcribing…"
                            } else {
                                "Dictate"
                            },
                        )
                        .text_color(if self.voice.phase == Phase::Recording {
                            self.theme.danger
                        } else {
                            self.theme.accent
                        })
                        .when(!self.voice_settings.stt || self.running, |s| s.opacity(0.4))
                        .on_click(cx.listener(|this, _, _, cx| this.dictate(cx))),
                    )
                    .child(div().flex_1().child(
                        Input::new(&self.composer).appearance(false).disabled(
                            !self.connected
                                || (self.running && self.target == self.running_session),
                        ),
                    ))
                    .child(
                        self.button(
                            "send",
                            if self.running {
                                "Working…"
                            } else {
                                "Send ↑"
                            },
                        )
                        .bg(if can_send {
                            self.theme.accent_soft
                        } else {
                            self.theme.card
                        })
                        .text_color(self.theme.text)
                        .on_click(cx.listener(|this, _, window, cx| this.send(window, cx))),
                    )
                    .child(
                        self.button(
                            "read-aloud",
                            if self.read_aloud() {
                                "Voice on"
                            } else {
                                "Voice off"
                            },
                        )
                        .when(self.read_aloud(), |s| s.text_color(self.theme.accent))
                        .on_click(cx.listener(|this, _, _, cx| {
                            this.preferences.read_aloud = Some(!this.read_aloud());
                            if !this.read_aloud()
                                && matches!(this.voice.phase, Phase::Playing | Phase::Preparing)
                            {
                                this.voice.cancel();
                            }
                            if let Err(e) = this.preferences.save() {
                                this.error = Some(e.to_string());
                            }
                            cx.notify();
                        })),
                    )
                    .child(
                        self.button("listen", "Listen")
                            .when(!self.voice_settings.tts || dictating, |s| s.opacity(0.4))
                            .on_click(cx.listener(|this, _, _, cx| {
                                if !matches!(
                                    this.voice.phase,
                                    Phase::Recording | Phase::Transcribing
                                ) {
                                    this.listen(cx);
                                }
                            })),
                    ),
            )
            .child(
                div()
                    .text_size(px(self.theme.typography.caption))
                    .text_color(self.theme.muted)
                    .child(short(&self.status, 160)),
            )
            .into_any_element()
    }

    fn appearance_panel(&self, cx: &mut Context<Self>) -> AnyElement {
        div().absolute().top(px(68.)).right(px(20.)).w(px(390.)).p_5()
            .bg(self.theme.panel).border_1().border_color(self.theme.line).rounded_xl().shadow_lg()
            .flex().flex_col().gap_4()
            .on_mouse_down(MouseButton::Left, |_,_,cx| cx.stop_propagation())
            .child(div().flex().items_center().justify_between()
                .child(div().text_size(px(self.theme.typography.heading)).font_family(self.theme.font_display.clone()).child("Make it yours"))
                .child(self.button("close-appearance","Close").on_click(cx.listener(|this,_,_,cx|{this.appearance_open=false;cx.notify();}))))
            .child(div().text_size(px(self.theme.typography.body)).child(self.theme.name.clone()))
            .child(div().text_size(px(self.theme.typography.caption)).text_color(self.theme.muted)
                .child(format!("{} Lab appearance · {}",self.theme.source,if self.theme.dark {"dark"}else{"light"})))
            .child(div().flex().gap_2().children(self.theme.charts.iter().map(|c| div().size(px(22.)).rounded_full().bg(*c))))
            .child(div().text_size(px(self.theme.typography.caption)).text_color(self.theme.muted).child("Wallpaper presence on this device"))
            .child(div().flex().gap_2().children([("Quiet",0.14_f32),("Soft",0.22),("Vivid",0.40)].into_iter().map(|(label,strength)| {
                self.button(SharedString::from(format!("wallpaper-{label}")),label)
                    .when((self.preferences.wallpaper_strength-strength).abs()<0.01,|s|s.bg(self.theme.accent_soft).text_color(self.theme.accent))
                    .on_click(cx.listener(move |this,_,_,cx| {this.preferences.wallpaper_strength=strength;
                        if let Err(e)=this.preferences.save(){this.error=Some(e.to_string());}cx.notify();}))
            })))
            .child(div().text_size(px(self.theme.typography.caption)).text_color(self.theme.muted).child("Colors, type and wallpaper follow your saved Calliope appearance. This canvas adds its own softer lighting."))
            .child(self.button("sync-appearance","Sync from Lab").text_color(self.theme.accent)
                .on_click(cx.listener(|this,_,_,cx|{this.sync_foundations();cx.notify();})))
            .into_any_element()
    }

    fn login_panel(&mut self, cx: &mut Context<Self>) -> AnyElement {
        if self.connected && !self.connection_editing {
            return div().absolute().inset_0().bg(self.theme.scrim).flex().items_center().justify_center()
                .child(div().w(px(440.)).p_6().bg(self.theme.panel).rounded_xl().border_1().border_color(self.theme.line).flex().flex_col().gap_4()
                    .child(crate::brand::lockup(40.,24.))
                    .child("Signed in").child(self.config.email.clone()).child(self.config.base_url.clone())
                    .child(div().text_size(px(12.)).text_color(self.theme.muted).child("Unsent drafts stay on this device, separate for each server and account."))
                    .child(self.button("change-connection","Change connection").on_click(cx.listener(|s,_,window,cx|{
                        s.server_address.update(cx,|f,cx|f.set_value(s.config.base_url.clone(),window,cx));
                        s.email.update(cx,|f,cx|f.set_value(s.config.email.clone(),window,cx));
                        s.password.update(cx,|f,cx|f.set_value("",window,cx));
                        s.connection_editing=true;cx.notify();
                    })))
                    .child(self.button("close-account","Close").on_click(cx.listener(|s,_,_,cx|{s.login=false;cx.notify();}))))
                .into_any_element();
        }
        div().absolute().inset_0().bg(self.theme.scrim).flex().items_center().justify_center()
            .child(div().w(px(440.)).p_6().bg(self.theme.panel).border_1().border_color(self.theme.line).rounded_xl().shadow_lg().flex().flex_col().gap_4()
                .child(div().text_size(px(self.theme.typography.title)).child("Connect to Calliope"))
                .child(Input::new(&self.server_address))
                .child(Input::new(&self.email)).child(Input::new(&self.password))
                .child(div().text_size(px(self.theme.typography.caption)).line_height(relative(1.5)).text_color(self.theme.muted).child("Uses your normal Calliope login. Your password is never saved. The session is stored privately on this computer."))
                .child(div().flex().justify_between().child(self.button("cancel-login","Close").on_click(cx.listener(|this,_,_,cx|{this.login=false;cx.notify();})))
                    .child(self.button("sign-in",if self.busy{"Connecting…"}else{"Sign in →"}).bg(self.theme.accent_soft).text_color(self.theme.text).on_click(cx.listener(|this,_,window,cx|this.sign_in(window,cx)))))
                .when_some(self.error.clone(),|s,e|s.child(div().text_size(px(self.theme.typography.caption)).text_color(self.theme.danger).child(short(&e,300)))))
            .into_any_element()
    }
}

include!("canvas_records.rs");
include!("canvas_meaning.rs");

impl Render for CanvasApp {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        self.ensure_meaning(window,cx);
        if let Some(meaning)=&self.meaning{meaning.update(cx,|s,_|{s.theme=self.theme.clone();s.active=self.meaning_open&&!self.apps_open&&!self.business_open;});}
        if self.record_pins.is_none() && !self.config.session_cookie.is_empty() {
            let pins=cx.new(|cx|crate::app_pins::Pins::new(self.config.clone(),cx));
            self._subscriptions.push(cx.observe(&pins,|_,_,cx|cx.notify()));
            self.record_pins=Some(pins);
        }
        if let Some(apps) = &self.apps { apps.update(cx, |app, _| app.active = self.apps_open); }
        if self.apps_open {
            if self.apps.is_none() {
                self.apps = Some(cx.new(|cx| {
                    crate::published_apps::PublishedApps::with_pins(
                        self.config.clone(),
                        self.theme.clone(),
                        self.record_pins.clone(),
                        cx,
                    )
                }));
            }
            let apps = self.apps.as_ref().unwrap().clone();
            apps.update(cx, |app, cx| app.set_theme(self.theme.clone(), cx));
            return div()
                .size_full()
                .flex()
                .flex_col()
                .bg(self.theme.bg)
                .text_color(self.theme.text)
                .font_family(self.theme.font_body.clone())
                .child(
                    div()
                        .px_6()
                        .py_3()
                        .child(self.button("apps-back", "← Canvas").on_click(cx.listener(
                            |this, _, _, cx| {
                                this.apps_open = false;
                                if let Some(pins)=&this.record_pins {pins.update(cx,|p,_|p.refresh());}
                                cx.notify();
                            },
                        ))),
                )
                .child(div().flex_1().min_h_0().child(apps))
                .into_any_element();
        }
        if self.business_open {
            if self.business.is_none() {
                self.business =
                    Some(cx.new(|cx| crate::business::BusinessDesk::new(self.theme.clone(), cx)));
            }
            let business = self.business.as_ref().unwrap().clone();
            // Isolated app previews own their supplied theme and viewport.
            // The desktop demo navigation is not part of a generated app.
            if business.read(cx).is_preview() {
                return div().size_full().child(business).into_any_element();
            }
            business.update(cx, |view, cx| view.set_theme(self.theme.clone(), cx));
            return div()
                .size_full()
                .flex()
                .flex_col()
                .bg(self.theme.bg)
                .text_color(self.theme.text)
                .font_family(self.theme.font_body.clone())
                .child(
                    div()
                        .h(px(60.))
                        .flex_shrink_0()
                        .px_6()
                        .flex()
                        .items_center()
                        .gap_3()
                        .border_b_1()
                        .border_color(self.theme.line)
                        .child(
                            self.button("business-back", "← Canvas")
                                .on_click(cx.listener(|this, _, _, cx| {
                                    this.business_open = false;
                                    this.fit();
                                    cx.notify();
                                })),
                        )
                        .child(div().flex_1().child("Calliope / Business desk"))
                        .when(business.read(cx).is_case(), |s| {
                            s.child(
                                self.button("business-discuss", "Discuss this case")
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.discuss_case(window, cx)
                                    })),
                            )
                        })
                        .child(
                            self.button("business-web", "Open web view ↗")
                                .on_click(|_, _, cx| cx.open_url(crate::business::URL)),
                        ),
                )
                .child(div().flex_1().min_h_0().child(business))
                .into_any_element();
        }
        if self.meaning_open && let Some(meaning)=self.meaning.clone(){
            let header=self.header(window,cx);
            return div().size_full().bg(self.theme.bg).text_color(self.theme.text).font_family(self.theme.font_body.clone()).flex().flex_col()
                .child(header).child(div().flex_1().min_h_0().child(meaning))
                .when_some(self.error.clone(),|s,e|s.child(div().px_6().py_2().text_color(self.theme.warning).child(short(&e,220))))
                .when(self.appearance_open,|s|s.child(self.appearance_panel(cx)))
                .when(self.login,|s|s.child(self.login_panel(cx))).into_any_element();
        }
        let pointer = cx.entity().downgrade();
        let drag_events = canvas(
            |_, _, _| (),
            move |_, _, window, _| {
                let moving = pointer.clone();
                window.on_mouse_event(move |e: &MouseMoveEvent, phase, _, cx| {
                    if phase == DispatchPhase::Capture {
                        let _ = moving.update(cx, |this, cx| this.track_drag(e, cx));
                    }
                });
                let releasing = pointer.clone();
                window.on_mouse_event(move |e: &MouseUpEvent, phase, window, cx| {
                    if phase == DispatchPhase::Capture {
                        let _ = releasing.update(cx, |this, cx| this.finish_drag(e, window, cx));
                    }
                });
            },
        )
        .absolute()
        .size_full();
        let header = self.header(window,cx);
        let stage = self.stage(window, cx);
        let footer = if self.home.active_topic().is_some() {
            self.composer(window, cx)
        } else {
            self.home_footer(cx)
        };
        let topic_bar = self.home.active_topic().map(|_| self.topic_bar(cx));
        let drawer = if self.drawer {
            Some(self.drawer(cx))
        } else {
            None
        };
        let inspector = if self.inspector || self.history {
            Some(self.inspector(window, cx))
        } else {
            None
        };
        div()
            .relative()
            .size_full()
            .bg(self.theme.bg)
            .text_color(self.theme.text)
            .font_family(self.theme.font_body.clone())
            .flex()
            .flex_col()
            .child(header)
            .when_some(topic_bar, |s, bar| s.child(bar))
            .when_some(self.error.clone().filter(|_| !self.login), |s, e| {
                s.child(
                    div()
                        .px_6()
                        .py_2()
                        .bg(self.theme.accent_soft)
                        .flex()
                        .items_center()
                        .gap_3()
                        .child(
                            div()
                                .flex_1()
                                .text_size(px(self.theme.typography.caption))
                                .child(short(&e, 220)),
                        )
                        .child(
                            self.button("dismiss-error", "Dismiss")
                                .on_click(cx.listener(|this, _, _, cx| {
                                    this.error = None;
                                    cx.notify();
                                })),
                        ),
                )
            })
            .child(
                div()
                    .relative()
                    .flex()
                    .flex_1()
                    .min_h_0()
                    .min_w_0()
                    .when_some(drawer, |s, d| s.child(d))
                    .child(stage)
                    .when_some(inspector, |s, d| s.child(d)),
            )
            .child(footer)
            .when(self.appearance_open, |s| s.child(self.appearance_panel(cx)))
            .when(self.login, |s| s.child(self.login_panel(cx)))
            .child(drag_events)
            .into_any_element()
    }
}

/// Each place a chart appears gets its own card: the renderer measures its width.
pub(super) fn chart_placement(surface: &str, pin: &str, expanded: bool) -> String {
    format!("{surface}@{pin}{}", if expanded { "+inspector" } else { "" })
}
