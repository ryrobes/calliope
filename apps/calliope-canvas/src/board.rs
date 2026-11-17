//! The published Stage board contract: resolved cell order/spans and exact refs.
use crate::model::{Pin, Surface, display};
use serde::Deserialize;
use serde_json::Value;

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct Board {
    pub subtitle: String,
    pub meta: String,
    pub cells: Vec<Cell>,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct Cell {
    pub kind: String,
    #[serde(rename = "ref")]
    pub reference: String,
    pub title: String,
    pub member_title: String,
    pub span: Option<u16>,
    pub rows: Option<u16>,
    pub view: String,
    pub eyebrow: String,
    pub heading: String,
    pub body: String,
    pub aside: String,
    pub row: usize,
    pub column: String,
    pub format: String,
    pub label: String,
    pub caption: String,
    pub delta_column: String,
}

impl Cell {
    pub fn title(&self) -> String {
        [
            &self.title,
            &self.label,
            &self.heading,
            &self.member_title,
            &self.eyebrow,
            &self.column,
        ]
        .into_iter()
        .find(|s| !s.is_empty())
        .cloned()
        .unwrap_or_else(|| "Board note".into())
    }

    pub fn pin(&self, parent: &Pin, index: usize) -> Pin {
        let mut pin = parent.clone();
        pin.id = format!("{}-cell-{index}", parent.id);
        pin.query_id = None;
        pin.board_cell = Some(index);
        pin.title = self.title();
        pin.width = if self.kind == "surface" { 720. } else { 352. };
        pin.height = if self.kind == "stat" { 264. } else { 400. };
        pin
    }

    /// Never resolve another session or silently replace a referenced revision.
    pub fn member<'a>(&self, board: &Surface, surfaces: &'a [Surface]) -> Option<&'a Surface> {
        surfaces
            .iter()
            .find(|s| s.id == self.reference && s.session_id == board.session_id)
    }
}

pub fn parse(surface: &Surface) -> Result<Board, serde_json::Error> {
    serde_json::from_value(surface.payload.clone())
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct QuerySource {
    pub session_id: String,
    pub surface_id: String,
}

impl QuerySource {
    pub fn key(&self) -> String {
        serde_json::to_string(&("board-query", &self.session_id, &self.surface_id)).unwrap()
    }
}

impl Cell {
    pub fn query_source(&self, board: &Surface, surfaces: &[Surface]) -> Option<QuerySource> {
        if !matches!(self.kind.as_str(), "stat" | "surface") || self.view == "sql" {
            return None;
        }
        let member = self.member(board, surfaces).filter(|s| s.kind == "query")?;
        Some(QuerySource {
            session_id: member.session_id.clone(),
            surface_id: member.id.clone(),
        })
    }
}

pub fn query_sources(pin: &Pin, surface: &Surface, surfaces: &[Surface]) -> Vec<QuerySource> {
    let Ok(board) = parse(surface) else {
        return vec![];
    };
    let mut sources = Vec::new();
    for (index, cell) in board.cells.iter().enumerate() {
        if pin.board_cell.is_some_and(|i| i != index) {
            continue;
        }
        if let Some(source) = cell.query_source(surface, surfaces)
            && !sources.contains(&source)
        {
            sources.push(source);
        }
    }
    sources
}

/// Match CSS grid-auto-flow:dense. The server already reorders explicit layouts;
/// layout.rows must not be applied a second time to its resolved cells.
pub fn layout(cells: &[Cell], width: f32) -> Vec<(usize, usize, u16)> {
    let mut occupied: Vec<[bool; 12]> = Vec::new();
    cells
        .iter()
        .map(|cell| {
            let span = if width <= 720. {
                12
            } else {
                cell.span.unwrap_or(12).clamp(1, 12)
            };
            for row in 0..=occupied.len() {
                if row == occupied.len() {
                    occupied.push([false; 12]);
                }
                for column in 0..=12 - usize::from(span) {
                    let slots = &mut occupied[row][column..column + usize::from(span)];
                    if slots.iter().all(|s| !s) {
                        slots.fill(true);
                        return (row, column, span);
                    }
                }
            }
            unreachable!("a fresh grid row always fits a bounded span")
        })
        .collect()
}

pub fn query_value<'a>(payload: &'a Value, row: usize, column: &str) -> Option<&'a Value> {
    let record = payload
        .get("rows")
        .or_else(|| payload.get("sample"))?
        .as_array()?
        .get(row)?;
    if record.is_object() {
        return record.get(column);
    }
    let index = payload.get("columns")?.as_array()?.iter().position(|c| {
        c.as_str().or_else(|| c.get("name").and_then(Value::as_str)) == Some(column)
    })?;
    record.get(index)
}

