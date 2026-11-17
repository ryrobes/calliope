impl Meaning {
    fn button(&self, id: impl Into<ElementId>, label: impl Into<SharedString>) -> Stateful<Div> {
        div()
            .id(id)
            .px_3()
            .py_2()
            .rounded_lg()
            .text_size(px(13.))
            .cursor_pointer()
            .text_color(self.theme.muted)
            .hover(|s| s.bg(self.theme.line).text_color(self.theme.text))
            .child(label.into())
    }
    fn color(&self, kind: &str) -> Rgba {
        match kind {
            "metric" => self.theme.success,
            "artifact" => self.theme.warning,
            _ => self.theme.accent,
        }
    }
    pub fn summary(node: &Value, theme: &CanvasTheme, z: f32) -> AnyElement {
        let preview = node.get("preview");
        let series: Vec<f32> = items(node, "series")
            .into_iter()
            .filter_map(|n| n.as_f64().map(|v| v as f32))
            .collect();
        let mut body = div()
            .flex()
            .flex_col()
            .gap(px(4. * z))
            .flex_shrink_0()
            .line_height(relative(1.2))
            .min_h_0()
            .overflow_hidden();
        if node["kind"] == "relation" {
            body = body
                .child(
                    div()
                        .text_size(px(12. * z))
                        .text_color(theme.muted)
                        .line_clamp(2)
                        .child(text(node, "description")),
                )
                .when_some(preview, |s, p| {
                    s.child(
                        div()
                            .text_size(px(10. * z))
                            .text_color(theme.muted)
                            .child(format!(
                                "{} profiled rows · {}",
                                number(&p["value"]),
                                short(&text(p, "at"), 16)
                            )),
                    )
                });
        } else if let Some(p) = preview {
            body = body
                .child(
                    div()
                        .flex()
                        .items_end()
                        .flex_shrink_0()
                        .gap(px(12. * z))
                        .child(div().text_size(px(30. * z)).child(number(&p["value"])))
                        .when(series.len() > 1, |s| {
                            s.child(sparkline(series, theme.success, z))
                        }),
                )
                .child(
                    div()
                        .text_size(px(10. * z))
                        .flex_shrink_0()
                        .text_color(theme.muted)
                        .line_clamp(1)
                        .child(format!(
                            "{} · {}",
                            text(p, "label"),
                            short(&text(p, "at"), 16)
                        )),
                );
        } else {
            body = body.child(
                div()
                    .text_size(px(12. * z))
                    .text_color(theme.muted)
                    .line_clamp(2)
                    .child(short(&text(node, "description"), 220)),
            );
        }
        if node["kind"] == "artifact" {
            body = body.child(div().flex().gap(px(5. * z)).overflow_hidden().children(
                items(node, "dependencies").into_iter().take(2).map(|d| {
                    div()
                        .px(px(6. * z))
                        .py(px(3. * z))
                        .rounded(px(5. * z))
                        .bg(with_alpha(theme.accent, 0.08))
                        .text_size(px(10. * z))
                        .child(short(&text(&d, "object_ref"), 26))
                }),
            ));
        }
        body.into_any_element()
    }
    fn node_card(&mut self, n: Value, p: [f32; 2], cx: &mut Context<Self>) -> AnyElement {
        let l = self.layout();
        let z = l.zoom;
        let id = text(&n, "id");
        let title = text(&n, "title");
        let kind = text(&n, "kind");
        let selected = id == self.selected;
        let drag_id = id.clone();
        let open_id = id.clone();
        let area = text(&n, "area");
        let overview = self.area.is_empty();
        let color = self.color(&kind);
        let summary = Self::summary(&n, &self.theme, z);
        div()
            .id(SharedString::from(format!("meaning-node-{id}")))
            .occlude()
            .absolute()
            .left(px(l.pan[0] + p[0] * z))
            .top(px(l.pan[1] + p[1] * z))
            .w(px(300. * z))
            .h(px(170. * z))
            .p(px(12. * z))
            .rounded(px(15. * z))
            .bg(with_alpha(self.theme.card, 0.98))
            .border_1()
            .border_color(if selected {
                color
            } else {
                with_alpha(self.theme.line, 0.8)
            })
            .shadow_md()
            .flex()
            .flex_col()
            .gap(px(5. * z))
            .line_height(relative(1.2))
            .overflow_hidden()
            .cursor_pointer()
            .on_mouse_down(MouseButton::Left, |_, _, cx| cx.stop_propagation())
            .on_click(cx.listener(move |s, _, _, cx| {
                if overview {
                    s.open_area(area.clone(), cx);
                } else if open_id.starts_with("group:") {
                    s.expand_group(&open_id, cx);
                } else {
                    s.selected = open_id.clone();
                    s.selected_edge = None;
                    cx.notify();
                }
            }))
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap(px(6. * z))
                    .h(px(12. * z))
                    .flex_shrink_0()
                    .cursor(CursorStyle::OpenHand)
                    .child(div().size(px(5. * z)).rounded_full().bg(color))
                    .child(
                        div()
                            .text_size(px(10. * z))
                            .text_color(self.theme.muted)
                            .child(
                                match kind.as_str() {
                                    "relation" => "SOURCE",
                                    "cube" => "CUBE",
                                    "group" => "COLLAPSED GROUP",
                                    "idea" => "METRIC IDEA",
                                    _ => kind.as_str(),
                                }
                                .to_uppercase(),
                            ),
                    )
                    .child(div().flex_1())
                    .when(z >= 0.65, |s| {
                        s.child(
                            div()
                                .text_size(px(9. * z))
                                .text_color(color)
                                .child(text(&n, "status")),
                        )
                    })
                    .on_mouse_down(
                        MouseButton::Left,
                        cx.listener(move |s, e: &MouseDownEvent, _, cx| {
                            s.selected = drag_id.clone();
                            s.selected_edge = None;
                            s.drag = Some(Drag::Node(drag_id.clone(), e.position, p));
                            cx.stop_propagation();
                            if drag_id.starts_with("group:") {
                                return;
                            }
                            cx.notify();
                        }),
                    ),
            )
            .child(
                div()
                    .text_size(px((18. * z).max(12.)))
                    .h(px((44. * z).max(30.)))
                    .flex_shrink_0()
                    .font_family(self.theme.font_display.clone())
                    .line_clamp(2)
                    .child(title),
            )
            .when(z >= 0.60, |s| {
                s.child(
                    div()
                        .h(px(50. * z))
                        .flex_shrink_0()
                        .overflow_hidden()
                        .child(summary),
                )
            })
            .child(div().flex_1())
            .when(z >= 0.6, |s| {
                s.child(
                    div()
                        .text_size(px(10. * z))
                        .flex_shrink_0()
                        .text_color(self.theme.muted)
                        .line_clamp(1)
                        .child(text(&n, "subtitle")),
                )
            })
            .into_any_element()
    }
    fn map(&mut self, cx: &mut Context<Self>) -> AnyElement {
        let layout = self.layout();
        let z = layout.zoom;
        let theme = self.theme.clone();
        let display_visible = self.display_visible();
        let edges: Vec<_> = self
            .display_edges()
            .into_iter()
            .filter(|e| {
                display_visible.contains(&text(e, "from"))
                    && display_visible.contains(&text(e, "to"))
            })
            .collect();
        let selected = self.selected.clone();
        let selected_edge = self.selected_edge.clone();
        let drawings: Vec<_> = edges
            .iter()
            .filter_map(|e| {
                let a = *layout.positions.get(&text(e, "from"))?;
                let b = *layout.positions.get(&text(e, "to"))?;
                let (start, end, ca, cb) = edge_points(a, b);
                let transform =
                    |p: [f32; 2]| point(px(layout.pan[0] + p[0] * z), px(layout.pan[1] + p[1] * z));
                let active = e["from"] == selected
                    || e["to"] == selected
                    || Some(text(e, "id")) == selected_edge;
                Some((
                    transform(start),
                    transform(end),
                    transform(ca),
                    transform(cb),
                    active,
                    matches!(
                        e["state"].as_str(),
                        Some("unverified" | "implied" | "contradicted")
                    ),
                ))
            })
            .collect();
        let weak = cx.entity().downgrade();
        let lines = canvas(
            move |bounds, _, cx| {
                let _ = weak.update(cx, |s, cx| {
                    let resized = s.viewport != bounds;
                    s.viewport = bounds;
                    if s.fit_pending && f32::from(bounds.size.width) > 0. {
                        // Fit after layout knows the detail panel's actual height.
                        s.fit();
                        cx.notify();
                    } else if resized {
                        cx.notify();
                    }
                });
                bounds
            },
            move |_, bounds, window, _| {
                for (a, b, ca, cb, active, dashed) in drawings {
                    let mut path = PathBuilder::stroke(px(if active { 1.6 } else { 1. }));
                    if dashed {
                        path = path.dash_array(&[px(5.), px(5.)]);
                    }
                    path.move_to(bounds.origin + a);
                    path.cubic_bezier_to(bounds.origin + b, bounds.origin + ca, bounds.origin + cb);
                    if let Ok(path) = path.build() {
                        window.paint_path(
                            path,
                            with_alpha(
                                if active { theme.accent } else { theme.line },
                                if active { 0.8 } else { 0.4 },
                            ),
                        );
                    }
                }
            },
        )
        .absolute()
        .size_full();
        let visible_nodes = self.display_nodes();
        let cards: Vec<_> = visible_nodes
            .into_iter()
            .filter_map(|n| {
                let p = self.layout().positions.get(&text(&n, "id")).copied()?;
                let l = self.layout();
                let w = f32::from(self.viewport.size.width);
                let h = f32::from(self.viewport.size.height);
                if l.pan[0] + (p[0] + 300.) * z < 0.
                    || l.pan[1] + (p[1] + 170.) * z < 0.
                    || l.pan[0] + p[0] * z > w
                    || l.pan[1] + p[1] * z > h
                {
                    return None;
                }
                Some(self.node_card(n, p, cx))
            })
            .collect();
        let labels: Vec<_> = self
            .edge_labels()
            .into_iter()
            .map(|label| {
                let [x, y, w, h] = label.rect;
                let id = label.id.clone();
                div()
                    .id(SharedString::from(label.id))
                    .absolute()
                    .left(px(x))
                    .top(px(y))
                    .w(px(w))
                    .h(px(h))
                    .overflow_hidden()
                    .flex()
                    .items_center()
                    .justify_center()
                    .rounded_sm()
                    .bg(self.theme.bg)
                    .text_color(self.theme.muted)
                    .text_size(px(10.))
                    .line_height(relative(1.))
                    .cursor_pointer()
                    .child(label.label)
                    .on_mouse_down(MouseButton::Left, |_, _, cx| cx.stop_propagation())
                    .on_click(cx.listener(move |s, _, _, cx| {
                        if id.starts_with("group-edge:") {
                            s.catalog_open = true;
                        } else {
                            s.selected_edge = Some(id.clone());
                        }
                        cx.notify();
                    }))
                    .into_any_element()
            })
            .collect();
        div().id("meaning-map").relative().flex_1().min_h_0().min_w_0().overflow_hidden()
            .on_mouse_down(MouseButton::Left,cx.listener(|s,e:&MouseDownEvent,_,cx|{s.drag=Some(Drag::Pan(e.position,s.layout().pan));cx.notify();}))
            .on_scroll_wheel(cx.listener(|s,e:&ScrollWheelEvent,_,cx|{let delta=e.delta.pixel_delta(px(24.));s.zoom(s.layout().zoom*(f32::from(delta.y)*0.006).exp(),e.position);cx.stop_propagation();cx.notify();}))
            .when_some(self.theme.wallpaper.clone(),|s,p|s.child(img(p).absolute().size_full().object_fit(ObjectFit::Cover).opacity(0.08)))
            .child(lines).children(labels).children(cards)
            .when(self.graph["nodes"].as_array().is_none_or(|n|n.is_empty()),|s|s.child(div().absolute().left(px(36.)).top(px(35.)).w(px(500.)).flex().flex_col().gap_3()
                .child(div().text_size(px(27.)).child(if self.busy{"Finding your business areas…"}else if self.notice.is_some(){"Meaning is unavailable"}else{"Meaning is still taking shape"}))
                .child(div().text_color(self.theme.muted).child(if self.notice.is_some(){"Your published data was not replaced. Retry when the connection is available."}else{"Published areas will appear here as Calliope recognizes your company's data."}))))
            .child(div().absolute().bottom(px(10.)).left(px(16.)).text_size(px(11.)).text_color(self.theme.muted).child(format!("{} expanded · {} grouped · {} total · scroll to zoom · drag to pan",self.visible.len(),items(&self.graph,"nodes").len().saturating_sub(self.visible.len()),items(&self.graph,"nodes").len())))
            .into_any_element()
    }
}

