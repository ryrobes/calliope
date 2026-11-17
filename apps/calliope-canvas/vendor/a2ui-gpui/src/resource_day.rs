use crate::{app::GpuiApp, components::Walk, walker::render_node};
use a2ui_base::model::{component_context::ComponentContext, component_model::ComponentModel};
use gpui::{prelude::*, *};
use gpui_component::ActiveTheme;
use serde_json::Value;
pub(super) fn render(
    walk: &Walk,
    ctx: &ComponentContext,
    model: &ComponentModel,
    window: &mut Window,
    cx: &mut Context<GpuiApp>,
) -> AnyElement {
    let resources = model
        .get_property::<Vec<String>>("resources")
        .unwrap_or_default();
    let positions = model
        .get_property::<Vec<Value>>("positions")
        .unwrap_or_default();
    let children = model
        .get_property::<Vec<String>>("children")
        .unwrap_or_default();
    let start = model.get_property::<f32>("start").unwrap_or(360.);
    let end = model.get_property::<f32>("end").unwrap_or(1200.);
    let compact = f32::from(window.viewport_size().width) < 700.;
    let mut root = div()
        .w_full()
        .min_w_0()
        .flex()
        .gap_3()
        .when(compact, |s| s.flex_col());
    for (lane, name) in resources.iter().enumerate() {
        let mut track = div()
            .relative()
            .w_full()
            .when(!compact, |s| s.h(px((end - start) * 0.8)))
            .when(compact, |s| s.flex().flex_col().gap_2());
        if !compact {
            for minute in ((start as usize)..(end as usize)).step_by(120) {
                track = track.child(
                    div()
                        .absolute()
                        .top(px((minute as f32 - start) * 0.8))
                        .w_full()
                        .border_t_1()
                        .border_color(cx.theme().border)
                        .text_size(px(10.))
                        .text_color(cx.theme().muted_foreground)
                        .child(format!("{:02}:{:02}", minute / 60, minute % 60)),
                );
            }
        }
        for (i, id) in children.iter().enumerate() {
            let Some(pos) = positions.get(i) else {
                continue;
            };
            if pos["lane"].as_u64() != Some(lane as u64) {
                continue;
            }
            let top = (pos["start"].as_f64().unwrap_or(start as f64) as f32 - start) * 0.8;
            let height = ((pos["end"].as_f64().unwrap_or(0.) - pos["start"].as_f64().unwrap_or(0.))
                as f32
                * 0.8
                - 4.)
                .max(90.);
            let card = div()
                .id(SharedString::from(format!("appointment-{id}")))
                .p_2()
                .rounded_lg()
                .border_1()
                .border_color(cx.theme().border)
                .bg(cx.theme().secondary)
                .min_w_0()
                .when(!compact, |s| {
                    s.absolute()
                        .top(px(top))
                        .left(px(44.))
                        .right_0()
                        .h(px(height))
                        .overflow_y_scroll()
                })
                .child(render_node(
                    id,
                    ctx.data_context.base_path(),
                    walk,
                    window,
                    cx,
                ));
            track = track.child(card);
        }
        root = root.child(
            div()
                .flex_1()
                .min_w_0()
                .when(compact, |s| s.w_full())
                .flex()
                .flex_col()
                .gap_3()
                .p_3()
                .border_1()
                .border_color(cx.theme().border)
                .rounded_xl()
                .child(div().text_size(px(16.)).child(name.clone()))
                .child(track),
        );
    }
    root.into_any_element()
}
