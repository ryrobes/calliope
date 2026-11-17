//! `CResultTable`: stored query results, read-only. Rows are virtualized, the
//! header follows horizontal scroll, numbers are right-aligned and formatted by
//! the column's server-chosen `format`, and a tapped cell opens its full value.
//! Ported from the Canvas pinned-object grid (`calliope-canvas/src/grid.rs`).
use gpui::{prelude::*, *};
use gpui_component::ActiveTheme;
use serde_json::Value;

const ROW: f32 = 30.;
const CELL_CHARS: usize = 200;

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum Format {
    Text,
    /// Grouped whole numbers.
    Integer,
    /// Numbers that are identifiers or years: right-aligned, never grouped.
    Plain,
    Decimal,
    Boolean,
    Time,
    Json,
}

impl Format {
    fn parse(token: &str) -> Self {
        match token {
            "integer" => Self::Integer,
            "plain" => Self::Plain,
            "decimal" => Self::Decimal,
            "boolean" => Self::Boolean,
            "time" => Self::Time,
            "json" => Self::Json,
            _ => Self::Text,
        }
    }
    fn numeric(self) -> bool {
        matches!(self, Self::Integer | Self::Plain | Self::Decimal)
    }
}

/// Thousands separators on the integer part of a plain decimal string.
fn group(number: &str) -> String {
    let (sign, digits) = number.strip_prefix('-').map_or(("", number), |d| ("-", d));
    let (whole, fraction) = digits.split_once('.').map_or((digits, None), |(w, f)| (w, Some(f)));
    let mut grouped = String::new();
    for (i, c) in whole.chars().enumerate() {
        if i > 0 && (whole.len() - i) % 3 == 0 {
            grouped.push(',');
        }
        grouped.push(c);
    }
    match fraction {
        Some(f) => format!("{sign}{grouped}.{f}"),
        None => format!("{sign}{grouped}"),
    }
}

fn number(text: &str, format: Format) -> Option<String> {
    let text = text.trim();
    let plain = text.strip_prefix('-').unwrap_or(text);
    let (whole, fraction) = plain.split_once('.').unwrap_or((plain, ""));
    if whole.is_empty() || !whole.bytes().all(|b| b.is_ascii_digit()) || !fraction.bytes().all(|b| b.is_ascii_digit()) {
        let value: f64 = text.parse().ok().filter(|v: &f64| v.is_finite())?;
        return number(&value.to_string(), format);
    }
    if format == Format::Plain {
        return Some(text.trim_end_matches(".0").to_owned());
    }
    // Past f64's exact range, keep the stored digits and only group them.
    if whole.len() > 15 {
        return Some(group(text));
    }
    let value: f64 = text.parse().ok()?;
    Some(if value.fract() == 0. {
        group(&format!("{value:.0}"))
    } else if value.abs() >= 1. {
        group(&format!("{value:.2}"))
    } else {
        let short = format!("{value:.4}");
        let short = short.trim_end_matches('0').trim_end_matches('.');
        if short.is_empty() || short == "0" || short == "-0" { format!("{value:e}") } else { short.to_owned() }
    })
}

/// What a cell shows. The full, unformatted value is `raw`.
pub(crate) fn display(value: &Value, format: Format) -> String {
    let text = match value {
        Value::Null => return "—".into(),
        Value::Bool(b) => b.to_string(),
        Value::Number(n) if format.numeric() => number(&n.to_string(), format).unwrap_or_else(|| n.to_string()),
        Value::String(s) if format.numeric() => number(s, format).unwrap_or_else(|| s.clone()),
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        other => other.to_string(),
    };
    let line = text.replace(['\n', '\r', '\t'], " ");
    if line.chars().count() > CELL_CHARS {
        line.chars().take(CELL_CHARS).chain(['…']).collect()
    } else {
        line
    }
}

fn raw(value: &Value) -> String {
    match value {
        Value::Null => "NULL".into(),
        Value::String(s) => s.clone(),
        Value::Array(_) | Value::Object(_) => serde_json::to_string_pretty(value).unwrap_or_default(),
        other => other.to_string(),
    }
}

fn count(n: usize) -> String {
    group(&n.to_string())
}

