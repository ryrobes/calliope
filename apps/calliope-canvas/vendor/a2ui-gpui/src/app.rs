//! `GpuiApp` — the GPUI view that owns the surface state and implements the
//! per-frame `Render` loop.
//!
//! This is the GPUI counterpart of the iced [`IcedApp`] and the egui
//! `EguiApp`: it owns the [`MessageProcessor`], the function map, the
//! [`FocusManager`] (a read-only shadow for parity; GPUI native focus drives
//! actual interaction), the gallery samples, the locally-tracked
//! [`open_modals`] set, the remote-image cache, and the per-component-id
//! `Entity<…State>` caches for the stateful widgets (TextField / Slider /
//! ChoicePicker).
//!
//! `render()` draws the same dark, modern chrome as the other galleries: a
//! branded sidebar sample browser + a breadcrumb-topped preview pane, then a
//! dimmed-scrim centered overlay panel for each open Modal (layered as absolute
//! children on top of the root). Widget interactions are `cx.listener` closures
//! that fire after the render borrow is released and mutate the runtime
//! directly — no egui-style collect-then-apply buffer.
//!
//! [`IcedApp`]: a2ui_iced::IcedApp
//! [`open_modals`]: GpuiApp::open_modals

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use a2ui_base::catalog::function_api::FunctionImplementation;
use a2ui_base::components::dispatch_event;
use a2ui_base::event::{InputEvent, InputKey};
use a2ui_base::focus::FocusManager;
use a2ui_base::interaction::apply_event_result;
use a2ui_base::message_processor::MessageProcessor;
use a2ui_base::model::component_context::ComponentContext;
use a2ui_base::protocol::server_to_client::A2uiMessage;

use gpui::prelude::*;
use gpui::{AnyElement, Bounds, Pixels, FocusHandle, KeyDownEvent, ClickEvent, Context, Entity, Image, Render, Subscription, div, px};
use gpui_component::button::{Button, ButtonVariants as _};
#[cfg(not(feature = "gpui-next"))]
use gpui_component::divider::Divider;
#[cfg(feature = "gpui-next")]
use gpui_component::separator::Separator as Divider;
use gpui_component::input::InputState;
use gpui_component::select::{SearchableVec, SelectState};
use gpui_component::slider::SliderState;
use gpui_component::TitleBar;
use gpui_component::{h_flex, v_flex};
use serde_json::Value;

use crate::components::{Walk, ChoiceOption};
use crate::theme;
use crate::walker::render_node;

/// The GPUI app view — owns all runtime state, implements GPUI's `Render` trait
/// (rebuilt each frame).
pub struct GpuiApp {
    /// Let a native host choose the keyboard for the focused field.
    pub on_input_kind: Option<Arc<dyn Fn(&str) + Send + Sync>>,
    pub(crate) last_input_kind: String,
    pub(crate) active_input: Option<String>,
    pub(crate) input_order: RefCell<Vec<String>>,
    pub(crate) rendered_nodes: RefCell<std::collections::HashSet<String>>,
    pub(crate) input_bounds: HashMap<String, Bounds<Pixels>>,
    pub(crate) field_errors: HashMap<String, String>,
    pub(crate) focus_requested: Option<String>,
    pub(crate) field_picker: Option<crate::field_controls::Picker>,
    pub(crate) picker_focus: Option<FocusHandle>,
    /// Host wakeup, independent of GPUI polling and paint frequency.
    pub wake_host: Option<Arc<dyn Fn() + Send + Sync>>,
    data_revision: u64,
    pub chart_renderer: Option<crate::vega::ChartRenderer>,
    pub(crate) chart_views: RefCell<HashMap<String, Entity<crate::vega::VegaChart>>>,
    pub(crate) result_tables: RefCell<HashMap<String, Entity<crate::result_table::ResultTable>>>,
    pub(crate) grid_widths: RefCell<HashMap<String,Vec<f32>>>,
    pub(crate) grid_measured: HashMap<String,Vec<f32>>,
    pub(crate) grid_drag: Option<(String,usize,f32,f32)>,
    pub workspace_display_font: gpui::SharedString,
    embedded: bool,
    /// A read-only fragment inside its host's card; no nested page scroll.
    pub summary_preview: bool,
    pub summary_scale: f32,
    viewport_width: f32,
    /// Height the embedded surface needs to show everything unscrolled, for
    /// hosts that size it to fit (a card in a thread) instead of filling a pane.
    pub content_height: f32,
    /// Padding around an embedded surface; `None` keeps the page default (1.5 rem).
    pub frame_padding: Option<f32>,
    pub interaction_disabled: bool,
    pub refresh_pending: bool,
    pub(crate) scroll_handles: RefCell<HashMap<String,gpui::ScrollHandle>>,
    pub(crate) scroll_available: RefCell<HashMap<String,f32>>,
    pub(crate) stack_widths: RefCell<HashMap<String,f32>>,
    pub(crate) workspace_scroll: gpui::ScrollHandle,
    actions: Vec<Value>,
    pub(crate) processor: MessageProcessor,
    functions: HashMap<String, Box<dyn FunctionImplementation>>,
    focus: FocusManager,
    samples: Vec<(String, Vec<A2uiMessage>)>,
    selected_sample: usize,
    open_modals: HashSet<String>,
    /// Remote-image cache: a resolved URL → its decoded gpui [`Image`] once the
    /// background fetch completes (`None` = attempted but failed, so it isn't
    /// refetched). Cleared on sample switch. Mirrors the iced/egui image caches.
    pub(crate) image_cache: HashMap<String, Option<Arc<Image>>>,
    /// URLs a background fetch is already in flight for (so a not-yet-decoded
    /// image isn't refetched every frame).
    pub(crate) image_pending: RefCell<HashSet<String>>,
    /// Locally-tracked active tab index for Tabs components whose `activeTab`
    /// is **not** a data binding (the gallery samples fall here). Keyed by
    /// component id. Bound Tabs write to the model instead.
    pub(crate) local_tabs: HashMap<String, usize>,
    /// Per-component-id `InputState` for TextField / DateTimeInput.
    #[cfg(feature = "gpui-next")]
    pub(crate) textarea_states: RefCell<HashMap<String, Entity<gpui_component::input::TextareaState>>>,
    pub(crate) input_model_values: RefCell<HashMap<String, String>>,
    pub(crate) text_states: RefCell<HashMap<String, Entity<InputState>>>,
    /// Per-component-id `SliderState` for Slider.
    pub(crate) slider_states: RefCell<HashMap<String, Entity<SliderState>>>,
    /// Per-component-id `SelectState` for single-select ChoicePicker.
    pub(crate) select_states: RefCell<HashMap<String, Entity<SelectState<SearchableVec<ChoiceOption>>>>>,
    /// Keeps every lazy-create subscription alive (dropping a [`Subscription`]
    /// unsubscribes it). Cleared on sample switch alongside the state caches.
    pub(crate) subscriptions: RefCell<HashMap<String, Subscription>>,
}

