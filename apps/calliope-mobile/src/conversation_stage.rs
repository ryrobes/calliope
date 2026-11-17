fn stage_category(item: &Value) -> &'static str {
    if item["kind"] == "image"
        || item["payload"]["mime_type"]
            .as_str()
            .is_some_and(|s| s.starts_with("image/"))
    {
        "Images"
    } else if item["kind"] == "query" || sql_receipt(item) {
        "Data"
    } else if matches!(item["kind"].as_str(), Some("artifact" | "app")) {
        "Apps"
    } else {
        "Files"
    }
}
/// Receipts of SQL Callie ran (or froze for approval) in the local admin or workspace lane.
fn sql_receipt(item: &Value) -> bool {
    item["kind"] == "action"
        && matches!(item["payload"]["run"]["action_snapshot"]["executor"].as_str(), Some("local_sql" | "workspace_sql"))
}
fn kind_label(item: &Value) -> String {
    let label = match (item["kind"].as_str().unwrap_or(""), item["payload"]["mode"].as_str()) {
        ("query", _) | ("cube", _) => "Table",
        ("chart", _) => "Chart",
        ("image", _) => "Image",
        ("document", _) => "Document",
        ("metric", _) => "Metric",
        ("answer", _) => "Answer",
        ("board", _) => "Board",
        ("find", _) => "Search",
        ("map", _) => "Map",
        ("sketch", _) => "Sketch",
        ("input", _) => "Question",
        ("email", _) => "Email",
        ("artifact", _) if item["payload"]["runtime_kind"] == "a2ui" => "App",
        ("artifact", _) => "Dashboard",
        ("action", Some("specialist_thread" | "specialist_run")) => "Specialist",
        ("action", _) if sql_receipt(item) => "SQL receipt",
        ("action", _) => "Activity",
        (other, _) => return other.to_owned(),
    };
    label.into()
}
fn kind_glyph(item: &Value) -> Glyph {
    match kind_label(item).as_str() {
        "Table" => Glyph::Table,
        "Chart" => Glyph::ChartLine,
        "Image" => Glyph::Image,
        "Document" => Glyph::FileText,
        "Metric" => Glyph::Gauge,
        "Answer" => Glyph::MessageSquareText,
        "Board" | "Dashboard" => Glyph::LayoutDashboard,
        "Search" => Glyph::Search,
        "Map" => Glyph::Map,
        "Sketch" => Glyph::PenTool,
        "Question" => Glyph::MessageCircleQuestionMark,
        "Email" => Glyph::Mail,
        "App" => Glyph::AppWindow,
        "Specialist" => Glyph::Bot,
        "SQL receipt" => Glyph::Database,
        "Activity" => Glyph::Activity,
        _ => Glyph::File,
    }
}
/// Phone-width cells: thousands separators, millions compacted.
fn cell_label(value: &Value) -> String {
    let number = value
        .as_f64()
        .or_else(|| value.as_str().and_then(|s| s.trim().parse::<f64>().ok()).filter(|_| value.as_str().is_some_and(|s| s.contains('.'))));
    match number {
        // Years and small counts read better without separators.
        Some(n) if n.fract() == 0. && n.abs() < 10_000. => format!("{n:.0}"),
        Some(n) if n.abs() >= 1e6 => format!("{:.2}M", n / 1e6),
        Some(n) if n.abs() >= 1e3 => {
            let whole = format!("{:.0}", n.abs());
            let mut grouped = String::new();
            for (i, digit) in whole.chars().enumerate() {
                if i > 0 && (whole.len() - i) % 3 == 0 {
                    grouped.push(',');
                }
                grouped.push(digit);
            }
            format!("{}{grouped}", if n < 0. { "-" } else { "" })
        }
        Some(n) if n.fract() != 0. => format!("{n:.2}"),
        _ if value.is_null() => "—".into(),
        _ => excerpt(value.as_str().map(str::to_owned).unwrap_or_else(|| value.to_string()).as_str(), 24),
    }
}
fn chart_detail(spec: &Value) -> String {
    let mark = spec["mark"]
        .as_str()
        .or_else(|| spec["mark"]["type"].as_str())
        .or_else(|| spec["layer"][0]["mark"].as_str())
        .unwrap_or("Vega-Lite");
    let mut letters = mark.chars();
    let mark = letters
        .next()
        .map(|first| first.to_uppercase().collect::<String>() + letters.as_str())
        .unwrap_or_else(|| "Vega-Lite".into());
    match spec["data"]["values"].as_array().map_or(0, Vec::len) {
        0 => format!("{mark} chart"),
        points => format!("{mark} chart · {points} points"),
    }
}
/// A chart's inline data as table columns and rows; phase 0 shows the numbers
/// until charts render natively. Encoded fields lead, in x, y, color order.
fn chart_rows(spec: &Value) -> (Vec<Value>, Vec<Value>) {
    let rows = spec["data"]["values"].as_array().cloned().unwrap_or_default();
    let Some(first) = rows.first().and_then(Value::as_object) else {
        return (vec![], vec![]);
    };
    let mut columns: Vec<String> = ["x", "y", "color", "theta", "size"]
        .iter()
        .filter_map(|channel| spec["encoding"][channel]["field"].as_str())
        .filter(|field| first.contains_key(*field))
        .map(str::to_owned)
        .collect();
    for key in first.keys() {
        if !columns.contains(key) {
            columns.push(key.clone());
        }
    }
    (columns.into_iter().map(Value::String).collect(), rows)
}
fn summary_text(item: &Value) -> String {
    let p = &item["payload"];
    ["answer", "headline", "description", "summary", "markdown", "text", "subtitle", "prompt"]
        .iter()
        .find_map(|k| p[k].as_str().filter(|s| !s.trim().is_empty()))
        .map(|s| excerpt(s, 240))
        .unwrap_or_default()
}
/// Picture for items whose content is not an image file: a dashboard or app's
/// published thumbnail, a sketch's rendered preview.
fn visual_preview(item: &Value) -> Option<String> {
    let p = &item["payload"];
    match item["kind"].as_str() {
        Some("artifact") => {
            let slug = p["slug"].as_str().or_else(|| item["artifact_slug"].as_str())?;
            let kind = if p["app_kind"] == "dashboard" { "dashboard" } else { "app" };
            slug.chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
                .then(|| format!("/thumbs/{kind}/{slug}.png"))
        }
        Some("sketch") if p["element_count"].as_u64().unwrap_or(0) > 0 => {
            p["preview_url"].as_str().filter(|u| u.starts_with('/')).map(str::to_owned)
        }
        _ => None,
    }
}
fn card_version_of(item: &Value) -> String {
    crate::stage_cards::card_version(item["kind"].as_str().unwrap_or(""), &item["payload"])
}
/// Items the server draws as native cards; the rest keep the host's preview.
fn projected(item: &Value) -> bool {
    match item["kind"].as_str() {
        Some("query" | "chart" | "find" | "answer" | "metric" | "decision") => true,
        Some("action") => {
            let p = &item["payload"];
            (matches!(p["mode"].as_str(), Some("specialist_thread" | "specialist_run")) && p["transient"] != true)
                || (p["run"].is_object()
                    && (p["mode"] == "action_receipt"
                        || matches!(p["run"]["action_snapshot"]["executor"].as_str(), Some("local_sql" | "workspace_sql"))))
        }
        _ => false,
    }
}
/// The working note behind an exploration step, shown as the web Stage does.
fn exploration_intent(item: &Value) -> Option<String> {
    (item["presentation"]["phase"] == "exploration" && item["kind"] != "sketch")
        .then(|| text(&item["source"]["exploration"], "intent"))
        .filter(|intent| !intent.trim().is_empty())
}
// Included in conversations.rs: authenticated, on-demand previews of saved receipts.
#[derive(Default)]
struct StagePreview {
    loading: bool,
    path: Option<std::path::PathBuf>,
    text: Option<String>,
    error: String,
}
impl Conversations {
    /// A projected item's native card, requested on first use; `None` until it
    /// is ready or when the item keeps its built-in preview.
    fn native_card(&mut self, item: &Value) -> Option<(Entity<GpuiApp>, f32)> {
        let session = self
            .selected
            .as_ref()
            .and_then(|key| self.assistant.chats.get(key))
            .map(|c| c.session.clone())
            .unwrap_or_default();
        let id = text(item, "id");
        self.stage_cards.request(&session, &[(id.clone(), card_version_of(item))]);
        self.stage_cards.get(&id).map(|c| (c.renderer.clone(), c.height))
    }
    /// Builds projected cards, runs their action recipes and follows up answers.
    fn poll_cards(&mut self, cx: &mut Context<Self>) {
        while let Ok((key, next, result)) = self.team_rx.try_recv() {
            if let Err(e) = result {
                if let Some(chat) = self.assistant.chats.get_mut(&key) {
                    chat.metadata["team_mode"] = json!(!next);
                }
                self.notice = format!("Team mode was not changed: {e:#}");
            }
            cx.notify();
        }
        self.stage_cards.set_theme(&self.theme, cx);
        if self.stage_cards.poll(cx) {
            cx.notify();
        }
        while let Ok((id, follow, result)) = self.act_rx.try_recv() {
            match result {
                Ok(done) if !follow => {
                    // An approval changes only its own item: show its new state.
                    self.stage_cards.reload(&id);
                    self.notice = done;
                    self.refresh_thread(cx);
                }
                Ok(done) => {
                    self.stage_cards.remove(&id);
                    // Hidden until the reloaded thread drops it from the open set.
                    self.stage_cards.set_state(&id, "answered");
                    self.notice = done.clone();
                    if let Some(key) = self.selected.clone() {
                        let now = std::time::Instant::now();
                        self.watch.insert(key, (now + Duration::from_secs(90), now - Duration::from_secs(5), done));
                    }
                }
                Err(e) => {
                    if let Some(card) = self.stage_cards.get_mut(&id) {
                        card.busy = false;
                        card.error = format!("{e:#}");
                    }
                    // A changed or closed question refreshes to its current state.
                    self.refresh_thread(cx);
                }
            }
            cx.notify();
        }
        for run in self.stage_cards.submissions(cx) {
            if run.open {
                if let Err(e) = crate::host::open_url(&run.path) {
                    self.notice = e;
                }
                continue;
            }
            let (api, tx) = (self.api.clone(), self.act_tx.clone());
            std::thread::spawn(move || {
                let _ = tx.send((run.id, run.follow, api.post(&run.path, &run.body).map(|_| run.done)));
            });
            cx.notify();
        }
        // An answer becomes a new turn Callie runs; attach to it as it streams.
        if let Some(key) = self.selected.clone() {
            if let Some((deadline, last, done)) = self.watch.get(&key).cloned() {
                let chat = self.assistant.chats.entry(key.clone()).or_default();
                let now = std::time::Instant::now();
                let attach = chat.retry.is_some() && !chat.loading;
                if chat.running || attach || now > deadline {
                    self.watch.remove(&key);
                    // The live turn's own status line takes over from "Answer sent".
                    if self.notice == done {
                        self.notice.clear();
                    }
                    if attach {
                        chat.error.clear();
                        self.assistant.send(self.api.clone(), &key, String::new(), Value::Null, true);
                    }
                    cx.notify();
                } else if !chat.loading && now.duration_since(last) >= Duration::from_secs(2) {
                    self.watch.insert(key.clone(), (deadline, now, done));
                    let session = chat.session.clone();
                    self.assistant.load_thread(self.api.clone(), &key, &session);
                }
            }
        }
    }
    fn open_stage_app(&mut self, id: &str, cx: &mut Context<Self>) {
        let slug = self
            .selected
            .as_ref()
            .and_then(|key| self.assistant.chats.get(key))
            .and_then(|c| {
                stage_items(&c.surfaces)
                    .into_iter()
                    .find(|s| s["id"] == id && s["payload"]["runtime_kind"] == "a2ui")
            })
            .map(|s| text(&s["payload"], "slug"));
        if let Some(slug) = slug {
            self.assistant.voice.cancel();
            cx.emit(OpenApp(slug, None));
        }
    }

