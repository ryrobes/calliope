extern crate gpui_mobile;
#[path = "../../calliope-canvas/src/app_pins.rs"]
mod app_pins;
#[path = "../../calliope-canvas/src/app_context.rs"]
mod app_context;
#[path = "../../calliope-canvas/src/app_assistant.rs"]
mod app_assistant;
#[path = "../../calliope-canvas/src/turn_outputs.rs"]
mod turn_outputs;
#[path = "../../calliope-canvas/src/app_stream.rs"]
mod app_stream;
#[path = "../../calliope-canvas/src/voice.rs"]
mod voice;
mod api;
mod browser_login;
#[path = "../../calliope-canvas/src/native_link.rs"]
mod native_link;
mod conversations;
#[path = "../../calliope-canvas/src/app_drafts.rs"]
mod app_drafts;
#[path = "../../calliope-canvas/src/app_workspace.rs"]
mod app_workspace;
#[path = "../../calliope-canvas/src/app_connection.rs"]
mod app_connection;
#[path = "../../calliope-canvas/src/business_filters.rs"]
mod business_filters;
#[path = "../../calliope-canvas/src/brand.rs"]
mod brand;
#[cfg(target_os = "android")]
mod host;
#[cfg(target_os = "ios")]
#[path = "host_ios.rs"]
mod host;
mod model;
#[path = "../../calliope-canvas/src/published_apps.rs"]
mod published_apps;
#[path = "../../calliope-canvas/src/server_charts.rs"]
mod server_charts;
#[path = "../../calliope-canvas/src/stage_cards.rs"]
mod stage_cards;
mod business_probe {
    pub use super::shared_probe::Request;
    pub fn start(
        _: std::sync::Arc<dyn Fn() + Send + Sync>,
    ) -> Option<std::sync::mpsc::Receiver<Request>> {
        None
    }
}
#[path = "../../calliope-canvas/src/mailbox.rs"]
mod mailbox;
#[path = "../../calliope-canvas/src/business_probe.rs"]
mod shared_probe;
#[path = "../../calliope-canvas/src/theme.rs"]
mod theme;
mod ui;

use gpui::{prelude::*, *};

fn launch(cx: &mut App) {
    gpui_kit::init(cx);
    brand::init(cx);
    gpui_component::Theme::change(gpui_component::ThemeMode::Dark, None, cx);
    // Use a real platform fallback before the server fonts have loaded.
    gpui_component::Theme::global_mut(cx).mono_font_family =
        if cfg!(target_os = "ios") { "Menlo" } else { "Roboto" }.into();
    cx.open_window(WindowOptions::default(), |window, cx| {
        let shell = cx.new(|cx| MobileShell::new(window, cx));
        cx.new(|cx| gpui_component::Root::new(shell, window, cx))
    })
    .expect("Open mobile surface");
    cx.activate(true);
    log::info!("Calliope GPUI surface ready");
}