#[derive(Default)]
pub(crate) struct Table {
    names: Vec<String>,
    labels: Vec<SharedString>,
    formats: Vec<Format>,
    widths: Vec<f32>,
    rows: Vec<Value>,
    cells: Vec<Vec<SharedString>>,
    summary: String,
}

impl Table {
    pub(crate) fn new(columns: &Value, rows: &Value, row_count: Option<u64>, truncated: bool) -> Self {
        let rows = rows.as_array().cloned().unwrap_or_default();
        let mut names = Vec::new();
        let mut formats = Vec::new();
        for (i, column) in columns.as_array().into_iter().flatten().enumerate() {
            let name = column.as_str().or_else(|| column["name"].as_str()).map(str::to_owned).unwrap_or_else(|| format!("Column {}", i + 1));
            formats.push(Format::parse(column["format"].as_str().unwrap_or("text")));
            names.push(name);
        }
        let value = |row: &Value, i: usize, name: &str| -> Value {
            row.get(i).filter(|_| row.is_array()).or_else(|| row.get(name)).cloned().unwrap_or(Value::Null)
        };
        let cells: Vec<Vec<SharedString>> = rows
            .iter()
            .map(|row| names.iter().enumerate().map(|(i, name)| display(&value(row, i, name), formats[i]).into()).collect())
            .collect();
        let labels: Vec<SharedString> = names.iter().map(|n| n.replace('_', " ").into()).collect();
        let widths = labels
            .iter()
            .enumerate()
            .map(|(i, label)| {
                let longest = cells.iter().take(60).map(|row| row[i].chars().count().min(40)).max().unwrap_or(0).max(label.chars().count().min(28));
                (longest as f32 * 7.2 + 24.).clamp(64., 280.)
            })
            .collect();
        let stored = row_count.map_or(rows.len(), |n| (n as usize).max(rows.len()));
        let mut summary = if stored > rows.len() {
            format!("Showing {} of {} rows", count(rows.len()), count(stored))
        } else {
            format!("{} {}", count(rows.len()), if rows.len() == 1 { "row" } else { "rows" })
        };
        summary.push_str(&format!(" · {} {}", count(names.len()), if names.len() == 1 { "column" } else { "columns" }));
        if truncated {
            summary.push_str(" · the query had more rows");
        }
        Self { labels, formats, widths, cells, summary, rows, names }
    }

    fn raw(&self, row: usize, column: usize) -> String {
        let source = &self.rows[row];
        raw(source.get(column).filter(|_| source.is_array()).or_else(|| source.get(&self.names[column])).unwrap_or(&Value::Null))
    }
}

fn max_scroll(handle: &ScrollHandle) -> (Pixels, Pixels) {
    let max = handle.max_offset();
    #[cfg(feature = "gpui-next")]
    return (max.x, max.y);
    #[cfg(not(feature = "gpui-next"))]
    return (max.width, max.height);
}

pub(crate) struct ResultTable {
    source: (Value, Value, Option<u64>, bool),
    table: Table,
    scroll: UniformListScrollHandle,
    /// The table's drawn width; narrow results stretch their columns to fill it.
    viewport: f32,
    visible_rows: usize,
    scale: f32,
    cell: Option<(usize, usize)>,
    copied: bool,
}

impl ResultTable {
    pub(crate) fn new() -> Self {
        Self {
            source: (Value::Null, Value::Null, None, false),
            table: Table::default(),
            scroll: UniformListScrollHandle::new(),
            viewport: 0.,
            visible_rows: 12,
            scale: 1.,
            cell: None,
            copied: false,
        }
    }

    pub(crate) fn set(&mut self, columns: Value, rows: Value, row_count: Option<u64>, truncated: bool, visible_rows: usize, scale: f32, cx: &mut Context<Self>) {
        let source = (columns, rows, row_count, truncated);
        if source != self.source {
            let table = Table::new(&source.0, &source.1, row_count, truncated);
            if table.names != self.table.names {
                self.scroll = UniformListScrollHandle::new();
            }
            self.table = table;
            self.source = source;
            self.cell = None;
            cx.notify();
        }
        if (self.visible_rows, self.scale) != (visible_rows, scale) {
            (self.visible_rows, self.scale) = (visible_rows, scale);
            cx.notify();
        }
    }
}

