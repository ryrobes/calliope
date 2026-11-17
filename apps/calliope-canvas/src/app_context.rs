//! Immutable per-turn evidence and reviewable changes. No model-generated paths,
//! SQL, attachments or business actions enter the mutation path.
use anyhow::{Result, bail, ensure};
use serde_json::{Value, json};

fn sensitive(s: &str) -> bool {
    let s = s.to_ascii_lowercase();
    [
        "password",
        "secret",
        "token",
        "cookie",
        "authorization",
        "api_key",
        "apikey",
    ]
    .iter()
    .any(|x| s.contains(x))
}
fn bounded(v: &Value, depth: usize) -> Value {
    if depth > 5 {
        return Value::Null;
    }
    match v {
        Value::String(s) => json!(s.chars().take(1800).collect::<String>()),
        Value::Array(a) => Value::Array(a.iter().take(12).map(|v| bounded(v, depth + 1)).collect()),
        Value::Object(o) => Value::Object(
            o.iter()
                .filter(|(k, _)| !sensitive(k))
                .take(20)
                .map(|(k, v)| (k.clone(), bounded(v, depth + 1)))
                .collect(),
        ),
        _ => v.clone(),
    }
}
pub fn capture(snapshot: &Value, surface: &Value, offline: bool, fetched_at: u64) -> Value {
    let data = &surface["data"];
    let mut visible = Vec::new();
    let mut fields = Vec::new();
    let mut actions = Vec::new();
    for n in surface["nodes"].as_array().into_iter().flatten() {
        let p = &n["props"];
        let id = n["id"].as_str().unwrap_or("");
        let path = n["bindings"]["value"]["path"].as_str().unwrap_or("");
        if p["visible"] == false
            || p["sensitive"] == true
            || p["type"] == "password"
            || p["kind"] == "password"
            || sensitive(id)
            || sensitive(path)
            || sensitive(p["label"].as_str().unwrap_or(""))
        {
            continue;
        }
        let kind = n["component"].as_str().unwrap_or("");
        let form = crate::app_workspace::forms(snapshot).find(|f| {
            let root = f["path"].as_str().unwrap_or("");
            path.starts_with(&format!("{root}/"))
                && path != format!("{root}/{}", f["key"].as_str().unwrap_or("id"))
        });
        if matches!(
            kind,
            "CField" | "TextField" | "CheckBox" | "ChoicePicker" | "CRecordPicker"
        ) && n["rendered"] == true
            && form.is_some()
            && p["readOnly"] != true
            && p["disabled"] != true
            && p["value"].to_string().len() < 6000
            && fields.len() < 40
        {
            let form = form.unwrap();
            let root = form["path"].as_str().unwrap_or("");
            let key = form["key"].as_str().unwrap_or("id");
            // Refreshed snapshots intentionally retain the local draft. Compare
            // against the form's SQL source, never its hydrated editable copy.
            let saved = form["source"]
                .as_str()
                .and_then(|source| crate::app_workspace::model(snapshot).pointer(source))
                .filter(|source| {
                    !source[key].is_null()
                        && data.pointer(root).is_some_and(|draft| draft[key] == source[key])
                })
                .and_then(|source| source.pointer(&path[root.len()..]));
            let edited = saved.map(|value| data.pointer(path) != Some(value));
            let saved = saved.filter(|value| value.to_string().len() < 6000);
            fields.push(json!({"id":id,"label":p["label"],"component":kind,"kind":p["kind"],"path":path,"value":p["value"],"options":p["options"],"required":p["required"],"min":p["min"],"max":p["max"],"step":p["step"],"edited":edited,"saved_available":saved.is_some(),"saved_value":saved.map(|v|bounded(v,0))}));
        }
        let event = p["onSelect"]
            .as_str()
            .or_else(|| p.pointer("/action/event/name").and_then(Value::as_str));
        if let Some(event) = event {
            let command = snapshot["commands"]
                .as_array()
                .is_some_and(|a| a.iter().any(|v| v == event));
            let navigation = snapshot["navigation"]["events"]
                .as_object()
                .is_some_and(|events| events.contains_key(event));
            if actions.len() < 80
                && matches!(kind, "Button" | "CRecordItem")
                && p["disabled"] != true
                && (n["rendered"] == true || navigation && !command)
            {
                let label = p["label"]
                    .as_str()
                    .map(str::to_owned)
                    .or_else(|| {
                        surface["nodes"]
                            .as_array()
                            .and_then(|a| a.iter().find(|child| child["id"] == p["child"]))
                            .and_then(|child| child["props"]["text"].as_str())
                            .map(str::to_owned)
                    })
                    .unwrap_or_else(|| event.to_owned());
                actions.push(json!({"id":id,"label":label,"event":event,"record":p["recordId"],"navigation":navigation && !command,"submit":command}));
            }
        }
        if n["rendered"] == true && visible.len() < 100 {
            let mut facts = serde_json::Map::new();
            for key in [
                "text",
                "label",
                "title",
                "subtitle",
                "detail",
                "badge",
                "value",
                "rows",
                "columns",
                "items",
                "recordId",
                "selectedId",
            ] {
                if !p[key].is_null() {
                    facts.insert(key.into(), bounded(&p[key], 0));
                }
            }
            if kind == "CVegaChart" {
                facts.insert("chart_data".into(), bounded(&p["spec"]["data"], 0));
            }
            if !facts.is_empty() {
                visible.push(json!({"id":id,"component":kind,"content":facts}));
            }
        }
    }
    let records: Vec<_> = crate::app_workspace::forms(snapshot)
        .filter(|f| {
            fields.iter().any(|field| {
                field["path"].as_str().is_some_and(|p| {
                    p.starts_with(&format!("{}/", f["path"].as_str().unwrap_or("")))
                })
            })
        })
        .map(|f| {
            let path = f["path"].as_str().unwrap_or("");
            let id = data
                .pointer(path)
                .map(|v| &v[f["key"].as_str().unwrap_or("id")])
                .cloned()
                .unwrap_or_default();
            let label = surface["nodes"]
                .as_array()
                .and_then(|a| {
                    a.iter()
                        .find(|n| n["component"] == "CRecordItem" && n["props"]["recordId"] == id)
                })
                .map(|n| n["props"]["label"].clone())
                .unwrap_or_default();
            json!({"form":path,"id":id,"label":label})
        })
        .collect();
    let mut result = json!({"kind":"app_workspace","schema":"calliope.app-context.v1","context_id":uuid::Uuid::new_v4().to_string(),"slug":snapshot["slug"],"version":snapshot["version"],"title":snapshot["title"],"location":crate::app_workspace::location(snapshot,data),"offline":offline,"fetched_at":fetched_at,"records":records,"record_links":snapshot["records"].as_array().cloned().unwrap_or_default(),"fields":fields,"actions":actions,"visible":visible});
    while result.to_string().len() > 44_000 {
        if result["visible"].as_array_mut().unwrap().pop().is_none() {
            if result["fields"].as_array_mut().unwrap().pop().is_none() {
                break;
            }
        }
    }
    result
}