impl GpuiApp {
    /// Construct with the registered catalogs + the merged function map,
    /// mirroring the iced/egui hosts.
    pub fn new(
        catalogs: Vec<a2ui_base::catalog::Catalog>,
        functions: HashMap<String, Box<dyn FunctionImplementation>>,
    ) -> Self {
        Self {
            on_input_kind: None,
            last_input_kind: String::new(),
            active_input: None,
            input_order: RefCell::new(Vec::new()),
            rendered_nodes: Default::default(),
            input_bounds: HashMap::new(),
            field_errors: HashMap::new(),
            focus_requested: None,
            field_picker: None,
            picker_focus: None,
            wake_host: None,
            data_revision: 0,
            chart_renderer: None,
            grid_widths: RefCell::new(HashMap::new()),
            grid_measured: HashMap::new(),
            grid_drag: None,
            chart_views: RefCell::new(HashMap::new()),
            result_tables: RefCell::new(HashMap::new()),
            workspace_display_font: "LT Superior Serif".into(),
            embedded: false,
            summary_preview: false,
            summary_scale: 1.,
            viewport_width: 0.,
            content_height: 0.,
            frame_padding: None,
            interaction_disabled: false,
            refresh_pending: false,
            scroll_handles: RefCell::new(HashMap::new()),
            scroll_available: RefCell::new(HashMap::new()),
            stack_widths: RefCell::new(HashMap::new()),
            workspace_scroll: gpui::ScrollHandle::new(),
            actions: Vec::new(),
            processor: MessageProcessor::new(catalogs),
            functions,
            focus: FocusManager::new(),
            samples: Vec::new(),
            selected_sample: 0,
            open_modals: HashSet::new(),
            image_cache: HashMap::new(),
            image_pending: RefCell::new(HashSet::new()),
            local_tabs: HashMap::new(),
            input_model_values: RefCell::new(HashMap::new()),
            text_states: RefCell::new(HashMap::new()),
            #[cfg(feature = "gpui-next")]
            textarea_states: RefCell::new(HashMap::new()),
            slider_states: RefCell::new(HashMap::new()),
            select_states: RefCell::new(HashMap::new()),
            subscriptions: RefCell::new(HashMap::new()),
        }
    }

    /// Host-owned navigation can restore scroll positions after a layout pass.
    pub fn view_state(&self) -> Value {
        let mut offsets=serde_json::Map::new();
        for (id,handle) in self.scroll_handles.borrow().iter() {
            offsets.insert(id.clone(),serde_json::json!([f32::from(handle.offset().x),f32::from(handle.offset().y)]));
        }
        serde_json::json!({"scroll":offsets,"page":[f32::from(self.workspace_scroll.offset().x),f32::from(self.workspace_scroll.offset().y)]})
    }
    pub fn restore_view_state(&mut self, state: &Value) {
        let offset=|v:&Value|gpui::point(px(v[0].as_f64().unwrap_or(0.) as f32),px(v[1].as_f64().unwrap_or(0.) as f32));
        self.workspace_scroll.set_offset(offset(&state["page"]));
        for (id,handle) in self.scroll_handles.borrow().iter() {
            handle.set_offset(offset(&state["scroll"][id]));
        }
    }
    pub fn close_overlay(&mut self) -> bool {
        if let Some(picker) = self.field_picker.take() { self.focus_requested=Some(picker.id); return true; }
        if let Some(id)=self.open_modals.iter().max().cloned() { self.open_modals.remove(&id); true } else { false }
    }