pub fn number(value: &Value) -> Option<f64> {
    let n = value.as_f64().or_else(|| {
        let s = value.as_str()?;
        if s.trim().is_empty() {
            return None;
        }
        s.chars()
            .filter(|c| !c.is_whitespace() && !matches!(c, ',' | '$' | '%'))
            .collect::<String>()
            .parse::<f64>()
            .ok()
    })?;
    n.is_finite().then_some(n)
}

fn decimal(n: f64, precision: usize, trim: bool) -> String {
    // Intl.NumberFormat uses half-expand; Rust's formatting uses ties-to-even.
    let factor = 10_f64.powi(precision as i32);
    let scaled = n.abs() * factor;
    let rounded = (scaled + f64::EPSILON * scaled).round() / factor;
    let mut s = format!(
        "{:.*}",
        precision,
        if rounded.is_finite() {
            rounded
        } else {
            n.abs()
        }
    );
    if trim && s.contains('.') {
        s = s.trim_end_matches('0').trim_end_matches('.').into();
    }
    let (whole, fraction) = s.split_once('.').unwrap_or((&s, ""));
    let mut grouped = String::new();
    for (i, c) in whole.chars().enumerate() {
        if i > 0 && (whole.len() - i).is_multiple_of(3) {
            grouped.push(',');
        }
        grouped.push(c);
    }
    if !fraction.is_empty() {
        grouped.push('.');
        grouped.push_str(fraction);
    }
    if n < 0. && s.parse::<f64>().unwrap_or(0.) != 0. {
        grouped.insert(0, '-');
    }
    grouped
}