fn sparkline(values: Vec<f32>, color: Rgba, z: f32) -> AnyElement {
    canvas(
        |_, _, _| (),
        move |bounds, _, window, _| {
            let min = values.iter().copied().fold(f32::INFINITY, f32::min);
            let max = values.iter().copied().fold(f32::NEG_INFINITY, f32::max);
            let range = (max - min).max(0.001);
            let mut path = PathBuilder::stroke(px(1.5));
            for (i, v) in values.iter().enumerate() {
                let p = bounds.origin
                    + point(
                        bounds.size.width * i as f32 / (values.len() - 1) as f32,
                        bounds.size.height * (1. - (v - min) / range),
                    );
                if i == 0 {
                    path.move_to(p);
                } else {
                    path.line_to(p);
                }
            }
            if let Ok(path) = path.build() {
                window.paint_path(path, color);
            }
        },
    )
    .w(px(75. * z))
    .h(px(25. * z))
    .into_any_element()
}

fn edge_points(a: [f32; 2], b: [f32; 2]) -> ([f32; 2], [f32; 2], [f32; 2], [f32; 2]) {
    if (a[0] - b[0]).abs() < 10. {
        let start = [a[0] + 300., a[1] + 85.];
        let end = [b[0] + 300., b[1] + 85.];
        (
            start,
            end,
            [start[0] + 60., start[1]],
            [end[0] + 60., end[1]],
        )
    } else {
        let forward = a[0] < b[0];
        let start = [a[0] + if forward { 300. } else { 0. }, a[1] + 85.];
        let end = [b[0] + if forward { 0. } else { 300. }, b[1] + 85.];
        let mid = (start[0] + end[0]) / 2.;
        (start, end, [mid, start[1]], [mid, end[1]])
    }
}

