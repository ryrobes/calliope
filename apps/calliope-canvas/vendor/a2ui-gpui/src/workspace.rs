//! Calliope's finite presentation vocabulary. Business logic stays in SQL;
//! these nodes only compose existing widgets and theme primitives.
use crate::{app::GpuiApp, components::Walk, walker::render_node};
use a2ui_base::{
    model::{component_context::ComponentContext, component_model::ComponentModel},
    protocol::common_types::DynamicString,
};
use gpui::{prelude::*, *};
use gpui_component::ActiveTheme;

fn token(model: &ComponentModel, key: &str, default: &str) -> String {
    model
        .get_property::<String>(key)
        .unwrap_or_else(|| default.to_string())
}
fn text(ctx: &ComponentContext, model: &ComponentModel, key: &str) -> String {
    model
        .get_property::<DynamicString>(key)
        .map(|s| ctx.data_context.resolve_dynamic_string(&s))
        .unwrap_or_default()
}
fn tone(name: &str, cx: &Context<GpuiApp>) -> Rgba {
    match name {
        "accent" => cx.theme().primary,
        "warm" => cx.theme().warning,
        "cool" => cx.theme().info,
        "success" => cx.theme().success,
        "normal" => cx.theme().foreground,
        _ => cx.theme().muted_foreground,
    }
    .into()
}
fn mix(a: Rgba, b: Rgba, amount: f32) -> Rgba {
    Rgba {
        r: a.r * (1. - amount) + b.r * amount,
        g: a.g * (1. - amount) + b.g * amount,
        b: a.b * (1. - amount) + b.b * amount,
        a: 1.,
    }
}

