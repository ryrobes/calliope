//! A measured, asynchronous chart image with at most one conversion in flight.
//! Rendering policy and the converter belong to the host, not the A2UI runtime.
use gpui::{prelude::*, *};
use gpui_component::ActiveTheme;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::rc::Rc;
use std::sync::Arc;
pub type ChartRenderer = Arc<dyn Fn(Value, u32, u32) -> Result<ChartOutput, String> + Send + Sync>;
pub struct ChartOutput {
    pub png: Vec<u8>,
    pub scene: ChartScene,
}
#[derive(Clone, Default, Deserialize, Serialize)]
pub struct ChartScene {
    pub width: f32,
    pub height: f32,
    pub hits: Vec<ChartHit>,
}
#[derive(Clone, Deserialize, Serialize)]
pub struct ChartHit {
    pub bounds: [f32; 4],
    pub center: [f32; 2],
    pub radius: f32,
    pub datum: Value,
    pub tooltip: String,
}
impl ChartScene {
    pub fn valid(&self) -> bool {
        self.width.is_finite()
            && self.height.is_finite()
            && self.width > 0.
            && self.height > 0.
            && self.width <= 4096.
            && self.height <= 4096.
            && self.hits.len() <= 10000
            && self.hits.iter().all(|h| {
                h.bounds
                    .iter()
                    .chain(h.center.iter())
                    .chain([&h.radius])
                    .all(|v| v.is_finite())
                    && h.radius >= 0.
            })
    }
    // Invert ObjectFit::Contain, including letterboxing and high-DPI images.
    fn pick(&self, x: f32, y: f32, width: f32, height: f32) -> Option<usize> {
        if self.width <= 0. || self.height <= 0. {
            return None;
        }
        let scale = (width / self.width).min(height / self.height);
        if scale <= 0. {
            return None;
        }
        let x = (x - (width - self.width * scale) / 2.) / scale;
        let y = (y - (height - self.height * scale) / 2.) / scale;
        self.hits.iter().rposition(|h| {
            let [l, t, r, b] = h.bounds;
            x >= l
                && x <= r
                && y >= t
                && y <= b
                && (h.radius == 0.
                    || (x - h.center[0]).powi(2) + (y - h.center[1]).powi(2) <= h.radius.powi(2))
        })
    }
}
pub(crate) type ChartSelect = Rc<dyn Fn(Value, &mut App)>;