    /// Replace the chart renderer (for example after a theme change); existing
    /// charts are rebuilt with it on the next render.
    pub fn set_chart_renderer(&mut self, renderer: Option<crate::vega::ChartRenderer>) {
        self.chart_renderer = renderer;
        self.chart_views.borrow_mut().clear();
    }

    pub fn retry_failed_charts(&mut self, cx: &mut Context<Self>) {
        for chart in self.chart_views.borrow().values() {
            chart.update(cx, |chart, cx| chart.retry(cx));
        }
    }

    /// Embed a surface in a host application, without the upstream sample gallery.
    pub fn load_surface(&mut self, messages: Vec<A2uiMessage>) -> Result<(), String> {
        self.processor.reset();
        for message in messages {
            self.processor.process_message(message).map_err(|e| e.to_string())?;
        }
        // A new app must not inherit another app's failed or pending charts.
        self.chart_views.borrow_mut().clear();
        self.result_tables.borrow_mut().clear();
        self.embedded = true;
        self.actions.clear();
        self.open_modals.clear();
        self.field_picker=None;
        self.field_errors.clear();
        self.input_bounds.clear();
        self.input_order.borrow_mut().clear();
        self.rendered_nodes.borrow_mut().clear();
        self.focus_requested=None;
        self.workspace_scroll.set_offset(gpui::point(px(0.),px(0.)));
        self.scroll_handles.borrow_mut().clear();
        self.scroll_available.borrow_mut().clear();
        self.stack_widths.borrow_mut().clear();
        self.text_states.borrow_mut().clear();
        self.input_model_values.borrow_mut().clear();
        #[cfg(feature = "gpui-next")]
        self.textarea_states.borrow_mut().clear();
        self.select_states.borrow_mut().clear();
        self.slider_states.borrow_mut().clear();
        self.subscriptions.borrow_mut().clear();
        self.data_changed();
        Ok(())
    }

    /// Reconcile a complete server snapshot without replacing unchanged input entities.
    pub fn reconcile_surface(&mut self, messages: Vec<A2uiMessage>) -> Result<(), String> {
        use a2ui_base::protocol::server_to_client::A2uiPayload;
        let mut ids = HashSet::new();
        let mut changed = HashSet::new();
        for mut message in messages {
            match &mut message.payload {
                A2uiPayload::CreateSurface(_) => continue,
                A2uiPayload::UpdateComponents(payload) => {
                    let old = self.processor.model.surfaces().next().map(|s|s.components.borrow().all().clone()).unwrap_or_default();
                    payload.update_components.components.retain(|node| {
                        let id=node["id"].as_str().unwrap_or("").to_string(); ids.insert(id.clone());
                        let equal=old.get(&id).is_some_and(|c| c.component_type==node["component"].as_str().unwrap_or("") && c.properties==node.as_object().map(|o|o.iter().filter(|(k,_)|k.as_str()!="id" && k.as_str()!="component").map(|(k,v)|(k.clone(),v.clone())).collect::<serde_json::Map<String,Value>>()).unwrap_or_default());
                        if !equal { changed.insert(id); } !equal
                    });
                },
                _ => {},
            }
            self.processor.process_message(message).map_err(|e|e.to_string())?;
        }
        for surface in self.processor.model.surfaces_mut() {
            let removed:Vec<_>=surface.components.borrow().all().keys().filter(|id|!ids.contains(*id)).cloned().collect();
            for id in removed {surface.components.borrow_mut().remove(&id);}
        }
        // Text bindings are stable within a record; changed pickers may have a new option set.
        self.text_states.borrow_mut().retain(|id,_|ids.contains(id));
        self.input_model_values.borrow_mut().retain(|id,_|ids.contains(id));
        #[cfg(feature = "gpui-next")]
        self.textarea_states.borrow_mut().retain(|id,_|ids.contains(id));
        self.select_states.borrow_mut().retain(|id,_|ids.contains(id) && !changed.contains(id));
        self.slider_states.borrow_mut().retain(|id,_|ids.contains(id));
        self.subscriptions.borrow_mut().retain(|id,_|ids.contains(id));
        self.chart_views.borrow_mut().retain(|id,_|ids.contains(id));
        self.result_tables.borrow_mut().retain(|id,_|ids.contains(id));
        self.scroll_handles.borrow_mut().retain(|id,_|ids.contains(id));
        self.scroll_available.borrow_mut().retain(|id,_|ids.contains(id));
        self.stack_widths.borrow_mut().retain(|id,_|ids.contains(id));
        self.data_changed();
        Ok(())
    }
    pub fn emit_action(&mut self, action: Value) {
        if !self.interaction_disabled {
            self.actions.push(action);
            if let Some(wake) = &self.wake_host { wake(); }
        }
    }
    pub fn data_revision(&self) -> u64 { self.data_revision }
    fn data_changed(&mut self) {
        self.data_revision = self.data_revision.wrapping_add(1);
        if let Some(wake) = &self.wake_host { wake(); }
    }
    pub fn validate_fields(&mut self) -> bool {
        let snapshot=self.probe_snapshot();
        self.field_errors.clear();
        let mut first=None;
        for id in self.input_order.borrow().iter() {
            let Some(node)=snapshot["nodes"].as_array().and_then(|nodes|nodes.iter().find(|n|n["id"]==*id && n["component"]=="CField")) else {continue};
            let props=&node["props"];
            let error=crate::fields::field_error(props["value"].as_str().unwrap_or(""),props);
            if !error.is_empty() {
                if first.is_none(){first=Some(id.clone());}
                self.field_errors.insert(id.clone(),error);
            }
        }
        self.focus_requested=first;
        self.field_errors.is_empty()
    }

