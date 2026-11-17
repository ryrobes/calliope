use super::*;
use crate::board::{self, QuerySource};

impl CanvasApp {
    pub(super) fn board_sources(&self, pin: &Pin, surface: &Surface) -> Vec<QuerySource> {
        self.threads
            .get(&surface.session_id)
            .map(|t| board::query_sources(pin, surface, &t.surfaces))
            .unwrap_or_default()
    }

    fn set_board_data(&mut self, sources: &[QuerySource], mode: &str) {
        for source in sources {
            let key = source.key();
            match mode {
                "saved" => {
                    self.home.board_queries.remove(&key);
                    self.board_reads.use_saved(&key);
                }
                "manual" => {
                    self.home.board_queries.insert(key, false);
                }
                "auto" => {
                    self.home.board_queries.insert(key, true);
                    self.board_reads.enqueue(source.clone());
                }
                _ => {
                    self.home.board_queries.entry(key).or_insert(false);
                    self.board_reads.enqueue(source.clone());
                }
            }
        }
        self.save();
    }

    pub(super) fn board_data_controls(
        &self,
        pin: &Pin,
        surface: &Surface,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let sources = self.board_sources(pin, surface);
        if sources.is_empty() {
            return div().into_any_element();
        }
        let all_auto = sources
            .iter()
            .all(|s| self.home.board_queries.get(&s.key()) == Some(&true));
        let any_auto = sources
            .iter()
            .any(|s| self.home.board_queries.get(&s.key()) == Some(&true));
        let any_read = sources
            .iter()
            .any(|s| self.home.board_queries.contains_key(&s.key()));
        let auto = sources.clone();
        let saved = sources.clone();
        div().px_3().py_2().flex().flex_col().gap_1()
            .child(div().flex().flex_wrap().gap_2()
                .child(self.button("board-refresh", "Refresh data")
                    .text_color(self.theme.accent)
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.set_board_data(&sources, "refresh"); cx.notify();
                    })))
                .child(self.button("board-auto", if all_auto { "Auto: on · 60s" } else if any_auto { "Auto: mixed" } else { "Auto: off" })
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.set_board_data(&auto, if all_auto { "manual" } else { "auto" }); cx.notify();
                    })))
                .when(any_read, |s| s.child(self.button("board-saved", "Use saved data")
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.set_board_data(&saved, "saved"); cx.notify();
                    })))))
            .child(div().px_2().text_size(px(self.theme.typography.caption)).text_color(self.theme.muted)
                .child(if any_read {
                    "Queries keep their original filters and dates. Notes remain as written."
                } else {
                    "Showing saved data. Refresh reads the same queries; notes remain as written."
                }))
            .into_any_element()
    }

    pub(super) fn board_reading(&self, member: &Surface) -> Option<&crate::live::Reading> {
        let source = QuerySource {
            session_id: member.session_id.clone(),
            surface_id: member.id.clone(),
        };
        self.home
            .board_queries
            .contains_key(&source.key())
            .then(|| self.board_reads.readings.get(&source.key()))
            .flatten()
    }

    pub(super) fn board_cell_status(&self, source: Option<QuerySource>, z: f32) -> AnyElement {
        let Some(source) = source else {
            return div().into_any_element();
        };
        let key = source.key();
        let mode = self.home.board_queries.get(&key);
        let reading = mode.and_then(|_| self.board_reads.readings.get(&key));
        let value = reading.and_then(|r| r.value.as_ref());
        let error = reading.and_then(|r| r.error.as_deref());
        let pending = reading.is_some_and(|r| r.pending.is_some());
        let mut status = if let Some(v) = value {
            let at = string(v, "observed_at");
            format!(
                "Read {} UTC{}",
                at.get(11..19).unwrap_or(&at),
                if v.pointer("/context/as_of").is_some_and(|v| !v.is_null()) {
                    " · fixed date"
                } else {
                    ""
                }
            )
        } else {
            "Saved result".into()
        };
        if error.is_some() {
            status.push_str(" · refresh failed");
        }
        if pending {
            status.push_str(" · refreshing…");
        } else if mode == Some(&true) {
            status.push_str(" · auto 60s");
        }
        div()
            .px(px(12. * z))
            .pb(px(8. * z))
            .flex()
            .flex_col()
            .gap(px(4. * z))
            .text_size(px(self.theme.typography.caption * z))
            .line_height(relative(1.4))
            .text_color(if error.is_some() {
                self.theme.warning
            } else {
                self.theme.muted
            })
            .child(status)
            .when_some(error, |s, e| s.child(short(e, 220)))
            .into_any_element()
    }

    pub(super) fn refresh_boards(&mut self, window: &Window) -> bool {
        if !self.connected || self.login || !window.is_window_active() {
            return false;
        }
        let now = Instant::now();
        let z = self.home.zoom;
        let w = f32::from(self.viewport.size.width);
        let h = f32::from(self.viewport.size.height);
        let mut sources = Vec::new();
        for pin in &self.home.pins {
            let x = self.home.pan[0] + pin.x * z;
            let y = self.home.pan[1] + pin.y * z;
            let inspected =
                self.inspector && !self.history && self.selected.as_ref() == Some(&pin.id);
            let visible = pin.space == self.home.active_space
                && x < w
                && x + pin.width * z > 0.
                && y < h
                && y + pin.height * z > 0.;
            if !inspected && (!visible || self.inspector && self.inspector_wide && !self.history) {
                continue;
            }
            if let Some(surface) = self.surface_for(pin).filter(|s| s.kind == "board") {
                sources.extend(self.board_sources(pin, &surface));
            }
        }
        for source in sources {
            if let Some(&auto) = self.home.board_queries.get(&source.key()) {
                self.board_reads.schedule(source, auto, now);
            }
        }
        let artifact_count = self
            .preview_pending
            .iter()
            .filter(|k| !k.starts_with("image:"))
            .count();
        let slots = 2_usize.saturating_sub(artifact_count + self.board_reads.pending_count());
        let mut changed = false;
        for _ in 0..slots {
            let Some((id, source)) = self.board_reads.begin_next(&self.home.board_queries, now)
            else {
                break;
            };
            changed = true;
            self.work(move |api, tx| {
                tx.send(Update::BoardRead(
                    id,
                    api.board_preview(&source).map_err(|e| e.to_string()),
                ))?;
                Ok(())
            });
        }
        changed
    }
}