pub(crate) struct VegaChart {
    spec: Value,
    height: u32,
    width: u32,
    revision: u64,
    pending: bool,
    image: Option<Arc<Image>>,
    error: Option<String>,
    renderer: ChartRenderer,
    scene: ChartScene,
    bounds: Bounds<Pixels>,
    hovered: Option<usize>,
    tip_position: Point<Pixels>,
    pub(crate) on_select: Option<ChartSelect>,
}
impl VegaChart {
    pub(crate) fn retry(&mut self, cx: &mut Context<Self>) {
        if self.error.take().is_some() {
            self.start(cx);
            cx.notify();
        }
    }
    pub(crate) fn probe_status(&self) -> Value {
        serde_json::json!({"width":self.width,"height":self.height,"revision":self.revision,
            "pending":self.pending,"ready":self.image.is_some()&&!self.pending,
            "error":self.error,"bounds":{"x":f32::from(self.bounds.origin.x),"y":f32::from(self.bounds.origin.y),"width":f32::from(self.bounds.size.width),"height":f32::from(self.bounds.size.height)},
            "scene":self.scene,"hovered":self.hovered})
    }
    pub fn new(spec: Value, height: u32, renderer: ChartRenderer) -> Self {
        Self {
            spec,
            height,
            width: 0,
            revision: 0,
            pending: false,
            image: None,
            error: None,
            renderer,
            scene: ChartScene::default(),
            bounds: Bounds::default(),
            hovered: None,
            tip_position: point(px(0.), px(0.)),
            on_select: None,
        }
    }
    pub fn set_spec(&mut self, spec: Value, height: u32, cx: &mut Context<Self>) {
        if self.spec != spec || self.height != height {
            self.spec = spec;
            self.height = height;
            self.revision += 1;
            self.image = None;
            self.error = None;
            self.scene = ChartScene::default();
            self.hovered = None;
            self.start(cx);
            cx.notify();
        }
    }
    fn measure(&mut self, width: f32, cx: &mut Context<Self>) {
        let width = ((width.max(180.) as u32 / 8) * 8).clamp(180, 2048);
        if self.width != width {
            self.width = width;
            self.revision += 1;
            self.error = None;
            self.hovered = None;
            self.start(cx);
        }
    }
    fn start(&mut self, cx: &mut Context<Self>) {
        if self.pending || self.width == 0 {
            return;
        }
        self.pending = true;
        let revision = self.revision;
        let (spec, width, height, renderer) = (
            self.spec.clone(),
            self.width,
            self.height,
            self.renderer.clone(),
        );
        cx.spawn(async move |view, cx| {
            let result = cx
                .background_executor()
                .spawn(async move { renderer(spec, width, height) })
                .await;
            let _ = view.update(cx, |this, cx| {
                this.pending = false;
                if this.revision != revision {
                    this.start(cx);
                    return;
                }
                match result {
                    Ok(output) => {
                        this.image =
                            Some(Arc::new(Image::from_bytes(ImageFormat::Png, output.png)));
                        this.scene = output.scene;
                        this.error = None;
                    }
                    Err(e) => {
                        this.image = None;
                        this.scene = ChartScene::default();
                        this.error = Some(e);
                    }
                }
                cx.notify();
            });
        })
        .detach();
    }
    fn hit(&self, position: Point<Pixels>) -> Option<usize> {
        if self.pending || self.error.is_some() {
            return None;
        }
        let local = position - self.bounds.origin;
        self.scene.pick(
            local.x.into(),
            local.y.into(),
            self.bounds.size.width.into(),
            self.bounds.size.height.into(),
        )
    }
    fn hover(&mut self, position: Point<Pixels>, cx: &mut Context<Self>) {
        let next = self.hit(position);
        if next != self.hovered {
            self.hovered = next;
            self.tip_position = position - self.bounds.origin;
            cx.notify();
        }
    }
}
impl Render for VegaChart {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let weak = cx.entity().downgrade();
        let mut root = div()
            .id("vega-chart")
            .relative()
            .w_full()
            .min_w_0()
            .h(px(self.height as f32))
            .overflow_hidden()
            .on_mouse_move(
                cx.listener(|this, event: &MouseMoveEvent, _, cx| this.hover(event.position, cx)),
            )
            .on_hover(cx.listener(|this, hovered: &bool, _, cx| {
                if !hovered && this.hovered.take().is_some() {
                    cx.notify();
                }
            }))
            .on_click(cx.listener(|this, event: &ClickEvent, _, cx| {
                if this.error.is_some() {
                    this.retry(cx);
                    return;
                }
                // Touch devices have no persistent hover; a tap reveals the same
                // mark details and dispatches the same bound event as a click.
                this.hover(event.position(), cx);
                if let (Some(index), Some(select)) = (this.hit(event.position()), &this.on_select) {
                    let hit = &this.scene.hits[index];
                    select(json!({"datum":hit.datum,"tooltip":hit.tooltip}), cx);
                }
            }));
        if self.hovered.is_some() && self.on_select.is_some() {
            root = root.cursor_pointer();
        }
        if let Some(image) = &self.image {
            root = root.child(
                img(image.clone())
                    .size_full()
                    .object_fit(ObjectFit::Contain),
            );
        } else {
            root = root.child(
                div()
                    .p_3()
                    .text_size(px(12.))
                    .text_color(cx.theme().muted_foreground)
                    .child(
                        self.error
                            .as_ref().map(|error| format!("{error} · Tap to retry"))
                            .unwrap_or_else(|| "Rendering chart…".into()),
                    ),
            );
        }
        root = root.child(
            canvas(
                move |bounds, _, cx| {
                    let _ = weak.update(cx, |this, cx| {
                        if this.bounds != bounds && this.hovered.take().is_some() {
                            cx.notify();
                        }
                        this.bounds = bounds;
                        this.measure(bounds.size.width.into(), cx)
                    });
                },
                |_, _, _, _| {},
            )
            .absolute()
            .top_0()
            .left_0()
            .size_full(),
        );
        if let Some(hit) = self
            .hovered
            .and_then(|i| self.scene.hits.get(i))
            .filter(|h| !h.tooltip.is_empty())
        {
            let width = (f32::from(self.bounds.size.width) - 12.).clamp(100., 280.);
            let lines = hit.tooltip.lines().count().min(8) as f32;
            let left = (f32::from(self.tip_position.x) + 12.)
                .min((f32::from(self.bounds.size.width) - width - 6.).max(0.));
            let top = (f32::from(self.tip_position.y) + 16.)
                .min((self.height as f32 - lines * 18. - 24.).max(0.));
            root = root.child(
                div()
                    .absolute()
                    .left(px(left))
                    .top(px(top))
                    .w(px(width))
                    .p_2()
                    .rounded_md()
                    .bg(cx.theme().popover)
                    .text_color(cx.theme().popover_foreground)
                    .border_1()
                    .border_color(cx.theme().border)
                    .shadow_md()
                    .text_size(px(12.))
                    .child(hit.tooltip.lines().take(8).collect::<Vec<_>>().join("\n")),
            );
        }
        root
    }
}

#[cfg(test)]
mod tests {
    use super::{ChartHit, ChartScene, Value};
    #[test]
    fn picking_contains_letterbox_layers_and_circles() {
        let rect = ChartHit {
            bounds: [20., 20., 80., 80.],
            center: [50., 50.],
            radius: 0.,
            datum: Value::Null,
            tooltip: String::new(),
        };
        let mut scene = ChartScene {
            width: 100.,
            height: 100.,
            hits: vec![rect.clone()],
        };
        assert_eq!(scene.pick(50., 100., 100., 200.), Some(0));
        assert_eq!(scene.pick(50., 20., 100., 200.), None);
        assert_eq!(scene.pick(100., 100., 200., 200.), Some(0));
        scene.hits.push(ChartHit {
            radius: 30.,
            ..rect
        });
        assert_eq!(scene.pick(50., 50., 100., 100.), Some(1));
        assert_eq!(scene.pick(21., 21., 100., 100.), Some(0));
        assert_eq!(scene.pick(90., 90., 100., 100.), None);
    }
    #[test]
    fn malformed_geometry_is_rejected() {
        let mut scene = ChartScene {
            width: 100.,
            height: 100.,
            hits: vec![],
        };
        assert!(scene.valid());
        scene.width = f32::NAN;
        assert!(!scene.valid());
    }
}
