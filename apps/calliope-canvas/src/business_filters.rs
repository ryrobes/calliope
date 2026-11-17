//! Debounced, read-only filter intent. Rendering and HTTP stay with the host.
use serde_json::{Value, json};

#[derive(Default)]
pub struct Filters {
    definition: Value,
    fields: Vec<Value>,
    seen: Value,
    due: Option<u64>,
}
impl Filters {
    pub fn install(&mut self, nodes: &Value, data: &Value) {
        self.definition = nodes
            .as_array()
            .and_then(|n| n.iter().find(|n| n["component"] == "CFilterBar"))
            .cloned()
            .unwrap_or(Value::Null);
        let path = self.definition["value"]["path"].as_str().unwrap_or("");
        self.fields = nodes
            .as_array()
            .into_iter()
            .flatten()
            .filter(|n| {
                n["value"]["path"]
                    .as_str()
                    .is_some_and(|p| p.starts_with(&format!("{path}/")))
            })
            .cloned()
            .collect();
        self.seen = data.pointer(path).cloned().unwrap_or(Value::Null);
        self.due = None;
    }
    pub fn observe(&mut self, data: &Value, now: u64) {
        if self.definition.is_null() {
            return;
        }
        let value = data
            .pointer(self.definition["value"]["path"].as_str().unwrap_or(""))
            .cloned()
            .unwrap_or(Value::Null);
        if self.seen == value {
            return;
        }
        self.seen = value;
        let valid = self.seen.is_object()
            && self.fields.iter().all(|f| {
                let v = data
                    .pointer(f["value"]["path"].as_str().unwrap_or(""))
                    .unwrap_or(&Value::Null);
                if f["component"] == "CField" {
                    a2ui_gpui::validate_field(v.as_str().unwrap_or(""), f).is_empty()
                } else if f["component"] == "ChoicePicker" || f["component"] == "CRecordPicker" {
                    v.as_array().is_some_and(|a| !a.is_empty())
                } else {
                    true
                }
            });
        self.due = valid.then(|| {
            now + self.definition["debounceMs"]
                .as_u64()
                .unwrap_or(250)
                .clamp(100, 1000)
        });
    }
    pub fn poll(&mut self, now: u64) -> Option<Value> {
        if self.due.is_none_or(|at| now < at) {
            return None;
        }
        self.due = None;
        Some(json!({"name":self.definition["onChange"],"context":self.seen}))
    }
    pub fn pending(&self) -> bool {
        self.due.is_some()
    }
    pub fn cancel(&mut self) {
        self.due = None;
    }
    pub fn matches(&self, submitted: &Value, data: &Value) -> bool {
        data.pointer(self.definition["value"]["path"].as_str().unwrap_or(""))
            .is_some_and(|v| v == submitted)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn debounce_latest_value_stale_reply_and_navigation() {
        let mut f = Filters::default();
        let nodes = json!([{"component":"CFilterBar","value":{"path":"/filters"},"onChange":"test_filter"}]);
        f.install(&nodes, &json!({"filters":{"search":""}}));
        assert!(f.poll(1000).is_none());
        f.observe(&json!({"filters":{"search":"a"}}), 0);
        f.observe(&json!({"filters":{"search":"ab"}}), 200);
        assert!(f.poll(300).is_none());
        let request = f.poll(450).unwrap();
        assert_eq!(request["context"]["search"], "ab");
        assert!(!f.matches(&request["context"], &json!({"filters":{"search":"abc"}})));
        f.observe(&json!({"filters":{"search":"abc"}}), 500);
        f.install(&json!([]), &json!({}));
        assert!(f.poll(2000).is_none());
    }
    #[test]
    fn incomplete_date_waits_for_valid_input() {
        let mut f = Filters::default();
        let nodes = json!([{"component":"CFilterBar","value":{"path":"/filters"},"onChange":"test_filter"},{"component":"CField","kind":"date","required":true,"value":{"path":"/filters/date"}}]);
        f.install(&nodes, &json!({"filters":{"date":"2026-10-08"}}));
        f.observe(&json!({"filters":{"date":"2026-"}}), 0);
        assert!(f.poll(1000).is_none());
        f.observe(&json!({"filters":{"date":"2026-10-09"}}), 1000);
        assert!(f.poll(1300).is_some());
    }
}
