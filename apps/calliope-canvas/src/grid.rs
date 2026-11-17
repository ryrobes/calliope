//! Bounded server results, without an additional client-side row/column cutoff.
use crate::{model::display, theme::CanvasTheme};
use gpui::{prelude::*, *};
use gpui_component::scroll::{Scrollbar, ScrollbarShow};
use serde_json::Value;

#[derive(Default)]
struct GridData {
    columns: Vec<String>,
    rows: Vec<Value>,
    widths: Vec<f32>,
    summary: String,
}

impl GridData {
    fn from_payload(payload: &Value) -> Self {
        let rows = payload
            .get("rows")
            .or_else(|| payload.get("sample"))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut columns: Vec<String> = payload
            .get("columns")
            .and_then(Value::as_array)
            .map(|cols| {
                cols.iter()
                    .enumerate()
                    .map(|(i, c)| {
                        c.as_str()
                            .or_else(|| c.get("name").and_then(Value::as_str))
                            .map(str::to_owned)
                            .unwrap_or_else(|| format!("Column {}", i + 1))
                    })
                    .collect()
            })
            .unwrap_or_default();
        if columns.is_empty() {
            for row in &rows {
                if let Some(object) = row.as_object() {
                    for key in object.keys() {
                        if !columns.contains(key) {
                            columns.push(key.clone());
                        }
                    }
                } else if let Some(array) = row.as_array() {
                    while columns.len() < array.len() {
                        columns.push(format!("Column {}", columns.len() + 1));
                    }
                }
            }
        }
        let widths = columns
            .iter()
            .enumerate()
            .map(|(i, c)| {
                let longest = rows
                    .iter()
                    .take(40)
                    .map(|row| {
                        display(row.get(c).or_else(|| row.get(i)).unwrap_or(&Value::Null))
                            .chars()
                            .take(40)
                            .count()
                    })
                    .max()
                    .unwrap_or(0)
                    .max(c.chars().count());
                (longest as f32 * 7.5 + 28.).clamp(140., 300.)
            })
            .collect();
        let total = payload
            .get("row_count")
            .and_then(Value::as_u64)
            .unwrap_or(rows.len() as u64);
        let summary = if total > rows.len() as u64 {
            format!(
                "{} of {total} rows loaded · {} columns",
                rows.len(),
                columns.len()
            )
        } else {
            format!(
                "{} rows loaded · {} columns{}",
                rows.len(),
                columns.len(),
                if payload["truncated"] == true {
                    " · limited sample"
                } else {
                    ""
                }
            )
        };
        Self {
            columns,
            rows,
            widths,
            summary,
        }
    }

    fn value(&self, row: usize, column: usize) -> String {
        let row = &self.rows[row];
        display(
            row.get(&self.columns[column])
                .or_else(|| row.get(column))
                .unwrap_or(&Value::Null),
        )
    }
}

struct DataGrid {
    payload: Value,
    data: GridData,
    scroll: UniformListScrollHandle,
    theme: CanvasTheme,
    scale: f32,
    cell: Option<(usize, usize)>,
    copied: bool,
}

/// State belongs to this placement and viewing mode, never to another account.
pub fn element(
    id: String,
    payload: &Value,
    scale: f32,
    theme: &CanvasTheme,
    window: &mut Window,
    cx: &mut App,
) -> AnyElement {
    let state = window.use_keyed_state(SharedString::from(id), cx, |_, _| DataGrid {
        payload: Value::Null,
        data: GridData::default(),
        scroll: UniformListScrollHandle::new(),
        theme: theme.clone(),
        scale,
        cell: None,
        copied: false,
    });
    state.update(cx, |grid, _| {
        if grid.payload != *payload {
            let data = GridData::from_payload(payload);
            if grid.data.columns != data.columns {
                grid.scroll = UniformListScrollHandle::new();
            }
            grid.data = data;
            grid.payload = payload.clone();
            grid.cell = None;
        }
        grid.scale = scale;
        grid.theme = theme.clone();
    });
    state.into_any_element()
}

