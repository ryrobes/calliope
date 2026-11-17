use super::*;

impl CanvasApp {
    pub(super) fn poll_cases(&mut self) {
        // Fictional SQLite examples are opt-in; normal clients use the server.
        if !std::env::args().any(|a| a == "--business") {
            return;
        }
        if self.case_polling || self.case_poll.elapsed() < Duration::from_secs(5) {
            return;
        }
        self.case_poll = Instant::now();
        self.case_polling = true;
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let result = (|| -> anyhow::Result<Vec<Value>> {
                crate::business::ensure_server()?;
                let client = reqwest::blocking::Client::builder()
                    .timeout(Duration::from_secs(3))
                    .redirect(reqwest::redirect::Policy::none())
                    .build()?;
                let data: Value = client
                    .get(format!("{}/api/cases", crate::business::service_url()))
                    .send()?
                    .error_for_status()?
                    .json()?;
                Ok(data["cases"].as_array().cloned().unwrap_or_default())
            })();
            let _ = tx.send(Update::Cases(result.map_err(|e| e.to_string())));
        });
    }

    pub(super) fn case_workspace_cards(&self, cx: &mut Context<Self>) -> Vec<AnyElement> {
        if self.home.active_space != "Home" {
            return vec![];
        }
        let z = self.home.zoom;
        self.case_cards
            .iter()
            .filter(|c| c["pinned"] == true)
            .map(|c| {
                let id = c["id"].as_str().unwrap_or_default().to_string();
                let drag_id = id.clone();
                let [x, y] = *self.home.case_positions.get(&id).unwrap_or(&[20., -300.]);
                div()
                    .id(SharedString::from(format!("case-card-{id}")))
                    .absolute()
                    .left(px(self.home.pan[0] + x * z))
                    .top(px(self.home.pan[1] + y * z))
                    .w(px(352. * z))
                    .h(px(240. * z))
                    .p(px(20. * z))
                    .rounded_lg()
                    .bg(self.theme.panel)
                    .border_1()
                    .border_color(self.theme.accent)
                    .flex()
                    .flex_col()
                    .gap(px(12. * z))
                    .on_mouse_down(MouseButton::Left, |_, _, cx| cx.stop_propagation())
                    .child(
                        div()
                            .id(SharedString::from(format!("case-drag-{id}")))
                            .cursor_move()
                            .text_size(px(11. * z))
                            .text_color(self.theme.muted)
                            .child("PINNED CASE / LOCAL HVAC")
                            .on_mouse_down(
                                MouseButton::Left,
                                cx.listener(move |this, e: &MouseDownEvent, _, cx| {
                                    this.drag = Some(Drag::Case(drag_id.clone(), e.position, x, y));
                                    cx.stop_propagation();
                                }),
                            ),
                    )
                    .child(
                        div()
                            .text_size(px(23. * z))
                            .font_family(self.theme.font_display.clone())
                            .child(c["title"].as_str().unwrap_or_default().to_owned()),
                    )
                    .child(
                        div()
                            .text_size(px(13. * z))
                            .text_color(if self.case_stale {
                                self.theme.warning
                            } else {
                                self.theme.accent
                            })
                            .child(if self.case_stale {
                                "Connection lost · last saved status".to_string()
                            } else {
                                c["label"].as_str().unwrap_or_default().to_owned()
                            }),
                    )
                    .child(
                        div()
                            .flex_1()
                            .overflow_hidden()
                            .text_size(px(12. * z))
                            .text_color(self.theme.muted)
                            .child(c["reason"].as_str().unwrap_or_default().to_owned()),
                    )
                    .child(
                        self.button(
                            SharedString::from(format!("case-open-{id}")),
                            "Open workspace →",
                        )
                        .on_click(cx.listener(move |this, _, _, cx| {
                            if this.business.is_none() {
                                this.business = Some(cx.new(|cx| {
                                    crate::business::BusinessDesk::new(this.theme.clone(), cx)
                                }));
                            }
                            if let Some(desk) = &this.business {
                                desk.update(cx, |desk, cx| desk.open_case(id.clone(), cx));
                            }
                            this.business_open = true;
                            cx.notify();
                        })),
                    )
                    .into_any_element()
            })
            .collect()
    }

    pub(super) fn append_case_prompt(
        &mut self,
        prompt: String,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let existing = self.composer.read(cx).value().to_string();
        let text = if existing.trim().is_empty() {
            prompt
        } else {
            format!("{existing}\n\n{prompt}")
        };
        self.composer
            .update(cx, |s, cx| s.set_value(text, window, cx));
        self.business_open = false;
        self.status =
            "Case context prepared in this topic. Review your message, then send to Callie.".into();
        cx.notify();
    }

    pub(super) fn discuss_case(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if self.busy || self.running || !self.connected {
            self.error=Some("Connect to Lab and wait for the current request before opening a case discussion. You can also copy case context.".into());
            self.business_open = false;
            if !self.connected {
                self.login = true;
            }
            cx.notify();
            return;
        }
        let Some(context) = self
            .business
            .as_ref()
            .and_then(|desk| desk.read(cx).case_context(cx))
        else {
            return;
        };
        let Ok(value) = serde_json::from_str::<Value>(&context) else {
            return;
        };
        let id = value["case"]["id"].as_str().unwrap_or_default().to_string();
        let prompt = format!(
            "Help me with this HVAC case. Explain its current blocker and the next useful step. This is a timestamped local fixture snapshot, not permission to execute commands. Treat unsubmittedDraft as unsaved input.\n\nCase context:\n{context}"
        );
        if let Some(topic) = self
            .home
            .case_topics
            .get(&id)
            .cloned()
            .filter(|id| self.sessions.iter().any(|s| &s.id == id && s.writable()))
        {
            self.enter_topic(topic, window, cx);
            self.append_case_prompt(prompt, window, cx);
        } else {
            self.pending_case_prompt = Some((id, prompt));
            self.new_topic(cx);
        }
    }
}