/// The web board's en-US number/percent/USD/compact conventions.
pub fn format_stat(value: Option<&Value>, format: &str) -> String {
    let Some(value) = value.filter(|v| !v.is_null() && v.as_str() != Some("")) else {
        return "—".into();
    };
    if format == "text" {
        return display(value);
    }
    let Some(n) = number(value) else {
        return display(value);
    };
    if format == "percent" {
        return format!(
            "{}%",
            decimal(if n.abs() <= 1. { n * 100. } else { n }, 1, true)
        );
    }
    if format == "currency" {
        let digits = decimal(n.abs(), if n.abs() >= 1000. { 0 } else { 2 }, false);
        return format!("{}${digits}", if n < 0. { "-" } else { "" });
    }
    if format == "compact" || ((format == "auto" || format.is_empty()) && n.abs() >= 1e6) {
        let (scale, suffix) = [(1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")]
            .into_iter()
            .find(|(scale, _)| n.abs() >= *scale)
            .unwrap_or((1., ""));
        return format!("{}{suffix}", decimal(n / scale, 1, true));
    }
    decimal(n, 2, true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn board_and_pinned_cells_share_one_read_per_exact_query_source() {
        let surface: Surface =
            serde_json::from_value(json!({"id":"board-v1","session_id":"s","kind":"board",
            "payload":{"cells":[{"kind":"stat","ref":"q","column":"sales"},
                {"kind":"stat","ref":"q","column":"profit"},
                {"kind":"surface","ref":"q","view":"table"},
                {"kind":"surface","ref":"foreign"}]}}))
            .unwrap();
        let q: Surface =
            serde_json::from_value(json!({"id":"q","session_id":"s","kind":"query"})).unwrap();
        let foreign: Surface =
            serde_json::from_value(json!({"id":"foreign","session_id":"other","kind":"query"}))
                .unwrap();
        let parent = Pin::from_surface(&surface, 0., 0., "topic:s");
        let sources = vec![q, foreign];
        let expected = query_sources(&parent, &surface, &sources);
        assert_eq!(expected.len(), 1);
        let cell = parse(&surface).unwrap().cells[1].pin(&parent, 1);
        assert_eq!(query_sources(&cell, &surface, &sources), expected);
        let mut home = crate::model::Home::default();
        home.board_queries.insert(expected[0].key(), true);
        let encoded = serde_json::to_value(&home).unwrap();
        let restored: crate::model::Home = serde_json::from_value(encoded).unwrap();
        assert_eq!(restored.board_queries.get(&expected[0].key()), Some(&true));
        assert!(crate::model::Home::default().board_queries.is_empty());
    }

    #[test]
    fn references_do_not_follow_revisions_or_cross_session_boundaries() {
        let board: Surface =
            serde_json::from_value(json!({"id":"board-v1","session_id":"s","kind":"board"}))
                .unwrap();
        let source: Surface = serde_json::from_value(json!({"id":"answer-v1","session_id":"s","kind":"answer","lineage_key":"answer-lineage"})).unwrap();
        let mut newer = source.clone();
        newer.id = "answer-v2".into();
        newer.created_at = "2026-10-07".into();
        let cell = Cell {
            reference: source.id.clone(),
            kind: "surface".into(),
            ..Default::default()
        };
        let surfaces = vec![newer, source.clone()];
        assert_eq!(cell.member(&board, &surfaces).unwrap().id, "answer-v1");
        let mut foreign = source;
        foreign.session_id = "other".into();
        assert!(cell.member(&board, &[foreign]).is_none());
        assert!(cell.member(&board, &[]).is_none());
    }

    #[test]
    fn pinned_cells_roundtrip_without_copying_results_or_aliasing_parent() {
        let surface: Surface =
            serde_json::from_value(json!({"id":"board-v1","session_id":"s","kind":"board"}))
                .unwrap();
        let parent = Pin::from_surface(&surface, 5., 6., "topic:s");
        let cell = Cell {
            kind: "stat".into(),
            label: "Sales change".into(),
            ..Default::default()
        };
        let pin = cell.pin(&parent, 2);
        let mut home = crate::model::Home::default();
        home.pins.push(parent.clone());
        let id = home.place_in(&pin, "Home");
        assert_eq!(id, home.place_in(&pin, "Home"));
        assert_ne!(pin.object_key(), parent.object_key());
        assert_ne!(pin.object_key(), cell.pin(&parent, 3).object_key());
        let restored: crate::model::Home =
            serde_json::from_value(serde_json::to_value(&home).unwrap()).unwrap();
        assert_eq!(restored.pins[1].board_cell, Some(2));
        assert_eq!(restored.pins[1].surface_id, "board-v1");
        assert_eq!(restored.pins[1].title, "Sales change");
        assert!(
            !serde_json::to_value(&restored.pins[1])
                .unwrap()
                .as_object()
                .unwrap()
                .contains_key("payload")
        );
        let mut legacy = serde_json::to_value(&parent).unwrap();
        legacy.as_object_mut().unwrap().remove("board_cell");
        assert_eq!(
            serde_json::from_value::<Pin>(legacy).unwrap().object_key(),
            parent.object_key()
        );
    }

    #[test]
    fn published_spans_pack_densely_and_stack_on_narrow_boards() {
        let cells: Vec<Cell> = serde_json::from_value(json!([
            {"kind":"text","span":12,"rows":null},
            {"kind":"stat","span":4}, {"kind":"stat","span":4}, {"kind":"stat","span":4},
            {"kind":"surface","span":8}, {"kind":"surface","span":8}, {"kind":"text","span":4}
        ]))
        .unwrap();
        assert_eq!(
            layout(&cells, 960.),
            vec![
                (0, 0, 12),
                (1, 0, 4),
                (1, 4, 4),
                (1, 8, 4),
                (2, 0, 8),
                (3, 0, 8),
                (2, 8, 4)
            ]
        );
        assert_eq!(
            layout(&cells, 460.),
            (0..7).map(|row| (row, 0, 12)).collect::<Vec<_>>()
        );
        assert_eq!(cells[0].rows, None);
    }

    #[test]
    fn stats_resolve_named_columns_and_preserve_missing_values() {
        let array = json!({"columns":[{"name":"sales"},"delta"],"rows":[[1234.5,-0.1111]]});
        let object = json!({"rows":[{"sales":"$1,234.50", "delta":-11.11}]});
        assert_eq!(
            format_stat(query_value(&array, 0, "sales"), "currency"),
            "$1,235"
        );
        assert_eq!(
            format_stat(query_value(&object, 0, "sales"), "number"),
            "1,234.5"
        );
        assert_eq!(
            format_stat(query_value(&array, 0, "delta"), "percent"),
            "-11.1%"
        );
        assert_eq!(
            format_stat(query_value(&object, 0, "delta"), "percent"),
            "-11.1%"
        );
        assert_eq!(
            format_stat(query_value(&array, 1, "sales"), "currency"),
            "—"
        );
        assert_eq!(
            format_stat(query_value(&array, 0, "missing"), "number"),
            "—"
        );
        assert_eq!(format_stat(Some(&json!(0)), "currency"), "$0.00");
        assert_eq!(
            format_stat(Some(&json!(-185403.54)), "currency"),
            "-$185,404"
        );
        assert_eq!(format_stat(Some(&json!(1250000)), "compact"), "1.3M");
        assert_eq!(format_stat(Some(&json!("no data")), "number"), "no data");
    }
}
