// Shared record navigation and pinned-work collection for desktop and Android.
impl PublishedApps {
    pub fn open_record(
        &mut self,
        reference: Value,
        ask: bool,
        cx: &mut Context<Self>,
    ) -> Result<()> {
        crate::app_pins::validate_reference(&reference)?;
        anyhow::ensure!(
            self.retry.is_none() && !self.command_pending,
            "Finish or resolve the pending submission first"
        );
        self.pending_record = Some((reference, ask));
        cx.notify();
        Ok(())
    }
    fn start_record(&mut self, cx: &mut Context<Self>) {
        let Some((reference, ask)) = self.pending_record.clone() else {
            return;
        };
        self.capture_layout(cx);
        let slug = reference["slug"].as_str().unwrap().to_owned();
        self.record_entry = slug != self.slug;
        if slug != self.slug {
            if !self.slug.is_empty() && !self.snapshot.is_null() {
                self.return_stack.push((self.slug.clone(), self.workspace.current(&self.snapshot, &self.renderer.read(cx).data_value("/"))));
                if self.return_stack.len() > 32 { self.return_stack.remove(0); }
            }
            self.slug = slug;
            self.workspace = Default::default();
            self.snapshot = Value::Null;
            self.saved_model = Value::Null;
            self.retry = None;
            self.draft_error = None;
            self.renderer.update(cx, |r, _| {
                let _ = r.load_surface(vec![]);
            });
            self.restore(cx);
            self.workspace.history.clear();
        }
        if self.retry.is_some() || self.draft_error.is_some() {
            self.pending_record = None;
            self.notice = "Resolve this app's pending submission or draft storage error before opening a record.".into();
            self.failed = true;
            cx.notify();
            return;
        }
        if self.offline {
            let cached = self
                .workspace
                .visits
                .values()
                .find(|visit| {
                    visit.snapshot["records"]
                        .as_array()
                        .is_some_and(|rs| rs.iter().any(|r| r["reference"] == reference))
                })
                .cloned();
            self.pending_record = None;
            if let Some(visit) = cached {
                self.show_visit(visit, cx);
                if ask {
                    self.assistant_open(cx);
                }
            } else {
                self.notice =
                    "That record is not cached on this device. Reconnect to open it.".into();
                self.failed = true;
                cx.notify();
            }
            return;
        }
        self.menu_open = false;
        self.status_open = false;
        self.drafts_open = false;
        let mut body = json!({"record":reference});
        let unfinished = !self.workspace.dirty().is_empty()
            || self
                .store
                .load(&self.slug)
                .is_ok_and(|draft| !draft.follows_latest());
        if unfinished && !self.snapshot.is_null() {
            body["version"] = self.snapshot["version"].clone();
        }
        self.send(body, cx);
    }
    fn pin_current(&mut self, cx: &mut Context<Self>) {
        if self.busy || self.offline || self.retry.is_some() {
            return;
        }
        if let Some(reference) = self.snapshot["records"][0].get("reference").cloned() {
            if !self.pins.read(cx).contains(&reference) {
                self.pins.update(cx, |p, cx| {
                    p.change(json!({"operation":"pin","reference":reference}), cx)
                });
            }
        }
    }
    fn pins_view(&mut self, cx: &mut Context<Self>) -> AnyElement {
        use gpui_component::{
            Disableable,
            button::{Button, ButtonVariants},
        };
        let items: Vec<_> = self
            .pins
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
        let notice = self.pins.read(cx).notice.clone();
        let changing = self.pins.read(cx).changing;
        div().px_4().flex_shrink_0().flex().flex_col().gap_2()
            .when(!items.is_empty(),|s|s
                .child(div().flex().items_center().justify_between().child(div().text_size(px(12.)).text_color(self.theme.accent).child("PINNED WORK"))
                    .child(Button::new("refresh-pins").ghost().label("Refresh").on_click(cx.listener(|s,_,_,cx|s.pins.update(cx,|p,_|p.refresh())))))
                .child(div().id("pinned-work-scroll").max_h(px(310.)).overflow_y_scroll().grid().grid_cols(((self.picker_width/330.).floor() as u16).clamp(1,4)).gap_3()
                    .children(items.into_iter().map(|(id,title,status,reference,renderer)| {
                        let open=reference.clone();let ask=reference.clone();let remove=id.clone();
                        if let Some(r)=&renderer {r.update(cx,|r,cx|{if r.summary_scale!=1. {r.summary_scale=1.;cx.notify();}});}
                        div().id(SharedString::from(format!("pin-{id}"))).h(px(270.)).min_w_0().rounded_xl().bg(self.theme.card).border_1().border_color(self.theme.line).flex().flex_col().overflow_hidden()
                            .child(div().px_4().pt_3().flex().items_center().gap_2().child(div().flex_1().min_w_0().text_size(px(17.)).line_clamp(1).child(title))
                                .child(Button::new(SharedString::from(format!("unpin-{id}"))).ghost().label("×").disabled(changing).on_click(cx.listener(move|s,_,_,cx|s.pins.update(cx,|p,cx|p.change(json!({"operation":"remove","id":remove}),cx))))))
                            .child(div().flex_1().min_h_0().when_some(renderer,|s,r|s.child(r)))
                            .child(div().px_4().text_size(px(10.)).text_color(self.theme.muted).line_clamp(1).child(status))
                            .child(div().px_3().py_1().flex().justify_between()
                                .child(Button::new(SharedString::from(format!("pin-open-{id}"))).ghost().label("Open record →").on_click(cx.listener(move|s,_,_,cx|{if let Err(e)=s.open_record(open.clone(),false,cx){s.notice=e.to_string();s.failed=true;cx.notify();}})))
                                .child(Button::new(SharedString::from(format!("pin-ask-{id}"))).ghost().label("Ask Callie").on_click(cx.listener(move|s,_,_,cx|{if let Err(e)=s.open_record(ask.clone(),true,cx){s.notice=e.to_string();s.failed=true;cx.notify();}}))))
                    }))))
            .when_some(notice,|s,n|s.child(div().text_size(px(12.)).text_color(self.theme.warning).child(n)))
            .into_any_element()
    }
}