fn trailing_proposal(text: &str) -> Option<(usize, Value)> {
    text.match_indices('{')
        .rev()
        .filter(|(at, _)| text.len() - at <= 24_000)
        .find_map(|(at, _)| {
            let value: Value = serde_json::from_str(text[at..].trim()).ok()?;
            (value["context_id"].is_string()
                && (value["changes"].is_array() || value["navigate"].is_string()))
            .then_some((at, value))
        })
}
pub fn proposal(text: &str) -> Result<Option<Value>> {
    if let Some((_, tail)) = text.split_once("```calliope-ui") {
        let (body, rest) = tail
            .split_once("```")
            .ok_or_else(|| anyhow::anyhow!("Incomplete proposed changes"))?;
        ensure!(
            !rest.contains("```calliope-ui") && body.len() < 24_000,
            "Expected one bounded proposal"
        );
        return Ok(Some(serde_json::from_str(body.trim())?));
    }
    Ok(trailing_proposal(text).map(|(_, v)| v))
}
pub fn prose(text: &str) -> &str {
    if let Some((before, _)) = text.split_once("```calliope-ui") {
        return before.trim();
    }
    if let Some((at, _)) = trailing_proposal(text) {
        return text[..at].trim();
    }
    text.trim()
}

/// Returns semantic component operations only after ALL preconditions pass.
pub fn review(captured: &Value, current: &Value, proposal: &Value) -> Result<Vec<Value>> {
    ensure!(
        proposal["context_id"] == captured["context_id"],
        "This proposal belongs to a different message"
    );
    ensure!(
        captured["slug"] == current["slug"]
            && captured["version"] == current["version"]
            && captured["location"] == current["location"],
        "Return to the original record and app version to review this proposal"
    );
    if let Some(id) = proposal["navigate"].as_str() {
        ensure!(
            proposal.get("changes").is_none(),
            "Navigation cannot also edit a form"
        );
        let old = captured["actions"]
            .as_array()
            .and_then(|a| a.iter().find(|a| a["id"] == id && a["navigation"] == true))
            .ok_or_else(|| anyhow::anyhow!("This is not an offered navigation action"))?;
        ensure!(
            current["actions"]
                .as_array()
                .is_some_and(|a| a.iter().any(|a| a == old)),
            "Navigation changed; ask again"
        );
        return Ok(vec![json!({"op":"press","id":id})]);
    }
    let changes = proposal["changes"]
        .as_array()
        .ok_or_else(|| anyhow::anyhow!("Expected field changes"))?;
    ensure!(
        !changes.is_empty() && changes.len() <= 40,
        "Expected 1–40 field changes"
    );
    let mut result = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for change in changes {
        let id = change["field"].as_str().unwrap_or("");
        ensure!(seen.insert(id), "A field appears twice");
        let find = |context: &Value| {
            context["fields"]
                .as_array()
                .and_then(|a| a.iter().find(|f| f["id"] == id))
                .cloned()
        };
        let old =
            find(captured).ok_or_else(|| anyhow::anyhow!("Field was not offered for editing"))?;
        let now = find(current).ok_or_else(|| anyhow::anyhow!("Field is no longer editable"))?;
        ensure!(
            old == now,
            "A proposed field changed since this message. Ask Callie to update the suggestion"
        );
        let value = &change["value"];
        match old["component"].as_str().unwrap_or("") {
            "CField" | "TextField" => ensure!(
                value.is_string() && value.as_str().unwrap().len() <= 6000,
                "Expected bounded text"
            ),
            "CheckBox" => ensure!(value.is_boolean(), "Expected a checkbox value"),
            "ChoicePicker" | "CRecordPicker" => ensure!(
                value
                    .as_array()
                    .is_some_and(|a| a.iter().all(|v| old["options"]
                        .as_array()
                        .is_some_and(|o| o.iter().any(|o| o["value"] == *v)))),
                "Choose an offered option"
            ),
            _ => bail!("Unsupported field"),
        }
        result.push(json!({"op":"fill","id":id,"value":value}));
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn context() -> Value {
        json!({"context_id":"a","slug":"test","version":1,"location":"A","fields":[{"id":"notes","component":"CField","value":"old"}],"actions":[{"id":"submit","navigation":false}]})
    }
    #[test]
    fn stale_record_version_and_intervening_edits_never_apply() {
        let c = context();
        let p = json!({"context_id":"a","changes":[{"field":"notes","value":"new"}]});
        assert_eq!(review(&c, &c, &p).unwrap().len(), 1);
        for (key, v) in [
            ("location", json!("B")),
            ("version", json!(2)),
            ("slug", json!("other")),
        ] {
            let mut n = c.clone();
            n[key] = v;
            assert!(review(&c, &n, &p).is_err());
        }
        let mut n = c.clone();
        n["fields"][0]["value"] = json!("user edit");
        assert!(review(&c, &n, &p).is_err());
    }
    #[test]
    fn no_partial_changes_unknown_fields_or_command_navigation() {
        let c = context();
        assert!(review(&c,&c,&json!({"context_id":"a","changes":[{"field":"notes","value":"ok"},{"field":"id","value":"bad"}]})).is_err());
        assert!(review(&c, &c, &json!({"context_id":"a","navigate":"submit"})).is_err());
        assert!(
            review(
                &c,
                &c,
                &json!({"context_id":"wrong","changes":[{"field":"notes","value":"new"}]})
            )
            .is_err()
        );
    }
    #[test]
    fn plain_json_from_reply_formatter_remains_reviewable() {
        let text = "Prepared notes.\n\n{\"context_id\":\"a\",\"changes\":[{\"field\":\"notes\",\"value\":\"new\"}]}";
        assert_eq!(prose(text), "Prepared notes.");
        assert!(review(&context(), &context(), &proposal(text).unwrap().unwrap()).is_ok());
    }
    #[test]
    fn malformed_or_duplicate_proposals_are_rejected() {
        assert!(proposal("```calliope-ui {} ``` ```calliope-ui {} ```").is_err());
        assert!(proposal("```calliope-ui {").is_err());
        assert!(proposal("ordinary reply").unwrap().is_none());
    }
    #[test]
    fn only_rendered_non_secret_fields_are_exposed() {
        let s = json!({"slug":"test","version":1,"forms":[{"path":"/state/form","key":"id"}],"messages":[{},{},{"updateDataModel":{"value":{"state":{"form":{"id":1}}}}}]});
        let n = |id: &str, rendered: bool| json!({"id":id,"component":"CField","rendered":rendered,"props":{"label":id,"value":"private"},"bindings":{"value":{"path":format!("/state/form/{id}")}}});
        let c = capture(
            &s,
            &json!({"data":{"state":{"form":{"id":1}}},"nodes":[n("notes",true),n("password",true),n("hidden",false),n("id",true)]}),
            false,
            0,
        );
        assert_eq!(c["fields"].as_array().unwrap().len(), 1);
        assert!(!c.to_string().contains("password"));
        assert!(!c.to_string().contains("hidden"));
    }

    #[test]
    fn refreshed_drafts_stay_distinct_from_last_read_saved_values() {
        let mut snapshot = json!({"slug":"test","version":1,
            "forms":[{"path":"/state/form","source":"/record","key":"id"}],
            "messages":[{},{},{"updateDataModel":{"value":{
                "state":{"form":{"id":"A","notes":"Local edit"}},
                "record":{"id":"A","notes":"Saved note"}}}}]});
        let surface = json!({"data":{"state":{"form":{"id":"A","notes":"Local edit"}}},
            "nodes":[{"id":"notes","component":"CField","rendered":true,
                "props":{"label":"Notes","value":"Local edit"},
                "bindings":{"value":{"path":"/state/form/notes"}}}]});
        let field = |s: &Value| capture(s, &surface, true, 123)["fields"][0].clone();
        assert_eq!(field(&snapshot)["edited"], true);
        assert_eq!(field(&snapshot)["saved_value"], "Saved note");
        // A stale source for a different record must not be presented as saved evidence.
        snapshot["messages"][2]["updateDataModel"]["value"]["record"]["id"] = json!("B");
        assert_eq!(field(&snapshot)["saved_available"], false);
        assert!(field(&snapshot)["edited"].is_null());
        snapshot["messages"][2]["updateDataModel"]["value"]["record"] =
            json!({"id":"A","notes":"Local edit"});
        assert_eq!(field(&snapshot)["edited"], false);
    }

    #[test]
    fn collapsed_queue_offers_declared_navigation_but_never_commands() {
        let snapshot = json!({"slug":"test","version":1,"commands":["save"],
            "navigation":{"events":{"select":{"/state/selected":{"path":"/context/id"}},"save":{}}}});
        let surface = json!({"data":{},"nodes":[
            {"id":"visit-b","component":"CRecordItem","rendered":false,
             "props":{"label":"Visit B","recordId":"B","onSelect":"select"}},
            {"id":"save","component":"Button","rendered":false,
             "props":{"label":"Save","action":{"event":{"name":"save"}}}}]});
        let c = capture(&snapshot, &surface, false, 123);
        assert_eq!(c["actions"].as_array().unwrap().len(), 1);
        assert_eq!(c["actions"][0]["navigation"], true);
        let p = json!({"context_id":c["context_id"],"navigate":"visit-b"});
        assert!(review(&c, &c, &p).is_ok());
    }
}