pub(super) fn render_workspace(
    walk: &Walk,
    ctx: &ComponentContext,
    model: &ComponentModel,
    window: &mut Window,
    cx: &mut Context<GpuiApp>,
) -> AnyElement {
    let scale = walk.presentation_scale;
    let rem_size = f32::from(window.rem_size());
    let spacing = |units: f32| px(rem_size * units * scale);
    let color = tone(&token(model, "tone", "neutral"), cx);
    match model.component_type.as_str() {
        "CLink" | "CRecordLink" => {
            let id=ctx.component_id.clone();
            let key=id.clone();
            let record = model.component_type == "CRecordLink";
            let binding = model.get_property::<serde_json::Value>("reference").unwrap_or_default();
            let reference = binding["path"].as_str().map(|p|ctx.data_context.get(p).unwrap_or_default()).unwrap_or(binding);
            let disabled=walk.interaction_disabled || if record {reference.is_null()} else {text(ctx,model,"href").is_empty()};
            div().id(SharedString::from(id.clone())).min_h(px(44.*scale)).px_2().py_2()
                .text_color(if disabled {cx.theme().muted_foreground}else{cx.theme().primary})
                .child(format!("{} {}",text(ctx,model,"label"),if record {"→"} else {"↗"}))
                .when(!disabled,|el|el.cursor_pointer().focusable()
                    .on_click(cx.listener(move|this,_,_,cx|{this.emit_action(serde_json::json!({"name":"open_link","context":{"componentId":id}}));cx.notify();}))
                    .on_key_down(cx.listener(move|this,e:&KeyDownEvent,_,cx|{if matches!(e.keystroke.key.as_str(),"enter"|"space"){this.emit_action(serde_json::json!({"name":"open_link","context":{"componentId":key}}));cx.stop_propagation();cx.notify();}})))
                .into_any_element()
        }
        "CVegaChart" => {
            let Some(renderer) = walk.chart_renderer else {
                return div().child("Chart renderer is not installed").into_any_element();
            };
            let binding = model.get_property::<serde_json::Value>("spec").unwrap_or_default();
            let spec = binding["path"].as_str().and_then(|p| ctx.data_context.get(p)).unwrap_or_default();
            let height = model.get_property::<u32>("height").unwrap_or(260).clamp(180,960);
            let mut views = walk.chart_views.borrow_mut();
            let id = ctx.component_id.clone();
            if !views.contains_key(&id) && views.len() >= 16 {
                return div().child("Chart count limit reached").into_any_element();
            }
            let host = cx.entity().downgrade();
            let on_select = model.get_property::<String>("onSelect").map(|name| {
                let id = id.clone();
                std::rc::Rc::new(move |mut context: serde_json::Value, cx: &mut gpui::App| {
                    context["componentId"] = serde_json::json!(id);
                    let _ = host.update(cx,|app,cx| { app.emit_action(serde_json::json!({"name":name,"context":context})); cx.notify(); });
                }) as crate::vega::ChartSelect
            });
            let view = views.entry(id).or_insert_with(|| cx.new(|_| crate::vega::VegaChart::new(spec.clone(),height,renderer.clone()))).clone();
            view.update(cx, |chart,cx| { chart.on_select = on_select; chart.set_spec(spec,height,cx); });
            view.into_any_element()
        }
        "CFilterBar" => {
            let mut controls = div().flex().flex_row().flex_wrap().items_end().gap_3().flex_1().min_w_0();
            for id in model.get_property::<Vec<String>>("children").unwrap_or_default() {
                controls = controls.child(div().w(px(210.)).max_w_full().child(render_node(&id,ctx.data_context.base_path(),walk,window,cx)));
            }
            div().w_full().min_w_0().p_3().rounded(px(8.)).bg(mix(cx.theme().popover.into(),cx.theme().background.into(),0.45))
                .border_1().border_color(cx.theme().border).flex().flex_row().flex_wrap().items_end().gap_3()
                .child(div().pb_2().text_size(px(11.)).text_color(cx.theme().muted_foreground).child(text(ctx,model,"label")))
                .child(controls).into_any_element()
        }
        "CSection" => {
            let collapsed=token(model,"collapse","never")=="phone" && f32::from(window.viewport_size().width)<=600.;
            if collapsed {
                let summary=text(ctx,model,"summary");
                let key=format!("section:{}:{}",ctx.component_id,summary);
                let open=walk.local_tabs.get(&key).copied().unwrap_or(0)==1;
                let keyboard_key=key.clone();
                let mut section=div().w_full().min_w_0().flex().flex_col().gap(spacing(0.75));
                section=section.child(div().id(SharedString::from(key.clone())).min_h(px(48.*scale)).px_3().py_2().rounded_lg().bg(cx.theme().popover).border_1().border_color(cx.theme().border).cursor_pointer().focusable()
                    .flex().items_center().gap(spacing(0.5))
                    .child(div().flex_1().min_w_0().flex().flex_col().child(text(ctx,model,"title")).when(!summary.is_empty(),|s|s.child(div().text_size(px(12.*scale)).text_color(cx.theme().muted_foreground).child(summary))))
                    .child(if open {"Hide"}else{"Choose"})
                    .on_click(cx.listener(move|this,_,_,cx|{this.local_tabs.insert(key.clone(),if open{0}else{1});cx.notify();}))
                    .on_key_down(cx.listener(move|this,e:&KeyDownEvent,_,cx|{if matches!(e.keystroke.key.as_str(),"enter"|"space"){this.local_tabs.insert(keyboard_key.clone(),if open{0}else{1});cx.stop_propagation();cx.notify();}})));
                if open {if let Some(id)=model.child(){section=section.child(render_node(&id,ctx.data_context.base_path(),walk,window,cx));}}
                return section.into_any_element();
            }
            let mut heading=div().border_l_2().border_color(color).pl(spacing(0.75)).flex().flex_col().gap(spacing(0.25))
                .child(div().text_size(px(16.*scale)).font_weight(FontWeight::SEMIBOLD).child(text(ctx,model,"title")));
            let subtitle=text(ctx,model,"subtitle");
            if !subtitle.is_empty() {heading=heading.child(div().text_size(px(12.*scale)).text_color(cx.theme().muted_foreground).child(subtitle));}
            let mut section=div().w_full().min_w_0().border_t_1().border_color(cx.theme().border).pt(spacing(1.)).flex().flex_col().gap(spacing(0.75)).child(heading);
            if let Some(id)=model.child(){section=section.child(render_node(&id,ctx.data_context.base_path(),walk,window,cx));}
            section.into_any_element()
        }
        "CStack" => {
            let collapse = token(model, "collapse", "never");
            let width=walk.stack_widths.borrow().get(&ctx.component_id).copied()
                .unwrap_or_else(||f32::from(window.viewport_size().width));
            let row = token(model, "direction", "column") == "row"
                && !(collapse == "phone" && width <= 600. || collapse == "tablet" && width <= 900.);
            let gap = match token(model, "gap", "md").as_str() {
                "none" => 0.,
                "xs" => 4.,
                "sm" => 8.,
                "lg" => 24.,
                "xl" => 32.,
                _ => 16.,
            };
            let mut el = div().relative().flex().w_full().min_w_0().gap(px(gap*scale));
            el = if row {
                el.flex_row().items_start()
            } else {
                el.flex_col()
            };
            if model.get_property::<bool>("wrap").unwrap_or(false) { el = el.flex_wrap(); }
            for id in model
                .get_property::<Vec<String>>("children")
                .unwrap_or_default()
            {
                if id=="record-pane" && ctx.component_id=="root" {continue;}
                let weight = walk
                    .components
                    .get(&id)
                    .and_then(|c| c.get_property::<f32>("weight"));
                let mut slot = div().min_w_0();
                if row {
                    if let Some(w) = weight {
                        slot = slot.flex_basis(px(0.));
                        slot.style().flex_grow = Some(w.clamp(0.1, 12.));
                    } else {
                        slot.style().flex_shrink = Some(1.);
                    }
                } else {
                    slot = slot.w_full();
                }
                el = el.child(slot.child(render_node(
                    &id,
                    ctx.data_context.base_path(),
                    walk,
                    window,
                    cx,
                )));
            }
            if collapse != "never" {
                let weak=cx.entity().downgrade();
                let id=ctx.component_id.clone();
                el=el.child(canvas(move|bounds,_,cx|{
                    let width=f32::from(bounds.size.width);
                    let _=weak.update(cx,|this,cx|{
                        let mut sizes=this.stack_widths.borrow_mut();
                        if sizes.get(&id).is_none_or(|old|(old-width).abs()>0.5) {sizes.insert(id.clone(),width);cx.notify();}
                    });
                },|_,_,_,_|{}).absolute().top_0().left_0().size_full());
            }
            el.into_any_element()
        }
        "CField" => crate::fields::render(walk,ctx,model,window,cx),
        "CAttachments" => crate::fields::attachments(walk,ctx,model,cx),
        "CDataGrid" => crate::grid::render(walk,ctx,model,window,cx),
        "CMetric" => {
            // A governed number, already formatted by the projection.
            let meaning = token(model, "meaning", "neutral");
            let delta_color: Rgba = match meaning.as_str() { "good" => cx.theme().success.into(), "bad" => cx.theme().danger.into(), _ => cx.theme().muted_foreground.into() };
            let status = text(ctx, model, "status");
            let delta = text(ctx, model, "delta");
            let caption = text(ctx, model, "caption");
            div().w_full().min_w_0().flex().flex_col().gap(spacing(0.35))
                .when(!status.is_empty(), |d| d.child(div().text_size(px(11.*scale)).text_color(cx.theme().primary).child(status.to_uppercase())))
                .child(div().flex().items_baseline().gap(spacing(0.6)).flex_wrap()
                    .child(div().font_family(walk.workspace_display_font.clone()).text_size(px(34.*scale)).text_color(cx.theme().foreground).child(text(ctx, model, "value")))
                    .when(!delta.is_empty(), |d| d.child(div().text_size(px(14.*scale)).text_color(delta_color).child(delta))))
                .when(!caption.is_empty(), |d| d.child(div().text_size(px(12.*scale)).text_color(cx.theme().muted_foreground).child(caption)))
                .into_any_element()
        }
        "CMarkdown" => {
            // Markdown state is keyed in the window: one id per card and node.
            let id = SharedString::from(format!("md-{}-{}", walk.surface_id, ctx.component_id));
            let content = text(ctx, model, "text");
            #[cfg(feature = "gpui-next")]
            let view = gpui_component::text::TextView::markdown(id, content).selectable(true).scrollable(false);
            #[cfg(not(feature = "gpui-next"))]
            let view = gpui_component::text::TextView::markdown(id, content, window, cx).selectable(true);
            div().w_full().min_w_0().text_size(px(14.*scale))
                .when(token(model, "tone", "normal") == "muted", |d| d.text_color(cx.theme().muted_foreground))
                .child(view).into_any_element()
        }
        "CResultTable" => {
            let binding = model.get_property::<serde_json::Value>("rows").unwrap_or_default();
            let rows = binding["path"].as_str().and_then(|p| ctx.data_context.get(p)).unwrap_or_default();
            let columns = model.get_property::<serde_json::Value>("columns").unwrap_or_default();
            let row_count = model.get_property::<u64>("rowCount");
            let truncated = model.get_property::<bool>("truncated").unwrap_or(false);
            let visible = model.get_property::<usize>("visibleRows").unwrap_or(12).clamp(3, 50);
            let view = walk.result_tables.borrow_mut().entry(ctx.component_id.clone())
                .or_insert_with(|| cx.new(|_| crate::result_table::ResultTable::new())).clone();
            view.update(cx, |table, cx| table.set(columns, rows, row_count, truncated, visible, scale, cx));
            view.into_any_element()
        }
        "CRecordPane" => {
            let mut pane=div().id("record-inspector").w(px(340.)).max_w_full().flex_shrink_0().p_4().rounded_xl().border_1().border_color(cx.theme().border).bg(cx.theme().popover).flex().flex_col().gap_3()
                .child(div().text_size(px(16.)).child(text(ctx,model,"title")));
            if let Some(id)=model.child(){pane=pane.child(render_node(&id,ctx.data_context.base_path(),walk,window,cx));}
            pane.into_any_element()
        },
        "CSurface" => {
            let panel: Rgba = cx.theme().popover.into();
            let border: Rgba = cx.theme().border.into();
            let mut el = div().w_full().min_w_0().rounded(px(10.)).border_1();
            el = match token(model, "treatment", "soft").as_str() {
                "plain" => el.border_color(gpui::transparent_black()),
                "row" => div().w_full().min_w_0().border_b_1().border_color(border),
                "inset" => el.bg(mix(panel,cx.theme().background.into(),0.55)).border_color(gpui::transparent_black()),
                "outlined" => el.bg(panel).border_color(border),
                "wash" => el
                    .bg(linear_gradient(
                        125.,
                        linear_color_stop(mix(panel, color, 0.15), 0.),
                        linear_color_stop(mix(cx.theme().background.into(), color, 0.02), 1.),
                    ))
                    .border_color(mix(border, color, 0.26)),
                _ => el
                    .bg(mix(panel, color, 0.05))
                    .border_color(mix(border, color, 0.12)),
            };
            let padding = match token(model, "padding", "md").as_str() {
                "none" => 0.,
                "sm" => 12.,
                "lg" => 32.,
                _ => 22.,
            };
            el = el.p(px(padding*scale));
            if let Some(id) = model.child() {
                el = el.child(render_node(
                    &id,
                    ctx.data_context.base_path(),
                    walk,
                    window,
                    cx,
                ));
            }
            el.into_any_element()
        }
        "CText" => {
            let size = token(model, "size", "body");
            let width: f32 = window.viewport_size().width.into();
            let font_size = match size.as_str() {
                "eyebrow" => 10.,
                "caption" => 12.,
                "title" => 18.,
                "display" => (width * 0.036).clamp(32., 52.),
                "metric" => 28.,
                _ => 14.,
            };
            let mut el = div()
                .min_w_0()
                .text_color(tone(&token(model, "tone", "normal"), cx))
                .text_size(px(font_size*scale))
                .line_height(relative(if size == "display" {
                    1.06
                } else if size == "title" {
                    1.22
                } else {
                    1.45
                }));
            match token(model, "family", "body").as_str() {
                "display" => el = el.font_family(walk.workspace_display_font.clone()),
                "mono" => el = el.font_family(cx.theme().mono_font_family.clone()),
                _ => {}
            }
            el.child(text(ctx, model, "text")).into_any_element()
        }
        "CBadge" => div()
            .flex()
            .child(
                div()
                    .text_size(px(10.*scale))
                    .text_color(color)
                    .rounded_full()
                    .px(px(9.*scale))
                    .py(px(4.*scale))
                    .border_1()
                    .border_color(mix(cx.theme().border.into(), color, 0.3))
                    .bg(mix(cx.theme().popover.into(), color, 0.1))
                    .child(text(ctx, model, "text")),
            )
            .into_any_element(),
        "CMeter" => {
            let fraction = model
                .get_property::<f32>("value")
                .unwrap_or(0.)
                .clamp(0., 1.);
            div()
                .w_full()
                .flex()
                .flex_col()
                .gap(spacing(0.5))
                .child(
                    div()
                        .flex()
                        .justify_between()
                        .gap(spacing(0.75))
                        .text_size(px(11.*scale))
                        .text_color(cx.theme().muted_foreground)
                        .child(text(ctx, model, "label"))
                        .child(format!("{}%", (fraction * 100.).round())),
                )
                .child(
                    div()
                        .w_full()
                        .h(px(4.*scale))
                        .rounded_full()
                        .overflow_hidden()
                        .bg(mix(cx.theme().background.into(), color, 0.15))
                        .child(
                            div()
                                .h_full()
                                .w(relative(fraction))
                                .rounded_full()
                                .bg(color),
                        ),
                )
                .into_any_element()
        }
        "CArtwork" => {
            // An enum resolved by the renderer, never a definition-supplied URL/path.
            if token(model, "asset", "") != "neighborhood" {
                return div().into_any_element();
            }
            static ART: std::sync::OnceLock<std::sync::Arc<Image>> = std::sync::OnceLock::new();
            let artwork = ART.get_or_init(|| std::sync::Arc::new(Image::from_bytes(
                ImageFormat::Png, include_bytes!("../../../business/assets/neighborhood.png").to_vec(),
            ))).clone();
            let height = if token(model, "size", "banner") == "portrait" {
                280.
            } else {
                216.
            };
            div()
                .relative()
                .w_full()
                .h(px(height))
                .rounded(px(10.))
                .overflow_hidden()
                .border_1()
                .border_color(cx.theme().border)
                .child(
                    img(artwork)
                        .size_full()
                        .object_fit(ObjectFit::Cover)
                        .opacity(0.9),
                )
                .child(
                    div()
                        .absolute()
                        .bottom_0()
                        .left_0()
                        .right_0()
                        .px_3()
                        .py_2()
                        .text_size(px(9.))
                        .text_color(rgb(0xebeee2))
                        .bg(rgba(0x14251bcc))
                        .child("Neighborhood study / concept artwork"),
                )
                .into_any_element()
        }
        _ => div().into_any_element(),
    }
}