    pub fn has_field_errors(&self) -> bool { !self.field_errors.is_empty() }

    pub fn data_value(&self, path: &str) -> Value {
        self.processor.model.surfaces().next()
            .and_then(|s| s.data_model.borrow().get(path).cloned()).unwrap_or(Value::Null)
    }

    pub fn take_actions(&mut self) -> Vec<Value> { std::mem::take(&mut self.actions) }

    /// Populate the sample browser with `(name, messages)` pairs and load the
    /// sample at `initial`. Pressing a sidebar entry switches samples live.
    pub fn set_samples(&mut self, samples: Vec<(String, Vec<A2uiMessage>)>, initial: usize) {
        self.samples = samples;
        self.load_sample(initial);
    }

    /// Load sample `idx`: reset the processor (keeping catalogs), replay its
    /// messages, refresh focus, clear modals + caches. No-op if out of range.
    fn load_sample(&mut self, idx: usize) {
        let Some(messages) = self.samples.get(idx).map(|(_, m)| m.clone()) else {
            return;
        };
        self.processor.reset();
        for msg in &messages {
            let _ = self.processor.process_message(msg.clone());
        }
        self.focus.reset();
        if let Some(surface) = self.processor.model.surfaces().next() {
            let components = surface.components.borrow();
            self.focus.rebuild_from_components(&components);
        }
        self.open_modals.clear();
        // Drop the previous sample's decoded images + widget state so caches
        // don't grow unbounded / leak across many sample switches.
        self.image_cache.clear();
        self.image_pending.borrow_mut().clear();
        self.local_tabs.clear();
        self.text_states.borrow_mut().clear();
        self.input_model_values.borrow_mut().clear();
        #[cfg(feature = "gpui-next")]
        self.textarea_states.borrow_mut().clear();
        self.slider_states.borrow_mut().clear();
        self.select_states.borrow_mut().clear();
        self.subscriptions.borrow_mut().clear();
        self.selected_sample = idx;
    }

    /// Write a value to the current surface's data model at absolute JSON
    /// Pointer `path` (empty path is an unbound widget's no-op write-back).
    pub fn set_data_value(&mut self, path: &str, value: Value) {
        if path.is_empty() {
            return;
        }
        let changed = if let Some(surface) = self.processor.model.surfaces_mut().next() {
            let mut data = surface.data_model.borrow_mut();
            if data.get(path) == Some(&value) { false }
            else { data.set(path, value); true }
        } else { false };
        if changed {
            if !self.field_errors.is_empty() {
                let snapshot = self.probe_snapshot();
                self.field_errors.retain(|id, error| {
                    let Some(node) = snapshot["nodes"].as_array()
                        .and_then(|nodes| nodes.iter().find(|n| n["id"] == *id)) else { return false };
                    let props = &node["props"];
                    *error = crate::fields::field_error(props["value"].as_str().unwrap_or(""), props);
                    !error.is_empty()
                });
            }
            self.data_changed();
        }
    }

    // -----------------------------------------------------------------------
    // Render
    // -----------------------------------------------------------------------