use gpui_component::input::{Input, InputState};
use serde_json::json;
enum ShellReply {
    Loaded(api::Connection, anyhow::Result<theme::Assets>),
    BrowserReady(browser_login::Flow, bool),
}
struct MobileShell {
    login_generation: u64,
    browser_flow: Option<browser_login::Flow>,
    browser_cancel: Option<std::sync::Arc<std::sync::atomic::AtomicBool>>,
    destination: Option<native_link::Destination>,
    apps: Option<Entity<published_apps::PublishedApps>>,
    conversations: Option<Entity<conversations::Conversations>>,
    chat_tab: bool,
    _chat_subscription: Option<Subscription>,
    config: api::Connection,
    fields: [Entity<InputState>; 3],
    connecting: bool,
    account: bool,
    account_editing: bool,
    _field_subscriptions: Vec<Subscription>,
    message: String,
    background: Rgba,
    foreground: Rgba,
    font: SharedString,
    palette: Option<theme::CanvasTheme>,
    /// Width and tallest height seen at that width; a shorter window means the keyboard is up.
    tallest: (f32, f32),
    keyboard_up: bool,
    tx: mailbox::Sender<(u64, ShellReply)>,
    rx: std::sync::mpsc::Receiver<(u64, ShellReply)>,
    probe: Option<std::sync::mpsc::Receiver<shared_probe::Request>>,
}
impl MobileShell {
    fn new(window: &mut Window, cx: &mut Context<Self>) -> Self {
        let (tx, rx, wake) = mailbox::channel();
        #[cfg(target_os = "android")]
        let path = model::data_dir().join("ui-probe.sock");
        #[cfg(target_os = "ios")]
        let path = std::path::PathBuf::from("ui-probe.sock");
        let _ = std::fs::remove_file(&path);
        let probe = shared_probe::start_at(path, tx.notifier());
        let sender = tx.clone();
        std::thread::spawn(move || {
            if let Ok(Some(flow))=browser_login::Flow::load() {
                let _=sender.send((0,ShellReply::BrowserReady(flow,false)));return;
            }
            let config = api::Connection::load();
            let result = match &config {
                Ok(c) if !c.session_cookie.is_empty() => Self::appearance(c),
                Ok(_) => Err(anyhow::anyhow!("Sign in to your Calliope instance.")),
                Err(e) => Err(anyhow::anyhow!(e.to_string())),
            };
            let _ = sender.send((0,ShellReply::Loaded(config.unwrap_or_default(), result)));
        });
        cx.spawn(async move |view, cx| {
            loop {
                let woke = smol::future::or(
                    async {
                        let _ = wake.recv().await;
                        true
                    },
                    async {
                        smol::Timer::after(std::time::Duration::from_millis(120)).await;
                        false
                    },
                )
                .await;
                if view
                    .update(cx, |s, cx| {
                        if woke {
                            cx.notify();
                        }
                        if s.account && !s.connecting && host::take_back() {
                            if s.apps.is_some() {
                                // Back walks out one level: the connection form, then Account.
                                if s.account_editing { s.account_editing = false; } else { s.account = false; }
                                cx.notify();
                            } else {
                                host::background();
                            }
                        }
                    })
                    .is_err()
                {
                    break;
                }
            }
        })
        .detach();
        let fields = [
                cx.new(|cx| {
                    InputState::new(window, cx).placeholder("https://your-calliope.example")
                }),
                cx.new(|cx| InputState::new(window, cx).placeholder("Email")),
                cx.new(|cx| {
                    InputState::new(window, cx)
                        .placeholder("Password")
                        .masked(true)
                }),
            ];
        let field_subscriptions=fields.iter().map(|field|cx.subscribe(field,|_,_,_: &gpui_component::input::InputEvent,cx|cx.notify())).collect();
        Self {
            login_generation:0, browser_flow:None, browser_cancel:None, destination:None,
            apps: None,
            conversations: None,
            chat_tab: false,
            _chat_subscription: None,
            config: api::Connection::default(),
            fields,
            _field_subscriptions: field_subscriptions,
            connecting: true,
            account: true,
            account_editing: true,
            message: "Connecting to Calliope…".into(),
            background: rgb(0x111827),
            foreground: rgb(0xe5e7eb),
            font: if cfg!(target_os = "ios") { ".SystemUIFont" } else { "Roboto" }.into(),
            palette: None,
            tallest: (0., 0.),
            keyboard_up: false,
            tx,
            rx,
            probe,
        }
    }
    fn appearance(config: &api::Connection) -> anyhow::Result<theme::Assets> {
        let api = api::Api::new(config.clone())?;
        match api.get("/api/calliope/ui-apps") {
            Ok(_) => {
                let appearance = api.get("/api/calliope/appearance")?;
                app_drafts::atomic(
                    &app_drafts::account_root(config).join("appearance.json"),
                    &serde_json::to_vec(&appearance)?,
                )?;
                theme::load(&api)
            }
            Err(e)
                if app_drafts::transport_error(&e)
                    && app_drafts::account_root(config).join("index.json").exists() =>
            {
                let appearance =
                    std::fs::read(app_drafts::account_root(config).join("appearance.json"))?;
                Ok(theme::Assets {
                    theme: theme::CanvasTheme::from_appearance(&serde_json::from_slice(
                        &appearance,
                    )?),
                    fonts: theme::cached_fonts(&config.base_url),
                    warnings: vec!["Offline · using cached appearance".into()],
                })
            }
            Err(e) => Err(e),
        }
    }
    fn edit_connection(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.fields[0].update(cx, |f, cx| f.set_value(self.config.base_url.clone(), window, cx));
        self.fields[1].update(cx, |f, cx| f.set_value(self.config.email.clone(), window, cx));
        self.fields[2].update(cx, |f, cx| f.set_value("", window, cx));
        self.account_editing = true;
        cx.notify();
    }
    fn connect(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if self.connecting {
            return;
        }
        let base_url = self.fields[0]
            .read(cx)
            .value()
            .trim()
            .trim_end_matches('/')
            .to_owned();
        let email = self.fields[1].read(cx).value().trim().to_owned();
        let password = self.fields[2].read(cx).value().to_string();
        if base_url.is_empty() || email.is_empty() || password.is_empty() {
            self.message = "Enter your server, email and password.".into();
            cx.notify();
            return;
        }
        let browser_url = if base_url == self.config.base_url {
            self.config.browser_url.clone()
        } else {
            base_url.clone()
        };
        let config = api::Connection {
            base_url,
            browser_url,
            email,
            session_cookie: String::new(),
        };
        self.fields[2].update(cx, |f, cx| f.set_value("", window, cx));
        self.connecting = true;
        self.message = "Signing in…".into();
        self.login_generation+=1;let generation=self.login_generation;
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let mut current = config.clone();
            let result = (|| {
                current = api::Api::login(config, password)?;
                Self::appearance(&current)
            })();
            let _ = tx.send((generation,ShellReply::Loaded(current, result)));
        });
        cx.notify();
    }
}
impl Render for MobileShell {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        while let Ok((generation, reply)) = self.rx.try_recv() {
            if generation!=self.login_generation {continue;}
            let (config,result)=match reply {
                ShellReply::BrowserReady(flow,open)=>{self.browser_ready(flow,open,window,cx);continue;},
                ShellReply::Loaded(config,result)=>(config,result),
            };
            if self.browser_flow.take().is_some() {
                self.browser_cancel=None;
                if !config.session_cookie.is_empty() {
                    if let Err(e)=config.save() {self.message=e.to_string();self.connecting=false;cx.notify();continue;}
                } else {browser_login::Flow::clear();}
            }
            self.connecting = false;
            self.fields[2].update(cx, |field, cx| field.set_value("", window, cx));
            for (i, value) in [config.base_url.clone(), config.email.clone()]
                .into_iter()
                .enumerate()
            {
                self.fields[i].update(cx, |f, cx| f.set_value(value, window, cx));
            }
            match result {
                Ok(assets) => {
                    self.config = config.clone();
                    for font in assets.fonts {
                        let _ = cx.text_system().add_fonts(vec![font.into()]);
                    }
                    assets.theme.apply(cx);
                    let theme = gpui_component::Theme::global_mut(cx);
                    theme.tokens = theme.colors.into();
                    gpui_component::Theme::sync_base(cx);
                    self.background = assets.theme.bg;
                    self.foreground = assets.theme.text;
                    self.font = assets.theme.font_body.clone();
                    self.palette = Some(assets.theme.clone());
                    let weak = cx.entity().downgrade();
                    self.conversations = Some(cx.new(|cx| conversations::Conversations::new(config.clone(), assets.theme.clone(), window, cx)));
                    self._chat_subscription = Some(cx.subscribe(self.conversations.as_ref().unwrap(), |s,_,event:&conversations::OpenApp,cx| {
                        if let Some(apps)=&s.apps {
                            match apps.update(cx,|a,cx|match &event.1 {
                                Some(reference)=>a.open_record(reference.clone(),false,cx),
                                None=>a.request_open(event.0.clone(),cx),
                            }) {
                                Ok(())=>{s.chat_tab=false;cx.notify();},
                                Err(e)=>{if let Some(chat)=&s.conversations {chat.update(cx,|c,cx|c.report_error(e.to_string(),cx));}},
                            }
                        }
                    }));
                    self.chat_tab = false;
                    self.apps = Some(cx.new(|cx| {
                        let mut apps = published_apps::PublishedApps::new(config, assets.theme, cx);
                        apps.on_connection = Some(std::rc::Rc::new(move |cx| {
                            let _ = weak.update(cx, |s, cx| {
                                s.account = true;
                                s.account_editing = false;
                                s.message = "Your Calliope connection".into();
                                cx.notify();
                            });
                        }));
                        apps
                    }));
                    self.account = false;
                    self.account_editing = false;
                    self.message = "Connected".into();
                    self.open_destination(cx);
                }
                Err(e) => {
                    self.account = true;
                    self.account_editing=true;
                    // Include the cause (DNS, TLS, connection refused), not just
                    // reqwest's otherwise indistinguishable request error.
                    self.message = format!("{e:#}");
                }
            }
        }
        if !self.connecting {if let Some(url)=host::take_link() {self.incoming_link(&url,window,cx);}}
        let requests: Vec<_> = self
            .probe
            .as_ref()
            .map(|p| p.try_iter().collect())
            .unwrap_or_default();
        for request in requests {
            if request.body["op"]=="open_link" {
                self.incoming_link(request.body["url"].as_str().unwrap_or_default(),window,cx);
                let _=request.reply.send(json!({"ok":true}));continue;
            }
            if request.body["op"] == "navigate" && self.apps.is_some() && !self.account {
                self.chat_tab = request.body["screen"] == "conversations";
                window.blur(cx);
                cx.notify();
                let _ = request.reply.send(json!({"ok":true}));
                continue;
            }
            if request.body["op"] == "account" {
                self.account = true;
                self.account_editing = self.apps.is_none();
                let _ = request.reply.send(json!({"ok":true}));
                continue;
            }
            if !self.account {
                if self.chat_tab {
                    if let Some(chat) = &self.conversations {
                        let value = chat.update(cx, |s,cx|s.probe(&request.body,window,cx));
                        let _ = request.reply.send(value);
                        continue;
                    }
                }
                if let Some(apps) = &self.apps {
                    apps.update(cx, |a, cx| a.enqueue_probe(request, cx));
                    continue;
                }
            }
            let value = match request.body["op"].as_str().unwrap_or("") {
                "inspect" => {
                    json!({"account":true,"editing":self.account_editing,"signed_in":self.apps.is_some(),"busy":self.connecting,"notice":self.message,"server":self.fields[0].read(cx).value().as_ref(),"email":self.fields[1].read(cx).value().as_ref(),"has_password":!self.fields[2].read(cx).value().is_empty(),"browser_code":self.browser_flow.as_ref().map(|f|f.code.clone()),"browser_path":self.browser_flow.as_ref().map(|f|f.verification_path.clone())})
                }
                "press" if request.body["id"]=="change_connection" => {self.edit_connection(window,cx);json!({"ok":true})}
                "fill" if !self.connecting && self.account_editing => {
                    match request.body["id"].as_str().and_then(|id| {
                        ["server", "email", "password"]
                            .iter()
                            .position(|f| *f == id)
                    }) {
                        Some(i) => {
                            self.fields[i].update(cx, |f, cx| {
                                f.set_value(
                                    request.body["value"].as_str().unwrap_or(""),
                                    window,
                                    cx,
                                )
                            });
                            json!({"ok":true})
                        }
                        None => json!({"error":"Unknown connection field"}),
                    }
                }
                "press" if request.body["id"]=="browser_connect" => {self.browser_connect(window,cx);json!({"ok":true})}
                "press" if request.body["id"]=="cancel_browser" => {self.cancel_browser(cx);json!({"ok":true})}
                "press" if request.body["id"] == "connect" => {
                    self.connect(window, cx);
                    json!({"ok":true})
                }
                "press"
                    if request.body["id"] == "cancel"
                        && self.apps.is_some()
                        && !self.connecting =>
                {
                    self.account = false;
                    self.account_editing=false;
                    self.fields[2].update(cx,|s,cx|s.set_value("",window,cx));
                    json!({"ok":true})
                }
                _ => json!({"error":"Connection screen is active"}),
            };
            let _ = request.reply.send(value);
        }
        if let Some(apps) = &self.apps {
            apps.update(cx, |a, _| a.active = !self.account && !self.chat_tab);
        }
        if let Some(chat) = &self.conversations {
            chat.update(cx, |a,_|a.active = !self.account && self.chat_tab);
        }
        let viewport = window.viewport_size();
        let (width, height) = (f32::from(viewport.width), f32::from(viewport.height));
        if (self.tallest.0 - width).abs() > 1. {
            self.tallest = (width, height);
        } else {
            self.tallest.1 = self.tallest.1.max(height);
        }
        let keyboard = height < self.tallest.1 * 0.8;
        if self.keyboard_up && !keyboard {
            // Back closed the keyboard; drop the caret with it.
            window.blur(cx);
        }
        self.keyboard_up = keyboard;
        let root = div()
            .size_full()
            .flex()
            .flex_col()
            .bg(self.background)
            .text_color(self.foreground)
            .font_family(self.font.clone());
        if !self.account && !self.fields[2].read(cx).value().is_empty() {
            self.fields[2].update(cx, |field, cx| field.set_value("", window, cx));
        }
        if self.account && self.account_editing {
            for (field,kind) in self.fields.iter().zip(["url","email","password"]) {
                if field.read(cx).focus_handle(cx).is_focused(window) {host::input_kind(kind);break;}
            }
        }
        let t = self.palette.clone().unwrap_or_default();
        if let Some(apps) = self.apps.clone() {
            if !self.account || !self.account_editing {
                let content = if self.account {
                    self.account_view(&t, cx)
                } else if self.chat_tab {
                    self.conversations.as_ref().unwrap().clone().into_any_element()
                } else {
                    apps.into_any_element()
                };
                // The tab bar steps aside while the keyboard is up.
                let nav = !self.keyboard_up && (self.account || !self.chat_tab || height >= 320.);
                let (callie, apps_tab) = (!self.account && self.chat_tab, !self.account && !self.chat_tab);
                return root.child(div().flex_1().min_h_0().overflow_hidden().child(content)).when(nav, |s| {
                    s.child(
                        div().flex_shrink_0().px_2().pb_1().bg(t.panel).border_t_1().border_color(t.line).flex()
                            .child(ui::nav_tab("nav-callie", ui::Glyph::MessageCircle, "Ask Callie", callie, &t,
                                cx.listener(|s,_,w,cx|{w.blur(cx);s.account=false;s.chat_tab=true;cx.notify();})))
                            .child(ui::nav_tab("nav-apps", ui::Glyph::LayoutGrid, "Apps", apps_tab, &t,
                                cx.listener(|s,_,w,cx|{w.blur(cx);s.account=false;s.chat_tab=false;cx.notify();})))
                            .child(ui::nav_tab("nav-account", ui::Glyph::CircleUser, "Account", self.account, &t,
                                cx.listener(|s,_,w,cx|{w.blur(cx);s.account=true;s.account_editing=false;cx.notify();}))),
                    )
                });
            }
        }
        let label = |text: &'static str| ui::caption(text, &t);
        let hint = |text: &'static str| div().text_size(px(12.)).text_color(t.muted).child(text);
        root.child(
            div()
                .id("connection")
                .size_full()
                .overflow_y_scroll()
                .px_5()
                .py_6()
                .flex()
                .flex_col()
                .gap_5()
                .child(brand::lockup(44., 26.))
                .child(div().text_size(px(22.)).font_weight(FontWeight::SEMIBOLD)
                    .child(if self.apps.is_some(){"Change your connection"}else{"Sign in to Calliope"}))
                .child(div().flex().flex_col().gap_2()
                    .child(label("Calliope server"))
                    .child(Input::new(&self.fields[0]).disabled(self.connecting))
                    .child(hint("Your Calliope HTTPS address. A localhost address is for USB testing through your computer.")))
                .child(div().flex().flex_col().gap_2()
                    .child(ui::action("browser-connect", Some(ui::Glyph::Globe), "Continue in browser", ui::Tone::Primary, &t,
                        !self.connecting, cx.listener(|s,_,w,cx|s.browser_connect(w,cx))))
                    .child(hint("Google or your instance’s other sign-in methods. Your browser handles authentication."))
                    .when(self.browser_flow.is_some(), |s| s.child(ui::action("cancel-browser", Some(ui::Glyph::X), "Cancel browser sign-in",
                        ui::Tone::Outline, &t, true, cx.listener(|s,_,_,cx|s.cancel_browser(cx))))))
                .child(div().flex().items_center().gap_3()
                    .child(div().flex_1().h(px(1.)).bg(t.line))
                    .child(div().text_size(px(12.)).text_color(t.muted).child("or use a password"))
                    .child(div().flex_1().h(px(1.)).bg(t.line)))
                .child(div().flex().flex_col().gap_2()
                    .child(label("Email"))
                    .child(Input::new(&self.fields[1]).disabled(self.connecting))
                    .child(label("Password"))
                    .child(Input::new(&self.fields[2]).disabled(self.connecting)))
                .when(!self.message.is_empty(), |s| s.child(div().text_sm().text_color(t.muted).child(self.message.clone())))
                .child(ui::action("connect", Some(ui::Glyph::LogIn), if self.connecting {"Connecting…"} else {"Sign in"},
                    ui::Tone::Tonal, &t, !self.connecting, cx.listener(|s, _, window, cx| s.connect(window, cx))))
                .when(self.apps.is_some(), |s| {
                    s.child(ui::action("cancel", Some(ui::Glyph::ChevronLeft), "Back", ui::Tone::Quiet, &t, !self.connecting,
                        cx.listener(|s, _, _, cx| {
                            if !s.connecting {
                                s.account_editing=false;
                                cx.notify();
                            }
                        })))
                }),
        )
    }
}
impl MobileShell {
    fn account_view(&self, t: &theme::CanvasTheme, cx: &mut Context<Self>) -> AnyElement {
        let usb = reqwest::Url::parse(&self.config.base_url).ok()
            .is_some_and(|u| matches!(u.host_str(), Some("localhost"|"127.0.0.1"|"[::1]")));
        let server = reqwest::Url::parse(&self.config.base_url).ok()
            .and_then(|u| u.host_str().map(str::to_owned)).unwrap_or_else(|| self.config.base_url.clone());
        div()
            .id("account-summary")
            .size_full()
            .overflow_y_scroll()
            .px_4()
            .pt_5()
            .pb_6()
            .flex()
            .flex_col()
            .gap_5()
            .child(div().px_1().text_size(px(26.)).font_weight(FontWeight::SEMIBOLD).child("Account"))
            .child(
                div().p_4().rounded_xl().bg(t.card).border_1().border_color(t.line).flex().items_center().gap_3()
                    .child(brand::avatar(48.))
                    .child(div().flex_1().min_w_0().flex().flex_col().gap_0p5()
                        .child(div().text_size(px(16.)).font_weight(FontWeight::SEMIBOLD).truncate().child(self.config.email.clone()))
                        .child(div().flex().items_center().gap_1p5().text_size(px(13.)).text_color(t.muted)
                            .child(ui::glyph(ui::Glyph::Server, 14., t.muted))
                            .child(div().min_w_0().truncate().child(server)))
                        .when(usb, |s| s.child(div().text_size(px(12.)).text_color(t.warning).child("USB test connection through your computer.")))),
            )
            .child(
                div().flex().flex_col().gap_2()
                    .child(div().px_1().child(ui::caption("Connection", t)))
                    .child(div().rounded_xl().bg(t.card).border_1().border_color(t.line).overflow_hidden().flex().flex_col()
                        .child(ui::list_row("change-connection", ui::Glyph::Server, "Change connection",
                            Some("Server address, email or password".into()), t, true,
                            cx.listener(|s,_,window,cx| s.edit_connection(window,cx))))
                        .child(div().h(px(1.)).ml(px(64.)).bg(t.line))
                        .child(ui::list_row("reconnect-browser", ui::Glyph::Globe, "Reconnect in browser",
                            Some("Sign in again with Google or your company login".into()), t, true,
                            cx.listener(|s,_,w,cx|{s.edit_connection(w,cx);s.browser_connect(w,cx);})))),
            )
            .child(div().px_1().text_size(px(12.)).text_color(t.muted)
                .child("Unsent drafts stay on this device, separate for each server and account."))
            .into_any_element()
    }
}

#[cfg(target_os = "android")]
#[unsafe(no_mangle)]
fn android_main(_app: android_activity::AndroidApp) {}