impl Render for DataGrid {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let z = self.scale;
        let width = self.data.widths.iter().sum::<f32>() * z;
        let scroll = self.scroll.0.borrow().base_handle.clone();
        let offset = scroll.offset();
        let header = div()
            .relative()
            .h(px(32. * z))
            .flex_shrink_0()
            .overflow_hidden()
            .bg(self.theme.panel)
            .border_b_1()
            .border_color(self.theme.line)
            .child(
                div()
                    .absolute()
                    .left(offset.x)
                    .w(px(width))
                    .h_full()
                    .flex()
                    .children(self.data.columns.iter().enumerate().map(|(i, column)| {
                        div()
                            .w(px(self.data.widths[i] * z))
                            .flex_shrink_0()
                            .px(px(12. * z))
                            .h_full()
                            .flex()
                            .items_center()
                            .overflow_hidden()
                            .child(div().text_ellipsis().child(column.replace('_', " ")))
                    })),
            );
        let list = uniform_list(
            "grid-rows",
            self.data.rows.len().max(1),
            cx.processor(|this, range: std::ops::Range<usize>, _, cx| {
                let z = this.scale;
                if this.data.rows.is_empty() {
                    return vec![
                        div()
                            .w(px(this.data.widths.iter().sum::<f32>() * z))
                            .h(px(32. * z))
                            .px(px(12. * z))
                            .child("No rows returned")
                            .into_any_element(),
                    ];
                }
                range
                    .map(|row| {
                        div()
                            .id(("row", row))
                            .w(px(this.data.widths.iter().sum::<f32>() * z))
                            .h(px(32. * z))
                            .flex()
                            .border_b_1()
                            .border_color(this.theme.line)
                            .when(row % 2 == 1, |s| s.bg(this.theme.panel))
                            .children((0..this.data.columns.len()).map(|column| {
                                div()
                                    .id(("cell", column))
                                    .w(px(this.data.widths[column] * z))
                                    .h_full()
                                    .flex_shrink_0()
                                    .px(px(12. * z))
                                    .flex()
                                    .items_center()
                                    .overflow_hidden()
                                    .cursor_pointer()
                                    .hover(|s| s.bg(this.theme.hover))
                                    .child(
                                        div().text_ellipsis().child(this.data.value(row, column)),
                                    )
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.cell = Some((row, column));
                                        this.copied = false;
                                        cx.notify();
                                    }))
                            }))
                            .into_any_element()
                    })
                    .collect()
            }),
        )
        .size_full()
        // Scrollbar overlays must not cover the last loaded row/column.
        .pb(px(16.))
        .pr(px(16.))
        .track_scroll(self.scroll.clone())
        .with_horizontal_sizing_behavior(ListHorizontalSizingBehavior::Unconstrained)
        .on_scroll_wheel(cx.listener(|this, e: &ScrollWheelEvent, window, cx| {
            // GPUI's built-in list listener has already applied the wheel delta.
            // Remap Shift+wheel, then clamp before the fixed header reads it.
            let delta = e.delta.pixel_delta(window.line_height());
            let handle = this.scroll.0.borrow().base_handle.clone();
            let old = handle.offset();
            let next = if e.modifiers.shift && delta.x == px(0.) {
                point(old.x + delta.y, old.y - delta.y)
            } else {
                old
            };
            let max = handle.max_offset();
            handle.set_offset(point(
                next.x.clamp(-max.width, px(0.)),
                next.y.clamp(-max.height, px(0.)),
            ));
            cx.stop_propagation();
            cx.notify();
        }));
        div()
            .id("data-grid")
            .relative()
            .size_full()
            .min_h_0()
            .min_w_0()
            .flex()
            .flex_col()
            .text_size(px(self.theme.typography.caption * z))
            .text_color(self.theme.text)
            // Even at a scroll boundary, the grid must not move the canvas.
            .on_scroll_wheel(|_, _, cx| cx.stop_propagation())
            .child(header)
            .child(
                div()
                    .relative()
                    .flex_1()
                    .min_h_0()
                    .overflow_hidden()
                    .child(list)
                    .child(
                        div().absolute().inset_0().child(
                            Scrollbar::new(&self.scroll).scrollbar_show(ScrollbarShow::Always),
                        ),
                    ),
            )
            .child(
                div()
                    .px(px(12. * z))
                    .py(px(7. * z))
                    .flex_shrink_0()
                    .text_color(self.theme.muted)
                    .child(self.data.summary.clone()),
            )
            .when_some(self.cell, |root, (row, column)| {
                root.child(
                    div()
                        .id("cell-detail")
                        .occlude()
                        .absolute()
                        .inset_0()
                        .bg(self.theme.card)
                        .flex()
                        .flex_col()
                        .child(
                            div()
                                .px_3()
                                .py_2()
                                .flex()
                                .gap_3()
                                .items_center()
                                .child(
                                    div()
                                        .id("back-to-grid")
                                        .cursor_pointer()
                                        .text_color(self.theme.accent)
                                        .child("← Table")
                                        .on_click(cx.listener(|this, _, _, cx| {
                                            this.cell = None;
                                            cx.notify();
                                        })),
                                )
                                .child(div().flex_1().min_w_0().text_ellipsis().child(format!(
                                    "{} · row {}",
                                    self.data.columns[column],
                                    row + 1
                                )))
                                .child(
                                    div()
                                        .id("copy-cell")
                                        .cursor_pointer()
                                        .text_color(self.theme.accent)
                                        .child(if self.copied { "Copied" } else { "Copy" })
                                        .on_click(cx.listener(move |this, _, _, cx| {
                                            cx.write_to_clipboard(ClipboardItem::new_string(
                                                this.data.value(row, column),
                                            ));
                                            this.copied = true;
                                            cx.notify();
                                        })),
                                ),
                        )
                        .child(
                            div()
                                .id("cell-value")
                                .flex_1()
                                .min_h_0()
                                .overflow_y_scroll()
                                .p_3()
                                .child(
                                    div()
                                        .whitespace_normal()
                                        .child(self.data.value(row, column)),
                                ),
                        ),
                )
            })
    }
}

#[cfg(test)]
mod tests {
    use super::GridData;
    use serde_json::json;

    #[test]
    fn wide_results_keep_every_loaded_row_and_column() {
        let columns: Vec<_> = (0..25).map(|i| format!("field_{i}")).collect();
        let rows: Vec<_> = (0..200)
            .map(|r| (0..25).map(|c| r * 100 + c).collect::<Vec<_>>())
            .collect();
        let data =
            GridData::from_payload(&json!({"columns": columns, "rows": rows, "row_count": 1234}));
        assert_eq!(data.value(199, 24), "19924");
        assert_eq!(data.summary, "200 of 1234 rows loaded · 25 columns");
        assert!(data.widths.iter().all(|width| *width >= 140.));
    }

    #[test]
    fn metadata_and_object_rows_preserve_full_values() {
        let value = "long value ".repeat(100);
        let data = GridData::from_payload(
            &json!({"columns": [{"name": "body"}, {"name": "empty"}], "sample": [{"body": value}]}),
        );
        assert_eq!(data.value(0, 0), value);
        assert_eq!(data.value(0, 1), "—");
        let inferred =
            GridData::from_payload(&json!({"rows": [{"a": 1}, {"b": 2}], "truncated": true}));
        assert_eq!(inferred.columns, vec!["a", "b"]);
        assert!(inferred.summary.ends_with("limited sample"));
    }
}