    /// Build the current UI: a branded sidebar + the breadcrumb-topped preview
    /// pane, with any open Modals layered on top as absolute overlays (each
    /// behind a dimmed click-to-dismiss scrim).
    fn render_sidebar(&mut self, cx: &mut Context<Self>) -> AnyElement {
        // ── Brand header ───────────────────────────────────────────────────
        let mark = div()
            .text_color(theme::ACCENT)
            .text_size(px(18.))
            .child("◆");
        let title = v_flex()
            .child(div().text_color(theme::TEXT).text_size(px(15.)).child("A2UI"))
            .child(div().text_color(theme::SUBTEXT1).text_size(px(11.)).child("GPUI Gallery"));
        let brand = h_flex().gap_2p5().items_center().w_full().child(mark).child(title);

        // ── Section label ──────────────────────────────────────────────────
        let section = div().text_color(theme::SUBTEXT1).text_size(px(10.)).child("SAMPLES");

        // ── Sample rows ────────────────────────────────────────────────────
        let mut list = v_flex().gap_1();
        for (i, (name, _)) in self.samples.iter().enumerate() {
            let is_sel = i == self.selected_sample;
            let idx_color = if is_sel { theme::ACCENT } else { theme::SUBTEXT1 };
            let name_color = if is_sel { theme::TEXT } else { theme::SUBTEXT0 };
            let mut row = h_flex()
                .gap_2p5()
                .items_center()
                .w_full()
                .px_3()
                .py_2()
                .rounded_md()
                .id(gpui::SharedString::from(format!("sample-{i}")))
                .on_click(cx.listener(move |this, _: &ClickEvent, _window, cx| {
                    this.load_sample(i);
                    cx.notify();
                }))
                .child(
                    div()
                        .text_color(idx_color)
                        .text_size(px(11.))
                        .w(px(20.))
                        .child(format!("{:>2}", i + 1)),
                )
                .child(div().text_color(name_color).text_size(px(13.)).child(name.clone()));
            if is_sel {
                row = row.bg(theme::ACCENT_WASH);
            }
            list = list.child(row);
        }

        // ── Footer ─────────────────────────────────────────────────────────
        let footer = div()
            .text_color(theme::SUBTEXT1)
            .text_size(px(10.))
            .child(format!("{} samples", self.samples.len()));

        v_flex()
            .size_full()
            .bg(theme::MANTLE)
            .p_4()
            .gap_3()
            .w(px(248.))
            .child(brand)
            .child(Divider::horizontal().color(theme::LINE))
            .child(section)
            .child(div().id("sample-list").flex_1().overflow_y_scroll().child(list))
            .child(Divider::horizontal().color(theme::LINE))
            .child(footer)
            .into_any_element()
    }

    /// The main pane — a breadcrumb top bar over the rendered preview surface.
    fn render_main(&mut self, window: &mut gpui::Window, cx: &mut Context<Self>) -> AnyElement {
        let (sel, count) = (self.selected_sample, self.samples.len());
        let name = self
            .samples
            .get(sel)
            .map(|(n, _)| n.clone())
            .unwrap_or_default();

        let crumb = div().text_color(theme::SUBTEXT1).text_size(px(12.)).child("Preview");
        let sep = div().text_color(theme::SUBTEXT1).text_size(px(12.)).child("›");
        let title = div().text_color(theme::TEXT).text_size(px(14.)).child(name);
        let chip = div()
            .bg(theme::ACCENT_WASH)
            .rounded_md()
            .px_2()
            .py_1()
            .text_color(theme::ACCENT)
            .text_size(px(11.))
            .child(format!("{} / {count}", sel + 1));

        let bar = h_flex()
            .w_full()
            .items_center()
            .gap_2()
            .child(crumb)
            .child(sep)
            .child(title)
            .child(div().flex_1())
            .child(chip);
        let top_bar = div().w_full().px_5().py_3p5().bg(theme::MANTLE).child(bar);

        let preview = div()
            .id("preview")
            .flex_1()
            .size_full()
            .overflow_y_scroll()
            .p_6()
            .child(self.render_tree("root", window, cx));

        v_flex()
            .flex_1()
            .size_full()
            .bg(theme::BASE)
            .child(top_bar)
            .child(Divider::horizontal().color(theme::LINE))
            .child(preview)
            .into_any_element()
    }

    /// Walk a component subtree into an element tree. Returns a muted
    /// placeholder when the surface / root is missing.
    fn render_tree(
        &self,
        root_id: &str,
        window: &mut gpui::Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let Some(surface) = self.processor.model.surfaces().next() else {
            return div().text_color(theme::SUBTEXT1).child("No surface loaded.").into_any_element();
        };
        if !surface.components.borrow().contains(root_id) {
            return div().text_color(theme::SUBTEXT1).child("No root component").into_any_element();
        }

        let data_model = surface.data_model.borrow();
        let components = surface.components.borrow();
        let focused_id = self.focus.focused_id().map(str::to_string);
        let walk = Walk {
            presentation_scale: if self.summary_preview {self.summary_scale} else {1.},
            interaction_disabled: self.interaction_disabled,
            refresh_pending: self.refresh_pending,
            scroll_handles: &self.scroll_handles,
            scroll_available: &self.scroll_available,
            stack_widths: &self.stack_widths,
            workspace_display_font: &self.workspace_display_font,
            chart_renderer: &self.chart_renderer,
            chart_views: &self.chart_views,
            result_tables: &self.result_tables,
            surface_id: &surface.id,
            data_model: &data_model,
            components: &components,
            functions: &self.functions,
            focused_id: focused_id.as_deref(),
            image_cache: &self.image_cache,
            image_pending: &self.image_pending,
            local_tabs: &self.local_tabs,
            text_states: &self.text_states,
            input_order: &self.input_order,
            rendered_nodes: &self.rendered_nodes,
            field_errors: &self.field_errors,
            input_model_values: &self.input_model_values,
            #[cfg(feature = "gpui-next")]
            textarea_states: &self.textarea_states,
            slider_states: &self.slider_states,
            select_states: &self.select_states,
            subscriptions: &self.subscriptions,
            grid_widths: &self.grid_widths,
        };
        render_node(root_id, "", &walk, window, cx)
    }

