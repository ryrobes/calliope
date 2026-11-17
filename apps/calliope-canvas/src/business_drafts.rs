//! Durable local editing continuity; deliberately no offline command queue.
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};
pub fn key(snapshot: &Value) -> String {
    format!(
        "{}:{}",
        snapshot["view"].as_str().unwrap_or("dossier"),
        snapshot["selected"].as_str().unwrap_or("cedar")
    )
}
fn escaped(s: &str) -> String {
    s.replace('~', "~0").replace('/', "~1")
}
/// Only the editor committed by a command is cleared; sibling drafts survive.
pub fn acknowledge(entry: &Value, remote: &Value, paths: &Value) -> Value {
    let mut out = entry.clone();
    for path in paths
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
    {
        if !matches!(path, "/notes" | "/handoff" | "/receipt" | "/appointment") {
            continue;
        }
        for key in ["local", "base"] {
            if let Some(target) = out[key].pointer_mut(path) {
                *target = remote.pointer(path).cloned().unwrap_or(Value::Null);
            }
        }
        if let Some(conflicts) = out["conflicts"].as_object_mut() {
            conflicts.retain(|p, _| p != path && !p.starts_with(&format!("{path}/")));
        }
    }
    out
}
pub fn reconcile(base: &Value, local: &Value, remote: &Value, previous: &Value) -> Value {
    fn merge(
        b: Option<&Value>,
        l: Option<&Value>,
        r: Option<&Value>,
        path: &str,
        conflicts: &mut Map<String, Value>,
    ) -> Option<Value> {
        if path == "/id" || path == "/version" {
            return r.cloned();
        }
        if l == b {
            return r.cloned();
        }
        if r == b || l == r {
            return l.cloned();
        }
        if let (Some(Value::Object(b)), Some(Value::Object(l)), Some(Value::Object(r))) = (b, l, r)
        {
            let keys: BTreeSet<_> = b.keys().chain(l.keys()).chain(r.keys()).collect();
            let mut out = Map::new();
            for k in keys {
                if let Some(v) = merge(
                    b.get(k),
                    l.get(k),
                    r.get(k),
                    &format!("{path}/{}", escaped(k)),
                    conflicts,
                ) {
                    out.insert(k.clone(), v);
                }
            }
            return Some(Value::Object(out));
        }
        conflicts.insert(path.into(), json!({"local":l,"remote":r}));
        l.cloned()
    }
    let mut conflicts = Map::new();
    let draft =
        merge(Some(base), Some(local), Some(remote), "", &mut conflicts).unwrap_or(Value::Null);
    if let Some(prior) = previous.as_object() {
        for path in prior.keys() {
            if draft.pointer(path) != remote.pointer(path) {
                conflicts.insert(
                    path.clone(),
                    json!({"local":draft.pointer(path),"remote":remote.pointer(path)}),
                );
            }
        }
    }
    if !base["received_date"].is_null() && base["version"] != remote["version"] && local != base {
        conflicts.insert(
            "/version".into(),
            json!({"local":base["version"],"remote":remote["version"],"recordChanged":true}),
        );
    }
    if previous["/version"]["recordChanged"] == true {
        conflicts.insert("/version".into(), previous["/version"].clone());
    }
    json!({"base":remote,"local":draft,"conflicts":conflicts})
}
pub fn resolve(entry: &Value, use_server: bool) -> Value {
    let mut out = entry.clone();
    if use_server && entry["conflicts"]["/version"]["recordChanged"] == true {
        out["local"] = entry["base"].clone();
    }
    if use_server {
        if let Some(conflicts) = entry["conflicts"].as_object() {
            for path in conflicts.keys() {
                let next = entry["base"].pointer(path).cloned();
                let (parent, k) = path.rsplit_once('/').unwrap_or(("", path));
                let k = k.replace("~1", "/").replace("~0", "~");
                if let Some(object) = out["local"]
                    .pointer_mut(parent)
                    .and_then(Value::as_object_mut)
                {
                    if let Some(v) = next {
                        object.insert(k, v);
                    } else {
                        object.remove(&k);
                    }
                }
            }
        }
    }
    out["conflicts"] = json!({});
    out
}
pub fn consumes(name: &str) -> bool {
    matches!(
        name,
        "save_deal"
            | "counter_save"
            | "counter_add"
            | "counter_remove"
            | "counter_visit"
            | "receiving_post"
            | "dispatch_confirm"
            | "field_queue"
    )
}
pub fn navigation(name: &str) -> bool {
    matches!(
        name,
        "set_view"
            | "select_deal"
            | "hvac_select"
            | "counter_select"
            | "receiving_select"
            | "open_record"
            | "close_record"
            | "back_record"
            | "refresh"
            | "filter"
            | "hvac_filter"
            | "hvac_chart_select"
            | "hvac_chart_clear"
            | "receiving_filter"
            | "receiving_page"
            | "dispatch_preview"
            | "dispatch_select"
            | "dispatch_filter"
            | "field_select"
            | "adaptive_select"
            | "case_select"
            | "case_tool"
            | "case_save"
            | "case_pin"
            | "case_history"
            | "case_preferences"
            | "case_receipt_preview"
            | "case_receive_confirm"
            | "case_schedule_preview"
            | "case_schedule_confirm"
    )
}
pub fn load() -> BTreeMap<String, Value> {
    std::fs::read(crate::model::data_dir().join("business-drafts-v2.json"))
        .ok()
        .and_then(|v| serde_json::from_slice::<BTreeMap<String, Value>>(&v).ok())
        .filter(|v| v.len() <= 100)
        .unwrap_or_default()
}
pub fn save(entries: &BTreeMap<String, Value>) -> anyhow::Result<()> {
    crate::model::private_write(
        &crate::model::data_dir().join("business-drafts-v2.json"),
        &serde_json::to_vec(entries)?,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn independent_updates_and_conflicts() {
        let base =
            json!({"version":1,"notes":"base","lines":{"a":{"quantity":"1","unit_price":"2.00"}}});
        let mut local = base.clone();
        local["notes"] = json!("mine");
        local["lines"]["a"]["quantity"] = json!("2");
        let mut remote = base.clone();
        remote["version"] = json!(2);
        remote["lines"]["a"]["unit_price"] = json!("3.00");
        let merged = reconcile(&base, &local, &remote, &json!({}));
        assert_eq!(merged["local"]["notes"], "mine");
        assert_eq!(merged["local"]["version"], 2);
        assert_eq!(merged["local"]["lines"]["a"]["unit_price"], "3.00");
        assert_eq!(merged["conflicts"], json!({}));
        remote["lines"]["a"]["quantity"] = json!("4");
        let e = reconcile(&base, &local, &remote, &json!({}));
        let e = reconcile(&e["base"], &e["local"], &remote, &e["conflicts"]);
        assert!(!e["conflicts"]["/lines/a/quantity"].is_null());
        assert_eq!(resolve(&e, true)["local"]["lines"]["a"]["quantity"], "4");
        assert_eq!(resolve(&e, true)["local"]["notes"], "mine");
        assert_eq!(resolve(&e, false)["local"]["lines"]["a"]["quantity"], "2");
    }
    #[test]
    fn case_partial_ack_preserves_sibling_drafts() {
        let base = json!({"version":1,"notes":"old","handoff":"","receipt":{"version":1,"quantity":"0"},"appointment":{"version":1,"start":"08:00"}});
        let mut local = base.clone();
        local["handoff"] = json!("Roof access");
        local["receipt"]["quantity"] = json!("1");
        local["appointment"]["start"] = json!("14:00");
        let mut remote = base.clone();
        remote["receipt"]["version"] = json!(2);
        let e = acknowledge(
            &json!({"base":base,"local":local,"conflicts":{}}),
            &remote,
            &json!(["/receipt"]),
        );
        let result = reconcile(&e["base"], &e["local"], &remote, &e["conflicts"]);
        assert_eq!(result["local"]["receipt"], remote["receipt"]);
        assert_eq!(result["local"]["appointment"]["start"], "14:00");
        assert_eq!(result["local"]["handoff"], "Roof access");
    }
    #[test]
    fn receiving_requires_review() {
        let base = json!({"version":1,"received_date":"2026-10-08","lines":{"a":{"quantity":"0"}}});
        let mut local = base.clone();
        local["lines"]["a"]["quantity"] = json!("2");
        let mut remote = base.clone();
        remote["version"] = json!(2);
        let e = reconcile(&base, &local, &remote, &json!({}));
        let e = reconcile(&e["base"], &e["local"], &remote, &e["conflicts"]);
        assert_eq!(e["conflicts"]["/version"]["recordChanged"], true);
        assert_eq!(resolve(&e, true)["local"], remote);
    }
    #[test]
    fn deleted_line_conflict() {
        let base = json!({"lines":{"a":{"quantity":"1"}},"notes":""});
        let local = json!({"lines":{"a":{"quantity":"2"}},"notes":"mine"});
        let remote = json!({"lines":{},"notes":""});
        let e = reconcile(&base, &local, &remote, &json!({}));
        assert!(e["conflicts"].get("/lines/a").is_some());
        assert_eq!(
            resolve(&e, true)["local"],
            json!({"lines":{},"notes":"mine"})
        );
    }
}
