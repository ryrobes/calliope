// Live app summaries on the same movable canvas as threads and stage objects.
impl CanvasApp {
    fn open_record_pin(&mut self, reference: Value, ask: bool, cx: &mut Context<Self>) {
        if self.apps.is_none() {
            self.apps = Some(cx.new(|cx| {
                crate::published_apps::PublishedApps::with_pins(
                    self.config.clone(),
                    self.theme.clone(),
                    self.record_pins.clone(),
                    cx,
                )
            }));
        }
        if let Some(apps) = &self.apps {
            match apps.update(cx, |app, cx| app.open_record(reference, ask, cx)) {
                Ok(()) => self.apps_open = true,
                Err(error) => self.error = Some(error.to_string()),
            }
        }
        cx.notify();
    }
    fn record_cards(&mut self, cx: &mut Context<Self>) -> Vec<AnyElement> {
        if self.home.active_space != "Home" {
            return vec![];
        }
        let Some(pins) = &self.record_pins else {
            return vec![];
        };
        let pins = pins.clone();
        let entries: Vec<_> = pins
            .read(cx)
            .items
            .iter()
            .map(|p| {
                (
                    p.id(),
                    p.title(),
                    p.status(),
                    p.value["reference"].clone(),
                    p.renderer.clone(),
                )
            })
            .collect();
        self.home
            .record_positions
            .retain(|id, _| entries.iter().any(|p| &p.0 == id));
        let z = self.home.zoom;
        entries.into_iter().enumerate().map(|(index,(id,title,status,reference,renderer))| {
            let pin_store=pins.clone();
            if let Some(r)=&renderer {r.update(cx,|r,cx|{if (r.summary_scale-z).abs()>0.001 {r.summary_scale=z;cx.notify();}});}
            let geometry=*self.home.record_positions.entry(id.clone()).or_insert([48.+(index%3) as f32*392.,260.+(index/3) as f32*338.,368.,310.]);
            let [x,y,w,h]=geometry;
            let drag=id.clone();let resize=id.clone();let remove=id.clone();let open=reference.clone();let ask=reference;
            div().id(SharedString::from(format!("record-pin-{id}"))).occlude().absolute()
                .left(px(self.home.pan[0]+x*z)).top(px(self.home.pan[1]+y*z)).w(px(w*z)).h(px(h*z))
                .bg(with_alpha(self.theme.card,0.96)).border_1().border_color(with_alpha(self.theme.accent,0.5)).rounded(px(18.*z)).shadow_lg()
                .flex().flex_col().overflow_hidden()
                .on_mouse_down(MouseButton::Left,|_,_,cx|cx.stop_propagation())
                .child(div().px(px(16.*z)).pt(px(13.*z)).flex_shrink_0().flex().flex_col().gap_1().cursor(CursorStyle::OpenHand)
                    .child(div().text_size(px(10.*z)).text_color(self.theme.accent).child("LIVE RECORD · PINNED HOME"))
                    .child(div().text_size(px(20.*z)).font_family(self.theme.font_display.clone()).child(title))
                    .on_mouse_down(MouseButton::Left,cx.listener(move|s,e:&MouseDownEvent,_,cx|{s.selected=None;s.drag=Some(Drag::Record(drag.clone(),e.position,x,y));cx.stop_propagation();cx.notify();})))
                .child(div().flex_1().min_h_0().when_some(renderer,|s,r|s.child(r)))
                .child(div().px(px(16.*z)).text_size(px(10.*z)).text_color(self.theme.muted).line_clamp(1).child(status))
                .child(div().px(px(10.*z)).py_1().flex_shrink_0().flex().items_center().justify_between().border_t_1().border_color(self.theme.line)
                    .child(self.button(SharedString::from(format!("open-record-{id}")),"Open record →").text_size(px(11.*z)).px(px(5.*z)).py(px(4.*z)).text_color(self.theme.accent).on_click(cx.listener(move|s,_,_,cx|s.open_record_pin(open.clone(),false,cx))))
                    .child(self.button(SharedString::from(format!("ask-record-{id}")),"Ask Callie").text_size(px(11.*z)).px(px(5.*z)).py(px(4.*z)).on_click(cx.listener(move|s,_,_,cx|s.open_record_pin(ask.clone(),true,cx))))
                    .child(self.button(SharedString::from(format!("remove-record-{id}")),"×").text_size(px(12.*z)).px(px(5.*z)).py(px(4.*z)).on_click(move|_,_,cx|pin_store.update(cx,|p,cx|p.change(serde_json::json!({"operation":"remove","id":remove}),cx)))))
                .child(div().id(SharedString::from(format!("resize-record-{id}"))).absolute().bottom_0().right_0().size(px(14.)).cursor(CursorStyle::ResizeUpLeftDownRight)
                    .on_mouse_down(MouseButton::Left,cx.listener(move|s,e:&MouseDownEvent,_,cx|{s.drag=Some(Drag::RecordResize(resize.clone(),e.position,[w,h]));cx.stop_propagation();cx.notify();})))
                .into_any_element()
        }).collect()
    }
}