    /// One open Modal's `content` subtree in a centered elevated panel with a
    /// title row + close button, layered over a dimmed click-to-dismiss scrim
    /// (built as a sibling absolute layer behind the panel). Returns `None` if
    /// the modal / its content is missing.
    fn render_modal_overlay(
        &mut self,
        modal_id: &str,
        window: &mut gpui::Window,
        cx: &mut Context<Self>,
    ) -> Option<AnyElement> {
        let surface = self.processor.model.surfaces().next()?;

        // Resolve the modal's content id + optional title in one borrow.
        let (content_id, title): (Option<String>, String) = {
            let components = surface.components.borrow();
            let m = components.get(modal_id)?;
            if m.component_type != "Modal" {
                return None;
            }
            let content = m.get_property::<String>("content");
            let title = m
                .get_property::<String>("title")
                .unwrap_or_else(|| "Dialog".to_string());
            (content, title)
        };
        let content_id = content_id?;

        let content_tree = {
            let data_model = surface.data_model.borrow();
            let components = surface.components.borrow();
            let focused_id = self.focus.focused_id().map(str::to_string);
            let walk = Walk {
            presentation_scale: if self.summary_preview {self.summary_scale} else {1.},
                interaction_disabled: self.interaction_disabled,
            refresh_pending: self.refresh_pending,
            scroll_handles: &self.scroll_handles,
            scroll_available: &self.scroll_available,
            stack_widths: &self.stack_widths,
                workspace_display_font: &self.workspace_display_font,
            chart_renderer: &self.chart_renderer,
            chart_views: &self.chart_views,
            result_tables: &self.result_tables,
                surface_id: &surface.id,
                data_model: &data_model,
                components: &components,
                functions: &self.functions,
                focused_id: focused_id.as_deref(),
                image_cache: &self.image_cache,
                image_pending: &self.image_pending,
                local_tabs: &self.local_tabs,
                text_states: &self.text_states,
            input_order: &self.input_order,
            rendered_nodes: &self.rendered_nodes,
            field_errors: &self.field_errors,
            input_model_values: &self.input_model_values,
            #[cfg(feature = "gpui-next")]
            textarea_states: &self.textarea_states,
                slider_states: &self.slider_states,
                select_states: &self.select_states,
                subscriptions: &self.subscriptions,
            grid_widths: &self.grid_widths,
            };
            render_node(&content_id, "", &walk, window, cx)
        };

        // ── Panel chrome ───────────────────────────────────────────────────
        let close_id = gpui::SharedString::from(format!("modal-close-{modal_id}"));
        let close = Button::new(close_id).ghost().label("✕")
            .on_click(cx.listener({
                let mid = modal_id.to_string();
                move |this, _: &ClickEvent, _window, cx| {
                    this.open_modals.remove(&mid);
                    cx.notify();
                }
            }));
        let title_row = h_flex()
            .w_full()
            .items_center()
            .child(div().text_color(theme::TEXT).text_size(px(14.)).child(title))
            .child(div().flex_1())
            .child(close);
        let panel_body = v_flex().gap_3p5().w_full().child(title_row).child(content_tree);
        let panel = div()
            .bg(theme::MANTLE)
            .border_1()
            .border_color(theme::EDGE)
            .rounded_lg()
            .p_6()
            .w(px(480.))
            .child(panel_body);

        // Scrim fills the viewport behind the panel; clicking it dismisses.
        let backdrop_id = gpui::SharedString::from(format!("modal-backdrop-{modal_id}"));
        let backdrop = div()
            .absolute()
            .size_full()
            .bg(theme::SCRIM)
            .id(backdrop_id)
            .on_click(cx.listener({
                let mid = modal_id.to_string();
                move |this, _: &ClickEvent, _window, cx| {
                    this.open_modals.remove(&mid);
                    cx.notify();
                }
            }));

        Some(
            div()
                .absolute()
                .size_full()
                .flex()
                .items_center()
                .justify_center()
                .child(backdrop)
                .child(div().relative().child(panel))
                .into_any_element(),
        )
    }

    // -----------------------------------------------------------------------
    // Activation (Button press → action / Modal open-close)
    // -----------------------------------------------------------------------