impl Render for ResultTable {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let z = self.scale;
        let theme = cx.theme();
        let (line, head, even, muted, accent, text, card) =
            (theme.table_row_border, theme.table_head, theme.table_even, theme.muted_foreground, theme.primary, theme.foreground, theme.popover);
        let natural = self.table.widths.iter().sum::<f32>() * z;
        let stretch = if natural > 0. && self.viewport > natural { self.viewport / natural } else { 1. };
        let widths: Vec<f32> = self.table.widths.iter().map(|w| w * z * stretch).collect();
        let width = widths.iter().sum::<f32>();
        let offset = self.scroll.0.borrow().base_handle.offset();
        let weak = cx.entity().downgrade();
        let measure = canvas(
            move |bounds, _, cx| {
                let drawn = f32::from(bounds.size.width);
                let _ = weak.update(cx, |this, cx| {
                    if (this.viewport - drawn).abs() > 0.5 {
                        this.viewport = drawn;
                        cx.notify();
                    }
                });
            },
            |_, _, _, _| {},
        )
        .absolute()
        .top_0()
        .left_0()
        .size_full();
        let shown = self.table.rows.len().clamp(1, self.visible_rows);
        let mut body = shown as f32 * ROW * z;
        if self.cell.is_some() {
            body = body.max(140. * z);
        }
        let header = div().relative().h(px(ROW * z)).flex_shrink_0().overflow_hidden().bg(head).border_b_1().border_color(line).child(
            div().absolute().left(offset.x).w(px(width)).h_full().flex().children(self.table.labels.iter().enumerate().map(|(i, label)| {
                div()
                    .w(px(widths[i]))
                    .flex_shrink_0()
                    .px(px(10. * z))
                    .h_full()
                    .flex()
                    .items_center()
                    .when(self.table.formats[i].numeric(), |c| c.justify_end())
                    .overflow_hidden()
                    .text_color(muted)
                    .child(div().text_ellipsis().child(label.clone()))
            })),
        );
        let list = uniform_list(
            "result-rows",
            self.table.rows.len().max(1),
            cx.processor(move |this, range: std::ops::Range<usize>, _, cx| {
                let z = this.scale;
                let (widths, width) = (widths.clone(), width);
                if this.table.rows.is_empty() {
                    return vec![div().w(px(width)).h(px(ROW * z)).px(px(10. * z)).flex().items_center().text_color(muted).child("No rows returned").into_any_element()];
                }
                range
                    .map(|row| {
                        div()
                            .id(("row", row))
                            .w(px(width))
                            .h(px(ROW * z))
                            .flex()
                            .border_b_1()
                            .border_color(line)
                            .when(row % 2 == 1, |s| s.bg(even))
                            .children((0..this.table.labels.len()).map(|column| {
                                div()
                                    .id(("cell", column))
                                    .w(px(widths[column]))
                                    .h_full()
                                    .flex_shrink_0()
                                    .px(px(10. * z))
                                    .flex()
                                    .items_center()
                                    .when(this.table.formats[column].numeric(), |c| c.justify_end())
                                    .overflow_hidden()
                                    .cursor_pointer()
                                    .child(div().text_ellipsis().child(this.table.cells[row][column].clone()))
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.cell = Some((row, column));
                                        this.copied = false;
                                        cx.notify();
                                    }))
                            }))
                            .into_any_element()
                    })
                    .collect()
            }),
        )
        .w_full()
        .h(px(body));
        #[cfg(feature = "gpui-next")]
        let list = list.track_scroll(&self.scroll);
        #[cfg(not(feature = "gpui-next"))]
        let list = list.track_scroll(self.scroll.clone());
        let list = list
        .with_horizontal_sizing_behavior(ListHorizontalSizingBehavior::Unconstrained)
        .on_scroll_wheel(cx.listener(|this, e: &ScrollWheelEvent, window, cx| {
            // The list has already applied the delta. Remap Shift+wheel to
            // horizontal, clamp, and keep the gesture only when the table used
            // it: a short table must let vertical swipes scroll the page.
            let delta = e.delta.pixel_delta(window.line_height());
            let handle = this.scroll.0.borrow().base_handle.clone();
            let old = handle.offset();
            let next = if e.modifiers.shift && delta.x == px(0.) { point(old.x + delta.y, old.y - delta.y) } else { old };
            let (max_x, max_y) = max_scroll(&handle);
            handle.set_offset(point(next.x.clamp(-max_x, px(0.)), next.y.clamp(-max_y, px(0.))));
            if max_y > px(0.) || delta.x.abs() > delta.y.abs() || e.modifiers.shift {
                cx.stop_propagation();
            }
            cx.notify();
        }));
        div()
            .id("result-table")
            .relative()
            .w_full()
            .min_w_0()
            .flex()
            .flex_col()
            .overflow_hidden()
            .rounded(px(6. * z))
            .border_1()
            .border_color(line)
            .text_size(px(13. * z))
            .text_color(text)
            .child(measure)
            .child(header)
            .child(list)
            .child(div().px(px(10. * z)).py(px(6. * z)).flex_shrink_0().border_t_1().border_color(line).text_size(px(12. * z)).text_color(muted).child(self.table.summary.clone()))
            .when_some(self.cell.filter(|(r, c)| *r < self.table.rows.len() && *c < self.table.labels.len()), |root, (row, column)| {
                root.child(
                    div()
                        .id("cell-detail")
                        .occlude()
                        .absolute()
                        .inset_0()
                        .bg(card)
                        .flex()
                        .flex_col()
                        .child(
                            div()
                                .px(px(10. * z))
                                .py(px(8. * z))
                                .flex()
                                .gap_3()
                                .items_center()
                                .border_b_1()
                                .border_color(line)
                                .child(div().id("back-to-table").cursor_pointer().text_color(accent).child("← Table").on_click(cx.listener(|this, _, _, cx| {
                                    this.cell = None;
                                    cx.notify();
                                })))
                                .child(div().flex_1().min_w_0().text_ellipsis().text_color(muted).child(format!("{} · row {}", self.table.labels[column], row + 1)))
                                .child(div().id("copy-cell").cursor_pointer().text_color(accent).child(if self.copied { "Copied" } else { "Copy" }).on_click(cx.listener(move |this, _, _, cx| {
                                    cx.write_to_clipboard(ClipboardItem::new_string(this.table.raw(row, column)));
                                    this.copied = true;
                                    cx.notify();
                                }))),
                        )
                        .child(div().id("cell-value").flex_1().min_h_0().overflow_y_scroll().p(px(10. * z)).child(div().whitespace_normal().child(self.table.raw(row, column)))),
                )
            })
    }
}