    fn stage_item(&self, id: &str) -> Option<Value> {
        self.selected
            .as_ref()
            .and_then(|key| self.assistant.chats.get(key))
            .and_then(|c| stage_items(&c.surfaces).into_iter().find(|s| s["id"] == id))
    }
    fn toggle_stage(&mut self, id: &str) {
        if !self.collapsed.remove(id) {
            self.collapsed.insert(id.into());
        }
    }
    /// Opens the Stage with one reply output expanded and listed first.
    fn focus_stage(&mut self, id: &str) {
        if self.stage_item(id).is_none() {
            return;
        }
        self.assistant.voice.cancel();
        self.stage = true;
        self.stage_filter = "All".into();
        self.stage_focus = Some(id.into());
        self.collapsed.remove(id);
        // A focused working step opens its turn's fold.
        if let Some(turn) = self.stage_item(id).map(|item| text(&item, "turn_id")) {
            self.work_open.insert(turn);
        }
    }
    fn toggle_work(&mut self, turn: &str) {
        if !self.work_open.remove(turn) {
            self.work_open.insert(turn.into());
        }
    }
    fn ensure_preview(&mut self, id: &str) {
        let Some(item) = self.stage_item(id).filter(|item| item["kind"] != "chart") else {
            return;
        };
        if self
            .previews
            .get(id)
            .is_some_and(|p| p.loading || p.path.is_some() || p.text.is_some())
        {
            return;
        }
        let p = &item["payload"];
        let mime = text(p, "mime_type");
        // Dashboards and apps stay HTML; their published thumbnail stands in.
        let picture = visual_preview(&item);
        let image = picture.is_some()
            || item["kind"] == "image"
            || matches!(mime.as_str(), "image/png" | "image/jpeg" | "image/webp");
        let textual = matches!(
            mime.as_str(),
            "application/json" | "text/plain" | "text/markdown" | "text/csv"
        );
        if !image && !textual {
            return;
        }
        // Files Callie wrote are served by id; few carry a download URL.
        let stored = item["kind"] == "document" && (p["storage_path"].is_string() || p["source_path"].is_string());
        let url = picture
            .or_else(|| p["image_url"].as_str().or_else(|| p["download_url"].as_str()).map(str::to_owned))
            .or_else(|| stored.then(|| format!("/api/calliope/files/{id}")))
            .unwrap_or_default();
        if url.is_empty() {
            return;
        }
        self.previews.insert(
            id.into(),
            StagePreview {
                loading: true,
                ..Default::default()
            },
        );
        let api = self.api.clone();
        let id = id.to_owned();
        let tx = self.preview_tx.clone();
        std::thread::spawn(move || {
            let result = (|| -> anyhow::Result<StagePreview> {
                uuid::Uuid::parse_str(&id)?;
                // Api.asset refuses redirects and foreign origins before attaching credentials.
                let bytes = api.asset(&url, if image { 8 * 1024 * 1024 } else { 1024 * 1024 })?;
                if image {
                    anyhow::ensure!(
                        bytes.starts_with(b"\x89PNG\r\n\x1a\n")
                            || bytes.starts_with(b"\xff\xd8\xff")
                            || (bytes.starts_with(b"RIFF") && bytes.get(8..12) == Some(b"WEBP")),
                        "Preview is not a supported image"
                    );
                    let path = crate::model::data_dir()
                        .join("stage-previews")
                        .join(format!("{id}.image"));
                    crate::model::private_write(&path, &bytes)?;
                    Ok(StagePreview {
                        path: Some(path),
                        ..Default::default()
                    })
                } else {
                    let value = String::from_utf8(bytes)?;
                    let formatted = if mime == "application/json" {
                        serde_json::from_str::<Value>(&value)
                            .ok()
                            .and_then(|v| serde_json::to_string_pretty(&v).ok())
                            .unwrap_or(value)
                    } else {
                        value
                    };
                    Ok(StagePreview {
                        text: Some(excerpt(&formatted, 16000)),
                        ..Default::default()
                    })
                }
            })();
            let _ = tx.send((id, result));
        });
    }
    fn poll_stage(&mut self, cx: &mut Context<Self>) {
        while let Ok((id, result)) = self.preview_rx.try_recv() {
            self.previews.insert(
                id,
                result.unwrap_or_else(|e| StagePreview {
                    error: e.to_string(),
                    ..Default::default()
                }),
            );
            cx.notify();
        }
    }
    fn stage_card(&mut self, item: &Value, cx: &mut Context<Self>) -> AnyElement {
        let id = text(item, "id");
        let p = &item["payload"];
        let expanded = !self.collapsed.contains(&id);
        if expanded {
            self.ensure_preview(&id);
        }
        let browser_id = id.clone();
        let preview_id = id.clone();
        let kind = text(item, "kind");
        let row_count = p["row_count"]
            .as_u64()
            .unwrap_or(p["rows"].as_array().map_or(0, |r| r.len() as u64));
        let (columns, rows) = if kind == "chart" {
            chart_rows(&p["spec"])
        } else {
            (p["columns"].as_array().cloned().unwrap_or_default(), p["rows"].as_array().cloned().unwrap_or_default())
        };
        let row_count = if kind == "chart" { rows.len() as u64 } else { row_count };
        // Results render natively when the server can project them; the
        // bounded preview below stays as the fallback and while loading.
        let native = if expanded && projected(item) { self.native_card(item) } else { None };
        let date = text(item, "created_at")
            .chars()
            .take(16)
            .collect::<String>()
            .replace('T', " ");
        let detail = if kind == "chart" {
            chart_detail(&p["spec"])
        } else if kind == "query" && native.is_some() {
            // The native table states its own row and column counts.
            String::new()
        } else if kind == "query" {
            format!(
                "{row_count} rows · {} columns · saved result",
                columns.len()
            )
        } else if p["bytes"].as_u64().is_some() {
            format!(
                "{} · {:.0} KB",
                text(p, "mime_type"),
                p["bytes"].as_u64().unwrap_or(0) as f64 / 1024.
            )
        } else {
            date
        };
        let summary = ["description", "summary", "markdown", "text", "subtitle"]
            .iter()
            .find_map(|k| p[k].as_str())
            .unwrap_or("");
        let mut card = div()
            .flex_shrink_0()
            .min_w_0()
            .p_4()
            .rounded_xl()
            .bg(self.theme.card)
            .border_1()
            .border_color(if self.stage_focus.as_deref() == Some(id.as_str()) {
                self.theme.accent
            } else {
                self.theme.line
            })
            .flex()
            .flex_col()
            .gap_3()
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap_2()
                    .child(
                        div()
                            .flex_1()
                            .text_size(px(11.))
                            .text_color(self.theme.accent)
                            .child(kind_label(item).to_uppercase()),
                    )
                    .child(
                        ui::tinted_icon_button(
                            SharedString::from(format!("expand-{id}")),
                            if expanded { Glyph::ChevronUp } else { Glyph::ChevronDown },
                            self.theme.muted,
                            &self.theme,
                            true,
                            cx.listener(move |s, _, _, cx| {
                                s.toggle_stage(&preview_id);
                                cx.notify();
                            }),
                        )
                        .mr(px(-10.))
                        .my(px(-10.)),
                    ),
            )
            .child(div().font_weight(FontWeight::SEMIBOLD).child(title(item)))
            .when(!detail.is_empty(), |s| {
                s.child(
                    div()
                        .text_size(px(11.))
                        .text_color(self.theme.muted)
                        .child(detail),
                )
            })
            .when_some(exploration_intent(item), |c, intent| {
                c.child(
                    div()
                        .text_size(px(12.))
                        .text_color(self.theme.muted)
                        .child(format!("Why Callie ran this · {}", excerpt(&intent, 240))),
                )
            });
        let has_native = native.is_some();
        if expanded {
            if let Some((renderer, height)) = native {
                card = card.child(div().w_full().h(px(height)).child(renderer));
            } else if !columns.is_empty() && !rows.is_empty() {
                // A bounded receipt preview with horizontal scrolling, never a re-execution.
                let mut table = div().flex().flex_col();
                let mut header = div().flex().bg(self.theme.accent_soft);
                for col in columns.iter().take(80) {
                    header = header.child(
                        div()
                            .w(px(156.))
                            .flex_shrink_0()
                            .p_2()
                            .text_sm()
                            .font_weight(FontWeight::SEMIBOLD)
                            .child(
                                col.as_str()
                                    .unwrap_or(col["name"].as_str().unwrap_or("Field"))
                                    .to_owned(),
                            ),
                    );
                }
                table = table.child(header);
                for row in rows.iter().take(30) {
                    let mut line = div().flex().border_b_1().border_color(self.theme.line);
                    for (index, col) in columns.iter().take(80).enumerate() {
                        let name = col.as_str().unwrap_or(col["name"].as_str().unwrap_or(""));
                        let value = if row.is_array() {
                            &row[index]
                        } else {
                            &row[name]
                        };
                        let label = cell_label(value);
                        line = line.child(
                            div()
                                .w(px(156.))
                                .flex_shrink_0()
                                .p_2()
                                .text_sm()
                                .child(excerpt(&label, 200)),
                        );
                    }
                    table = table.child(line);
                }
                card = card
                    .child(
                        div()
                            .id(SharedString::from(format!("table-{id}")))
                            .max_h(px(320.))
                            .overflow_scroll()
                            .child(table),
                    )
                    .child(
                        div()
                            .text_size(px(11.))
                            .text_color(self.theme.muted)
                            .child(format!(
                                "Showing {} of {row_count} rows · swipe across for columns",
                                rows.len().min(30)
                            )),
                    );
            }
            if !summary.is_empty() {
                card = card.child(
                    TextView::markdown(
                        SharedString::from(format!("stage-text-{id}")),
                        excerpt(summary, 12000),
                    )
                    .selectable(true)
                    .scrollable(false)
                    .on_link_click({
                        let base = self.api.config.browser_url.clone();
                        move |href, _, _, _| {
                            if let Ok(url) =
                                reqwest::Url::parse(&base).and_then(|b| b.join(href.as_ref()))
                            {
                                let _ = crate::host::open_url(url.as_str());
                            }
                        }
                    }),
                );
            }
            if let Some(preview) = self.previews.get(&id) {
                if preview.loading {
                    card = card.child(
                        div()
                            .text_sm()
                            .text_color(self.theme.muted)
                            .child("Loading preview…"),
                    );
                }
                if let Some(path) = &preview.path {
                    card = card.child(
                        img(path.clone())
                            .w_full()
                            .h(px(240.))
                            .object_fit(ObjectFit::Contain)
                            .rounded_lg(),
                    );
                }
                if let Some(value) = &preview.text {
                    card = card.child(
                        div()
                            .id(SharedString::from(format!("file-{id}")))
                            .max_h(px(360.))
                            .overflow_y_scroll()
                            .child(
                                TextView::markdown(
                                    SharedString::from(format!("file-text-{id}")),
                                    // Markdown documents read as documents; other text stays verbatim.
                                    if p["mime_type"] == "text/markdown" {
                                        value.clone()
                                    } else {
                                        format!(
                                            "````{}\n{}\n````",
                                            if p["mime_type"] == "application/json" { "json" } else { "text" },
                                            value
                                        )
                                    },
                                )
                                .selectable(true)
                                .scrollable(false),
                            ),
                    );
                }
                if !preview.error.is_empty() {
                    card = card.child(
                        div()
                            .text_sm()
                            .text_color(self.theme.warning)
                            .child(preview.error.clone()),
                    );
                }
            }
            if columns.is_empty()
                && summary.is_empty()
                && !has_native
                && !self.previews.contains_key(&id)
                && p["runtime_kind"] != "a2ui"
            {
                card = card.child(
                    div()
                        .text_sm()
                        .text_color(self.theme.muted)
                        .child("Open the full view for this item’s interactive content."),
                );
            }
        } else if !summary.is_empty() {
            card = card.child(
                div()
                    .text_sm()
                    .text_color(self.theme.muted)
                    .child(excerpt(summary, 160)),
            );
        }
        let mut actions = div().flex().flex_wrap().gap_2();
        if p["runtime_kind"] == "a2ui" {
            let native_id = id.clone();
            actions = actions.child(ui::action(
                SharedString::from(format!("native-{id}")),
                Some(Glyph::AppWindow),
                "Open app",
                Tone::Tonal,
                &self.theme,
                true,
                cx.listener(move |s, _, _, cx| s.open_stage_app(&native_id, cx)),
            ));
        }
        let first = p["runtime_kind"] != "a2ui";
        actions = actions.child(
            ui::action(
                SharedString::from(format!("browser-{id}")),
                Some(Glyph::ExternalLink),
                "Full view",
                Tone::Quiet,
                &self.theme,
                true,
                cx.listener(move |s, _, _, cx| s.open_browser(Some(&browser_id), cx)),
            )
            .when(first, |b| b.ml(px(-12.))),
        );
        card.child(actions).into_any_element()
    }
    /// The reply's lead output inline under its prose (phase 0: today's previews).
    fn hero_card(&mut self, item: &Value, pointed: bool, cx: &mut Context<Self>) -> AnyElement {
        let id = text(item, "id");
        let p = &item["payload"];
        if matches!(item["kind"].as_str(), Some("image" | "document")) || visual_preview(item).is_some() {
            self.ensure_preview(&id);
        }
        let open_id = id.clone();
        let browser_id = id.clone();
        let app_id = id.clone();
        let chart = item["kind"] == "chart";
        let (columns, rows) = if chart {
            chart_rows(&p["spec"])
        } else {
            (p["columns"].as_array().cloned().unwrap_or_default(), p["rows"].as_array().cloned().unwrap_or_default())
        };
        let row_count = p["row_count"].as_u64().unwrap_or(rows.len() as u64);
        let mut card = div()
            .id(SharedString::from(format!("hero-{id}")))
            .flex_shrink_0()
            .min_w_0()
            .p_3()
            .rounded_xl()
            .bg(self.theme.card)
            .border_1()
            .border_color(if pointed { self.theme.accent } else { self.theme.line })
            .flex()
            .flex_col()
            .gap_2()
            .child(
                div()
                    .text_size(px(11.))
                    .text_color(self.theme.accent)
                    .child(if pointed {
                        format!("{} · Callie pointed here", kind_label(item).to_uppercase())
                    } else {
                        kind_label(item).to_uppercase()
                    }),
            );
        let native = if chart || matches!(item["kind"].as_str(), Some("answer" | "decision")) { self.native_card(item) } else { None };
        card = card.child(div().font_weight(FontWeight::SEMIBOLD).child(title(item)));
        if let Some((renderer, height)) = native {
            card = card.child(div().w_full().h(px(height)).child(renderer));
        } else if !columns.is_empty() && !rows.is_empty() {
            let shown = columns.iter().take(3).collect::<Vec<_>>();
            let name = |col: &Value| col.as_str().unwrap_or(col["name"].as_str().unwrap_or("")).to_owned();
            let mut table = div().flex().flex_col().text_sm();
            let mut header = div().flex().gap_2().text_color(self.theme.muted);
            for col in &shown {
                header = header.child(div().flex_1().min_w_0().truncate().child(name(col)));
            }
            table = table.child(header);
            for row in rows.iter().take(5) {
                let mut line = div().flex().gap_2().py_1().border_t_1().border_color(self.theme.line);
                for (index, col) in shown.iter().enumerate() {
                    let value = if row.is_array() { &row[index] } else { &row[name(col).as_str()] };
                    line = line.child(div().flex_1().min_w_0().truncate().child(cell_label(value)));
                }
                table = table.child(line);
            }
            let footer = if chart {
                chart_detail(&p["spec"])
            } else {
                format!(
                    "{row_count} rows{}",
                    if columns.len() > 3 { format!(" · {} columns", columns.len()) } else { String::new() }
                )
            };
            card = card.child(table).child(div().text_size(px(11.)).text_color(self.theme.muted).child(footer));
        } else if chart {
            card = card.child(div().text_sm().text_color(self.theme.muted).child(chart_detail(&p["spec"])));
        } else if let Some(path) = self.previews.get(&id).and_then(|p| p.path.clone()) {
            card = card.child(img(path).w_full().h(px(200.)).object_fit(ObjectFit::Contain).rounded_lg());
        } else if let Some(text) = self.previews.get(&id).and_then(|p| p.text.clone()) {
            // A text file Callie wrote: its opening, as the document reads.
            let markdown = p["mime_type"] == "text/markdown";
            let shown = excerpt(&text, 1200);
            card = card.child(
                div().max_h(px(260.)).overflow_hidden().child(
                    TextView::markdown(
                        SharedString::from(format!("hero-text-{id}")),
                        if markdown { shown } else { format!("````text\n{shown}\n````") },
                    )
                    .selectable(true)
                    .scrollable(false),
                ),
            );
        } else {
            let summary = summary_text(item);
            if !summary.is_empty() {
                card = card.child(div().text_sm().text_color(self.theme.muted).child(summary));
            }
        }
        let mut actions = div().flex().flex_wrap().gap_1().ml(px(-12.)).child(ui::action(
            SharedString::from(format!("hero-open-{id}")),
            Some(Glyph::Layers),
            "On Stage",
            Tone::Quiet,
            &self.theme,
            true,
            cx.listener(move |s, _, w, cx| {
                w.blur(cx);
                s.focus_stage(&open_id);
                cx.notify();
            }),
        ));
        if p["runtime_kind"] == "a2ui" {
            actions = actions.child(ui::action(
                SharedString::from(format!("hero-app-{id}")),
                Some(Glyph::AppWindow),
                "Open app",
                Tone::Tonal,
                &self.theme,
                true,
                cx.listener(move |s, _, _, cx| s.open_stage_app(&app_id, cx)),
            ));
        } else {
            actions = actions.child(ui::action(
                SharedString::from(format!("hero-full-{id}")),
                Some(Glyph::ExternalLink),
                "Full view",
                Tone::Quiet,
                &self.theme,
                true,
                cx.listener(move |s, _, _, cx| s.open_browser(Some(&browser_id), cx)),
            ));
        }
        card.child(actions).into_any_element()
    }
    /// Other deliverables as chips, then the agent's working steps folded.
    fn turn_extras(&mut self, turn: &str, chips: &[Value], work: &[Value], cx: &mut Context<Self>) -> Option<AnyElement> {
        if chips.is_empty() && work.is_empty() {
            return None;
        }
        let mut extras = div().flex().flex_col().gap_2();
        if !chips.is_empty() {
            let mut row = div().flex().flex_wrap().gap_2();
            for item in chips {
                let id = text(item, "id");
                let target = id.clone();
                row = row.child(ui::chip(
                    SharedString::from(format!("chip-{id}")),
                    Some(kind_glyph(item)),
                    excerpt(&title(item), 32),
                    None,
                    false,
                    &self.theme,
                    true,
                    cx.listener(move |s, _, w, cx| {
                        w.blur(cx);
                        s.focus_stage(&target);
                        cx.notify();
                    }),
                ));
            }
            extras = extras.child(row);
        }
        if !work.is_empty() {
            let open = self.work_open.contains(turn);
            let key = turn.to_owned();
            extras = extras.child(div().flex().child(ui::chip(
                SharedString::from(format!("work-{turn}")),
                Some(if open { Glyph::ChevronUp } else { Glyph::Layers }),
                if open {
                    "Hide working steps".to_owned()
                } else {
                    format!("Worked through {} step{}", work.len(), if work.len() == 1 { "" } else { "s" })
                },
                None,
                open,
                &self.theme,
                true,
                cx.listener(move |s, _, _, cx| {
                    s.toggle_work(&key);
                    cx.notify();
                }),
            )));
            if open {
                let mut list = div()
                    .ml_2()
                    .pl_3()
                    .border_l_2()
                    .border_color(self.theme.line)
                    .flex()
                    .flex_col();
                for item in work {
                    let id = text(item, "id");
                    let target = id.clone();
                    list = list.child(
                        div()
                            .id(SharedString::from(format!("work-item-{id}")))
                            .min_h(px(44.))
                            .flex()
                            .items_center()
                            .gap_2()
                            .text_sm()
                            .child(ui::glyph(kind_glyph(item), 15., self.theme.muted))
                            .child(div().flex_1().min_w_0().truncate().child(excerpt(&title(item), 48)))
                            .child(ui::glyph(Glyph::ChevronRight, 16., self.theme.muted))
                            .cursor_pointer()
                            .on_click(cx.listener(move |s, _, w, cx| {
                                w.blur(cx);
                                s.focus_stage(&target);
                                cx.notify();
                            })),
                    );
                }
                extras = extras.child(list);
            }
        }
        Some(extras.into_any_element())
    }
    /// Questions Callie is waiting on stay above the composer until answered.
    fn waiting_card(&mut self, requests: &[Value], session: &str, cx: &mut Context<Self>) -> Option<AnyElement> {
        let requests: Vec<&Value> = requests
            .iter()
            .filter(|r| self.stage_cards.state(&text(r, "id")) != Some("answered"))
            .collect();
        let first = *requests.first()?;
        let id = text(first, "id");
        self.stage_cards.request(session, &[(id.clone(), card_version_of(first))]);
        let p = &first["payload"];
        let browser_id = id.clone();
        let header = div().text_size(px(11.)).text_color(self.theme.accent).child(if requests.len() > 1 {
            format!("CALLIE IS WAITING FOR YOU · {} QUESTIONS", requests.len())
        } else {
            "CALLIE IS WAITING FOR YOU".into()
        });
        let mut card = div()
            .mx_3()
            .mb_2()
            .p_3()
            .rounded_lg()
            .bg(self.theme.accent_soft)
            .border_1()
            .border_color(self.theme.accent)
            .flex()
            .flex_col()
            .gap_2()
            .child(header);
        if let Some(native) = self.stage_cards.get(&id) {
            card = card
                .child(div().w_full().h(px(native.height)).rounded_md().overflow_hidden().child(native.renderer.clone()))
                .when(native.busy, |c| c.child(div().text_sm().text_color(self.theme.muted).child("Sending…")))
                .when(!native.error.is_empty(), |c| {
                    c.child(div().text_sm().text_color(self.theme.warning).child(native.error.clone()))
                });
        } else {
            let prompt = ["prompt", "description"]
                .iter()
                .find_map(|k| p[k].as_str().or_else(|| p["form_contract"][k].as_str()))
                .map(|s| excerpt(s, 200))
                .unwrap_or_default();
            card = card
                .child(div().font_weight(FontWeight::SEMIBOLD).child(title(first)))
                .when(!prompt.is_empty(), |s| s.child(div().text_sm().child(prompt)))
                .when(self.stage_cards.state(&id) == Some("loading"), |s| {
                    s.child(div().text_sm().text_color(self.theme.muted).child("Preparing the question…"))
                });
        }
        let native = self.stage_cards.get(&id).is_some();
        card = card.child(div().flex().child(ui::action(
            SharedString::from(format!("waiting-{id}")),
            Some(Glyph::ExternalLink),
            if native { "Open in browser" } else { "Answer in browser" },
            if native { Tone::Quiet } else { Tone::Primary },
            &self.theme,
            true,
            cx.listener(move |s, _, _, cx| s.open_browser(Some(&browser_id), cx)),
        )));
        Some(card.into_any_element())
    }
}
pub struct OpenApp(pub String, pub Option<Value>);
impl EventEmitter<OpenApp> for Conversations {}