    /// A node was activated (button press): dispatch `Enter` via the shared core
    /// logic, apply the result, then resolve any local Modal state change.
    /// Ported from `crates/iced/src/app.rs::handle_activate`.
    pub(crate) fn handle_activate(&mut self, node_id: &str) {
        if self.interaction_disabled { return; }
        let result = {
            let surface = match self.processor.model.surfaces().next() {
                Some(s) => s,
                None => return,
            };
            let comp_type = match surface.components.borrow().get(node_id) {
                Some(m) => m.component_type.clone(),
                None => return,
            };
            let data_model = surface.data_model.borrow();
            let components = surface.components.borrow();
            let ctx = ComponentContext::new(
                node_id.to_string(),
                surface.id.clone(),
                &data_model,
                &components,
                &self.functions,
                "",
                Some(node_id.to_string()),
            );
            dispatch_event(
                &comp_type,
                &ctx,
                &InputEvent::KeyPress {
                    key: InputKey::Enter,
                },
            )
        };

        if let Some(result) = result {
            if let a2ui_base::event::EventResult::Action { event_name, context, .. } = &result {
                self.emit_action(serde_json::json!({"name": event_name, "context": context}));
            }
            let _ = apply_event_result(&mut self.processor, result);
        }
        self.apply_modal_interaction(node_id);
    }

    /// Resolve a node activation into a local Modal state change. Activating a
    /// component that is some Modal's `trigger` opens that Modal; activating a
    /// Modal node directly toggles it closed. Ported from the iced/egui hosts.
    fn apply_modal_interaction(&mut self, node_id: &str) {
        let modal_id = {
            let Some(surface) = self.processor.model.surfaces().next() else {
                return;
            };
            let components = surface.components.borrow();
            let is_modal = components
                .get(node_id)
                .map(|m| m.component_type == "Modal")
                .unwrap_or(false);
            if is_modal {
                if self.open_modals.insert(node_id.to_string()) {
                    return; // was closed → now open
                }
                Some(node_id.to_string()) // was open → close
            } else {
                components.all().iter().find_map(|(id, m)| {
                    (m.component_type == "Modal"
                        && m.get_property::<String>("trigger").as_deref() == Some(node_id))
                    .then(|| id.clone())
                })
            }
        };

        match modal_id {
            Some(id) if id == node_id => {
                self.open_modals.remove(&id);
            }
            Some(id) => {
                self.open_modals.insert(id);
            }
            None => {}
        }
    }
}

