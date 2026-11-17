use crate::{app::GpuiApp, components::Walk, fields::field_error, walker::render_node};
use a2ui_base::model::{component_context::ComponentContext, component_model::ComponentModel};
use gpui::{prelude::*, *};
use gpui_component::ActiveTheme;
use serde_json::{Value, json};

pub fn paste_matrix(
    text: &str,
    row: usize,
    col: usize,
    columns: usize,
    cells: &[String],
    props: &std::collections::HashMap<String, Value>,
) -> Result<Vec<(String, String)>, String> {
    if text.len() > 16000 {
        return Err("Paste at most 400 cells.".into());
    }
    let normalized = text.replace("\r\n", "\n");
    let normalized = normalized.strip_suffix('\n').unwrap_or(&normalized);
    let mut writes = vec![];
    for (y, line) in normalized.split('\n').enumerate() {
        for (x, value) in line.split('\t').enumerate() {
            let id = if col + x < columns {
                cells.get((row + y) * columns + col + x)
            } else {
                None
            };
            let Some((id, p)) = id.and_then(|id| props.get(id).map(|p| (id, p))) else {
                return Err("Paste must stay within editable cells on this page.".into());
            };
            if p["readOnly"] == true || p["visible"] == false {
                return Err("Paste must stay within editable cells on this page.".into());
            }
            let error = field_error(value.trim(), p);
            if !error.is_empty() {
                return Err(format!(
                    "{}: {error}",
                    p["label"].as_str().unwrap_or("Field")
                ));
            }
            writes.push((id.clone(), value.trim().to_string()));
            if writes.len() > 400 {
                return Err("Paste at most 400 cells.".into());
            }
        }
    }
    Ok(writes)
}
pub(super) fn render(
    walk: &Walk,
    ctx: &ComponentContext,
    model: &ComponentModel,
    window: &mut Window,
    cx: &mut Context<GpuiApp>,
) -> AnyElement {
    let columns = model
        .get_property::<Vec<Value>>("columns")
        .unwrap_or_default();
    let cells = model
        .get_property::<Vec<String>>("children")
        .unwrap_or_default();
    if columns.is_empty() {
        return div().into_any_element();
    }
    let id = ctx.component_id.clone();
    let row_keys = model
        .get_property::<Vec<String>>("rowKeys")
        .unwrap_or_default();
    let compact = f32::from(window.viewport_size().width) < 700.;
    let widths = walk
        .grid_widths
        .borrow()
        .get(&id)
        .filter(|w| w.len() == columns.len())
        .cloned();
    let mut grid = div()
        .id(SharedString::from(format!("grid-{id}")))
        .w_full()
        .min_w_0()
        .flex()
        .flex_col()
        .gap_2();
    let mut props = std::collections::HashMap::new();
    for cell in &cells {
        if let Some(node) = walk
            .components
            .get(cell)
            .filter(|n| n.component_type == "CField")
        {
            let mut p = Value::Object(node.properties.clone());
            for k in ["visible", "required"] {
                if let Some(path) = p[k]["path"].as_str().map(str::to_owned) {
                    p[k] = ctx.data_context.get(&path).unwrap_or(Value::Null);
                }
            }
            props.insert(cell.clone(), p);
        }
    }
    let error_path = format!("/grid_errors/{id}");
    let error = ctx
        .data_context
        .get(&error_path)
        .and_then(|v| v.as_str().map(str::to_owned))
        .unwrap_or_default();
    if !error.is_empty() {
        grid = grid.child(
            div()
                .text_color(cx.theme().danger)
                .text_size(px(12.))
                .child(error),
        );
    }
    let mut table = div()
        .flex()
        .flex_col()
        .gap_2()
        .when(widths.is_some() && !compact, |s| {
            s.w(px(
                widths.as_ref().unwrap().iter().sum::<f32>() + 12. * columns.len() as f32
            ))
        })
        .when(widths.is_none() || compact, |s| s.w_full())
        .when(!compact, |s| s.min_w(px(columns.len() as f32 * 120.)));
    if !compact {
        let mut header = div().flex().gap_3();
        for (i, col) in columns.iter().enumerate() {
            let mut cell = div()
                .relative()
                .min_w_0()
                .text_size(px(11.))
                .text_color(cx.theme().muted_foreground);
            if let Some(w) = &widths {
                cell = cell.w(px(w[i])).flex_shrink_0();
            } else {
                cell = cell.flex_basis(px(0.));
                cell.style().flex_grow = Some(col["weight"].as_f64().unwrap_or(1.) as f32);
            }
            cell = cell.child(col["label"].as_str().unwrap_or("").to_string());
            let weak = cx.entity().downgrade();
            let measure_id = id.clone();
            let count = columns.len();
            cell = cell.child(
                canvas(
                    move |bounds, _, cx| {
                        let _ = weak.update(cx, |this, _| {
                            let measured = this
                                .grid_measured
                                .entry(measure_id.clone())
                                .or_insert_with(|| vec![0.; count]);
                            measured.resize(count, 0.);
                            measured[i] = f32::from(bounds.size.width);
                        });
                    },
                    |_, _, _, _| {},
                )
                .absolute()
                .size_full(),
            );
            if model.get_property::<bool>("resizable").unwrap_or(false) {
                let drag_id = id.clone();
                let initial = widths.clone().unwrap_or_else(|| {
                    columns
                        .iter()
                        .map(|c| c["weight"].as_f64().unwrap_or(1.) as f32 * 110.)
                        .collect()
                });
                cell = cell.child(
                    div()
                        .id(SharedString::from(format!("{id}-resize-{i}")))
                        .absolute()
                        .right_0()
                        .top_0()
                        .bottom_0()
                        .w(px(8.))
                        .cursor_col_resize()
                        .border_r_1()
                        .border_color(cx.theme().border)
                        .on_mouse_down(
                            MouseButton::Left,
                            cx.listener(move |this, e: &MouseDownEvent, _, cx| {
                                if this.interaction_disabled {
                                    return;
                                }
                                let widths = this
                                    .grid_measured
                                    .get(&drag_id)
                                    .filter(|w| {
                                        w.len() == initial.len() && w.iter().all(|v| *v > 0.)
                                    })
                                    .cloned()
                                    .unwrap_or_else(|| initial.clone());
                                this.grid_widths
                                    .borrow_mut()
                                    .insert(drag_id.clone(), widths.clone());
                                this.grid_drag =
                                    Some((drag_id.clone(), i, f32::from(e.position.x), widths[i]));
                                cx.stop_propagation();
                                cx.notify();
                            }),
                        ),
                );
            }
            header = header.child(cell);
        }
        table = table.child(header);
    }
    for (r, row) in cells.chunks(columns.len()).enumerate() {
        let key = row_keys.get(r).cloned().unwrap_or_else(|| row[0].clone());
        let mut line = div()
            .id(SharedString::from(format!("{id}-row-{key}")))
            .flex()
            .gap_3()
            .py_3()
            .border_t_1()
            .border_color(cx.theme().border);
        if compact {
            line = line.flex_col();
        }
        for (i, cell_id) in row.iter().enumerate() {
            let mut cell = div().min_w_0().flex().flex_col().gap_1();
            if compact {
                cell = cell.w_full().child(
                    div()
                        .text_size(px(11.))
                        .text_color(cx.theme().muted_foreground)
                        .child(columns[i]["label"].as_str().unwrap_or("").to_string()),
                );
            } else if let Some(w) = &widths {
                cell = cell.w(px(w[i])).flex_shrink_0();
            } else {
                cell = cell.flex_basis(px(0.));
                cell.style().flex_grow = Some(columns[i]["weight"].as_f64().unwrap_or(1.) as f32);
            }
            line = line.child(cell.child(render_node(
                cell_id,
                ctx.data_context.base_path(),
                walk,
                window,
                cx,
            )));
        }
        table = table.child(line);
    }
    grid = grid.child(
        div()
            .id(SharedString::from(format!("{id}-scroll")))
            .w_full()
            .overflow_x_scroll()
            .child(table),
    );
    if widths.is_some() && !compact {
        let id = id.clone();
        grid = grid.child(
            div()
                .id(SharedString::from(format!("{id}-reset")))
                .text_size(px(11.))
                .cursor_pointer()
                .child("Reset column widths")
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.grid_widths.borrow_mut().remove(&id);
                    cx.notify();
                })),
        );
    }
    use gpui_component::input::{Enter, IndentInline, MoveDown, MoveUp, OutdentInline, Paste};
    let actions = std::rc::Rc::new(GridActions {
        cells,
        props,
        columns: columns.len(),
        error_path,
    });
    let a = actions.clone();
    grid = grid
        .capture_action(cx.listener(move |this, _: &Paste, window, cx| a.paste(this, window, cx)));
    let a = actions.clone();
    grid = grid.capture_action(cx.listener(move |this, _: &Enter, window, cx| {
        a.navigate(
            this,
            if window.modifiers().shift { -1 } else { 1 },
            true,
            window,
            cx,
        )
    }));
    let a = actions.clone();
    grid = grid.capture_action(
        cx.listener(move |this, _: &MoveDown, window, cx| a.navigate(this, 1, true, window, cx)),
    );
    let a = actions.clone();
    grid = grid.capture_action(
        cx.listener(move |this, _: &MoveUp, window, cx| a.navigate(this, -1, true, window, cx)),
    );
    let a = actions.clone();
    grid = grid.capture_action(cx.listener(move |this, _: &IndentInline, window, cx| {
        a.navigate(this, 1, false, window, cx)
    }));
    grid.capture_action(cx.listener(move |this, _: &OutdentInline, window, cx| {
        actions.navigate(this, -1, false, window, cx)
    }))
    .into_any_element()
}
struct GridActions {
    cells: Vec<String>,
    props: std::collections::HashMap<String, Value>,
    columns: usize,
    error_path: String,
}
impl GridActions {
    fn focused(&self, this: &GpuiApp, window: &Window, cx: &Context<GpuiApp>) -> Option<usize> {
        this.text_states
            .borrow()
            .iter()
            .find(|(id, s)| {
                self.cells.contains(id) && s.read(cx).focus_handle(cx).is_focused(window)
            })
            .and_then(|(id, _)| self.cells.iter().position(|v| v == id))
    }
    fn navigate(
        &self,
        this: &mut GpuiApp,
        direction: isize,
        vertical: bool,
        window: &mut Window,
        cx: &mut Context<GpuiApp>,
    ) {
        if this.interaction_disabled {
            return;
        }
        let Some(index) = self.focused(this, window, cx) else {
            return;
        };
        let step = if vertical { self.columns as isize } else { 1 };
        let mut next = index as isize + direction * step;
        while next >= 0 && (next as usize) < self.cells.len() {
            let id = &self.cells[next as usize];
            if self
                .props
                .get(id)
                .is_some_and(|p| p["readOnly"] != true && p["visible"] != false)
            {
                if let Some(state) = this.text_states.borrow().get(id).cloned() {
                    state.update(cx, |s, cx| s.focus(window, cx));
                    cx.stop_propagation();
                    cx.notify();
                }
                return;
            }
            next += step * direction;
        }
    }
    fn paste(&self, this: &mut GpuiApp, window: &mut Window, cx: &mut Context<GpuiApp>) {
        if this.interaction_disabled {
            return;
        }
        let Some(index) = self.focused(this, window, cx) else {
            return;
        };
        let text = cx
            .read_from_clipboard()
            .and_then(|v| v.text())
            .unwrap_or_default();
        if !text.contains(['\t', '\n']) {
            return;
        }
        match paste_matrix(
            &text,
            index / self.columns,
            index % self.columns,
            self.columns,
            &self.cells,
            &self.props,
        ) {
            Ok(writes) => {
                for (id, value) in writes {
                    if let Some(path) = self.props[&id]["value"]["path"].as_str() {
                        this.set_data_value(path, json!(value));
                    }
                    if let Some(state) = this.text_states.borrow().get(&id).cloned() {
                        state.update(cx, |s, cx| s.set_value(value.clone(), window, cx));
                    }
                }
                this.set_data_value(&self.error_path, json!(""));
            }
            Err(error) => this.set_data_value(&self.error_path, json!(error)),
        }
        cx.stop_propagation();
        cx.notify();
    }
}
#[cfg(test)]
mod tests {
    use super::paste_matrix;
    use serde_json::json;
    #[test]
    fn atomic_paste() {
        let cells = vec!["a".into(), "b".into()];
        let p = std::collections::HashMap::from([
            ("a".into(), json!({"kind":"quantity","precision":0})),
            ("b".into(), json!({"kind":"quantity","precision":0})),
        ]);
        assert_eq!(paste_matrix("2\n3", 0, 0, 1, &cells, &p).unwrap().len(), 2);
        assert!(paste_matrix("2\n3.5", 0, 0, 1, &cells, &p).is_err());
        assert!(paste_matrix("2\t3", 0, 0, 1, &cells, &p).is_err());
    }
}
