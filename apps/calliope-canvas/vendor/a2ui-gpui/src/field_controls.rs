//! Typed pickers and focus use the same bindings as ordinary field entry.
use crate::app::GpuiApp;
use chrono::{Datelike, Duration, NaiveDate};
use gpui::{prelude::*, *};
use gpui_component::{
    ActiveTheme, Disableable,
    button::{Button, ButtonVariants},
};
use serde_json::{Value, json};

#[derive(Clone)]
pub(crate) struct Picker {
    pub id: String,
    props: Value,
    path: String,
    month: NaiveDate,
    value: String,
    time_scroll: [ScrollHandle; 2],
}

pub(crate) fn measured_input(
    id: &str,
    element: AnyElement,
    cx: &mut Context<GpuiApp>,
) -> AnyElement {
    let id = id.to_owned();
    let weak = cx.entity().downgrade();
    div()
        .relative()
        .w_full()
        .flex_1()
        .min_w_0()
        .child(element)
        .child(
            canvas(
                move |bounds, _, cx| {
                    let _ = weak.update(cx, |app, _| {
                        app.input_bounds.insert(id.clone(), bounds);
                    });
                },
                |_, _, _, _| {},
            )
            .absolute()
            .inset_0(),
        )
        .into_any_element()
}

fn focus_window(focus: &FocusHandle, window: &mut Window, cx: &mut App) {
    #[cfg(feature = "gpui-next")]
    window.focus(focus, cx);
    #[cfg(not(feature = "gpui-next"))]
    {
        let _ = cx;
        window.focus(focus);
    }
}
impl GpuiApp {
    pub(crate) fn advance_focus(
        &mut self,
        back: bool,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if self.field_picker.is_none() {
            if let Some(current) = self.focused_input(window, cx) {
                let order = self.input_order.borrow();
                if let Some(index) = order.iter().position(|id| id == &current) {
                    let next = if back {
                        (index + order.len() - 1) % order.len()
                    } else {
                        (index + 1) % order.len()
                    };
                    self.focus_requested = Some(order[next].clone());
                    cx.notify();
                    return;
                }
            }
        }
        #[cfg(feature = "gpui-next")]
        if back {
            window.focus_prev(cx);
        } else {
            window.focus_next(cx);
        }
        #[cfg(not(feature = "gpui-next"))]
        if back {
            window.focus_prev();
        } else {
            window.focus_next();
        }
        cx.notify();
    }
    pub(crate) fn focused_input(&self, window: &Window, cx: &App) -> Option<String> {
        for id in self.input_order.borrow().iter() {
            if self
                .text_states
                .borrow()
                .get(id)
                .is_some_and(|s| s.read(cx).focus_handle(cx).is_focused(window))
            {
                return Some(id.clone());
            }
            #[cfg(feature = "gpui-next")]
            if self
                .textarea_states
                .borrow()
                .get(id)
                .is_some_and(|s| s.read(cx).focus_handle(cx).is_focused(window))
            {
                return Some(id.clone());
            }
        }
        None
    }
    pub(crate) fn sync_input_focus(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if let Some(id) = self.focus_requested.take() {
            let focus = self
                .text_states
                .borrow()
                .get(&id)
                .map(|s| s.read(cx).focus_handle(cx));
            #[cfg(feature = "gpui-next")]
            let focus = focus.or_else(|| {
                self.textarea_states
                    .borrow()
                    .get(&id)
                    .map(|s| s.read(cx).focus_handle(cx))
            });
            if let Some(focus) = focus {
                focus_window(&focus, window, cx);
            }
            if let Some(bounds) = self.input_bounds.get(&id) {
                window.request_autoscroll(*bounds);
            }
        }
        let id = self.focused_input(window, cx);
        self.active_input = id.clone();
        let props = id.as_ref().and_then(|id| {
            self.processor
                .model
                .surfaces()
                .next()?
                .components
                .borrow()
                .get(id)
                .map(|n| Value::Object(n.properties.clone()))
        });
        let kind = props
            .as_ref()
            .map(|p| {
                p["kind"]
                    .as_str()
                    .or_else(|| p["variant"].as_str())
                    .unwrap_or("text")
            })
            .unwrap_or("text");
        let kind = if kind == "quantity" && props.as_ref().is_some_and(|p| p["precision"] == 0) {
            "integer"
        } else {
            kind
        };
        if kind != self.last_input_kind {
            self.last_input_kind = kind.to_owned();
            if let Some(callback) = &self.on_input_kind {
                callback(kind);
            }
        }
    }
    pub(crate) fn open_field_picker(
        &mut self,
        id: &str,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if self.interaction_disabled {
            return;
        }
        let snapshot = self.probe_snapshot();
        let Some(node) = snapshot["nodes"]
            .as_array()
            .and_then(|n| n.iter().find(|n| n["id"] == id))
        else {
            return;
        };
        let props = node["props"].clone();
        if !matches!(props["kind"].as_str(), Some("date" | "time"))
            || props["readOnly"] == true
            || props["visible"] == false
        {
            return;
        }
        let Some(path) = node["bindings"]["value"]["path"].as_str() else {
            return;
        };
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs() as i64;
        let today = chrono::DateTime::from_timestamp(now, 0)
            .unwrap()
            .date_naive();
        let value = props["value"].as_str().unwrap_or("").to_owned();
        let month = NaiveDate::parse_from_str(&value, "%Y-%m-%d")
            .unwrap_or(today)
            .with_day(1)
            .unwrap();
        let time_scroll = [ScrollHandle::new(), ScrollHandle::new()];
        for (index, (part, limit)) in value.split(':').zip([24, 60]).enumerate() {
            time_scroll[index].scroll_to_item(part.parse::<usize>().unwrap_or(0).min(limit - 1));
        }
        self.field_picker = Some(Picker {
            id: id.into(),
            props,
            path: path.into(),
            month,
            value,
            time_scroll,
        });
        let focus = self.picker_focus.get_or_insert_with(|| cx.focus_handle());
        focus_window(focus, window, cx);
        cx.notify();
    }
    fn choose_field_value(&mut self, value: String, window: &mut Window, cx: &mut Context<Self>) {
        let Some(picker) = self.field_picker.clone() else {
            return;
        };
        if !crate::fields::field_error(&value, &picker.props).is_empty() {
            return;
        }
        self.set_data_value(&picker.path, json!(value));
        if let Some(state) = self.text_states.borrow().get(&picker.id).cloned() {
            state.update(cx, |s, cx| s.set_value(value, window, cx));
        }
        self.field_errors.remove(&picker.id);
        self.field_picker = None;
        self.focus_requested = Some(picker.id);
        cx.notify();
    }
    pub(crate) fn render_field_picker(
        &mut self,
        _window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Option<AnyElement> {
        let picker = self.field_picker.clone()?;
        let mut body = div().flex().flex_col().gap_3();
        if picker.props["kind"] == "date" {
            body =
                body.child(
                    div()
                        .flex()
                        .items_center()
                        .justify_between()
                        .child(Button::new("previous-month").ghost().label("←").on_click(
                            cx.listener(|s, _, _, cx| {
                                if let Some(p) = &mut s.field_picker {
                                    p.month = (p.month - Duration::days(1)).with_day(1).unwrap();
                                }
                                cx.notify();
                            }),
                        ))
                        .child(picker.month.format("%B %Y").to_string())
                        .child(
                            Button::new("next-month")
                                .ghost()
                                .label("→")
                                .on_click(cx.listener(|s, _, _, cx| {
                                    if let Some(p) = &mut s.field_picker {
                                        p.month =
                                            (p.month + Duration::days(32)).with_day(1).unwrap();
                                    }
                                    cx.notify();
                                })),
                        ),
                );
            let offset = picker.month.weekday().num_days_from_monday();
            let mut grid = div().grid().grid_cols(7).gap_1();
            for day in ["M", "T", "W", "T", "F", "S", "S"] {
                grid = grid.child(div().text_center().text_sm().child(day));
            }
            for _ in 0..offset {
                grid = grid.child(div());
            }
            for day in 1..=31 {
                let Some(date) = picker.month.with_day(day) else {
                    break;
                };
                let value = date.format("%Y-%m-%d").to_string();
                let allowed = crate::fields::field_error(&value, &picker.props).is_empty();
                grid = grid.child(
                    Button::new(SharedString::from(format!("day-{day}")))
                        .ghost()
                        .min_h(px(40.))
                        .label(day.to_string())
                        .when(value == picker.value, |b| {
                            b.bg(cx.theme().primary)
                                .text_color(cx.theme().primary_foreground)
                        })
                        .disabled(!allowed)
                        .on_click(cx.listener(move |s, _, window, cx| {
                            s.choose_field_value(value.clone(), window, cx)
                        })),
                );
            }
            body = body.child(grid);
        } else {
            let mut parts = picker.value.split(':');
            let hour = parts
                .next()
                .and_then(|v| v.parse::<u32>().ok())
                .filter(|v| *v < 24)
                .unwrap_or(12);
            let minute = parts
                .next()
                .and_then(|v| v.parse::<u32>().ok())
                .filter(|v| *v < 60)
                .unwrap_or(0);
            let mut columns = div().flex().gap_3();
            for (label, count, selected) in [("Hour", 24, hour), ("Minute", 60, minute)] {
                let mut list = div()
                    .id(SharedString::from(format!("pick-{label}")))
                    .track_scroll(&picker.time_scroll[if label == "Hour" { 0 } else { 1 }])
                    .h(px(216.))
                    .overflow_y_scroll()
                    .flex()
                    .flex_col()
                    .gap_1();
                for value in 0..count {
                    list = list.child(
                        Button::new(SharedString::from(format!("{label}-{value}")))
                            .ghost()
                            .min_h(px(40.))
                            .label(format!("{value:02}"))
                            .when(value == selected, |b| {
                                b.bg(cx.theme().primary)
                                    .text_color(cx.theme().primary_foreground)
                            })
                            .on_click(cx.listener(move |s, _, _, cx| {
                                if let Some(p) = &mut s.field_picker {
                                    p.value = if label == "Hour" {
                                        format!("{value:02}:{minute:02}")
                                    } else {
                                        format!("{hour:02}:{value:02}")
                                    };
                                }
                                cx.notify();
                            })),
                    );
                }
                columns = columns.child(
                    div()
                        .flex_1()
                        .flex()
                        .flex_col()
                        .gap_2()
                        .child(label)
                        .child(list),
                );
            }
            let value = format!("{hour:02}:{minute:02}");
            body = body.child(columns).child(
                Button::new("use-time")
                    .label(format!("Use {value}"))
                    .on_click(cx.listener(move |s, _, window, cx| {
                        s.choose_field_value(value.clone(), window, cx)
                    })),
            );
        }
        let focus = self.picker_focus.clone()?;
        Some(
            div()
                .absolute()
                .inset_0()
                .bg(rgba(0x00000088))
                .flex()
                .items_center()
                .justify_center()
                .p_3()
                .on_mouse_down(MouseButton::Left, |_, _, cx| cx.stop_propagation())
                .child(
                    div()
                        .id("field-picker")
                        .track_focus(&focus)
                        .w(px(344.))
                        .max_w_full()
                        .max_h_full()
                        .overflow_y_scroll()
                        .rounded_xl()
                        .p_4()
                        .bg(cx.theme().background)
                        .border_1()
                        .border_color(cx.theme().border)
                        .flex()
                        .flex_col()
                        .gap_3()
                        .child(
                            picker.props["label"]
                                .as_str()
                                .unwrap_or("Choose a value")
                                .to_owned(),
                        )
                        .child(body)
                        .child(
                            div()
                                .flex()
                                .justify_between()
                                .child(
                                    Button::new("cancel-picker")
                                        .ghost()
                                        .label("Cancel")
                                        .on_click(cx.listener(|s, _, _, cx| {
                                            s.close_overlay();
                                            cx.notify();
                                        })),
                                )
                                .when(picker.props["required"] != true, |s| {
                                    s.child(
                                        Button::new("clear-picker")
                                            .ghost()
                                            .label("Clear")
                                            .on_click(cx.listener(|s, _, window, cx| {
                                                s.choose_field_value(String::new(), window, cx)
                                            })),
                                    )
                                }),
                        ),
                )
                .into_any_element(),
        )
    }
}