impl Render for GpuiApp {
    fn render(&mut self, window: &mut gpui::Window, cx: &mut Context<Self>) -> impl IntoElement {
        self.sync_input_focus(window, cx);
        self.input_order.borrow_mut().clear();
        self.rendered_nodes.borrow_mut().clear();
        if self.summary_preview {
            return div().size_full().overflow_hidden().p(gpui::px(16.*self.summary_scale)).child(self.render_tree("root", window, cx)).into_any_element();
        }
        if self.embedded {
            let narrow = f32::from(window.viewport_size().width)<1000.;
            let pane = self.processor.model.surfaces().next().is_some_and(|s|s.components.borrow().contains("record-pane"));
            // Resolve the scroll body's width before laying out percentage-based
            // A2UI containers. An indefinite block width can otherwise measure a
            // plain text-led root at zero and wrap it one character per line.
            let weak=cx.entity().downgrade();
            let measure=gpui::canvas(move |bounds,_,cx| {
                let width=f32::from(bounds.size.width);
                let _=weak.update(cx,|this,cx| { if (this.viewport_width-width).abs()>0.5 {this.viewport_width=width;cx.notify();} });
            },|_,_,_,_|{}).absolute().top_0().left_0().size_full();
            let width=if self.viewport_width>0. {self.viewport_width} else {f32::from(window.viewport_size().width)};
            let pad=self.frame_padding.unwrap_or(1.5*f32::from(window.rem_size()));
            let weak=cx.entity().downgrade();
            let fit=gpui::canvas(move |bounds,_,cx| {
                // Content plus the scroll body's padding: the whole surface, unscrolled.
                let height=f32::from(bounds.size.height)+2.*pad;
                let _=weak.update(cx,|this,cx| { if (this.content_height-height).abs()>0.5 {this.content_height=height;cx.notify();} });
            },|_,_,_,_|{}).absolute().top_0().left_0().size_full();
            let content_width=(width-2.*pad).max(1.);
            let mut root=div().id("workspace-viewport").relative().size_full().flex()
                .capture_action(cx.listener(|this,_:&gpui_component::input::IndentInline,window,cx|{this.advance_focus(false,window,cx);cx.stop_propagation();}))
                .capture_action(cx.listener(|this,_:&gpui_component::input::OutdentInline,window,cx|{this.advance_focus(true,window,cx);cx.stop_propagation();}))
                .capture_key_down(cx.listener(|this,event:&KeyDownEvent,window,cx| {
                    if event.keystroke.key=="escape" && this.close_overlay(){cx.stop_propagation();cx.notify();}
                    else if event.keystroke.key=="tab" {this.advance_focus(event.keystroke.modifiers.shift,window,cx);cx.stop_propagation();cx.notify();}
                }))
                .on_mouse_move(cx.listener(|this,e:&gpui::MouseMoveEvent,_,cx|{if let Some((id,i,x,w))=&this.grid_drag {if let Some(widths)=this.grid_widths.borrow_mut().get_mut(id){widths[*i]=(*w+f32::from(e.position.x)-*x).clamp(80.,600.);}cx.notify();}}))
                .on_mouse_up(gpui::MouseButton::Left,cx.listener(|this,_,_,cx|{this.grid_drag=None;cx.notify();}))
                .on_mouse_up_out(gpui::MouseButton::Left,cx.listener(|this,_,_,cx|{this.grid_drag=None;cx.notify();}))
                .child(div().id("business-surface").relative().flex_1().min_w_0().h_full().overflow_y_scroll().track_scroll(&self.workspace_scroll).p(px(pad)).child(measure).child(div().w(px(content_width)).relative().child(self.render_tree("root",window,cx)).child(fit)));
            if pane {
                let inspector = self.render_tree("record-pane",window,cx);
                if narrow {
                    root=root.child(div().absolute().inset_0().bg(gpui::rgba(0x00000060)).child(div().id("record-sheet-scroll").absolute().right_3().top_3().bottom_3().w(px(360.)).max_w_full().overflow_y_scroll().child(inspector)));
                } else {
                    root=root.child(div().id("record-pane-scroll").w(px(364.)).flex_shrink_0().h_full().overflow_y_scroll().pt_6().pr_6().pb_6().child(inspector));
                }
            }
            if let Some(picker)=self.render_field_picker(window,cx){root=root.child(picker);}
            return root.into_any_element();
        }
        // Deterministic overlay order: iterate modals sorted by id. Collected
        // up front so each overlay is built as a discrete sequential borrow.
        let mut modal_ids: Vec<String> = self.open_modals.iter().cloned().collect();
        modal_ids.sort();

        let sidebar = self.render_sidebar(cx);
        let main = self.render_main(window, cx);
        let mut root = h_flex()
            .flex_1()
            .min_h_0()
            .w_full()
            .bg(theme::CRUST)
            .child(sidebar)
            .child(main);
        for modal_id in modal_ids {
            if let Some(overlay) = self.render_modal_overlay(&modal_id, window, cx) {
                root = root.child(overlay);
            }
        }
        // The `TitleBar` is the window's client-side decoration: a draggable
        // strip (move + double-click-to-maximize) carrying the OS window
        // controls (min / max / close) on Linux CSD. Without it the window
        // has no titlebar at all under GNOME Wayland (which ignores
        // server-side decoration requests).
        v_flex()
            .size_full()
            .child(
                TitleBar::new().child(
                    div()
                        .text_color(theme::SUBTEXT1)
                        .text_size(px(12.))
                        .child("A2UI · GPUI Gallery"),
                ),
            )
            .child(root)
            .into_any_element()
    }
}

#[cfg(test)]
mod host_tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn embedded_actions_resolve_latest_draft_and_loading_replaces_state() {
        let mut app = GpuiApp::new(vec![], HashMap::new());
        let wakes = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let count = wakes.clone();
        app.wake_host = Some(Arc::new(move || { count.fetch_add(1, std::sync::atomic::Ordering::SeqCst); }));
        let messages = |notes: &str| serde_json::from_value(json!([
            {"version":"v0.9","createSurface":{"surfaceId":"desk","catalogId":"basic"}},
            {"version":"v0.9","updateComponents":{"surfaceId":"desk","components":[
                {"id":"root","component":"Column","children":["save"]},
                {"id":"save","component":"Button","action":{"event":{"name":"save_deal","context":{"notes":{"path":"/draft/notes"},"version":{"path":"/draft/version"}}}}}
            ]}},
            {"version":"v0.9","updateDataModel":{"surfaceId":"desk","path":"/","value":{"draft":{"notes":notes,"version":2}}}}
        ])).unwrap();
        app.load_surface(messages("initial")).unwrap();
        let revision = app.data_revision();
        let before = wakes.load(std::sync::atomic::Ordering::SeqCst);
        app.set_data_value("/draft/notes", json!("initial"));
        assert_eq!(app.data_revision(), revision);
        assert_eq!(wakes.load(std::sync::atomic::Ordering::SeqCst), before);
        app.set_data_value("/draft/notes", json!("edited"));
        assert!(app.data_revision() > revision);
        assert!(wakes.load(std::sync::atomic::Ordering::SeqCst) > before);
        let before = wakes.load(std::sync::atomic::Ordering::SeqCst);
        app.handle_activate("save");
        assert!(wakes.load(std::sync::atomic::Ordering::SeqCst) > before);
        assert_eq!(app.take_actions(), vec![json!({"name":"save_deal","context":{"notes":"edited","version":2}})]);
        app.interaction_disabled = true;
        app.handle_activate("save");
        assert!(app.take_actions().is_empty());
        app.load_surface(messages("another record")).unwrap();
        assert_eq!(app.data_value("/draft/notes"),json!("another record"));
    }
}
