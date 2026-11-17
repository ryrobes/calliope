use super::*;
use crate::board::{self, Cell};

impl CanvasApp {
    pub(super) fn board_body(
        &self,
        pin: &Pin,
        surface: &Surface,
        z: f32,
        expanded: bool,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        // A board can appear on the canvas and in Explore at different scales.
        let mut view_pin = pin.clone();
        view_pin.id = format!("{}-{}", pin.id, if expanded { "detail" } else { "canvas" });
        let pin = &view_pin;
        let Ok(board) = board::parse(surface) else {
            return self.board_notice(
                "This board's layout could not be read. Open it in the browser.",
                z,
            );
        };
        let width = if expanded {
            if self.inspector_wide {
                f32::from(window.viewport_size().width) - 46.
            } else {
                446.
            }
        } else {
            pin.width
        };
        if let Some(index) = pin.board_cell {
            let Some(cell) = board.cells.get(index) else {
                return self
                    .board_notice("This cell is unavailable in the saved board version.", z);
            };
            let body = self.board_cell_body(pin, surface, cell, [z, width], expanded, window, cx);
            let source = self
                .threads
                .get(&surface.session_id)
                .and_then(|t| cell.query_source(surface, &t.surfaces));
            let content = div()
                .w_full()
                .p(px(12. * z))
                .when(expanded, |s| {
                    s.child(self.board_data_controls(pin, surface, cx))
                })
                .child(body)
                .child(self.board_cell_status(source, z));
            return self.board_scroll(pin, content.into_any_element(), z, expanded, window, cx);
        }
        if board.cells.is_empty() {
            return self.board_notice("This board has no cells yet.", z);
        }
        let placements = board::layout(&board.cells, width);
        let row_count = placements.iter().map(|p| p.0 + 1).max().unwrap_or(0);
        let mut rows: Vec<Vec<(usize, AnyElement)>> = (0..row_count).map(|_| Vec::new()).collect();
        let available = width - 24. - if expanded { 0. } else { 14. / z };
        let unit = (available - 110.) / 12.;
        for (index, cell) in board.cells.iter().enumerate() {
            let (row, column, span) = placements[index];
            let member_pin = cell.pin(pin, index);
            let cell_width = unit * f32::from(span) + 10. * f32::from(span - 1);
            let body =
                self.board_cell_body(&member_pin, surface, cell, [z, cell_width], expanded, window, cx);
            let source = self
                .threads
                .get(&surface.session_id)
                .and_then(|t| cell.query_source(surface, &t.surfaces));
            let keep = member_pin.clone();
            let home = member_pin.clone();
            let pinned_home = self
                .home
                .pins
                .iter()
                .any(|p| p.space == "Home" && p.object_key() == home.object_key());
            let kept_here =
                self.home.pins.iter().any(|p| {
                    p.space == self.home.active_space && p.object_key() == keep.object_key()
                });
            let tile = div()
                .id(SharedString::from(format!("board-cell-{}-{index}", pin.id)))
                .w(px(cell_width * z))
                .min_w_0()
                .flex_shrink_0()
                .rounded(px(self.theme.radius * 0.65 * z))
                .when(cell.kind != "text", |s| {
                    s.border_1()
                        .border_color(self.theme.line)
                        .bg(with_alpha(self.theme.card, 0.82))
                })
                .flex()
                .flex_col()
                .child(body)
                .child(self.board_cell_status(source, z))
                .when(expanded, |s| {
                    s.child(
                        div()
                            .mt_auto()
                            .px(px(12. * z))
                            .py(px(8. * z))
                            .flex()
                            .items_center()
                            .justify_end()
                            .gap(px(16. * z))
                            .text_size(px(self.theme.typography.caption * z))
                            .text_color(self.theme.muted)
                            .child(
                                div()
                                    .id("keep-cell")
                                    .cursor_pointer()
                                    .hover(|s| s.text_color(self.theme.accent))
                                    .child(if kept_here {
                                        "Kept here"
                                    } else {
                                        "Keep here +"
                                    })
                                    .on_mouse_down(MouseButton::Left, |_, _, cx| {
                                        cx.stop_propagation()
                                    })
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.home.place_in(&keep, &this.home.active_space.clone());
                                        this.save();
                                        this.status = "Board piece kept on this canvas".into();
                                        cx.notify();
                                    })),
                            )
                            .child(
                                div()
                                    .id("pin-cell-home")
                                    .cursor_pointer()
                                    .hover(|s| s.text_color(self.theme.accent))
                                    .child(if pinned_home {
                                        "Pinned Home"
                                    } else {
                                        "Pin Home ↗"
                                    })
                                    .on_mouse_down(MouseButton::Left, |_, _, cx| {
                                        cx.stop_propagation()
                                    })
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.home.place_in(&home, "Home");
                                        this.save();
                                        this.status =
                                            "Board piece pinned Home · saved source retained"
                                                .into();
                                        cx.notify();
                                    })),
                            ),
                    )
                });
            rows[row].push((column, tile.into_any_element()));
        }
        let grid = div()
            .w_full()
            .flex_shrink_0()
            .flex()
            .flex_col()
            .gap(px(10. * z))
            .children(rows.into_iter().map(|mut cells| {
                cells.sort_by_key(|(column, _)| *column);
                div()
                    .w_full()
                    .flex_shrink_0()
                    .flex()
                    .gap(px(10. * z))
                    .children(cells.into_iter().map(|(_, element)| element))
            }));
        let content = div()
            .w_full()
            .min_w_0()
            .p(px(12. * z))
            .flex()
            .flex_col()
            .gap(px(14. * z))
            .when(expanded, |s| {
                s.child(self.board_data_controls(pin, surface, cx))
            })
            .when(!board.subtitle.is_empty(), |s| {
                s.child(
                    div()
                        .text_size(px(self.theme.typography.body * z))
                        .line_height(relative(1.5))
                        .text_color(self.theme.muted)
                        .child(board.subtitle),
                )
            })
            .when(!board.meta.is_empty(), |s| {
                s.child(
                    div()
                        .text_size(px(self.theme.typography.caption * z))
                        .font_family(self.theme.font_mono.clone())
                        .text_color(self.theme.accent)
                        .child(board.meta),
                )
            })
            .child(grid)
            .child(
                div()
                    .text_size(px(self.theme.typography.caption * z))
                    .text_color(self.theme.muted)
                    .child("Saved layout and notes · original source versions retained"),
            );
        self.board_scroll(pin, content.into_any_element(), z, expanded, window, cx)
    }

    fn board_scroll(
        &self,
        pin: &Pin,
        content: AnyElement,
        z: f32,
        expanded: bool,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        if expanded {
            return div()
                .w_full()
                .flex_shrink_0()
                .child(content)
                .into_any_element();
        }
        let state = window.use_keyed_state(
            SharedString::from(format!("board-scroll-{}-{}", self.epoch, pin.id)),
            cx,
            |_, _| ScrollHandle::new(),
        );
        let handle = state.read(cx).clone();
        div()
            .relative()
            .size_full()
            .min_h_0()
            .child(
                div()
                    .id(SharedString::from(format!("board-scroll-{}", pin.id)))
                    .size_full()
                    .overflow_y_scroll()
                    .track_scroll(&handle)
                    .pr(px(14.))
                    .text_size(px(self.theme.typography.body * z))
                    .child(content)
                    .on_scroll_wheel(|_, _, cx| cx.stop_propagation()),
            )
            .child(
                div()
                    .absolute()
                    .inset_0()
                    .child(Scrollbar::vertical(&handle).scrollbar_show(ScrollbarShow::Always)),
            )
            .into_any_element()
    }

    fn board_notice(&self, message: &str, z: f32) -> AnyElement {
        div()
            .p(px(14. * z))
            .text_size(px(self.theme.typography.body * z))
            .line_height(relative(1.5))
            .text_color(self.theme.muted)
            .child(message.to_owned())
            .into_any_element()
    }

    fn board_cell_body(
        &self,
        pin: &Pin,
        board_surface: &Surface,
        cell: &Cell,
        dimensions: [f32; 2],
        expanded: bool,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let [z, width] = dimensions;
        if cell.kind == "text" {
            return div()
                .p(px(12. * z))
                .flex()
                .flex_col()
                .gap(px(9. * z))
                .when(!cell.eyebrow.is_empty(), |s| {
                    s.child(
                        div()
                            .text_size(px(self.theme.typography.caption * z))
                            .font_family(self.theme.font_mono.clone())
                            .text_color(self.theme.accent)
                            .child(cell.eyebrow.clone()),
                    )
                })
                .when(!cell.heading.is_empty(), |s| {
                    s.child(
                        div()
                            .text_size(px((width * 0.035).clamp(22., 34.) * z))
                            .font_weight(FontWeight::MEDIUM)
                            .line_height(relative(1.2))
                            .child(cell.heading.clone()),
                    )
                })
                .when(!cell.body.is_empty(), |s| {
                    s.child(
                        div()
                            .text_size(px(self.theme.typography.body * z))
                            .line_height(relative(1.55))
                            .child(cell.body.clone()),
                    )
                })
                .when(!cell.aside.is_empty(), |s| {
                    s.child(
                        div()
                            .text_size(px(self.theme.typography.caption * z))
                            .line_height(relative(1.5))
                            .text_color(self.theme.muted)
                            .child(cell.aside.clone()),
                    )
                })
                .into_any_element();
        }
        let member = self
            .threads
            .get(&board_surface.session_id)
            .and_then(|t| cell.member(board_surface, &t.surfaces));
        let Some(member) = member else {
            return div().flex().flex_col()
                .child(self.board_notice("This cell's source is unavailable in this conversation. Refresh or check it in Lab.", z))
                .when_some(browser_link(&self.config, board_surface), |s, url| s.child(self.button(
                    SharedString::from(format!("board-missing-{}", pin.id)), "Open board in browser ↗")
                    .on_click(move |_, _, cx| cx.open_url(&url))))
                .into_any_element();
        };
        let payload = self
            .board_reading(member)
            .and_then(|r| r.value.as_ref())
            .and_then(|v| v.get("result"))
            .unwrap_or(&member.payload);
        if cell.kind == "stat" {
            if member.kind != "query" {
                return self.board_notice("This stat needs a query result source.", z);
            }
            let value = board::query_value(payload, cell.row, &cell.column);
            let delta =
                board::query_value(payload, cell.row, &cell.delta_column).and_then(board::number);
            return div()
                .p(px(14. * z))
                .flex()
                .flex_col()
                .gap(px(9. * z))
                .child(
                    div()
                        .text_size(px(self.theme.typography.caption * z))
                        .text_color(self.theme.muted)
                        .child(if cell.label.is_empty() {
                            cell.title()
                        } else {
                            cell.label.clone()
                        }),
                )
                .child(
                    div()
                        .text_size(px((width * 0.10).clamp(24., 38.) * z))
                        .font_family(self.theme.font_mono.clone())
                        .text_color(self.theme.accent)
                        .child(board::format_stat(value, &cell.format)),
                )
                .when_some(delta, |s, delta| {
                    s.child(
                        div()
                            .text_size(px(self.theme.typography.caption * z))
                            .text_color(if delta > 0. {
                                self.theme.success
                            } else if delta < 0. {
                                self.theme.warning
                            } else {
                                self.theme.muted
                            })
                            .child(format!(
                                "{} {}",
                                if delta > 0. {
                                    "↗"
                                } else if delta < 0. {
                                    "↘"
                                } else {
                                    "—"
                                },
                                board::format_stat(
                                    Some(&serde_json::json!(delta.abs())),
                                    if cell.format == "percent" {
                                        "percent"
                                    } else {
                                        "auto"
                                    }
                                )
                            )),
                    )
                })
                .when(!cell.caption.is_empty(), |s| {
                    s.child(
                        div()
                            .text_size(px(self.theme.typography.caption * z))
                            .line_height(relative(1.45))
                            .text_color(self.theme.muted)
                            .child(cell.caption.clone()),
                    )
                })
                .when(value.is_none(), |s| {
                    s.child(
                        div()
                            .text_size(px(self.theme.typography.caption * z))
                            .text_color(self.theme.warning)
                            .child("Value unavailable in the saved result"),
                    )
                })
                .into_any_element();
        }
        if cell.kind != "surface" {
            return self.board_notice("This cell type opens in the browser.", z);
        }
        let title = if cell.title.is_empty() {
            member.title.clone()
        } else {
            cell.title.clone()
        };
        let mut tile = div().min_w_0().flex().flex_col().gap(px(10. * z)).child(
            div()
                .px(px(12. * z))
                .pt(px(12. * z))
                .text_size(px(self.theme.typography.body * z))
                .font_weight(FontWeight::MEDIUM)
                .child(title),
        );
        let height_units = cell.rows.unwrap_or(1).clamp(1, 3) as f32;
        match member.kind.as_str() {
            "query" if cell.view == "sql" => {
                let sql = string(&member.source, "sql");
                tile = tile.child(
                    div()
                        .id(SharedString::from(format!("board-sql-{}", pin.id)))
                        .max_h(px(height_units * 240. * z))
                        .overflow_y_scroll()
                        .p(px(12. * z))
                        .text_size(px(self.theme.typography.caption * z))
                        .font_family(self.theme.font_mono.clone())
                        .child(if sql.is_empty() {
                            "SQL is unavailable for this source.".into()
                        } else {
                            sql
                        })
                        .when(member.source["sql_truncated"] == true, |s| {
                            s.child("\nSQL excerpt · open source for details")
                        })
                        .on_scroll_wheel(|_, _, cx| cx.stop_propagation()),
                );
            }
            "query" => {
                if cell.view == "chart" {
                    tile = tile.child(self.board_notice(
                        "Chart view opens in the browser. Saved data is shown below.",
                        z,
                    ));
                }
                let rows = payload
                    .get("rows")
                    .or_else(|| payload.get("sample"))
                    .and_then(Value::as_array)
                    .map_or(0, Vec::len);
                let height =
                    ((rows + 1) as f32 * 32. + 62.).clamp(160., (height_units * 300.).min(660.));
                tile = tile.child(div().h(px(height * z)).w_full().child(crate::grid::element(
                    format!("board-grid-{}-{}", self.epoch, pin.id),
                    payload,
                    z,
                    &self.theme,
                    window,
                    cx,
                )));
            }
            "answer" | "document" => {
                let text = member.description();
                tile = tile.child(div().p(px(12. * z)).child(TextView::markdown(
                    SharedString::from(format!("board-markdown-{}", pin.id)),
                    text,
                    window,
                    cx,
                )));
            }
            "image" => {
                if let Some(path) = self.images.get(&member.id) {
                    tile = tile.child(
                        img(path.clone())
                            .w_full()
                            .h(px((width * 0.6).clamp(160., height_units * 300.) * z))
                            .object_fit(ObjectFit::Contain),
                    );
                } else {
                    let message = if self
                        .preview_pending
                        .contains(&format!("image:{}", member.id))
                    {
                        "Loading image from Lab…"
                    } else {
                        "Image preview unavailable. Open its source in the browser."
                    };
                    tile = tile.child(self.board_notice(message, z));
                }
            }
            "metric" => {
                let value = member.payload.pointer("/result/value");
                tile = tile.child(
                    div()
                        .p(px(14. * z))
                        .flex()
                        .flex_col()
                        .gap(px(8. * z))
                        .child(
                            div()
                                .font_family(self.theme.font_mono.clone())
                                .text_size(px(32. * z))
                                .text_color(self.theme.accent)
                                .child(board::format_stat(value, "auto")),
                        )
                        .child(
                            div()
                                .text_size(px(self.theme.typography.caption * z))
                                .child(member.description()),
                        ),
                );
            }
            "find" => {
                if let Some(hits) = member.payload["hits"].as_array() {
                    for hit in hits {
                        let name = ["title", "name", "relation"]
                            .iter()
                            .map(|k| string(hit, k))
                            .find(|s| !s.is_empty())
                            .unwrap_or_default();
                        tile = tile.child(
                            div()
                                .px(px(12. * z))
                                .py(px(6. * z))
                                .text_size(px(self.theme.typography.body * z))
                                .child(name),
                        );
                    }
                } else {
                    tile = tile
                        .child(self.board_notice("Open this source to inspect its evidence.", z));
                }
            }
            "chart" => {
                tile = tile.child(self.stage_chart(&super::chart_placement(&member.id, &pin.id, expanded), z));
            }
            _ => {
                tile = tile.child(self.board_notice(
                    &format!(
                        "{} view · open the interactive source in your browser.",
                        member.kind
                    ),
                    z,
                ));
            }
        }
        tile.child(
            div()
                .px(px(12. * z))
                .pb(px(10. * z))
                .flex()
                .flex_wrap()
                .gap(px(12. * z))
                .text_size(px(self.theme.typography.caption * z))
                .text_color(self.theme.muted)
                .child(format!("Saved {}", short(&member.created_at, 10)))
                .when_some(browser_link(&self.config, member), |s, url| {
                    s.child(
                        div()
                            .id(SharedString::from(format!("board-source-{}", pin.id)))
                            .cursor_pointer()
                            .text_color(self.theme.accent)
                            .child("Source ↗")
                            .on_mouse_down(MouseButton::Left, |_, _, cx| cx.stop_propagation())
                            .on_click(move |_, _, cx| cx.open_url(&url)),
                    )
                }),
        )
        .into_any_element()
    }

    pub(super) fn request_kept_images(&mut self) {
        let mut needed = HashMap::new();
        for pin in &self.home.pins {
            let Some(surface) = self.surface_for(pin) else {
                continue;
            };
            if surface.kind == "image" {
                needed.insert(surface.id.clone(), surface);
                continue;
            }
            if surface.kind != "board" {
                continue;
            }
            let Ok(board) = board::parse(&surface) else {
                continue;
            };
            let Some(thread) = self.threads.get(&surface.session_id) else {
                continue;
            };
            for (i, cell) in board.cells.iter().enumerate() {
                if pin.board_cell.is_some_and(|index| index != i) {
                    continue;
                }
                if let Some(member) = cell
                    .member(&surface, &thread.surfaces)
                    .filter(|s| s.kind == "image")
                {
                    needed.insert(member.id.clone(), member.clone());
                }
            }
        }
        for (id, surface) in needed {
            let key = format!("image:{id}");
            if self.images.contains_key(&id)
                || self.preview_errors.contains_key(&key)
                || !self.preview_pending.insert(key)
            {
                continue;
            }
            self.work(move |api, tx| {
                match api.image(&surface) {
                    Ok(path) => tx.send(Update::Image(id, path))?,
                    Err(error) => tx.send(Update::ImageFailed(id, error.to_string()))?,
                }
                Ok(())
            });
        }
    }
}
