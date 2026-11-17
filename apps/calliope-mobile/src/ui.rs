//! App-style touch controls for the phone screens: icon buttons, pills, a
//! segmented switch, the bottom tab bar and full-width actions. Every
//! control keeps a 44 px hit area even when its visible shape is smaller.
use crate::theme::CanvasTheme;
use gpui::{prelude::*, *};
use gpui_component::Icon;
pub use gpui_kit::assets::IconName as Glyph;

pub type OnClick = Box<dyn Fn(&ClickEvent, &mut Window, &mut App) + 'static>;

fn press(target: Stateful<Div>, enabled: bool, hover: Rgba, on_click: OnClick) -> Stateful<Div> {
    if enabled {
        target
            .cursor_pointer()
            .active(move |s| s.bg(hover))
            .on_click(on_click)
    } else {
        target.opacity(0.4)
    }
}

/// A round, borderless icon control (back, compose, refresh, copy…).
pub fn icon_button(
    id: impl Into<ElementId>,
    icon: Glyph,
    t: &CanvasTheme,
    enabled: bool,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Stateful<Div> {
    tinted_icon_button(id, icon, t.text, t, enabled, on_click)
}

pub fn tinted_icon_button(
    id: impl Into<ElementId>,
    icon: Glyph,
    color: Rgba,
    t: &CanvasTheme,
    enabled: bool,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Stateful<Div> {
    let target = div()
        .id(id)
        .size(px(44.))
        .flex_shrink_0()
        .rounded_full()
        .flex()
        .items_center()
        .justify_center()
        .child(Icon::new(icon).size(px(20.)).text_color(color));
    press(target, enabled, t.hover, Box::new(on_click))
}

/// The filled circle that sends (or stops) a message.
pub fn round_action(
    id: impl Into<ElementId>,
    icon: Glyph,
    t: &CanvasTheme,
    enabled: bool,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Stateful<Div> {
    let (bg, fg) = if enabled { (t.accent, t.on_accent) } else { (t.hover, t.muted) };
    let target = div()
        .id(id)
        .size(px(44.))
        .flex_shrink_0()
        .rounded_full()
        .bg(bg)
        .flex()
        .items_center()
        .justify_center()
        .child(Icon::new(icon).size(px(20.)).text_color(fg));
    if enabled {
        target.cursor_pointer().active(|s| s.opacity(0.8)).on_click(on_click)
    } else {
        target
    }
}

#[derive(Clone, Copy, PartialEq)]
pub enum Tone {
    /// Filled with the accent: the screen's one main action.
    Primary,
    /// Soft accent fill: a secondary action that still reads as a button.
    Tonal,
    /// Hairline outline.
    Outline,
    /// Accent text only, for low-emphasis actions.
    Quiet,
}

/// A pill-shaped button with an optional leading icon.
pub fn action(
    id: impl Into<ElementId>,
    icon: Option<Glyph>,
    label: impl Into<SharedString>,
    tone: Tone,
    t: &CanvasTheme,
    enabled: bool,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Stateful<Div> {
    let (bg, fg, border) = match tone {
        Tone::Primary => (Some(t.accent), t.on_accent, None),
        Tone::Tonal => (Some(t.accent_soft), t.accent, None),
        Tone::Outline => (None, t.text, Some(t.line)),
        Tone::Quiet => (None, t.accent, None),
    };
    let target = div()
        .id(id)
        .h(px(44.))
        .px(px(if tone == Tone::Quiet { 12. } else { 18. }))
        .flex_shrink_0()
        .rounded_full()
        .flex()
        .items_center()
        .justify_center()
        .gap_2()
        .text_size(px(14.))
        .font_weight(FontWeight::MEDIUM)
        .text_color(fg)
        .when_some(bg, |s, bg| s.bg(bg))
        .when_some(border, |s, line| s.border_1().border_color(line))
        .when_some(icon, |s, icon| s.child(Icon::new(icon).size(px(18.)).text_color(fg)))
        .child(div().min_w_0().truncate().child(label.into()));
    press(target, enabled, t.hover, Box::new(on_click))
}

/// A selectable chip (filters, Team, quick jumps). The visible pill is 34 px
/// tall inside a 44 px hit row.
pub fn chip(
    id: impl Into<ElementId>,
    icon: Option<Glyph>,
    label: impl Into<SharedString>,
    count: Option<usize>,
    selected: bool,
    t: &CanvasTheme,
    enabled: bool,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Stateful<Div> {
    let fg = if selected { t.accent } else { t.text };
    let pill = div()
        .h(px(34.))
        .px_3()
        .min_w_0()
        .rounded_full()
        .flex()
        .items_center()
        .gap_1p5()
        .border_1()
        .border_color(if selected { t.accent } else { t.line })
        .when(selected, |s| s.bg(t.accent_soft))
        .text_size(px(13.))
        .text_color(fg)
        .when_some(icon, |s, icon| s.child(Icon::new(icon).size(px(15.)).text_color(fg)))
        .child(div().min_w_0().truncate().child(label.into()))
        .when_some(count, |s, n| {
            s.child(div().text_size(px(12.)).text_color(if selected { t.accent } else { t.muted }).child(n.to_string()))
        });
    let target = div().id(id).h(px(44.)).min_w_0().flex_shrink_0().flex().items_center().child(pill);
    if enabled {
        let hover = t.hover;
        target
            .cursor_pointer()
            .active(move |s| s.opacity(0.75).bg(hover))
            .on_click(on_click)
    } else {
        target.opacity(0.4)
    }
}

/// Two or three mutually exclusive views in one rounded track.
pub fn segmented(t: &CanvasTheme) -> Div {
    div()
        .h(px(44.))
        .p(px(3.))
        .min_w_0()
        .rounded_full()
        .bg(t.panel)
        .border_1()
        .border_color(t.line)
        .flex()
        .items_center()
        .gap(px(2.))
}

pub fn segment(
    id: impl Into<ElementId>,
    label: impl Into<SharedString>,
    badge: Option<usize>,
    selected: bool,
    t: &CanvasTheme,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Stateful<Div> {
    div()
        .id(id)
        .flex_1()
        .min_w_0()
        .h_full()
        .px_3()
        .rounded_full()
        .flex()
        .items_center()
        .justify_center()
        .gap_1p5()
        .text_size(px(14.))
        .when(selected, |s| s.bg(t.accent_soft).font_weight(FontWeight::SEMIBOLD).text_color(t.accent))
        .when(!selected, |s| s.text_color(t.muted))
        .child(label.into())
        .when_some(badge.filter(|n| *n > 0), |s, n| {
            s.child(
                div()
                    .h(px(18.))
                    .min_w(px(18.))
                    .px_1()
                    .rounded_full()
                    .bg(if selected { t.accent } else { t.hover })
                    .text_color(if selected { t.on_accent } else { t.muted })
                    .text_size(px(11.))
                    .font_weight(FontWeight::SEMIBOLD)
                    .flex()
                    .items_center()
                    .justify_center()
                    .child(n.to_string()),
            )
        })
        .cursor_pointer()
        .on_click(on_click)
}

/// One destination in the bottom tab bar: icon in an indicator pill, label below.
pub fn nav_tab(
    id: impl Into<ElementId>,
    icon: Glyph,
    label: impl Into<SharedString>,
    selected: bool,
    t: &CanvasTheme,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Stateful<Div> {
    div()
        .id(id)
        .flex_1()
        .h(px(60.))
        .flex()
        .flex_col()
        .items_center()
        .justify_center()
        .gap_1()
        .child(
            div()
                .w(px(58.))
                .h(px(30.))
                .rounded_full()
                .flex()
                .items_center()
                .justify_center()
                .when(selected, |s| s.bg(t.accent_soft))
                .child(Icon::new(icon).size(px(21.)).text_color(if selected { t.accent } else { t.muted })),
        )
        .child(
            div()
                .text_size(px(12.))
                .text_color(if selected { t.text } else { t.muted })
                .when(selected, |s| s.font_weight(FontWeight::SEMIBOLD))
                .child(label.into()),
        )
        .cursor_pointer()
        .on_click(on_click)
}

/// A tappable settings-style row: icon, title, optional detail, chevron.
pub fn list_row(
    id: impl Into<ElementId>,
    icon: Glyph,
    title: impl Into<SharedString>,
    detail: Option<SharedString>,
    t: &CanvasTheme,
    enabled: bool,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> Stateful<Div> {
    let target = div()
        .id(id)
        .min_h(px(56.))
        .px_4()
        .py_2()
        .flex()
        .items_center()
        .gap_3()
        .child(
            div()
                .size(px(36.))
                .flex_shrink_0()
                .rounded_full()
                .bg(t.accent_soft)
                .flex()
                .items_center()
                .justify_center()
                .child(Icon::new(icon).size(px(18.)).text_color(t.accent)),
        )
        .child(
            div()
                .flex_1()
                .min_w_0()
                .flex()
                .flex_col()
                .child(div().text_size(px(15.)).child(title.into()))
                .when_some(detail, |s, d| s.child(div().text_size(px(12.)).text_color(t.muted).child(d))),
        )
        .child(Icon::new(Glyph::ChevronRight).size(px(18.)).text_color(t.muted));
    press(target, enabled, t.hover, Box::new(on_click))
}

/// Small uppercase caption above a group.
pub fn caption(text: impl Into<SharedString>, t: &CanvasTheme) -> Div {
    div()
        .text_size(px(12.))
        .font_weight(FontWeight::MEDIUM)
        .text_color(t.muted)
        .child(text.into())
}

pub fn glyph(icon: Glyph, size: f32, color: Rgba) -> Icon {
    Icon::new(icon).size(px(size)).text_color(color)
}