#[cfg(test)]
mod tests {
    use super::{Format, Table, display};
    use serde_json::json;

    #[test]
    fn numbers_follow_the_column_format() {
        assert_eq!(display(&json!(15011642.52), Format::Decimal), "15,011,642.52");
        assert_eq!(display(&json!("15011642.5"), Format::Decimal), "15,011,642.50");
        assert_eq!(display(&json!("1234"), Format::Decimal), "1,234");
        assert_eq!(display(&json!(0.0034), Format::Decimal), "0.0034");
        assert_eq!(display(&json!(0.0000012), Format::Decimal), "1.2e-6");
        assert_eq!(display(&json!(-2500000), Format::Integer), "-2,500,000");
        assert_eq!(display(&json!(2019), Format::Plain), "2019");
        assert_eq!(display(&json!("123456789012345678901"), Format::Integer), "123,456,789,012,345,678,901");
        assert_eq!(display(&json!("n/a"), Format::Integer), "n/a");
        assert_eq!(display(&json!(null), Format::Integer), "—");
        assert_eq!(display(&json!(1234), Format::Text), "1234");
        assert_eq!(display(&json!({"a": 1}), Format::Json), "{\"a\":1}");
    }

    #[test]
    fn array_and_object_rows_read_the_same_columns() {
        let columns = json!([{"name": "product", "format": "text"}, {"name": "revenue", "format": "decimal"}]);
        let arrays = Table::new(&columns, &json!([["Golf Caddy", 15011642.52]]), Some(72), false);
        let objects = Table::new(&columns, &json!([{"revenue": 15011642.52, "product": "Golf Caddy"}]), None, true);
        assert_eq!(arrays.cells, objects.cells);
        assert_eq!(arrays.cells[0][1].as_ref(), "15,011,642.52");
        assert_eq!(arrays.summary, "Showing 1 of 72 rows · 2 columns");
        assert_eq!(objects.summary, "1 row · 2 columns · the query had more rows");
        assert_eq!(objects.raw(0, 1), "15011642.52");
    }
}
