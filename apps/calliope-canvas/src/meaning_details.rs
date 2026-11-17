// Selected-object workspace. Expensive metadata is fetched once per selection
// and graph refresh, off the UI thread; late replies cannot replace newer work.
impl Meaning {
    fn ensure_inspection(&mut self) {
        let node = self.selected_node().filter(|_| !self.area.is_empty());
        let key = node
            .as_ref()
            .map(|n| {
                format!(
                    "{}|{}|{}",
                    self.area,
                    text(n, "id"),
                    text(&self.graph, "as_of")
                )
            })
            .unwrap_or_default();
        if key == self.inspection_key {
            return;
        }
        self.inspection_key = key;
        self.inspection_request += 1;
        self.inspection = Value::Null;
        self.inspection_error = None;
        self.inspection_busy = false;
        let Some(node) = node else {
            return;
        };
        // Area/idea/breakdown/artifact details already travel with the graph.
        if !matches!(text(&node, "kind").as_str(), "metric" | "relation" | "cube") {
            return;
        }
        self.inspection_busy = true;
        let (api, tx, request) = (self.api.clone(), self.tx.clone(), self.inspection_request);
        std::thread::spawn(move || {
            let _ = tx.send(Update::Inspection(
                request,
                api.post("/api/calliope/meaning/inspect", &node["reference"]),
            ));
        });
    }
    fn detail_heading(&self, title: impl Into<SharedString>) -> Div {
        div()
            .text_size(px(10.))
            .text_color(self.theme.accent)
            .mb_2()
            .child(title.into())
    }
    fn detail_block(&self, title: &str, value: impl Into<SharedString>) -> Div {
        div()
            .flex_shrink_0()
            .mb_3()
            .child(self.detail_heading(title.to_uppercase()))
            .child(
                div()
                    .text_size(px(12.))
                    .line_height(relative(1.45))
                    .child(value.into()),
            )
    }
    fn detail_list(&self, title: &str, values: Vec<Value>) -> Div {
        div()
            .flex_shrink_0()
            .mb_3()
            .child(self.detail_heading(title.to_uppercase()))
            .when(values.is_empty(), |s| {
                s.child(div().text_color(self.theme.muted).child("None recorded."))
            })
            .children(values.into_iter().map(|v| {
                div()
                    .mb_2()
                    .pl_3()
                    .border_l_2()
                    .border_color(self.theme.line)
                    .text_size(px(12.))
                    .line_height(relative(1.4))
                    .child(detail_text(&v))
            }))
    }
    fn detail_link(&self, key: String, label: String, cx: &mut Context<Self>) -> AnyElement {
        let loaded = items(&self.graph, "nodes").iter().any(|n| n["id"] == key);
        // Only navigate identities in this authorized loaded neighborhood.
        // Missing dependencies remain readable references, not guessed edges.
        div()
            .id(SharedString::from(format!("detail-link-{key}")))
            .min_w_0()
            .px_2()
            .py_1()
            .rounded_md()
            .text_size(px(12.))
            .text_color(if loaded {
                self.theme.accent
            } else {
                self.theme.muted
            })
            .child(label)
            .when(loaded, |s| {
                s.cursor_pointer()
                    .hover(|s| s.bg(self.theme.line))
                    .on_click(cx.listener(move |s, _, _, cx| s.show_node(key.clone(), cx)))
            })
            .into_any_element()
    }
    fn metric_details(&self, n: &Value, d: &Value, cx: &mut Context<Self>) -> AnyElement {
        let observations = items(d, "observations");
        let samples = observation_points(&observations);
        let preview = n.get("preview");
        let mut reading = div()
            .flex_1()
            .min_w_0()
            .flex()
            .flex_col()
            .gap_2()
            .child(self.detail_heading("SAVED READING"));
        if let Some(p) = preview {
            reading = reading
                .child(
                    div()
                        .text_size(px(28.))
                        .text_color(self.theme.success)
                        .child(detail_text(&p["value"])),
                )
                .child(
                    div()
                        .text_size(px(10.))
                        .text_color(self.theme.muted)
                        .child(format!(
                            "{} · {}",
                            text(p, "label"),
                            detail_date(&text(p, "at"))
                        )),
                );
        } else {
            reading = reading.child(
                div()
                    .text_color(self.theme.muted)
                    .child("No saved reading available."),
            );
        }
        if samples.len() >= 2 {
            let lo = samples.iter().map(|p| p[1]).fold(f64::INFINITY, f64::min);
            let hi = samples
                .iter()
                .map(|p| p[1])
                .fold(f64::NEG_INFINITY, f64::max);
            reading = reading
                .child(
                    div()
                        .text_size(px(10.))
                        .text_color(self.theme.muted)
                        .child(format!(
                            "{} saved observations · range {} – {}",
                            samples.len(),
                            number(&json!(lo)),
                            number(&json!(hi))
                        )),
                )
                .child(observation_plot(
                    samples,
                    self.theme.success,
                    self.theme.line,
                ))
                .child(
                    div()
                        .flex()
                        .justify_between()
                        .text_size(px(10.))
                        .text_color(self.theme.muted)
                        .children(
                            [observations.last(), observations.first()]
                                .into_iter()
                                .flatten()
                                .map(|o| {
                                    let date = text(o, "observed_at");
                                    div().child(short(&detail_date(&date), 16))
                                }),
                        ),
                )
                .child(div().text_size(px(10.)).text_color(self.theme.muted).child(
                    "Daily saved snapshots at observation time, not a recalculation of old dates.",
                ));
        } else if !self.inspection_busy {
            reading = reading.child(div().text_size(px(11.)).text_color(self.theme.muted).child(
                if d["observations_restricted"] == true {
                    "Saved aggregate history is unavailable for this database role."
                } else {
                    "Not enough saved observations to draw a history."
                },
            ));
        }
        let mut scope = div()
            .flex_1()
            .min_w_0()
            .child(self.detail_block("Calculation scope", text(n, "subtitle")))
            .when(!text(d, "window").is_empty(), |s| {
                s.child(self.detail_block("Window", text(d, "window")))
            })
            .when(d.get("defaults").is_some(), |s| {
                s.child(self.detail_block("Definition defaults", detail_text(&d["defaults"])))
            })
            .child(self.detail_block("Formula", text(n, "formula")));
        if !observations.is_empty() {
            scope = scope.child(div().text_size(px(10.)).text_color(self.theme.muted).child(
                format!(
                    "History: definition v{} · canonical defaults · last saved value per UTC day",
                    detail_text(&n["version"])
                ),
            ));
        }
        let mut body = div()
            .flex()
            .flex_col()
            .gap_3()
            .child(
                div()
                    .flex()
                    .gap_6()
                    .flex_shrink_0()
                    .child(reading)
                    .child(scope),
            )
            .when(!text(d, "why").is_empty(), |s| {
                s.child(self.detail_block("Interpretation", text(d, "why")))
            })
            .when(!text(n, "description").is_empty(), |s| {
                s.child(self.detail_block("Definition notes", text(n, "description")))
            })
            .when(!items(n, "review").is_empty(), |s| {
                s.child(self.detail_list("Definition needs review", items(n, "review")))
            })
            .child(self.detail_heading("DECLARED SOURCES"));
        for source in items(n, "sources") {
            if let Some(source) = source.as_str() {
                body = body.child(self.detail_link(
                    format!(
                        "{}:{source}",
                        if source.starts_with("cubes.") {
                            "cube"
                        } else {
                            "relation"
                        }
                    ),
                    source.to_owned(),
                    cx,
                ));
            }
        }
        if !items(d, "grain_columns").is_empty() {
            body = body.child(self.detail_list("Grouping fields", items(d, "grain_columns")));
        }
        if !text(d, "sql").is_empty() {
            body = body.child(
                div()
                    .flex_shrink_0()
                    .p_3()
                    .rounded_lg()
                    .bg(with_alpha(self.theme.accent, 0.05))
                    .child(self.detail_heading("REGISTERED SQL · SHOWN, NOT EXECUTED"))
                    .child(
                        div()
                            .text_size(px(12.))
                            .font_family("monospace")
                            .child(text(d, "sql")),
                    ),
            );
        }
        if !observations.is_empty() {
            body = body
                .child(self.detail_heading("OBSERVATION RECEIPTS · NEWEST DATA FIRST"))
                .children(observations.into_iter().map(|o| {
                    div()
                        .flex_shrink_0()
                        .flex()
                        .gap_4()
                        .py_2()
                        .border_b_1()
                        .border_color(self.theme.line)
                        .child(
                            div()
                                .w(px(125.))
                                .text_color(self.theme.success)
                                .child(detail_text(&o["value"])),
                        )
                        .child(
                            div()
                                .flex_1()
                                .min_w_0()
                                .text_size(px(11.))
                                .child(format!(
                                    "Data as of {}",
                                    detail_date(&text(&o, "data_as_of"))
                                ))
                                .child(div().text_color(self.theme.muted).child(format!(
                                    "Saved {}",
                                    detail_date(&text(&o, "observed_at"))
                                ))),
                        )
                }));
        }
        body.into_any_element()
    }
    fn source_details(&self, n: &Value, d: &Value) -> AnyElement {
        let fields = items(d, "fields");
        let mut roles: std::collections::BTreeMap<String, usize> =
            std::collections::BTreeMap::new();
        for field in &fields {
            *roles.entry(text(field, "role")).or_default() += 1;
        }
        div().flex().flex_col().gap_2()
            .child(self.detail_block("Source",format!("{}\n{}",text(n,"source"),text(n,"description"))))
            .child(div().flex().flex_wrap().gap_2().flex_shrink_0().children(roles.into_iter().map(|(role,count)|
                div().px_2().py_1().rounded_md().bg(with_alpha(self.theme.accent,0.08)).text_size(px(11.))
                    .child(format!("{count} {}",if role.is_empty(){"unclassified"}else{&role})))) )
            .child(self.detail_heading(format!("FIELDS & MEANING · {}{}",fields.len(),if d["fields_more"]==true {" shown (first 80)"}else{" recorded"})))
            .when(fields.is_empty() && !self.inspection_busy, |s|s.child(div().text_color(self.theme.muted).child("No field meaning has been recorded for this source.")))
            .children(fields.into_iter().map(|field|div().flex_shrink_0().flex().gap_4().py_2().border_b_1().border_color(self.theme.line)
                .child(div().w(relative(0.4)).min_w_0().text_size(px(12.))
                    .child(text(&field,"col_name"))
                    .child(div().text_size(px(10.)).text_color(self.theme.muted).child(text(&field,"data_type"))))
                .child(div().flex_1().min_w_0().text_size(px(11.))
                    .child(div().text_color(self.theme.accent).child(format!("{} · {}",text(&field,"role"),text(&field,"basis"))))
                    .when(!text(&field,"what").is_empty(), |s|s.child(div().mt_1().text_color(self.theme.muted).child(text(&field,"what")))))))
            .child(div().mt_2().text_size(px(10.)).text_color(self.theme.muted)
                .child(format!("Meaning metadata updated {}. Field roles are recorded interpretations; no source rows are loaded here.",detail_date(&text(n,"updated_at")))))
            .into_any_element()
    }
    fn object_details(&self, n: &Value, cx: &mut Context<Self>) -> AnyElement {
        let d = &self.inspection["detail"];
        match text(n, "kind").as_str() {
            "metric" => self.metric_details(n, d, cx),
            "relation" => self.source_details(n, d),
            "cube" => div()
                .flex()
                .flex_col()
                .gap_3()
                .child(self.detail_block("Reusable data model", text(n, "description")))
                .child(self.detail_block("Declared row grain", text(n, "grain")))
                .child(self.detail_list("Source lineage", items(n, "sources")))
                .child(self.detail_list("Dimensions", items(n, "dimensions")))
                .child(self.detail_list(
                    "Numeric fields (not proof of additivity)",
                    items(n, "measures"),
                ))
                .child(self.detail_block("Association", text(n, "association")))
                .child(self.detail_block("Registered SQL", text(d, "sql")))
                .child(self.source_details(n, d))
                .into_any_element(),
            "area" => {
                div()
                    .flex()
                    .flex_col()
                    .gap_3()
                    .child(self.detail_block("Business context", text(n, "description")))
                    .child(
                        div()
                            .flex()
                            .gap_6()
                            .flex_shrink_0()
                            .child(div().flex_1().min_w_0().child(self.detail_list(
                                "Questions this area can help answer",
                                items(n, "questions"),
                            )))
                            .child(div().flex_1().min_w_0().child(
                                self.detail_list("Gaps & open questions", items(n, "gaps")),
                            )),
                    )
                    .into_any_element()
            }
            "artifact" => {
                let mut body = div()
                    .flex()
                    .flex_col()
                    .gap_3()
                    .child(self.detail_block("Purpose", text(n, "description")))
                    .child(self.detail_block(
                        "Experience",
                        format!(
                            "{} · {}",
                            text(n, "subtitle"),
                            if n["runtime"] == "a2ui" {
                                "Opens natively in Calliope"
                            } else {
                                "Opens in your OS browser"
                            }
                        ),
                    ))
                    .child(self.detail_heading("DEPENDENCIES IN THIS AREA"));
                for dep in items(n, "dependencies") {
                    let kind = if dep["kind"] == "table" {
                        "relation"
                    } else {
                        "metric"
                    };
                    body = body.child(
                        div()
                            .flex_shrink_0()
                            .mb_2()
                            .child(self.detail_link(
                                format!("{kind}:{}", text(&dep, "object_ref")),
                                text(&dep, "object_ref"),
                                cx,
                            ))
                            .child(
                                div()
                                    .pl_2()
                                    .text_size(px(10.))
                                    .text_color(self.theme.muted)
                                    .child(format!(
                                        "{} · {}",
                                        text(&dep, "kind"),
                                        text(&dep, "source")
                                    )),
                            ),
                    );
                }
                body.child(div().text_size(px(10.)).text_color(self.theme.muted)
                    .child("Only recorded dependencies matching this area are listed; this is not a complete app manifest.")).into_any_element()
            }
            "breakdown" => {
                div()
                    .flex()
                    .flex_col()
                    .gap_3()
                    .child(self.detail_block("Analysis to explore", text(n, "description")))
                    .child(
                        div()
                            .flex()
                            .gap_6()
                            .flex_shrink_0()
                            .child(
                                div()
                                    .flex_1()
                                    .min_w_0()
                                    .child(self.detail_list("Group by", items(n, "dimensions"))),
                            )
                            .child(
                                div()
                                    .flex_1()
                                    .min_w_0()
                                    .child(self.detail_list("Measure", items(n, "measures"))),
                            ),
                    )
                    .child(self.detail_block("Caveats", text(n, "caveat")))
                    .child(div().text_color(self.theme.muted).child(
                        "Suggested analysis. Ask Callie to refine it; no calculation has run.",
                    ))
                    .into_any_element()
            }
            _ => div()
                .child(self.detail_block("Why it may help", text(n, "description")))
                .child(self.detail_block("Proposed grain", text(n, "subtitle")))
                .child(self.detail_block("Proposed formula", text(n, "formula")))
                .child(div().text_color(self.theme.muted).child(
                    "An idea, not a registered metric. Ask Callie to turn it into a definition.",
                ))
                .into_any_element(),
        }
    }
    fn relationship_details(&self, e: &Value, cx: &mut Context<Self>) -> AnyElement {
        let nodes = items(&self.graph, "nodes");
        let mut body = div()
            .flex()
            .flex_col()
            .gap_3()
            .child(
                self.button("meaning-evidence-back", "← Object details")
                    .on_click(cx.listener(|s, _, _, cx| {
                        s.selected_edge = None;
                        cx.notify();
                    })),
            )
            .child(self.detail_heading(format!(
                "{} · {}",
                text(e, "label").to_uppercase(),
                text(e, "state").to_uppercase()
            )));
        for key in ["from", "to"] {
            let id = text(e, key);
            let label = nodes
                .iter()
                .find(|n| n["id"] == id)
                .map(|n| text(n, "title"))
                .unwrap_or_else(|| id.clone());
            body = body.child(self.detail_link(id, format!("{key}: {label}"), cx));
        }
        if let Some(evidence) = e["evidence"].as_object() {
            for (key, value) in evidence {
                if !value.is_null() {
                    body =
                        body.child(self.detail_block(&key.replace('_', " "), detail_text(value)));
                }
            }
        }
        body.child(div().text_size(px(11.)).text_color(self.theme.muted)
            .child("This describes the recorded evidence for the connection. A declared dependency is not verification of a business interpretation."))
            .into_any_element()
    }
    fn detail_connections(&self, n: &Value, cx: &mut Context<Self>) -> AnyElement {
        let nodes = items(&self.graph, "nodes");
        let connections: Vec<_> = items(&self.graph, "edges")
            .into_iter()
            .filter(|e| e["from"] == n["id"] || e["to"] == n["id"])
            .collect();
        div()
            .flex()
            .flex_col()
            .flex_1()
            .min_h_0()
            .gap_2()
            .child(self.detail_heading(format!(
                "CONNECTED OBJECTS · {} IN THIS AREA",
                connections.len()
            )))
            .when(!self.hidden_neighbors().is_empty(), |s| {
                s.child(
                    self.button(
                        "meaning-expand",
                        format!("Show {} on map", self.hidden_neighbors().len()),
                    )
                    .on_click(cx.listener(|s, _, _, cx| s.expand(cx))),
                )
            })
            .child(
                div()
                    .id(SharedString::from(format!(
                        "meaning-connections-{}",
                        self.selected
                    )))
                    .flex_1()
                    .min_h_0()
                    .overflow_y_scroll()
                    .children(connections.into_iter().map(|e| {
                        let other = if e["from"] == n["id"] {
                            text(&e, "to")
                        } else {
                            text(&e, "from")
                        };
                        let title = nodes
                            .iter()
                            .find(|n| n["id"] == other)
                            .map(|n| text(n, "title"))
                            .unwrap_or_else(|| other.clone());
                        let id = text(&e, "id");
                        div()
                            .mb_2()
                            .p_2()
                            .rounded_lg()
                            .border_1()
                            .border_color(if self.selected_edge.as_ref() == Some(&id) {
                                self.theme.accent
                            } else {
                                self.theme.line
                            })
                            .child(self.detail_link(other, title, cx))
                            .child(
                                div()
                                    .flex()
                                    .justify_between()
                                    .items_center()
                                    .child(
                                        div()
                                            .text_size(px(10.))
                                            .text_color(self.theme.muted)
                                            .child(format!(
                                                "{} · {}",
                                                text(&e, "label"),
                                                text(&e, "state")
                                            )),
                                    )
                                    .child(
                                        self.button(
                                            SharedString::from(format!("evidence-{id}")),
                                            "Why?",
                                        )
                                        .text_size(px(11.))
                                        .py_1()
                                        .on_click(
                                            cx.listener(move |s, _, _, cx| {
                                                s.selected_edge = Some(id.clone());
                                                cx.notify();
                                            }),
                                        ),
                                    ),
                            )
                    })),
            )
            .into_any_element()
    }
    fn details(&self, window: &Window, cx: &mut Context<Self>) -> AnyElement {
        let Some(mut n) = self.selected_node() else {
            return div().into_any_element();
        };
        // The registry may change between graph and detail reads. Keep the
        // value/definition/version together from the authorized detail response.
        if self.inspection["node"]["reference"] == n["reference"] {
            n = self.inspection["node"].clone();
        }
        let reference = n["reference"].clone();
        let pinned = self.pins.iter().any(|p| p["reference"] == reference);
        let discussion = self.discussion(&n);
        let open = n.clone();
        let edge = self.selected_edge.as_ref().and_then(|id| {
            items(&self.graph, "edges")
                .into_iter()
                .find(|e| e["id"] == *id)
        });
        let content = if let Some(edge) = edge {
            self.relationship_details(&edge, cx)
        } else {
            self.object_details(&n, cx)
        };
        let height = detail_height(
            f32::from(window.viewport_size().height),
            self.detail_expanded,
        );
        div()
            .id("meaning-detail-panel")
            .h(px(height))
            .flex_shrink_0()
            .border_t_1()
            .border_color(self.theme.line)
            .bg(with_alpha(self.theme.panel, 0.98))
            .px_6()
            .py_3()
            .flex()
            .flex_col()
            .gap_3()
            .overflow_hidden()
            .child(
                div()
                    .flex()
                    .gap_3()
                    .items_center()
                    .flex_shrink_0()
                    .child(
                        div()
                            .flex_1()
                            .min_w_0()
                            .child(
                                div()
                                    .text_size(px(19.))
                                    .font_family(self.theme.font_display.clone())
                                    .line_clamp(1)
                                    .child(text(&n, "title")),
                            )
                            .child(
                                div()
                                    .text_size(px(10.))
                                    .text_color(self.color(&text(&n, "kind")))
                                    .child(format!(
                                        "{} · {} · {}",
                                        text(&n, "kind").to_uppercase(),
                                        text(&n, "status"),
                                        text(&n, "basis")
                                    )),
                            ),
                    )
                    .child(
                        self.button("meaning-pin", if pinned { "Pinned" } else { "Pin to Home" })
                            .on_click(cx.listener(move |s, _, _, cx| {
                                if !pinned && !s.changing {
                                    s.change_pin(
                                        json!({"operation":"pin","reference":reference}),
                                        cx,
                                    );
                                }
                            })),
                    )
                    .child(self.button("meaning-ask", "Ask Callie").on_click(
                        cx.listener(move |_, _, _, cx| cx.emit(Action::Ask(discussion.clone()))),
                    ))
                    .when(n["kind"] == "artifact", |s| {
                        s.child(
                            self.button("meaning-open", "Open →")
                                .text_color(self.theme.accent)
                                .on_click(cx.listener(move |_, _, _, cx| {
                                    cx.emit(Action::Open(open.clone()))
                                })),
                        )
                    })
                    .child(
                        self.button(
                            "meaning-detail-expand",
                            if self.detail_expanded {
                                "Collapse ↓"
                            } else {
                                "Expand details ↑"
                            },
                        )
                        .on_click(cx.listener(|s, _, _, cx| {
                            s.detail_expanded = !s.detail_expanded;
                            cx.notify();
                        })),
                    ),
            )
            .child(
                div()
                    .flex_1()
                    .min_h_0()
                    .flex()
                    .gap_6()
                    .child(
                        div()
                            .id(SharedString::from(format!(
                                "meaning-detail-body-{}-{}",
                                self.selected,
                                self.selected_edge.as_deref().unwrap_or("object")
                            )))
                            .flex_1()
                            .min_w_0()
                            .min_h_0()
                            .overflow_y_scroll()
                            .text_size(px(12.))
                            .pr_2()
                            .when(self.inspection_busy, |s| {
                                s.child(
                                    div()
                                        .mb_3()
                                        .text_color(self.theme.muted)
                                        .child("Loading recorded details…"),
                                )
                            })
                            .when_some(self.inspection_error.clone(), |s, e| {
                                s.child(
                                    div()
                                        .mb_3()
                                        .text_color(self.theme.warning)
                                        .child(format!("Details unavailable: {e}"))
                                        .child(
                                            self.button("meaning-detail-retry", "Retry details")
                                                .on_click(cx.listener(|s, _, _, cx| {
                                                    s.inspection_key.clear();
                                                    cx.notify();
                                                })),
                                        ),
                                )
                            })
                            .child(content),
                    )
                    .child(
                        div()
                            .w(px(if f32::from(window.viewport_size().width) < 1150. {
                                255.
                            } else {
                                315.
                            }))
                            .flex_shrink_0()
                            .min_h_0()
                            .border_l_1()
                            .border_color(self.theme.line)
                            .pl_4()
                            .flex()
                            .flex_col()
                            .child(self.detail_connections(&n, cx)),
                    ),
            )
            .into_any_element()
    }
}

