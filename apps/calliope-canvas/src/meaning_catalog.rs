fn kind_label(kind: &str) -> &'static str {
    match kind {
        "metric" => "Metrics",
        "relation" => "Sources",
        "cube" => "Cubes",
        "artifact" => "Apps",
        "idea" => "Metric ideas",
        "breakdown" => "Breakdowns",
        _ => "All",
    }
}
impl Meaning {
    fn catalog_nodes(&self, cx: &Context<Self>) -> Vec<Value> {
        let q = self.catalog_search.read(cx).value().to_lowercase();
        let mut nodes: Vec<_> = items(&self.graph, "nodes")
            .into_iter()
            .filter(|n| {
                (self.catalog_kind.is_empty() || n["kind"] == self.catalog_kind)
                    && (q.is_empty()
                        || format!(
                            "{} {} {} {}",
                            text(n, "title"),
                            text(n, "description"),
                            text(n, "source"),
                            text(n, "formula")
                        )
                        .to_lowercase()
                        .contains(&q))
            })
            .collect();
        nodes.sort_by_key(|n| match n["kind"].as_str() {
            Some("metric") => 0,
            Some("relation") => 1,
            Some("artifact") => 2,
            Some("idea") => 3,
            Some("breakdown") => 4,
            _ => 5,
        });
        nodes
    }
    fn catalog_stats(&self) -> Vec<Value> {
        let stats = items(&self.graph, "catalog");
        if !stats.is_empty() {
            return stats;
        }
        // Older servers expose the same totals without the typed catalog summary.
        [("relation","sources"),("metric","metrics"),("artifact","artifacts"),("idea","metric_ideas"),("breakdown","breakdowns")]
            .iter().map(|(kind,key)|json!({"kind":kind,"loaded":items(&self.graph,"nodes").iter().filter(|n|n["kind"]==*kind).count(),"available":self.graph["totals"][key],"cap":if *kind=="idea"||*kind=="breakdown"{6}else{40}})).collect()
    }
    fn catalog_panel(&self, cx: &mut Context<Self>) -> AnyElement {
        let stats = self.catalog_stats();
        let all_loaded = items(&self.graph, "nodes").len();
        let all_available = stats
            .iter()
            .map(|v| v["available"].as_u64().unwrap_or(0))
            .sum::<u64>()
            + 1;
        let lower_bound = stats.iter().any(|v| v["available_is_lower_bound"] == true);
        let nodes = self.catalog_nodes(cx);
        let count = nodes.len();
        let loaded = if self.catalog_kind.is_empty() {
            all_loaded
        } else {
            stats
                .iter()
                .find(|v| v["kind"] == self.catalog_kind)
                .and_then(|v| v["loaded"].as_u64())
                .unwrap_or(0) as usize
        };
        let available = if self.catalog_kind.is_empty() {
            all_available
        } else {
            stats
                .iter()
                .find(|v| v["kind"] == self.catalog_kind)
                .and_then(|v| v["available"].as_u64())
                .unwrap_or(0)
        };
        let can_load = self.graph["more"] == true
            && self.limit < 40
            && (self.catalog_kind.is_empty()
                || stats
                    .iter()
                    .filter(|v| v["kind"] == self.catalog_kind)
                    .any(|v| {
                        v["loaded"].as_u64().unwrap_or(0)
                            < v["available"]
                                .as_u64()
                                .unwrap_or(0)
                                .min(v["cap"].as_u64().unwrap_or(40))
                    }));
        let capped = stats
            .iter()
            .filter(|v| self.catalog_kind.is_empty() || v["kind"] == self.catalog_kind)
            .any(|v| v["available"].as_u64().unwrap_or(0) > v["cap"].as_u64().unwrap_or(40));
        div().id("meaning-catalog").w(px(360.)).flex_shrink_0().h_full().min_h_0().flex().flex_col()
            .border_l_1().border_color(self.theme.line).bg(self.theme.panel).p_4().gap_3()
            .child(div().flex().items_center().justify_between()
                .child(div().text_size(px(19.)).font_family(self.theme.font_display.clone()).child("Objects in this area"))
                .child(self.button("meaning-catalog-close","×").on_click(cx.listener(|s,_,_,cx|{s.catalog_open=false;cx.notify();}))))
            .child(div().text_size(px(11.)).line_height(relative(1.4)).text_color(self.theme.muted)
                .child(format!("{all_loaded} loaded of {all_available}{} available · {} on map. Choose what to add; loading leaves the map unchanged.",if lower_bound{"+"}else{""},self.visible.len())))
            .child(Input::new(&self.catalog_search))
            .child(div().flex().flex_wrap().gap_1().children(["","metric","cube","relation","artifact","idea","breakdown"].iter().map(|kind|{
                let kind=kind.to_string();let selected=kind==self.catalog_kind;
                let n=if kind.is_empty(){all_loaded}else{stats.iter().find(|v|v["kind"]==kind).and_then(|v|v["loaded"].as_u64()).unwrap_or(0) as usize};
                self.button(SharedString::from(format!("catalog-kind-{kind}")),format!("{} {n}",kind_label(&kind)))
                    .text_size(px(11.)).px_2().py_1().when(selected,|s|s.bg(self.theme.card).text_color(self.theme.accent))
                    .on_click(cx.listener(move|s,_,_,cx|{s.catalog_kind=kind.clone();cx.notify();}))
            })))
            .child(div().text_size(px(11.)).text_color(self.theme.muted).child(format!("{count} matches · {loaded} of {available} loaded")))
            .child(div().id("meaning-catalog-list").flex_1().min_h_0().overflow_y_scroll().flex().flex_col().gap_2()
                .when(nodes.is_empty(),|s|s.child(div().p_3().text_size(px(13.)).text_color(self.theme.muted).child("No loaded objects match. Clear the search or load more metadata below.")))
                .children(nodes.into_iter().map(|n|{
                    let id=text(&n,"id");let hide=id.clone();let shown=self.visible.contains(&id);
                    let selected=id==self.selected;let is_area=n["kind"]=="area";
                    let value=n.get("preview").map(|p|format!("{} · {}",number(&p["value"]),text(p,"label")));
                    div().id(SharedString::from(format!("catalog-object-{id}"))).flex_shrink_0().p_3().rounded_lg().border_1()
                        .border_color(if selected{self.theme.accent}else{self.theme.line})
                        .bg(if selected{self.theme.card}else{with_alpha(self.theme.card,0.45)})
                        .flex().flex_col().gap_1()
                        .child(div().flex().justify_between().text_size(px(10.)).text_color(self.color(&text(&n,"kind")))
                            .child(if n["kind"]=="area"{"AREA".into()}else{kind_label(&text(&n,"kind")).to_uppercase()})
                            .child(text(&n,"status")))
                        .child(div().text_size(px(14.)).line_height(relative(1.3)).font_family(self.theme.font_display.clone()).line_clamp(2).child(text(&n,"title")))
                        .child(div().text_size(px(11.)).line_height(relative(1.4)).text_color(self.theme.muted).line_clamp(2).child(text(&n,"description")))
                        .when_some(value,|s,v|s.child(div().text_size(px(11.)).text_color(self.theme.muted).child(v)))
                        .child(div().flex().items_center().justify_between()
                            .child(self.button(SharedString::from(format!("catalog-show-{id}")),if shown{"Locate on map →"}else{"+ Add to map"})
                                .text_size(px(11.)).px_0().py_1().text_color(self.theme.accent)
                                .on_click(cx.listener(move|s,_,_,cx|s.show_node(id.clone(),cx))))
                            .when(shown&&!is_area,|s|s.child(self.button(SharedString::from(format!("catalog-hide-{hide}")),"Hide")
                                .text_size(px(11.)).px_2().py_1().on_click(cx.listener(move|s,_,_,cx|s.hide_node(&hide,cx))))))
                })))
            .child(div().flex_shrink_0().border_t_1().border_color(self.theme.line).pt_2().flex().flex_col().gap_1()
                .when(can_load,|s|s.child(self.button("meaning-catalog-load",if self.busy{"Loading metadata…"}else{"Load up to 12 more per type"})
                    .text_size(px(12.)).text_color(self.theme.accent).on_click(cx.listener(|s,_,_,cx|s.load_catalog(cx)))))
                .when(!can_load,|s|s.child(div().text_size(px(11.)).text_color(self.theme.muted).child(if capped{"Preview limit reached for this area."}else{"All objects of this type are loaded."})))
                .when(capped,|s|s.child(div().text_size(px(10.)).text_color(self.theme.muted).child("Preview: up to 40 sources, metrics or apps; 6 ideas or breakdowns."))))
            .into_any_element()
    }
}
