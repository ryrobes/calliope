//! Reusable selection, bounded scrolling, and visible asynchronous regions.
use crate::{app::GpuiApp, components::Walk, walker::render_node};
use a2ui_base::{model::{component_context::ComponentContext, component_model::ComponentModel}, protocol::common_types::DynamicString};
use gpui::{prelude::*, *};
use gpui_component::ActiveTheme;
use std::time::Duration;

fn text(ctx: &ComponentContext, model: &ComponentModel, key: &str) -> String {
    model.get_property::<DynamicString>(key).map(|v|ctx.data_context.resolve_dynamic_string(&v)).unwrap_or_default()
}

pub(super) fn render(walk:&Walk, ctx:&ComponentContext, model:&ComponentModel, window:&mut Window, cx:&mut Context<GpuiApp>) -> AnyElement {
    let id=ctx.component_id.clone();
    match model.component_type.as_str() {
        "CScrollArea" => {
            let handle=walk.scroll_handles.borrow_mut().entry(id.clone()).or_default().clone();
            let fixed=model.get_property::<u32>("height");
            let minimum=model.get_property::<u32>("minHeight").unwrap_or(160).clamp(160,600) as f32;
            let height=if let Some(fixed)=fixed {
                (fixed.clamp(160,1000) as f32).min(f32::from(window.viewport_size().height)*0.60)
            } else {
                walk.scroll_available.borrow().get(&id).copied().unwrap_or(minimum).max(minimum)
            };
            let mut body=div().id(SharedString::from(format!("scroll-{id}"))).w_full().min_w_0().max_h(px(height))
                .overflow_y_scroll().track_scroll(&handle).pr_2();
            if let Some(child)=model.child(){body=body.child(render_node(&child,ctx.data_context.base_path(),walk,window,cx));}
            // Stop at this boundary even when the inner list reaches an edge.
            let mut boundary=div().id(SharedString::from(format!("scroll-boundary-{id}"))).relative().w_full().min_w_0()
                .on_scroll_wheel(|_,_,cx|cx.stop_propagation()).child(body);
            if fixed.is_none() {
                let weak=cx.entity().downgrade();
                boundary=boundary.child(canvas(move|bounds,window,cx|{
                    let _=weak.update(cx,|this,cx|{
                        let viewport=this.workspace_scroll.bounds();
                        let bottom=if viewport.size.height>px(0.) {f32::from(viewport.bottom())} else {f32::from(window.viewport_size().height)};
                        // Use the unscrolled position: scrolling the page must
                        // not resize the list and create a moving scroll target.
                        let top=f32::from(bounds.origin.y-this.workspace_scroll.offset().y);
                        let height=(bottom-top-24.).max(minimum);
                        let mut sizes=this.scroll_available.borrow_mut();
                        if sizes.get(&id).is_none_or(|old|(old-height).abs()>0.5) {sizes.insert(id.clone(),height);cx.notify();}
                    });
                },|_,_,_,_|{}).absolute().top_0().left_0().size_full());
            }
            boundary.into_any_element()
        }
        "CRecordItem" => {
            let selected=text(ctx,model,"recordId")==text(ctx,model,"selectedId");
            let label=text(ctx,model,"label");
            let detail=text(ctx,model,"detail");
            let badge=text(ctx,model,"badge");
            let content=div().w_full().min_w_0().flex().flex_col().gap_2().text_left().whitespace_normal()
                .child(div().flex().flex_wrap().gap_2().child(div().flex_1().min_w_0().text_size(px(14.)).child(label))
                    .when(selected,|s|s.child(div().flex_shrink_0().text_size(px(11.)).text_color(cx.theme().primary).child("Selected"))))
                .when(!detail.is_empty(),|s|s.child(div().text_size(px(12.)).text_color(cx.theme().muted_foreground).child(detail)))
                .when(!badge.is_empty(),|s|s.child(div().text_size(px(11.)).text_color(cx.theme().info).child(badge)));
            let keyboard_id=id.clone();
            div().id(SharedString::from(id.clone()))
                .w_full().min_w_0().p_3().border_1().rounded_lg()
                .border_color(if selected {cx.theme().primary} else {cx.theme().border})
                .bg(if selected {cx.theme().secondary} else {cx.theme().popover})
                .when(!walk.interaction_disabled,|s|s.focusable().cursor_pointer()
                    .hover(|s|s.bg(cx.theme().secondary))
                    .focus(|s|s.border_color(cx.theme().primary))
                    .on_click(cx.listener(move|this,_,_,cx|{this.activate_record(&id);cx.notify();}))
                    .on_key_down(cx.listener(move|this,event:&KeyDownEvent,_,cx|{
                        if matches!(event.keystroke.key.as_str(),"enter"|"space") {
                            this.activate_record(&keyboard_id);cx.stop_propagation();cx.notify();
                        }
                    })))
                .child(content).into_any_element()
        }
        _ => {
            let status=text(ctx,model,"status");
            let revision=text(ctx,model,"revision");
            let (message,color)=if walk.refresh_pending {("Considering this context…",cx.theme().info)}
                else {match status.as_str(){"held"=>("Held · resume to follow context",cx.theme().warning),
                    "unavailable"|"error"=>("Suggestion unavailable · showing fallback",cx.theme().warning),
                    "pending"=>("Waiting for context…",cx.theme().muted_foreground),
                    _=>("Automatic · follows the current context",cx.theme().muted_foreground)}};
            let mut region=div().w_full().min_w_0().flex().flex_col().gap_3()
                .child(div().text_size(px(12.)).text_color(color).child(message));
            if let Some(child)=model.child(){region=region.child(div().w_full().min_w_0()
                .child(render_node(&child,ctx.data_context.base_path(),walk,window,cx))
                .with_animation(SharedString::from(format!("{id}:{revision}")),Animation::new(Duration::from_millis(240)),|el,t|el.opacity(0.45+0.55*t)));}
            region.into_any_element()
        }
    }
}

impl GpuiApp {
    pub(crate) fn activate_record(&mut self,id:&str) {
        if self.interaction_disabled {return;}
        let snapshot=self.probe_snapshot();
        if let Some(node)=snapshot["nodes"].as_array().and_then(|nodes|nodes.iter().find(|n|n["id"]==id && n["component"]=="CRecordItem")) {
            let p=&node["props"];
            self.emit_action(serde_json::json!({"name":p["onSelect"],"context":{"id":p["recordId"]}}));
        }
    }
}