fn detail_text(v: &Value) -> String {
    match v {
        Value::Null => "Not recorded".into(),
        Value::String(s) => s.clone(),
        Value::Object(m) if m.is_empty() => "None".into(),
        Value::Object(m) => m
            .iter()
            .map(|(k, v)| format!("{}: {}", k.replace('_', " "), detail_text(v)))
            .collect::<Vec<_>>()
            .join("\n"),
        Value::Array(a) => a.iter().map(detail_text).collect::<Vec<_>>().join(" · "),
        _ => v.to_string(),
    }
}
fn detail_date(s: &str) -> String {
    if s.is_empty() {
        return "not recorded".into();
    }
    let s = s.replace('T', " ");
    if let Some(dot) = s.find('.') {
        let zone = s[dot..]
            .find(['+', '-', 'Z'])
            .map(|i| &s[dot + i..])
            .unwrap_or("");
        format!("{}{zone}", &s[..dot])
    } else {
        s
    }
}
fn detail_height(window_height: f32, expanded: bool) -> f32 {
    if expanded {
        (window_height * 0.61).min((window_height - 240.).max(180.))
    } else {
        (window_height * 0.34)
            .clamp(235., 360.)
            .min((window_height - 240.).max(180.))
    }
}
fn observation_points(observations: &[Value]) -> Vec<[f64; 2]> {
    let mut points: Vec<_> = observations
        .iter()
        .filter_map(|o| {
            let time = o["time"]
                .as_f64()
                .or_else(|| o["time"].as_str()?.parse().ok())?;
            let value = o["value"].as_f64()?;
            (time.is_finite() && value.is_finite()).then_some([time, value])
        })
        .collect();
    points.sort_by(|a, b| a[0].total_cmp(&b[0]));
    points
}
fn observation_plot(points: Vec<[f64; 2]>, color: Rgba, line: Rgba) -> AnyElement {
    canvas(
        |_, _, _| (),
        move |bounds, _, window, _| {
            let xmin = points.first().map(|p| p[0]).unwrap_or(0.);
            let xmax = points.last().map(|p| p[0]).unwrap_or(0.);
            let ymin = points.iter().map(|p| p[1]).fold(f64::INFINITY, f64::min);
            let ymax = points
                .iter()
                .map(|p| p[1])
                .fold(f64::NEG_INFINITY, f64::max);
            let mut grid = PathBuilder::stroke(px(1.));
            for f in [0.1, 0.5, 0.9] {
                grid.move_to(bounds.origin + point(px(0.), bounds.size.height * f));
                grid.line_to(bounds.origin + point(bounds.size.width, bounds.size.height * f));
            }
            if let Ok(path) = grid.build() {
                window.paint_path(path, line);
            }
            let mut path = PathBuilder::stroke(px(2.));
            for (i, p) in points.iter().enumerate() {
                let x = if xmax == xmin {
                    0.5
                } else {
                    ((p[0] - xmin) / (xmax - xmin)) as f32
                };
                let y = if ymax == ymin {
                    0.5
                } else {
                    0.9 - 0.8 * ((p[1] - ymin) / (ymax - ymin)) as f32
                };
                let point = bounds.origin
                    + point(
                        px(4.) + (bounds.size.width - px(8.)) * x,
                        bounds.size.height * y,
                    );
                if i == 0 {
                    path.move_to(point);
                } else {
                    path.line_to(point);
                }
                window.paint_quad(fill(
                    Bounds::new(point - gpui::point(px(2.5), px(2.5)), size(px(5.), px(5.))),
                    color,
                ));
            }
            if let Ok(path) = path.build() {
                window.paint_path(path, color);
            }
        },
    )
    .w_full()
    .h(px(84.))
    .flex_shrink_0()
    .into_any_element()
}

#[cfg(test)]
mod detail_tests {
    use super::{detail_height, observation_points};
    use serde_json::json;
    #[test]
    fn history_uses_recorded_time_and_never_coerces_non_numeric_values() {
        let values = vec![
            json!({"time":"20","value":4}),
            json!({"time":10,"value":2}),
            json!({"value":7}),
            json!({"time":30,"value":"missing"}),
        ];
        assert_eq!(observation_points(&values), vec![[10., 2.], [20., 4.]]);
    }
    #[test]
    fn expansion_leaves_room_for_the_map_on_small_windows() {
        for height in [600., 700., 1000., 1800.] {
            assert!(detail_height(height, true) > detail_height(height, false));
            assert!(height - detail_height(height, true) >= 240.);
        }
    }
}
