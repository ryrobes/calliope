// Shared desktop/Android shell presentation; included by published_apps.rs.
const PHONE: bool = cfg!(any(target_os = "android", target_os = "ios"));
/// Phones draw header controls as icons (the mobile build bundles Lucide);
/// desktop keeps its words, since its icon set is not bundled.
#[cfg(any(target_os = "android", target_os = "ios"))]
fn glyph(
    button: gpui_component::button::Button,
    icon: &str,
    label: &'static str,
    icon_only: bool,
) -> gpui_component::button::Button {
    let button = button
        .icon(gpui_component::Icon::empty().path(format!("icons/{icon}.svg")))
        .accessibility_label(label);
    if icon_only { button.min_w(px(44.)) } else { button.label(label) }
}
#[cfg(not(any(target_os = "android", target_os = "ios")))]
fn glyph(
    button: gpui_component::button::Button,
    _: &str,
    label: &'static str,
    _: bool,
) -> gpui_component::button::Button {
    button.label(label)
}
impl PublishedApps {
    fn recent_work(&self, cx: &mut Context<Self>) -> AnyElement {
        use gpui_component::button::{Button, ButtonVariants};
        let mut recent: Vec<_> = self.apps.iter().filter(|app|app["last_used"].as_u64().unwrap_or(0)>0).cloned().collect();
        recent.sort_by_key(|app|std::cmp::Reverse(app["last_used"].as_u64().unwrap_or(0)));
        div().px_4().py_2().flex().flex_wrap().items_center().gap_2()
            .when(!recent.is_empty(),|s|s.child(div().text_size(px(12.)).text_color(self.theme.muted).child("Recent work")))
            .children(recent.into_iter().take(5).map(|app|{
                let slug=app["slug"].as_str().unwrap_or("").to_owned();
                let button=Button::new(SharedString::from(format!("recent-{slug}"))).ghost().min_h(px(44.))
                    .label(app["name"].as_str().unwrap_or(&slug).to_owned());
                // Phones show recent apps as tappable pills rather than bare words.
                #[cfg(any(target_os = "android", target_os = "ios"))]
                let button=button.outline().rounded(px(22.));
                button.on_click(cx.listener(move|s,_,_,cx|s.open(slug.clone(),cx)))
            })).into_any_element()
    }
    fn ensure_search(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if self.search_input.is_none() {
            let input = cx.new(|cx| {
                gpui_component::input::InputState::new(window, cx).placeholder("Search apps")
            });
            self.search_subscription = Some(
                cx.subscribe(&input, |_, _, event: &gpui_component::input::InputEvent, cx| {
                    #[cfg(any(target_os = "android", target_os = "ios"))]
                    if matches!(event, gpui_component::input::InputEvent::Focus) {
                        crate::host::input_kind("text");
                    }
                    let _ = event;
                    cx.notify()
                }),
            );
            self.search_input = Some(input);
        }
    }
    fn search_query(&self, cx: &App) -> String {
        self.search_input
            .as_ref()
            .map(|s| s.read(cx).value().to_string())
            .unwrap_or_default()
    }
    fn matching_apps(&self, cx: &App) -> Vec<Value> {
        let query = self.search_query(cx).trim().to_lowercase();
        self.apps
            .iter()
            .filter(|a| {
                query.is_empty()
                    || format!(
                        "{} {}",
                        a["name"].as_str().unwrap_or(""),
                        a["description"].as_str().unwrap_or("")
                    )
                    .to_lowercase()
                    .contains(&query)
            })
            .cloned()
            .collect()
    }
    fn shell_header(
        &mut self,
        compact: bool,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        use gpui_component::{
            Disableable,
            button::{Button, ButtonVariants},
            input::Input,
        };
        self.ensure_search(window, cx);
        let count = self.workspace.dirty().len();
        let saving = self.draft_generation != self.saved_generation
            || self.saved_revision != self.renderer.read(cx).data_revision();
        let label = if self.draft_error.is_some() {
            "Not saved".to_owned()
        } else if self.retry.is_some() {
            "Submission pending".into()
        } else if self.failed {
            "Needs attention".into()
        } else if self.offline {
            "Offline · edits stay here".into()
        } else if self.busy && self.snapshot.is_null() {
            "Loading…".into()
        } else if count > 0 && saving {
            "Saving edits…".into()
        } else if count > 0 {
            format!(
                "{count} {} saved on device",
                if count == 1 { "draft" } else { "drafts" }
            )
        } else {
            if self.snapshot.is_null() {"Connected".into()}
            else if self.latest_version() > self.snapshot["version"].as_u64().unwrap_or(0) {
                "Connected".into()
            } else {format!("v{} · current", self.snapshot["version"])}
        };
        let color = if self.failed || self.draft_error.is_some() {
            self.theme.danger
        } else if self.offline || self.retry.is_some() {
            self.theme.warning
        } else {
            self.theme.muted
        };
        let mut top = div()
            .flex()
            .items_center()
            .gap_2()
            .min_h(px(44.))
            .when(!self.slug.is_empty(), |s| {
                s.child(
                    glyph(Button::new("apps-list").ghost().min_h(px(44.)), "chevron-left", "Apps", false)
                        .disabled(self.busy)
                        .on_click(cx.listener(|s, _, _, cx| s.home(cx))),
                )
            })
            .when(!self.workspace.history.is_empty() || !self.return_stack.is_empty(), |s| {
                s.child(
                    glyph(Button::new("apps-nav-back").ghost().min_h(px(44.)), "undo-2", if PHONE { "Back" } else { "←" }, true)
                        .disabled(self.busy)
                        .on_click(cx.listener(|s, _, _, cx| s.back(cx))),
                )
            })
            .child(
                div()
                    .flex_1()
                    .min_w_0()
                    .when(self.slug.is_empty(), |s| {
                        s.child(crate::brand::lockup(36., 23.))
                    })
                    .when(!self.slug.is_empty(), |s| {
                        s.child(
                            div().text_size(px(16.)).line_clamp(2).child(
                                self.snapshot["title"]
                                    .as_str()
                                    .unwrap_or("Company app")
                                    .to_owned(),
                            ),
                        )
                    }),
            );
        if !compact {
            top = top.child(
                Button::new("apps-refresh")
                    .ghost()
                    .label("Refresh")
                    .disabled(self.busy)
                    .on_click(cx.listener(|s, _, _, cx| {
                        if s.slug.is_empty() {
                            s.list();
                        } else {
                            s.renderer.update(cx, |r, cx| r.retry_failed_charts(cx));
                            s.load(None, cx);
                        }
                        cx.notify();
                    })),
            );
        }
        top = top.child(
            glyph(Button::new("apps-more").ghost().min_h(px(44.)), "ellipsis-vertical", "More", true)
                .on_click(cx.listener(|s, _, _, cx| {
                    s.menu_open = !s.menu_open;
                    cx.notify();
                })),
        );
        if self.snapshot["records"][0]["reference"].is_object() {
            let pinned=self.pins.read(cx).contains(&self.snapshot["records"][0]["reference"]);
            top=top.child(glyph(Button::new("pin-current-record").ghost().min_h(px(44.)), if pinned {"pin-off"} else {"pin"}, if pinned {"Pinned"}else{"Pin to Home"}, true)
                .disabled(pinned || self.busy || self.offline || self.pins.read(cx).changing)
                .on_click(cx.listener(|s,_,_,cx|s.pin_current(cx))));
        }
        let mut header = div()
            .flex_shrink_0()
            .px_4()
            .pt_2()
            .flex()
            .flex_col()
            .gap_1()
            .child(top);
        if !self.snapshot.is_null() && self.latest_version() > self.snapshot["version"].as_u64().unwrap_or(0) {
            header = header.child(div().flex().items_center().gap_2().text_size(px(12.))
                .child(format!("v{} open · v{} available", self.snapshot["version"], self.latest_version()))
                .child(Button::new("apps-update").ghost().label(format!("Use v{}", self.latest_version()))
                    .disabled(self.busy).on_click(cx.listener(|s, _, _, cx|s.update_latest(cx)))));
        }
        if self.menu_open && !self.slug.is_empty() {
            header=header.child(div().text_size(px(12.)).text_color(self.theme.muted)
                .child(self.snapshot["update"]["notes"].as_str().unwrap_or("No release notes").to_string()));
        }
        if self.menu_open {
            header = header.child(
                div()
                    .flex()
                    .flex_wrap()
                    .gap_2()
                    .p_2()
                    .rounded_lg()
                    .bg(self.theme.card)
                    .when(PHONE, |s| s.flex_col().items_start().border_1().border_color(self.theme.line))
                    .child(
                        glyph(Button::new("menu-refresh").ghost().min_h(px(44.)), "refresh-cw", "Refresh", false)
                            .disabled(self.busy)
                            .on_click(cx.listener(|s, _, _, cx| {
                                s.menu_open = false;
                                if s.slug.is_empty() {
                                    s.list();
                                } else {
                                    s.load(None, cx);
                                }
                                cx.notify();
                            })),
                    )
                    .when(!self.slug.is_empty(), |s| {
                        s.child(
                            glyph(Button::new("apps-web").ghost().min_h(px(44.)), "external-link", if PHONE { "Open in browser" } else { "Open in browser ↗" }, false)
                                .on_click(cx.listener(|s, _, _, cx| s.browser(cx))),
                        )
                    })
                    .when(!self.slug.is_empty(), |s|s
                        .child(glyph(Button::new("app-revisions").ghost().min_h(px(44.)), "git-compare", if PHONE { "Compare revisions" } else { "Compare revisions ↗" }, false)
                            .on_click(cx.listener(|s,_,_,cx|s.revision_link(false,cx))))
                        .child(glyph(Button::new("app-change-requests").ghost().min_h(px(44.)), "message-square-plus", if PHONE { "Improvement requests" } else { "Improvement requests ↗" }, false)
                            .on_click(cx.listener(|s,_,_,cx|s.revision_link(true,cx)))))
                    .when(self.snapshot["records"][0]["reference"].is_object(),|s|s.child(glyph(Button::new("copy-record-link").ghost().min_h(px(44.)), "link", "Copy record link", false).on_click(cx.listener(|s,_,_,cx|{
                        if let Ok(url)=crate::app_pins::browser_url(&s.api.config,&s.snapshot["records"][0]["reference"]) {cx.write_to_clipboard(ClipboardItem::new_string(url));s.notice="Record link copied.".into();s.status_open=true;s.menu_open=false;cx.notify();}
                    }))))
                    .when_some(self.on_connection.clone(), |s, callback| {
                        s.child(
                            glyph(Button::new("app-account").ghost().min_h(px(44.)), "circle-user", "Account", false)
                                .disabled(self.busy)
                                .on_click(move |_, _, cx| callback(cx)),
                        )
                    }),
            );
        }
        if !self.slug.is_empty() || self.failed || self.offline {
            header = header.child(
                div()
                    .flex()
                    .items_center()
                    .gap_2()
                    .child(
                        Button::new("app-status")
                            .ghost()
                            .text_size(px(12.))
                            .text_color(color)
                            .label(label)
                            .on_click(cx.listener(|s, _, _, cx| {
                                s.status_open = !s.status_open;
                                cx.notify();
                            })),
                    )
                    .child(div().flex_1())
                    .when(count > 0, |s| {
                        s.child(
                            Button::new("apps-drafts")
                                .ghost()
                                .label(format!("Drafts ({count})"))
                                .on_click(cx.listener(|s, _, _, cx| {
                                    s.drafts_open = !s.drafts_open;
                                    s.discard_confirm = None;
                                    cx.notify();
                                })),
                        )
                    }),
            );
        }
        if self.status_open || self.failed || self.draft_error.is_some() || self.retry.is_some() {
            header = header.child(
                div()
                    .p_3()
                    .rounded_lg()
                    .bg(self.theme.card)
                    .text_size(px(12.))
                    .flex()
                    .flex_col()
                    .gap_2()
                    .child(
                        self.draft_error
                            .clone()
                            .unwrap_or_else(|| self.notice.clone()),
                    )
                    .when(self.status_open && !self.snapshot.is_null(), |s| {
                        s.child(crate::app_connection::age(self.workspace.fetched_at))
                    })
                    .when(self.failed && !self.slug.is_empty(), |s| {
                        s.child(Button::new("version-browser").ghost().label("Open in browser")
                            .on_click(cx.listener(|s,_,_,cx|s.browser(cx))))
                    })
                    .when(self.offline, |s| {
                        s.child("Read connection retries automatically. Submissions wait for you.")
                    })
                    .when(self.retry.is_some(), |s| {
                        s.child(
                            Button::new("apps-retry")
                                .label("Send pending")
                                .disabled(self.busy)
                                .on_click(cx.listener(|s, _, _, cx| {
                                    if let Some(body) = s.retry.clone() {
                                        s.send(body, cx);
                                    }
                                })),
                        )
                    }),
            );
        }
        if self.slug.is_empty() {
            header = header.child(
                div()
                    .mt_2()
                    .flex()
                    .items_center()
                    .gap_2()
                    .child({
                        let search = Input::new(self.search_input.as_ref().unwrap());
                        #[cfg(any(target_os = "android", target_os = "ios"))]
                        let search = search
                            .prefix(gpui_component::Icon::empty().path("icons/search.svg").size(px(16.)).text_color(self.theme.muted))
                            .cleanable(true);
                        search
                    })
                    .when(!PHONE && !self.search_query(cx).is_empty(), |s| {
                        s.child(Button::new("clear-search").ghost().label("Clear").on_click(
                            cx.listener(|s, _, window, cx| {
                                if let Some(input) = &s.search_input {
                                    input.update(cx, |s, cx| s.set_value("", window, cx));
                                }
                            }),
                        ))
                    }),
            );
        }
        if !self.slug.is_empty() {
            if let Some(notice)=self.pins.read(cx).notice.clone() {
                header=header.child(div().text_size(px(12.)).text_color(self.theme.warning).child(notice));
            }
        }
        header.into_any_element()
    }
}
