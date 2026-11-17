//! What one reply produced, in the order a narrow client shows it.
//!
//! Phase 0 of docs/STAGE_A2UI_PLAN.md: the item the reply pointed at (or its
//! newest published item) leads, other deliverables follow as chips, and the
//! agent's exploration folds into one "worked through" row. Questions the
//! agent is waiting on are pulled out so they can stay pinned. Pure JSON over
//! the session's Stage items, shared by the phone and later the desktop canvas.
use serde_json::Value;

#[derive(Debug, Default, Clone, PartialEq)]
pub struct TurnOutputs {
    pub hero: Option<Value>,
    /// The reply's citations named the hero, rather than it being inferred here.
    pub pointed: bool,
    pub chips: Vec<Value>,
    pub work: Vec<Value>,
}

/// A question or secure connection form the agent is still waiting on.
/// Input requests use the canonical lifecycle the web chat panel reads.
pub fn is_open_request(surface: &Value) -> bool {
    let p = &surface["payload"];
    (surface["kind"] == "input" && p["request_state"]["status"] == "open")
        || (p["mode"] == "secure_action_request"
            && p["run"].is_null()
            && !matches!(p["status"].as_str(), Some("cancelled" | "complete" | "completed")))
        || awaits_sql_approval(p)
}

/// A frozen local SQL change waiting for an administrator's review.
fn awaits_sql_approval(p: &Value) -> bool {
    let run = &p["run"];
    run["status"] == "planned"
        && run["action_snapshot"]["executor"] == "local_sql"
        && run["action_snapshot"]["receipt_only"] == true
        && !matches!(
            run["verification"]["local_sql_review"]["state"].as_str(),
            Some("retired" | "unavailable")
        )
}

/// Exploration, specialist transcripts, receipts and settled questions.
fn is_work(surface: &Value) -> bool {
    surface["presentation"]["phase"] == "exploration"
        || surface["kind"] == "input"
        || matches!(
            surface["payload"]["mode"].as_str(),
            Some(
                "specialist_thread"
                    | "specialist_run"
                    | "specialist_handoff_failed"
                    | "action_receipt"
                    | "secure_action_request"
            )
        )
}

/// `items` are the session's visible Stage items, newest first.
pub fn turn_outputs(turn_id: &str, receipt: &Value, items: &[Value]) -> TurnOutputs {
    if turn_id.is_empty() {
        return TurnOutputs::default();
    }
    let own: Vec<&Value> = items.iter().filter(|s| s["turn_id"] == turn_id).collect();
    let spotlight = receipt["citations"]["spotlight"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|s| s["surface_id"].as_str())
        .find_map(|id| own.iter().copied().find(|s| s["id"] == id));
    let pointed = spotlight.is_some();
    let hero = spotlight
        .or_else(|| own.iter().copied().find(|s| !is_work(s) && !is_open_request(s)))
        .cloned();
    let hero_id = hero.as_ref().map(|h| h["id"].clone());
    let mut outputs = TurnOutputs { hero, pointed, ..Default::default() };
    for surface in own {
        if Some(&surface["id"]) == hero_id.as_ref() || is_open_request(surface) {
            continue;
        }
        if is_work(surface) {
            outputs.work.push(surface.clone());
        } else {
            outputs.chips.push(surface.clone());
        }
    }
    outputs
}

/// Open requests across the whole conversation, oldest first.
pub fn open_requests(items: &[Value]) -> Vec<Value> {
    items.iter().rev().filter(|s| is_open_request(s)).cloned().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn stage() -> Vec<Value> {
        // Newest first, as the session endpoint returns them.
        vec![
            json!({"id":"chart","turn_id":"t","kind":"chart","title":"Direct Sales led revenue","presentation":{}}),
            json!({"id":"validate","turn_id":"t","kind":"query","title":"Join validation","presentation":{"phase":"exploration"}}),
            json!({"id":"top","turn_id":"t","kind":"query","title":"Top channel by year","presentation":{"phase":"exploration"}}),
            json!({"id":"older","turn_id":"earlier","kind":"query","presentation":{}}),
        ]
    }

    #[test]
    fn the_reply_spotlight_leads_even_when_it_was_exploration() {
        let receipt = json!({"citations":{"spotlight":[{"surface_id":"top","reason":"explicit"}]}});
        let out = turn_outputs("t", &receipt, &stage());
        assert_eq!(out.hero.unwrap()["id"], "top");
        assert!(out.pointed);
        assert_eq!(out.chips.iter().map(|s| &s["id"]).collect::<Vec<_>>(), [&json!("chart")]);
        assert_eq!(out.work.iter().map(|s| &s["id"]).collect::<Vec<_>>(), [&json!("validate")]);
    }

    #[test]
    fn without_citations_the_newest_deliverable_leads_and_exploration_folds() {
        let out = turn_outputs("t", &Value::Null, &stage());
        assert_eq!(out.hero.unwrap()["id"], "chart");
        assert!(!out.pointed);
        assert!(out.chips.is_empty());
        assert_eq!(out.work.len(), 2);
        // A spotlight naming another turn's item is not this reply's hero.
        let foreign = json!({"citations":{"spotlight":[{"surface_id":"older"}]}});
        assert_eq!(turn_outputs("t", &foreign, &stage()).hero.unwrap()["id"], "chart");
        assert_eq!(turn_outputs("", &Value::Null, &stage()), TurnOutputs::default());
    }

    #[test]
    fn open_questions_are_pinned_not_folded() {
        let mut items = stage();
        items.insert(0, json!({"id":"ask","turn_id":"t","kind":"input","payload":{"request_state":{"status":"open"}}}));
        items.insert(1, json!({"id":"done","turn_id":"t","kind":"input","payload":{"request_state":{"status":"answered"}}}));
        items.insert(2, json!({"id":"connect","turn_id":"t","kind":"action","payload":{"mode":"secure_action_request","status":"awaiting_input"}}));
        items.insert(3, json!({"id":"connected","turn_id":"t","kind":"action","payload":{"mode":"secure_action_request","run":{"status":"complete"}}}));
        let sql = |id: &str, status: &str| json!({"id":id,"turn_id":"t","kind":"action","payload":{"mode":"action_receipt",
            "run":{"status":status,"action_snapshot":{"executor":"local_sql","receipt_only":true}}}});
        items.insert(4, sql("approve", "planned"));
        items.insert(5, sql("ran", "complete"));
        let out = turn_outputs("t", &Value::Null, &items);
        let work: Vec<_> = out.work.iter().map(|s| s["id"].as_str().unwrap()).collect();
        assert_eq!(out.hero.unwrap()["id"], "chart");
        assert!(work.contains(&"done") && work.contains(&"connected") && work.contains(&"ran"));
        assert!(!work.contains(&"ask") && !work.contains(&"connect") && !work.contains(&"approve"));
        let open: Vec<_> = open_requests(&items).iter().map(|s| s["id"].clone()).collect();
        assert_eq!(open, [json!("approve"), json!("connect"), json!("ask")]);
    }
}
