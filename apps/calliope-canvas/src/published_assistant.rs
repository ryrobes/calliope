// Shared contextual conversation, included by published_apps.rs.
impl PublishedApps {
    fn assistant_poll(&mut self, cx: &mut Context<Self>) {
        if self.assistant.poll() {
            cx.notify();
        }
        let foreground = self.active;
        #[cfg(any(target_os = "android", target_os = "ios"))]
        let foreground = foreground && crate::host::is_foreground();
        if (!foreground || !self.assistant.open)
            && self.assistant.voice.phase != crate::voice::Phase::Idle
        {
            self.assistant.voice.cancel();
        }
        let location = if matches!(
            self.assistant.voice.phase,
            crate::voice::Phase::Recording | crate::voice::Phase::Transcribing
        ) {
            crate::app_workspace::location(&self.snapshot, &self.renderer.read(cx).data_value("/"))
        } else {
            String::new()
        };
        if matches!(
            self.assistant.voice.phase,
            crate::voice::Phase::Recording | crate::voice::Phase::Transcribing
        ) && (self.slug != self.assistant.voice_slug
            || location != self.assistant.voice_location)
        {
            self.assistant.voice.cancel();
        }
        for event in self.assistant.voice.poll() {
            match event {
                crate::voice::Event::Transcript(text) => {
                    let slug = self.assistant.voice_slug.clone();
                    let chat = self.assistant.chats.entry(slug.clone()).or_default();
                    if !chat.draft.is_empty() {
                        chat.draft.push(' ');
                    }
                    chat.draft.push_str(&text);
                    self.assistant.transcript_pending = Some((slug, chat.draft.clone()));
                }
                crate::voice::Event::Error(e) => {
                    self.assistant
                        .chats
                        .entry(self.assistant.voice_slug.clone())
                        .or_default()
                        .error = e;
                }
                _ => {}
            }
            cx.notify();
        }
        for (slug, session, turn) in std::mem::take(&mut self.assistant.completed) {
            if slug == self.slug && self.assistant.open && foreground && !turn.is_empty() {
                if let Some(settings) = self.assistant.voice_settings.clone().filter(|s| {
                    s.tts
                        && crate::theme::Preferences::load()
                            .read_aloud
                            .unwrap_or(s.auto_read)
                }) {
                    self.assistant.voice_slug = slug;
                    self.assistant
                        .voice
                        .speak(self.api.clone(), session, turn, settings, true);
                }
            }
        }
    }
    fn assistant_dictate(&mut self, cx: &mut Context<Self>) {
        self.assistant
            .chats
            .entry(self.slug.clone())
            .or_default()
            .error
            .clear();
        use crate::voice::Phase;
        match self.assistant.voice.phase {
            Phase::Recording => self.assistant.voice.stop_recording(),
            Phase::Idle => {
                if let Some(settings) = self.assistant.voice_settings.clone().filter(|s| s.stt) {
                    // Request permission before starting a voice task. Android's
                    // permission dialog pauses the activity; cancelling a worker
                    // there would otherwise swallow its useful denial message.
                    #[cfg(any(target_os = "android", target_os = "ios"))]
                    if let Err(error) = crate::host::audio_request_permission() {
                        self.assistant.chats.entry(self.slug.clone()).or_default().error = error;
                        cx.notify();
                        return;
                    }
                    self.assistant.voice_slug = self.slug.clone();
                    self.assistant.voice_location = crate::app_workspace::location(
                        &self.snapshot,
                        &self.renderer.read(cx).data_value("/"),
                    );
                    self.assistant.voice.record(self.api.clone(), settings);
                }
            }
            _ => self.assistant.voice.cancel(),
        }
        cx.notify();
    }
    fn assistant_read(&mut self, index: usize, cx: &mut Context<Self>) {
        self.assistant
            .chats
            .entry(self.slug.clone())
            .or_default()
            .error
            .clear();
        if let Some(c) = self.assistant.chats.get(&self.slug) {
            if let Some(t) = c.turns.get(index) {
                if let Some(settings) = self.assistant.voice_settings.clone().filter(|s| s.tts) {
                    if !t.id.is_empty() {
                        self.assistant.voice_slug = self.slug.clone();
                        self.assistant.voice.speak(
                            self.api.clone(),
                            c.session.clone(),
                            t.id.clone(),
                            settings,
                            false,
                        );
                    }
                }
            }
        }
        cx.notify();
    }
    fn app_context(&self, cx: &App) -> Value {
        let renderer = self.renderer.read(cx);
        let key = format!(
            "{}:{}:{}:{}:{}",
            self.slug,
            self.snapshot["version"],
            renderer.context_revision(),
            self.offline,
            self.workspace.fetched_at
        );
        if let Some((old, value)) = &*self.assistant.context_cache.borrow() {
            if *old == key {
                return value.clone();
            }
        }
        let value = crate::app_context::capture(
            &self.snapshot,
            &renderer.probe_snapshot(),
            self.offline,
            self.workspace.fetched_at,
        );
        *self.assistant.context_cache.borrow_mut() = Some((key, value.clone()));
        value
    }
    fn assistant_open(&mut self, cx: &mut Context<Self>) {
        if self.slug.is_empty() {
            return;
        }
        self.assistant.open = true;
        self.assistant.load(self.api.clone(), &self.slug);
        cx.notify();
    }
    fn assistant_send(&mut self, retry: bool, window: &mut Window, cx: &mut Context<Self>) {
        if self.busy || self.offline || self.snapshot.is_null() {
            return;
        }
        let question = self
            .assistant
            .input
            .as_ref()
            .map(|i| i.read(cx).value().to_string())
            .unwrap_or_default();
        if !retry && question.trim().is_empty() {
            return;
        }
        let mut context = self.app_context(cx);
        context["context_id"] = json!(uuid::Uuid::new_v4().to_string());
        context["intent"] = json!(if self.assistant.improvement {"improve"} else {"assist"});
        context["viewport"] = json!({"width":f32::from(window.viewport_size().width).round() as u32,
            "height":f32::from(window.viewport_size().height).round() as u32});
        self.assistant
            .send(self.api.clone(), &self.slug, question, context, retry);
        if self
            .assistant
            .chats
            .get(&self.slug)
            .is_some_and(|c| c.running)
            && !retry
        {
            if let Some(input) = &self.assistant.input {
                input.update(cx, |s, cx| s.set_value("", window, cx));
            }
        }
        cx.notify();
    }
    fn assistant_apply(
        &mut self,
        index: usize,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Result<()> {
        anyhow::ensure!(
            !self.busy && self.retry.is_none() && !self.command_pending,
            "Wait for the current app request"
        );
        let turn = self
            .assistant
            .chats
            .get(&self.slug)
            .and_then(|c| c.turns.get(index))
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("Reply unavailable"))?;
        anyhow::ensure!(!turn.applied, "Already applied");
        anyhow::ensure!(
            !self.assistant.chats[&self.slug].running,
            "Wait for Callie to finish"
        );
        let proposal = crate::app_context::proposal(&turn.answer)?
            .ok_or_else(|| anyhow::anyhow!("No proposed edits"))?;
        let operations =
            crate::app_context::review(&turn.context, &self.app_context(cx), &proposal)?;
        if proposal["navigate"].is_string() {
            let result = self
                .renderer
                .update(cx, |r, cx| r.interact(&operations[0], window, cx));
            anyhow::ensure!(
                result.get("error").is_none(),
                "{}",
                result["error"].as_str().unwrap_or("Unable to navigate")
            );
        } else {
            self.renderer
                .update(cx, |r, cx| r.apply_draft_changes(&operations, window, cx))
                .map_err(anyhow::Error::msg)?;
        }
        self.assistant.chats.get_mut(&self.slug).unwrap().turns[index].applied = true;
        self.persist(cx);
        self.notice = "Callie’s suggestion is in your draft. Review it before submitting.".into();
        self.failed = false;
        cx.notify();
        Ok(())
    }
    fn assistant_probe(&self, cx: &App) -> Value {
        let chat = self.assistant.chats.get(&self.slug);
        #[cfg(any(target_os = "android", target_os = "ios"))]
        let audio_host = crate::host::audio_status();
        #[cfg(not(any(target_os = "android", target_os = "ios")))]
        let audio_host = Value::Null;
        json!({"open":self.assistant.open,"voice":format!("{:?}",self.assistant.voice.phase),"audio_host":audio_host,"stt":self.assistant.voice_settings.as_ref().is_some_and(|s|s.stt),"tts":self.assistant.voice_settings.as_ref().is_some_and(|s|s.tts),"draft":chat.map(|c|&c.draft),"context":self.app_context(cx),"session":chat.map(|c|&c.session),"running":chat.is_some_and(|c|c.running),"loading":chat.is_some_and(|c|c.loading),"error":chat.map(|c|&c.error),"turns":chat.map(|c|c.turns.iter().map(|t|json!({"id":t.id,"question":t.question,"answer":t.answer,"context":t.context,"applied":t.applied})).collect::<Vec<_>>())})
    }
    fn assistant_view(
        &mut self,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Option<AnyElement> {
        use gpui_component::{
            Disableable,
            button::{Button, ButtonVariants},
            input::InputEvent,
        };
        if self.slug.is_empty() {
            return None;
        }
        let compact = f32::from(window.viewport_size().width) < 700.;
        if !self.assistant.open {
            return Some(
                div()
                    .absolute()
                    .bottom(px(18.))
                    .right(px(18.))
                    .child(
                        div()
                            .id("ask-callie")
                            .px_3()
                            .py_2()
                            .rounded_full()
                            .bg(self.theme.card)
                            .border_1()
                            .border_color(self.theme.accent)
                            .shadow_lg()
                            .flex()
                            .items_center()
                            .gap_2()
                            .cursor_pointer()
                            .child(crate::brand::avatar(38.))
                            .child("Ask Callie")
                            .on_click(cx.listener(|s, _, _, cx| s.assistant_open(cx))),
                    )
                    .into_any_element(),
            );
        }
        if self.assistant.input.is_none() {
            let input = cx.new(|cx| {
                let state = crate::app_assistant::ComposerState::new(window, cx)
                    .placeholder("Ask about this record…");
                #[cfg(any(target_os = "android", target_os = "ios"))]
                let state = state.rows(3);
                #[cfg(not(any(target_os = "android", target_os = "ios")))]
                let state = state.multi_line(true);
                state
            });
            self.assistant.subscription =
                Some(
                    cx.subscribe_in(&input, window, |s, _, event: &InputEvent, _window, cx| {
                        match event {
                            InputEvent::PressEnter { .. } => {}
                            InputEvent::Change => {
                                let value = s
                                    .assistant
                                    .input
                                    .as_ref()
                                    .map(|i| i.read(cx).value().to_string())
                                    .unwrap_or_default();
                                s.assistant
                                    .chats
                                    .entry(s.assistant.input_slug.clone())
                                    .or_default()
                                    .draft = value;
                            }
                            #[cfg(any(target_os = "android", target_os = "ios"))]
                            InputEvent::Focus => crate::host::input_kind("longText"),
                            _ => {}
                        }
                        cx.notify();
                    }),
                );
            self.assistant.input = Some(input);
        }
        if self.assistant.input_slug != self.slug {
            self.assistant.load(self.api.clone(), &self.slug);
            self.assistant.input_slug = self.slug.clone();
            let draft = self
                .assistant
                .chats
                .entry(self.slug.clone())
                .or_default()
                .draft
                .clone();
            self.assistant
                .input
                .as_ref()
                .unwrap()
                .update(cx, |s, cx| s.set_value(draft, window, cx));
        }
        if let Some((slug, text)) = self.assistant.transcript_pending.take() {
            if slug == self.slug {
                self.assistant
                    .input
                    .as_ref()
                    .unwrap()
                    .update(cx, |s, cx| s.set_value(text, window, cx));
            }
        }
        let current = self.app_context(cx);
        let chat = self.assistant.chats.entry(self.slug.clone()).or_default();
        let running = chat.running;
        let loading = chat.loading;
        let initialized = chat.initialized;
        let error = chat.error.clone();
        let progress = chat.progress.clone();
        let retry = chat.retry.is_some();
        let turns = chat.turns.clone();
        let disabled = running || loading || !initialized || self.offline || self.busy || retry;
        let app_title = self.snapshot["title"].as_str().unwrap_or("This app");
        let record = current["records"]
            .as_array()
            .and_then(|a| a.first())
            .and_then(|r| r["label"].as_str());
        let title = record
            .map(|r| format!("{r} · {app_title}"))
            .unwrap_or_else(|| app_title.to_owned());
        let voice_phase = self.assistant.voice.phase;
        let stt = self
            .assistant
            .voice_settings
            .as_ref()
            .is_some_and(|s| s.stt);
        let tts = self
            .assistant
            .voice_settings
            .as_ref()
            .is_some_and(|s| s.tts);
        let mut body = div()
            .id("callie-conversation-scroll")
            .flex_1()
            .min_h_0()
            .overflow_y_scroll()
            .flex()
            .flex_col()
            .gap_4()
            .p_4();
        if turns.len() > 1 {
            body = body.child(
                Button::new("callie-history")
                    .ghost()
                    .label(if self.assistant.history {
                        "Hide earlier conversation".into()
                    } else {
                        format!("Earlier conversation ({})", turns.len() - 1)
                    })
                    .on_click(cx.listener(|s, _, _, cx| {
                        s.assistant.history = !s.assistant.history;
                        cx.notify();
                    })),
            );
        }
        if turns.is_empty() {
            body=body.child(div().text_color(self.theme.muted).child(if loading{"Loading your conversation…"}else{"Talk through this record, dictate your findings, or ask me to prepare a form. You review changes before submitting."}));
        }
        for (index, turn) in turns.iter().enumerate().skip(if self.assistant.history {
            0
        } else {
            turns.len().saturating_sub(1)
        }) {
            let stale = turn.context["location"] != current["location"]
                || turn.context["version"] != current["version"];
            let mut reply = div().flex().flex_col().gap_2().child(
                div()
                    .font_weight(FontWeight::SEMIBOLD)
                    .child(turn.question.clone()),
            );
            if stale {
                reply = reply.child(
                    div()
                        .text_size(px(11.))
                        .text_color(self.theme.warning)
                        .child("From another record or app version"),
                );
            }
            if let Some(link)=turn.context["record_links"].as_array().and_then(|a|a.first()) {
                let reference=link["reference"].clone();
                let title=link["title"].as_str().unwrap_or("Captured record").to_owned();
                reply=reply.child(Button::new(SharedString::from(format!("chat-record-{index}"))).ghost().label(format!("Open {title} →"))
                    .disabled(self.busy || self.retry.is_some())
                    .on_click(cx.listener(move|s,_,_,cx|{if let Err(e)=s.open_record(reference.clone(),false,cx){s.notice=e.to_string();s.failed=true;cx.notify();}})));
            }
            reply = reply.child(
                div()
                    .text_size(px(14.))
                    .child(crate::app_context::prose(&turn.answer).to_owned()),
            );
            match crate::app_context::proposal(&turn.answer) {
                Ok(Some(proposal)) => {
                    let check = crate::app_context::review(&turn.context, &current, &proposal);
                    let mut card = div()
                        .p_3()
                        .rounded_lg()
                        .bg(self.theme.accent_soft)
                        .border_1()
                        .border_color(self.theme.line)
                        .flex()
                        .flex_col()
                        .gap_2();
                    for change in proposal["changes"].as_array().into_iter().flatten() {
                        let id = change["field"].as_str().unwrap_or("");
                        let label = turn.context["fields"]
                            .as_array()
                            .and_then(|a| a.iter().find(|f| f["id"] == id))
                            .and_then(|f| f["label"].as_str())
                            .unwrap_or(id);
                        let value = change["value"]
                            .as_str()
                            .map(str::to_owned)
                            .unwrap_or_else(|| change["value"].to_string());
                        card = card
                            .child(
                                div()
                                    .text_size(px(12.))
                                    .text_color(self.theme.muted)
                                    .child(label.to_owned()),
                            )
                            .child(value);
                    }
                    let label = if turn.applied {
                        "Applied to draft"
                    } else if proposal["navigate"].is_string() {
                        "Open suggested record"
                    } else {
                        "Use these draft changes"
                    };
                    card = card.child(
                        Button::new(SharedString::from(format!("callie-apply-{index}")))
                            .label(label)
                            .disabled(turn.applied || running || self.busy || check.is_err())
                            .on_click(cx.listener(move |s, _, window, cx| {
                                if let Err(e) = s.assistant_apply(index, window, cx) {
                                    s.assistant.chats.entry(s.slug.clone()).or_default().error =
                                        e.to_string();
                                    cx.notify();
                                }
                            })),
                    );
                    if !turn.applied {
                        if let Err(e) = check {
                            card = card.child(
                                div()
                                    .text_size(px(12.))
                                    .text_color(self.theme.warning)
                                    .child(e.to_string()),
                            );
                        }
                    }
                    reply = reply.child(card);
                }
                Err(e) if !running => {
                    reply = reply.child(div().text_color(self.theme.warning).child(format!(
                        "Suggestion couldn’t be read: {e}. Ask Callie to try again."
                    )))
                }
                _ => {}
            }
            if tts && !turn.id.is_empty() {
                reply = reply.child(
                    Button::new(SharedString::from(format!("callie-read-{index}")))
                        .ghost()
                        .label("Read aloud")
                        .on_click(cx.listener(move |s, _, _, cx| s.assistant_read(index, cx))),
                );
            }
            body = body.child(reply);
        }
        #[cfg(any(target_os = "android", target_os = "ios"))]
        let composer = gpui_component::input::Textarea::new(self.assistant.input.as_ref().unwrap())
            .disabled(disabled)
            .h(px(76.))
            .into_any_element();
        #[cfg(not(any(target_os = "android", target_os = "ios")))]
        let composer = gpui_component::input::Input::new(self.assistant.input.as_ref().unwrap())
            .disabled(disabled)
            .h(px(76.))
            .into_any_element();
        let height = (f32::from(window.viewport_size().height) * if compact { 0.82 } else { 0.78 })
            .min(720.);
        let panel = div()
            .id("callie-app-conversation")
            .absolute()
            .bottom_0()
            .right_0()
            .w(if compact {
                window.viewport_size().width
            } else {
                px(460.)
            })
            .max_w_full()
            .h(px(height))
            .max_h_full()
            .bg(self.theme.panel)
            .border_1()
            .border_color(self.theme.line)
            .rounded_t_xl()
            .shadow_lg()
            .flex()
            .flex_col()
            .overflow_hidden()
            .child(
                div()
                    .p_3()
                    .flex()
                    .items_center()
                    .gap_2()
                    .border_b_1()
                    .border_color(self.theme.line)
                    .child(crate::brand::avatar(36.))
                    .child(
                        div()
                            .flex_1()
                            .min_w_0()
                            .flex()
                            .flex_col()
                            .child(crate::brand::wordmark(16.))
                            .child(
                                div()
                                    .text_size(px(11.))
                                    .text_color(self.theme.muted)
                                    .child(title),
                            ),
                    )
                    .child(Button::new("close-callie").ghost().label("Close").on_click(
                        cx.listener(|s, _, _, cx| {
                            s.assistant.open = false;
                            cx.notify();
                        }),
                    )),
            )
            .child(body)
            .child(Button::new("callie-request-mode").ghost()
                .label(if self.assistant.improvement {"Improvement request · switch to conversation"} else {"Suggest an app improvement"})
                .disabled(disabled).on_click(cx.listener(|s,_,_,cx|{s.assistant.improvement=!s.assistant.improvement;cx.notify();})))
            .when(self.assistant.improvement, |s|s.child(div().px_4().text_size(px(12.)).text_color(self.theme.muted)
                .child("Send shares this request and current screen context with the app owner. Your report stays a draft.")))
            .when(voice_phase != crate::voice::Phase::Idle, |s| {
                s.child(
                    div()
                        .px_4()
                        .flex()
                        .items_center()
                        .justify_between()
                        .child(format!(
                            "{}",
                            match voice_phase {
                                crate::voice::Phase::Recording => "Listening…",
                                crate::voice::Phase::Transcribing => "Transcribing…",
                                crate::voice::Phase::Preparing => "Preparing speech…",
                                _ => "Speaking…",
                            }
                        ))
                        .child(
                            Button::new("stop-callie-audio")
                                .ghost()
                                .label("Stop")
                                .on_click(cx.listener(|s, _, _, cx| {
                                    s.assistant.voice.cancel();
                                    cx.notify();
                                })),
                        ),
                )
            })
            .when(running, |s| {
                s.child(
                    Button::new("stop-callie-reply")
                        .ghost()
                        .label("Stop reply")
                        .on_click(cx.listener(|s, _, _, cx| {
                            s.assistant.stop(s.api.clone(), &s.slug);
                            cx.notify();
                        })),
                )
            })
            .when(running, |s| {
                s.child(
                    div()
                        .px_4()
                        .py_2()
                        .text_color(self.theme.muted)
                        .child(progress),
                )
            })
            .when(!error.is_empty(), |s| {
                s.child(
                    div()
                        .px_4()
                        .py_2()
                        .text_color(self.theme.warning)
                        .child(error),
                )
            })
            .when(retry && !running, |s| {
                s.child(
                    Button::new("retry-callie")
                        .label("Reconnect to reply")
                        .disabled(self.offline)
                        .on_click(cx.listener(|s, _, w, cx| s.assistant_send(true, w, cx))),
                )
            })
            .when(!initialized && !loading, |s| {
                s.child(
                    Button::new("reload-callie")
                        .label("Retry conversation")
                        .on_click(cx.listener(|s, _, _, cx| {
                            s.assistant.load(s.api.clone(), &s.slug);
                            cx.notify();
                        })),
                )
            })
            .child(
                div()
                    .p_3()
                    .border_t_1()
                    .border_color(self.theme.line)
                    .flex()
                    .flex_col()
                    .gap_2()
                    .child(composer)
                    .child(
                        div()
                            .flex()
                            .items_center()
                            .justify_between()
                            .gap_2()
                            .child(div().text_size(px(11.)).text_color(self.theme.muted).child(
                                if self.offline {
                                    "Reconnect to talk with Callie"
                                } else {
                                    "Uses the record shown when you send"
                                },
                            ))
                            .when(stt, |s| {
                                s.child(
                                    Button::new("dictate-callie")
                                        .ghost()
                                        .label(if voice_phase == crate::voice::Phase::Recording {
                                            "Done"
                                        } else {
                                            "Dictate"
                                        })
                                        .disabled(self.offline || running)
                                        .on_click(
                                            cx.listener(|s, _, _, cx| s.assistant_dictate(cx)),
                                        ),
                                )
                            })
                            .child(
                                Button::new("send-callie")
                                    .primary()
                                    .label(if self.assistant.improvement {"Send request"} else {"Send"})
                                    .disabled(disabled)
                                    .on_click(
                                        cx.listener(|s, _, w, cx| s.assistant_send(false, w, cx)),
                                    ),
                            ),
                    ),
            );
        Some(panel.into_any_element())
    }
}