impl Render for Meaning {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        self.ensure_inspection();
        if self.clear_catalog_search {
            self.clear_catalog_search = false;
            self.catalog_search
                .update(cx, |input, cx| input.set_value("", window, cx));
        }
        let title = if self.area.is_empty() {
            "How your business connects".into()
        } else {
            items(&self.graph, "nodes")
                .iter()
                .find(|n| n["kind"] == "area")
                .map(|n| text(n, "title"))
                .unwrap_or_else(|| "Meaning".into())
        };
        let map = self.map(cx);
        let details = if self.area.is_empty() {
            None
        } else {
            Some(self.details(window, cx))
        };
        let weak = cx.entity().downgrade();
        let events = canvas(
            |_, _, _| (),
            move |_, _, window, _| {
                let moving = weak.clone();
                window.on_mouse_event(move |e: &MouseMoveEvent, phase, _, cx| {
                    if phase == DispatchPhase::Capture {
                        let _ = moving.update(cx, |s, cx| {
                            if !e.dragging() {
                                if s.drag.take().is_some() {
                                    s.save();
                                }
                                return;
                            }
                            let Some(drag) = s.drag.clone() else {
                                return;
                            };
                            let mut l = s.layout();
                            match drag {
                                Drag::Pan(start, pan) => {
                                    l.pan = [
                                        pan[0] + f32::from(e.position.x - start.x),
                                        pan[1] + f32::from(e.position.y - start.y),
                                    ];
                                }
                                Drag::Node(id, start, p) => {
                                    l.positions.insert(
                                        id,
                                        [
                                            p[0] + f32::from(e.position.x - start.x) / l.zoom,
                                            p[1] + f32::from(e.position.y - start.y) / l.zoom,
                                        ],
                                    );
                                }
                            }
                            s.layouts.insert(s.area.clone(), l);
                            cx.notify();
                        });
                    }
                });
                let released = weak.clone();
                window.on_mouse_event(move |_: &MouseUpEvent, phase, _, cx| {
                    if phase == DispatchPhase::Capture {
                        let _ = released.update(cx, |s, _| {
                            if s.drag.take().is_some() {
                                s.save();
                            }
                        });
                    }
                });
            },
        )
        .absolute()
        .size_full();
        div().relative().size_full().flex().flex_col().text_color(self.theme.text).font_family(self.theme.font_body.clone()).bg(self.theme.bg)
            .child(div().px_6().py_3().flex_shrink_0().flex().items_center().gap_3()
                .when(!self.area.is_empty(),|s|s.child(self.button("meaning-areas","← Areas").on_click(cx.listener(|s,_,_,cx|s.open_area(String::new(),cx)))))
                .child(div().flex_1().min_w_0().text_size(px(25.)).font_family(self.theme.font_display.clone()).line_clamp(1).child(title))
                .when(self.area.is_empty(),|s|s.child(div().w(px(260.)).child(Input::new(&self.search))))
                .child(div().text_size(px(11.)).text_color(self.theme.muted).child(if self.busy{"Refreshing…"}else if self.stale{"Last received map"}else{"Published Meaning"}))
                .child(self.button("meaning-fit","Fit").on_click(cx.listener(|s,_,_,cx|{s.fit();s.save();cx.notify();})))
                .child(self.button("meaning-refresh","Refresh").on_click(cx.listener(|s,_,_,cx|s.load(cx)))))
            .when_some(self.notice.clone(),|s,n|s.child(div().px_6().py_2().text_size(px(12.)).text_color(self.theme.warning).child(short(&n,240))))
            .child(div().flex_1().min_h_0().flex().child(map).when(self.catalog_open&&!self.area.is_empty(),|s|s.child(self.catalog_panel(cx))))
            .child(div().px_6().py_2().flex_shrink_0().flex().items_center().gap_2()
                .child(div().flex_1().min_w_0().text_size(px(11.)).text_color(self.theme.muted).child("━ Declared / published   ┄ Suggested / implied · Connections open evidence"))
                .when(!self.area.is_empty(),|s|s
                    .child(self.button("meaning-reset","Reset view").on_click(cx.listener(|s,_,_,cx|s.reset_view(cx))))
                    .child(self.button("meaning-browse","Browse objects…").when(self.catalog_open,|s|s.bg(self.theme.card).text_color(self.theme.accent))
                        .on_click(cx.listener(|s,_,_,cx|{s.catalog_open=!s.catalog_open;cx.notify();})))))
            .when_some(details,|s,d|s.child(d)).child(events)
    }
}
