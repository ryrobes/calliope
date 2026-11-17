use crate::{
    app::GpuiApp,
    components::{Walk, bound_input},
};
use a2ui_base::{
    model::{component_context::ComponentContext, component_model::ComponentModel},
    protocol::common_types::DynamicString,
};
use gpui::{prelude::*, *};
use gpui_component::{ActiveTheme, Disableable};
use serde_json::{Value, json};

pub fn field_error(value: &str, p: &Value) -> String {
    if p["visible"] == false || p["readOnly"] == true {
        return String::new();
    }
    let v = value.trim();
    if v.is_empty() {
        return if p["required"] == true {
            "This field is required.".into()
        } else {
            String::new()
        };
    }
    let max = p["maxLength"].as_u64().unwrap_or(4000) as usize;
    if value.chars().count() > max {
        return format!("Use at most {max} characters.");
    }
    match p["kind"].as_str().unwrap_or("text") {
        "money" | "quantity" => {
            let digits = v.strip_prefix('-').unwrap_or(v);
            let parts: Vec<_> = digits.split('.').collect();
            if parts.len() > 2
                || parts
                    .iter()
                    .any(|part| part.is_empty() || !part.bytes().all(|c| c.is_ascii_digit()))
            {
                return "Enter a number without commas or symbols.".into();
            }
            let precision = p["precision"].as_u64().unwrap_or(2) as usize;
            if parts.get(1).is_some_and(|s| s.len() > precision) {
                return format!("Use at most {precision} decimal places.");
            }
            let n = v.parse::<f64>().unwrap_or(f64::NAN);
            for (key, low) in [("minimum", true), ("maximum", false)] {
                if let Some(bound) = p[key].as_str() {
                    if let Ok(b) = bound.parse::<f64>() {
                        if (low && n < b) || (!low && n > b) {
                            return format!(
                                "Must be {} {bound}.",
                                if low { "at least" } else { "no more than" }
                            );
                        }
                    }
                }
            }
        }
        "date" => {
            if chrono::NaiveDate::parse_from_str(v, "%Y-%m-%d")
                .ok()
                .is_none_or(|d| d.format("%Y-%m-%d").to_string() != v)
            {
                return "Use a real date in YYYY-MM-DD format.".into();
            }
            if p["minimum"].as_str().is_some_and(|b| v < b) {
                return format!("Must be on or after {}.", p["minimum"].as_str().unwrap());
            }
            if p["maximum"].as_str().is_some_and(|b| v > b) {
                return format!("Must be on or before {}.", p["maximum"].as_str().unwrap());
            }
        }
        "time" => {
            if chrono::NaiveTime::parse_from_str(v, "%H:%M")
                .ok()
                .is_none_or(|d| d.format("%H:%M").to_string() != v)
            {
                return "Use a time in HH:MM format.".into();
            }
        }
        _ => {}
    }
    String::new()
}
pub(super) fn render(
    walk: &Walk,
    ctx: &ComponentContext,
    model: &ComponentModel,
    window: &mut Window,
    cx: &mut Context<GpuiApp>,
) -> AnyElement {
    let mut props = Value::Object(model.properties.clone());
    for key in [
        "value", "label", "unit", "error", "help", "visible", "required",
    ] {
        if let Some(path) = props[key]["path"].as_str().map(str::to_owned) {
            props[key] = ctx.data_context.get(&path).unwrap_or(Value::Null);
        }
    }
    if props["visible"] == false {
        return div().into_any_element();
    }
    let value = props["value"].as_str().unwrap_or("");
    let kind = props["kind"].as_str().unwrap_or("text");
    let binding = model.get_property::<DynamicString>("value");
    let label = props["label"].as_str().unwrap_or("");
    let placeholder = match kind {
        "date" => "YYYY-MM-DD",
        "time" => "HH:MM",
        _ => label,
    };
    let server = props["error"].as_str().unwrap_or("");
    let error = if !server.is_empty() {
        server.to_string()
    } else if !value.is_empty() || walk.field_errors.contains_key(&ctx.component_id) {
        field_error(value, &props)
    } else {
        String::new()
    };
    let mut entry = div().flex().items_center().gap_2().child(bound_input(
        walk,
        ctx,
        &ctx.component_id,
        value,
        placeholder.to_string().into(),
        binding,
        kind == "longText",
        walk.interaction_disabled || props["readOnly"] == true,
        100.,
        window,
        cx,
    ));
    if matches!(kind,"date"|"time") && props["readOnly"] != true {
        use gpui_component::button::Button;
        let id=ctx.component_id.clone();
        entry=entry.child(Button::new(SharedString::from(format!("picker-{id}")))
            .label(if kind=="date" {"Calendar"} else {"Time"})
            .disabled(walk.interaction_disabled)
            .on_click(cx.listener(move|this,_,window,cx|{this.open_field_picker(&id,window,cx);})));
    }
    if let Some(unit) = props["unit"].as_str().filter(|s| !s.is_empty()) {
        entry = entry.child(
            div()
                .flex_shrink_0()
                .text_size(px(11.))
                .text_color(cx.theme().muted_foreground)
                .child(unit.to_string()),
        );
    }
    div()
        .w_full()
        .min_w_0()
        .flex()
        .flex_col()
        .gap_1()
        .child(
            div()
                .text_size(px(12.))
                .text_color(cx.theme().muted_foreground)
                .child(format!(
                    "{label}{}",
                    if props["required"] == true { " *" } else { "" }
                )),
        )
        .child(entry)
        .when(!error.is_empty(), |s| {
            s.child(
                div()
                    .text_size(px(11.))
                    .text_color(cx.theme().danger)
                    .child(error),
            )
        })
        .when(props["help"].is_string(), |s| {
            s.child(
                div()
                    .text_size(px(11.))
                    .text_color(cx.theme().muted_foreground)
                    .child(props["help"].as_str().unwrap_or("").to_string()),
            )
        })
        .into_any_element()
}
pub(super) fn attachments(
    walk: &Walk,
    ctx: &ComponentContext,
    model: &ComponentModel,
    cx: &mut Context<GpuiApp>,
) -> AnyElement {
    use gpui_component::button::{Button, ButtonVariants};
    let binding = model
        .properties
        .get("value")
        .cloned()
        .unwrap_or(Value::Null);
    let path = binding["path"].as_str().unwrap_or("").to_string();
    let ids = binding
        .as_array()
        .cloned()
        .or_else(|| {
            ctx.data_context
                .get(&path)
                .and_then(|v| v.as_array().cloned())
        })
        .unwrap_or_default();
    let names_path = model
        .properties
        .get("names")
        .and_then(|v| v["path"].as_str())
        .unwrap_or("");
    let names = ctx.data_context.get(names_path).unwrap_or_default();
    let offline = model.get_property::<bool>("offline").unwrap_or(false);
    let read_only = model.get_property::<bool>("readOnly").unwrap_or(false);
    let add_path = path.clone();
    let mut el = div()
        .flex()
        .flex_col()
        .gap_2()
        .child(
            model
                .properties
                .get("label")
                .and_then(Value::as_str)
                .unwrap_or("Attachments")
                .to_string(),
        )
        .when(!read_only, |s| {
            s.child(
                Button::new(SharedString::from(format!("{}-add", ctx.component_id)))
                    .label(if offline {
                        "Attach field evidence…"
                    } else {
                        "Attach photo or document…"
                    })
                    .disabled(walk.interaction_disabled || ids.len() >= if offline { 3 } else { 5 })
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.emit_action(
                            json!({"name":"choose_attachment","context":{"path":add_path}}),
                        );
                        cx.notify();
                    })),
            )
        })
        .when(!read_only, |s| {
            s.child(
                div()
                    .text_size(px(11.))
                    .text_color(cx.theme().muted_foreground)
                    .child(if offline {
                        "Saved on this device · 3 files, 2 MB each"
                    } else {
                        "PNG, JPEG, PDF or text · up to 5 files, 5 MB each"
                    }),
            )
        });
    #[cfg(any(target_os = "android", target_os = "ios"))]
    if !read_only && !offline {
        let camera_path = path.clone();
        el = el.child(
            Button::new(SharedString::from(format!("{}-camera", ctx.component_id)))
                .label("Take photo")
                .disabled(walk.interaction_disabled || ids.len() >= 5)
                .on_click(cx.listener(move |this, _, _, cx| {
                    this.emit_action(
                        json!({"name":"capture_photo","context":{"path":camera_path}}),
                    );
                    cx.notify();
                })),
        );
    }
    for id in ids.iter().filter_map(Value::as_str) {
        let open = id.to_string();
        let remove = id.to_string();
        let path = path.clone();
        let name = names[id].as_str().unwrap_or(id).to_string();
        el = el.child(
            div()
                .flex()
                .gap_2()
                .child(
                    Button::new(SharedString::from(format!("att-{id}")))
                        .ghost()
                        .label(name)
                        .disabled(offline)
                        .on_click(cx.listener(move |this, _, _, cx| {
                            this.emit_action(
                                json!({"name":"open_attachment","context":{"id":open}}),
                            );
                            cx.notify();
                        })),
                )
                .when(!read_only, |s| {
                    s.child(
                        Button::new(SharedString::from(format!("rm-{id}")))
                            .ghost()
                            .label("Remove")
                            .disabled(walk.interaction_disabled)
                            .on_click(cx.listener(move |this, _, _, cx| {
                                let mut ids = this
                                    .data_value(&path)
                                    .as_array()
                                    .cloned()
                                    .unwrap_or_default();
                                ids.retain(|v| v.as_str() != Some(&remove));
                                this.set_data_value(&path, json!(ids));
                                if offline {
                                    this.emit_action(
                                    json!({"name":"remove_local_evidence","context":{"id":remove}}),
                                );
                                }
                                cx.notify();
                            })),
                    )
                }),
        );
    }
    el.into_any_element()
}
#[cfg(test)]
mod tests {
    use super::field_error;
    use serde_json::json;
    #[test]
    fn typed_rules() {
        assert_eq!(
            field_error("2.5", &json!({"kind":"quantity","precision":0})),
            "Use at most 0 decimal places."
        );
        assert!(!field_error("2026-02-30", &json!({"kind":"date"})).is_empty());
        assert!(field_error("2024-02-29", &json!({"kind":"date"})).is_empty());
        assert!(!field_error("25:00", &json!({"kind":"time"})).is_empty());
        assert!(field_error("", &json!({"required":true,"visible":false})).is_empty());
    }
}
